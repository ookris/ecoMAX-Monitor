"""Silnik alertow - sprawdza warunki alarmowe i wysyla powiadomienia."""

from __future__ import annotations

import time
import threading
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from database import Database
    from econet.decoder import BoilerData

logger = logging.getLogger(__name__)

ALERT_LABELS = {
    'alarm_mode':    'Tryb ALARM pieca',
    'temp_flue_high': 'Wysoka temp. spalin',
    'temp_co_low':   'Niska temp. CO',
    'fuel_low':      'Niski poziom paliwa',
    'no_connection': 'Brak polaczenia z EW11A',
}


class AlertEngine:
    """Sprawdza warunki alarmowe i emituje zdarzenia przy ich wystapeniu."""

    def __init__(self, database: Database, emit_fn: Callable[[dict], None]) -> None:
        self._db = database
        self._emit = emit_fn
        self._active = {}          # alert_type -> bool - czy alert jest aktywny
        self._last_data_time = None
        self._running = False
        self._lock = threading.Lock()         # chroni _last_data_time
        self._active_lock = threading.RLock() # chroni _active (reentrant: _check -> _fire)

    def check(self, boiler_data: BoilerData) -> None:
        """Wywolywane przy kazdym nowym odczycie z kotla."""
        with self._lock:
            self._last_data_time = time.time()

        cfg = self._db.get_all_config()

        # Tryb ALARM (mode=8)
        self._check('alarm_mode',
                    boiler_data.mode == 8,
                    'Piec przeszedl w tryb ALARM')

        # Wysoka temperatura spalin
        if boiler_data.temp_flue_gas is not None:
            try:
                limit = float(cfg.get('alert_temp_flue_max', '250'))
            except (ValueError, TypeError):
                limit = 250.0
            self._check('temp_flue_high',
                        boiler_data.temp_flue_gas > limit,
                        f'Temperatura spalin {boiler_data.temp_flue_gas:.1f}\u00b0C '
                        f'przekroczyla prog {limit:.0f}\u00b0C')

        # Niska temperatura CO (tylko gdy piec w trybie Zaplon=2 lub Praca=3)
        if boiler_data.temp_co is not None and boiler_data.mode in (2, 3):
            try:
                limit = float(cfg.get('alert_temp_co_min', '30'))
            except (ValueError, TypeError):
                limit = 30.0
            self._check('temp_co_low',
                        boiler_data.temp_co < limit,
                        f'Temperatura CO {boiler_data.temp_co:.1f}\u00b0C '
                        f'ponizej progu {limit:.0f}\u00b0C')

        # Niski poziom paliwa
        if boiler_data.fuel_level is not None:
            try:
                limit = float(cfg.get('alert_fuel_level_min', '15'))
            except (ValueError, TypeError):
                limit = 15.0
            self._check('fuel_low',
                        boiler_data.fuel_level < limit,
                        f'Poziom paliwa {boiler_data.fuel_level:.0f}% '
                        f'ponizej progu {limit:.0f}%')

    def _check(self, alert_type: str, condition: bool, message: str) -> None:
        """Wyslij alert przy zmianie stanu z nieaktywny -> aktywny."""
        with self._active_lock:
            was_active = self._active.get(alert_type, False)
            if condition and not was_active:
                self._active[alert_type] = True
                self._fire(alert_type, message)
            elif not condition and was_active:
                self._active[alert_type] = False

    def _fire(self, alert_type: str, message: str) -> None:
        alert = {
            'type':      alert_type,
            'label':     ALERT_LABELS.get(alert_type, alert_type),
            'message':   message,
            'timestamp': time.time(),
        }
        logger.warning("ALERT [%s]: %s", alert_type, message)
        self._db.save_alert(alert)
        try:
            self._emit(alert)
        except Exception as e:
            logger.error("Blad wysylania alertu WebSocket: %s", e, exc_info=True)

    def start(self):
        """Uruchom watek monitorujacy polaczenie z EW11A."""
        if hasattr(self, '_monitor_thread') and self._monitor_thread.is_alive():
            return
        self._running = True
        self._stop_event = threading.Event()
        self._monitor_thread = threading.Thread(target=self._monitor_connection, daemon=True)
        self._monitor_thread.start()
        logger.info("AlertEngine uruchomiony")

    def stop(self):
        self._running = False
        if hasattr(self, '_stop_event'):
            self._stop_event.set()
        if hasattr(self, '_monitor_thread'):
            self._monitor_thread.join(timeout=5)

    def _monitor_connection(self):
        """Co 30s sprawdza czy dane wciaz docieraja."""
        alerted = False
        try:
            while self._running:
                self._stop_event.wait(timeout=30)
                if not self._running:
                    break

                with self._lock:
                    last = self._last_data_time

                if last is None:
                    continue

                cfg = self._db.get_all_config()

                try:
                    limit_min = float(cfg.get('alert_no_connection_min', '5'))
                except (ValueError, TypeError):
                    limit_min = 5.0
                elapsed_min = (time.time() - last) / 60.0

                if elapsed_min > limit_min and not alerted:
                    self._fire('no_connection',
                               f'Brak danych z EW11A przez {elapsed_min:.0f} min')
                    alerted = True
                elif elapsed_min <= limit_min and alerted:
                    alerted = False
                    with self._active_lock:
                        self._active.pop('no_connection', None)
        finally:
            self._running = False
