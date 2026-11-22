"""Analityka pracy kotla liczona z kazdej wiarygodnej ramki 0x35 (bez throttlingu read_interval).

OperationTracker calkuje czasy (tryby, podajnik, wentylator, zapalarka), energie i paliwo
oraz liczy zdarzenia (rozpalenia, wejscia w prace) i co FLUSH_INTERVAL_SEC dopisuje
przyrosty do tabeli daily_stats (dzien lokalny -> metryka -> wartosc). Zmiany trybu
zapisuje do tabeli mode_events.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Przerwa w danych dluzsza niz ta nie jest calkowana (brak polaczenia, restart).
MAX_GAP_SEC = 60.0
FLUSH_INTERVAL_SEC = 60.0

MODE_KINDLING = 2
MODE_WORKING = 3

METRIC_OBSERVED = 'observed_s'
METRIC_FEEDER = 'feeder_s'
METRIC_FAN = 'fan_s'
METRIC_LIGHTER = 'lighter_s'
METRIC_ENERGY = 'energy_kwh'
METRIC_FUEL = 'fuel_kg'
METRIC_KINDLINGS = 'kindlings'
METRIC_WORK_CYCLES = 'work_cycles'
METRIC_LIGHTER_STARTS = 'lighter_starts'
MODE_METRIC_PREFIX = 'mode_s_'


def mode_metric(mode: int) -> str:
    return f'{MODE_METRIC_PREFIX}{int(mode)}'


def local_day(timestamp: float) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(timestamp))


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class OperationTracker:
    """Calkowanie czasu pracy i zliczanie zdarzen z kolejnych ramek.

    on_frame() dziala w watku odczytu z EW11A i aktualizuje wylacznie pamiec;
    zapis do bazy (przyrosty i zdarzenia trybu) wykonuje flush() - z osobnego
    watku uruchamianego przez start() albo recznie (testy, zamykanie aplikacji).
    """

    def __init__(
        self,
        database: Any,
        *,
        flush_interval: float = FLUSH_INTERVAL_SEC,
        max_gap: float = MAX_GAP_SEC,
    ) -> None:
        self._db = database
        self._flush_interval = flush_interval
        self._max_gap = max_gap
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._pending: dict[tuple[str, str], float] = defaultdict(float)
        self._pending_events: list[tuple[float, int, Optional[int]]] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reset_state()

    def _reset_state(self) -> None:
        self._last_ts: Optional[float] = None
        self._last_mode: Optional[int] = None
        self._last_lighter: Optional[bool] = None
        # Stan z poprzedniej ramki - obowiazywal do nadejscia biezacej.
        self._prev_feeder = False
        self._prev_fan = False
        self._prev_power_kw: Optional[float] = None
        self._prev_fuel_kg_h: Optional[float] = None

    # ------------------------------------------------------------ watek zapisu

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._flush_loop, name='operation-tracker', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Zatrzymaj watek zapisu i zapisz pozostale dane."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.flush()

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(timeout=self._flush_interval):
            try:
                self.flush()
            except Exception as e:  # pragma: no cover - zabezpieczenie watku
                logger.error("Blad zapisu analityki: %s", e, exc_info=True)

    # ---------------------------------------------------------------- ramki

    def on_frame(self, data: Any, now: Optional[float] = None) -> None:
        """Przetworz wiarygodna ramke danych (BoilerData). Tylko pamiec - bez zapisu do bazy."""
        now = time.time() if now is None else now
        mode = getattr(data, 'mode', None)
        with self._lock:
            if not isinstance(mode, int) or isinstance(mode, bool):
                # Ramka bez trybu: nie przypisujemy czasu do zadnego trybu - przerwa w ciaglosci.
                self._last_ts = None
                return

            day = local_day(now)
            dt = None
            if self._last_ts is not None:
                delta = now - self._last_ts
                if 0 < delta <= self._max_gap:
                    dt = delta
            if dt is not None and self._last_mode is not None:
                # Stan z poprzedniej ramki obowiazywal przez dt.
                self._add(day, METRIC_OBSERVED, dt)
                self._add(day, mode_metric(self._last_mode), dt)
                if self._prev_feeder:
                    self._add(day, METRIC_FEEDER, dt)
                if self._prev_fan:
                    self._add(day, METRIC_FAN, dt)
                if self._last_lighter:
                    self._add(day, METRIC_LIGHTER, dt)
                if self._prev_power_kw and self._prev_power_kw > 0:
                    self._add(day, METRIC_ENERGY, self._prev_power_kw * dt / 3600.0)
                if self._prev_fuel_kg_h and self._prev_fuel_kg_h > 0:
                    self._add(day, METRIC_FUEL, self._prev_fuel_kg_h * dt / 3600.0)

            if mode != self._last_mode:
                if self._last_mode is not None:
                    if mode == MODE_KINDLING:
                        self._add(day, METRIC_KINDLINGS, 1)
                    if mode == MODE_WORKING:
                        self._add(day, METRIC_WORK_CYCLES, 1)
                self._pending_events.append((now, mode, self._last_mode))
                self._last_mode = mode

            lighter = bool(getattr(data, 'lighter_on', False))
            if self._last_lighter is False and lighter:
                self._add(day, METRIC_LIGHTER_STARTS, 1)
            self._last_lighter = lighter
            self._prev_feeder = bool(getattr(data, 'feeder_on', False))
            self._prev_fan = bool(getattr(data, 'fan_on', False))
            self._prev_power_kw = _number(getattr(data, 'boiler_power_kw', None))
            self._prev_fuel_kg_h = _number(getattr(data, 'fuel_stream', None))
            self._last_ts = now

    def _add(self, day: str, metric: str, value: float) -> None:
        self._pending[(day, metric)] += value

    # ---------------------------------------------------------------- zapis

    def flush(self) -> None:
        """Dopisz zgromadzone przyrosty i zdarzenia trybu do bazy (nieudany zapis jest ponawiany)."""
        with self._flush_lock:
            with self._lock:
                pending = dict(self._pending)
                self._pending.clear()
                events = list(self._pending_events)
                self._pending_events.clear()

            failed_events = []
            for timestamp, mode, previous_mode in events:
                if not self._db.add_mode_event(timestamp=timestamp, mode=mode, previous_mode=previous_mode):
                    failed_events.append((timestamp, mode, previous_mode))
            stats_ok = self._db.add_daily_stats(pending) if pending else True

            if failed_events or not stats_ok:
                with self._lock:
                    self._pending_events[:0] = failed_events
                    if not stats_ok:
                        for key, value in pending.items():
                            self._pending[key] += value

    def reset(self) -> None:
        """Porzuc dane w pamieci (po wyczyszczeniu statystyk w bazie)."""
        with self._flush_lock, self._lock:
            self._pending.clear()
            self._pending_events.clear()
            self._reset_state()
