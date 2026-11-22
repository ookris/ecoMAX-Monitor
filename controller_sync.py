"""Synchronizacja danych ze sterownikiem w tle.

Watek roboczy wykonuje zapytania do sterownika zlecane po zmianie wersji ramek
(tabela wersji z ramki 0x35) oraz okresowo. Zapytania nie moga byc wykonywane
w watku odczytu z EW11A - odpowiedz odbiera ten sam watek.

Zadania po zmianie wersji sa odkladane o kilka sekund (debounce): zapis
parametru z aplikacji zmienia wersje ramki, a natychmiastowy odczyt 255
parametrow blokowalby kolejne zapisy (wspolna blokada transakcji polaczenia).

Zadania:
- 'params': odczyt parametrow (0x31/0xB1), odswiezenie cache limitow w API
  i zapis do historii zmian parametrow zmienionych poza aplikacja (panel, ecoNET).
- 'alarms': odczyt historii alarmow (0x3D/0xBD) do tabeli controller_alarms;
  nowe alarmy (poza pierwszym importem) zglaszane jako alerty aplikacji.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from econet import alarms, protocol
from econet.boiler_control import PARAMETERS
from econet.ecomax_parameters import parameter_key

logger = logging.getLogger(__name__)

TASK_PARAMS = 'params'
TASK_ALARMS = 'alarms'

# Bezpiecznik stronicowania historii alarmow (10 alarmow na strone).
_MAX_ALARM_PAGES = 30

# Klucz konfiguracji: pierwszy import historii alarmow zostal wykonany.
CONFIG_ALARMS_IMPORTED = 'controller_alarms_imported'

# Typy ramek, ktorych zmiana wersji oznacza zmiane parametrow sterownika.
# 0x38 = REQUEST_ECOMAX_PARAMETER_CHANGES (PyPlumIO odpytuje wtedy 0x31).
_PARAMS_FRAME_TYPES = frozenset({protocol.FRAME_GET_PARAMS, protocol.FRAME_GET_SETTINGS})

# Okno, w ktorym zmiana wykryta w sterowniku jest uznawana za skutek zapisu z aplikacji.
_OWN_WRITE_WINDOW_SEC = 180

# Opoznienie zadania po zmianie wersji ramki (kolejne zmiany przesuwaja termin).
DEFAULT_DEBOUNCE_SEC = 10.0

# Zadania okresowe: odstep w sekundach (awaryjnie, gdy wersje ramek nie sa dostepne).
_PERIODIC_TASKS: dict[str, float] = {
    TASK_PARAMS: 15 * 60,
    TASK_ALARMS: 15 * 60,
}

_CATALOG_KEYS = {param.index: param.key for param in PARAMETERS}


class ControllerSync:
    """Kolejka zadan synchronizacji wykonywana w osobnym watku."""

    def __init__(
        self,
        connection: Any,
        database: Any,
        *,
        on_parameters: Optional[Callable[[dict], None]] = None,
        on_new_alarm: Optional[Callable[[dict], None]] = None,
        debounce_sec: float = DEFAULT_DEBOUNCE_SEC,
    ) -> None:
        self._connection = connection
        self._db = database
        self._on_parameters = on_parameters
        self._on_new_alarm = on_new_alarm
        self._debounce_sec = debounce_sec
        self._due: dict[str, float] = {}        # zadania zlecone: zadanie -> termin
        self._periodic_next: dict[str, float] = {}  # zadania okresowe: zadanie -> termin
        self._current_task: Optional[str] = None
        self._due_lock = threading.Lock()
        self._wakeup = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._param_snapshot: Optional[dict[int, int]] = None

    # ------------------------------------------------------------ cykl zycia

    def start(self) -> None:
        if self._running:
            return
        add_listener = getattr(self._connection, 'add_frame_version_listener', None)
        if add_listener is None:
            logger.info("ControllerSync: polaczenie nie wspiera wersji ramek (symulator) - pomijam")
            return
        add_listener(self._on_frame_versions)
        self._running = True
        now = time.time()
        with self._due_lock:
            for task, period in _PERIODIC_TASKS.items():
                self._periodic_next[task] = now + period
        self._thread = threading.Thread(target=self._worker, name='controller-sync', daemon=True)
        self._thread.start()
        logger.info("ControllerSync uruchomiony")

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._running = False
        self._wakeup.set()
        if self._thread:
            self._thread.join(timeout=5)

    # -------------------------------------------------------------- zlecanie

    def request(self, task: str, delay: float = 0.0) -> None:
        """Zlec zadanie za `delay` sekund.

        Ponowne zlecenie oczekujacego zadania ustawia nowy termin (debounce):
        przy serii zmian wersji zadanie wykona sie raz, po ustaniu zmian.
        """
        with self._due_lock:
            self._due[task] = time.time() + max(0.0, delay)
        self._wakeup.set()

    def pending_tasks(self) -> dict[str, float]:
        """Zadania oczekujace (zadanie -> termin) lacznie z aktualnie wykonywanym (termin 0)."""
        with self._due_lock:
            pending = dict(self._due)
            if self._current_task:
                pending[self._current_task] = 0.0
            return pending

    def _on_frame_versions(self, changes: dict) -> None:
        # Wywolywane w watku odczytu z EW11A - tylko zlecamy prace.
        if any(frame_type in _PARAMS_FRAME_TYPES for frame_type in changes):
            self.request(TASK_PARAMS, delay=self._debounce_sec)
        if protocol.FRAME_GET_ALARMS in changes:
            self.request(TASK_ALARMS, delay=self._debounce_sec)

    # --------------------------------------------------------------- worker

    def _take_due_tasks(self, now: float) -> list[str]:
        """Pobierz zadania, ktorych termin minal (zlecone lub okresowe)."""
        with self._due_lock:
            ready = [task for task, due in self._due.items() if due <= now]
            for task in ready:
                del self._due[task]
            for task, due in self._periodic_next.items():
                if due <= now and task not in ready:
                    ready.append(task)
            for task in ready:
                if task in _PERIODIC_TASKS:
                    self._periodic_next[task] = now + _PERIODIC_TASKS[task]
        return ready

    def _next_due(self) -> Optional[float]:
        with self._due_lock:
            dues = list(self._due.values()) + list(self._periodic_next.values())
        return min(dues) if dues else None

    def run_due_tasks(self, now: Optional[float] = None) -> list[str]:
        """Wykonaj zadania, ktorych termin minal (uzywane przez watek i testy)."""
        now = time.time() if now is None else now
        ready = self._take_due_tasks(now)
        for task in ready:
            if not getattr(self._connection, 'connected', False):
                continue
            with self._due_lock:
                self._current_task = task
            try:
                self._run_task(task)
            except TimeoutError as e:
                logger.info("ControllerSync: zadanie %s - brak odpowiedzi sterownika (%s)", task, e)
            except Exception as e:
                logger.error("ControllerSync: blad zadania %s: %s", task, e, exc_info=True)
            finally:
                with self._due_lock:
                    self._current_task = None
        return ready

    def _worker(self) -> None:
        while self._running:
            # clear() przed wykonaniem zadan: zlecenie w trakcie ich wykonywania
            # ustawi zdarzenie ponownie i nie zostanie zgubione.
            self._wakeup.clear()
            self.run_due_tasks()
            next_due = self._next_due()
            timeout = 30.0 if next_due is None else min(30.0, max(0.2, next_due - time.time()))
            self._wakeup.wait(timeout=timeout)

    def _run_task(self, task: str) -> None:
        if task == TASK_PARAMS:
            self.sync_parameters()
        elif task == TASK_ALARMS:
            self.sync_alarms()

    # ----------------------------------------------------------- parametry

    def sync_parameters(self) -> None:
        response = self._connection.request_ecomax_parameters(start=0, count=255, timeout=3.0)
        parameters = response.get('parameters') or {}
        if not parameters:
            return
        if self._on_parameters:
            self._on_parameters(parameters)

        values = {
            index: entry['value']
            for index, entry in parameters.items()
            if entry.get('available') and entry.get('value') is not None
        }
        previous = self._param_snapshot
        self._param_snapshot = values
        if previous is None:
            return

        now = time.time()
        for index, new_value in sorted(values.items()):
            old_value = previous.get(index)
            if old_value is None or old_value == new_value:
                continue
            if self._db.has_recent_boiler_change(
                target_type='ecomax',
                param_index=index,
                new_value=str(new_value),
                since=now - _OWN_WRITE_WINDOW_SEC,
            ):
                continue
            key = _CATALOG_KEYS.get(index) or parameter_key(index)
            logger.info("Wykryto zmiane parametru %s (#%d) poza aplikacja: %s -> %s",
                        key, index, old_value, new_value)
            self._db.log_boiler_setting_change(
                source='panel',
                target_type='ecomax',
                target_index=0,
                param_key=key,
                param_index=index,
                old_value=str(old_value),
                new_value=str(new_value),
                status='ok',
                message='Zmiana wykryta w sterowniku (panel lub inne urządzenie)',
            )

    # -------------------------------------------------------------- alarmy

    def sync_alarms(self) -> dict[str, int]:
        """Pobierz historie alarmow ze sterownika i zapisz zmiany w bazie.

        Pierwszy import pobiera wszystkie strony i nie zglasza alertow; znacznik importu
        jest zapisywany dopiero po pelnym przebiegu (przerwany import zostanie powtorzony).
        Kolejne synchronizacje koncza stronicowanie na pierwszej stronie bez zmian
        (alarmy sa uporzadkowane od najnowszego), chyba ze w bazie sa starsze
        trwajace alarmy - wtedy czytamy dalej, aby uzupelnic ich koniec.
        """
        if not hasattr(self._connection, 'request_alerts'):
            return {'new': 0, 'updated': 0, 'unchanged': 0, 'invalid': 0, 'total': 0}
        future_limit = time.time() + 86400
        initial_import = self._db.get_config(CONFIG_ALARMS_IMPORTED, '0') != '1'
        oldest_ongoing = self._db.oldest_ongoing_controller_alarm_ts()
        stats = {'new': 0, 'updated': 0, 'unchanged': 0, 'invalid': 0, 'total': 0}
        new_alarms: list[dict] = []

        start = 0
        for _ in range(_MAX_ALARM_PAGES):
            page = self._connection.request_alerts(start=start, count=alarms.ALERTS_PAGE_SIZE)
            stats['total'] = page['total']
            page_changed = False
            page_oldest = None
            for alarm in page['alerts']:
                start_dt, end_dt = alarm['start'], alarm['end']
                if (
                    alarm.get('invalid')
                    or start_dt.timestamp() > future_limit
                    or (end_dt is not None and end_dt.timestamp() > future_limit)
                ):
                    # Uszkodzony znacznik czasu w sterowniku (np. po resecie zegara).
                    stats['invalid'] += 1
                    continue
                page_oldest = start_dt.timestamp() if page_oldest is None else min(page_oldest, start_dt.timestamp())
                result = self._db.upsert_controller_alarm(
                    code=alarm['code'],
                    start_text=start_dt.isoformat(sep=' '),
                    start_ts=start_dt.timestamp(),
                    end_text=end_dt.isoformat(sep=' ') if end_dt else None,
                    end_ts=end_dt.timestamp() if end_dt else None,
                )
                stats[result] += 1
                if result != 'unchanged':
                    page_changed = True
                if result == 'new':
                    new_alarms.append(alarm)

            start += alarms.ALERTS_PAGE_SIZE
            if start >= page['total'] or not page['alerts']:
                break
            if not initial_import and not page_changed:
                needs_ongoing_update = (
                    oldest_ongoing is not None and page_oldest is not None and page_oldest > oldest_ongoing
                )
                if not needs_ongoing_update:
                    break

        if initial_import:
            self._db.set_config(CONFIG_ALARMS_IMPORTED, '1')

        if stats['new'] or stats['updated']:
            logger.info("Alarmy sterownika: %d nowych, %d zaktualizowanych (lacznie w sterowniku: %d)",
                        stats['new'], stats['updated'], stats['total'])

        if not initial_import and self._on_new_alarm:
            for alarm in sorted(new_alarms, key=lambda a: a['start']):
                try:
                    self._on_new_alarm(alarm)
                except Exception as e:
                    logger.error("Blad zglaszania alarmu sterownika: %s", e, exc_info=True)
        return stats
