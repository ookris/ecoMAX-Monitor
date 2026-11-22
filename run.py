#!/usr/bin/env python3
"""Entry point dla ecoMAX Monitor.

Uruchomienie:
    python run.py                    # Tryb normalny (polaczenie z EW11A)
    python run.py --simulate         # Tryb symulacji (bez EW11A)
    python run.py --host 192.168.1.50 --port 8899  # Niestandardowy adres EW11A
"""

import argparse
import logging
import os
import random
import signal
import sys
import threading
import time

# Dodaj katalog app do sciezki
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alerts import AlertEngine
from analytics import OperationTracker
from controller_sync import ControllerSync
from database import Database
from derived_data import apply_config_overrides
from econet.connection import EW11AConnection
from econet.decoder import BoilerData
from logger import setup_file_logging, cleanup_old_logs
from web.app import create_app, socketio, emit_data, emit_alert
from web.routes import init_routes, update_boiler_limits_cache

# Stale startowe (niezalezne od bazy danych)
_FLASK_HOST        = "0.0.0.0"
_FLASK_PORT        = 5050
_DB_PATH           = "data/measurements.db"
_DB_SAVE_INTERVAL  = 60
_DB_RETENTION_DAYS = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('ecomax-monitor')

class _IgnoreInvalidSessionFilter(logging.Filter):
    """Filtr usuwa tylko szum: "Invalid session ..." z engineio/socketio."""

    def filter(self, record):
        if record.name not in ('engineio.server', 'socketio.server'):
            return True
        message = record.getMessage()
        return 'Invalid session' not in message


# Engine.IO/Socket.IO loguja "Invalid session" jako ERROR po restarcie serwera.
# To normalne zachowanie - klient probuje wznowic stara sesje, dostaje odmowe
# i automatycznie nawiazuje nowa. Nie jest to blad wymagajacy reakcji.
_invalid_session_filter = _IgnoreInvalidSessionFilter()
logging.getLogger('engineio.server').addFilter(_invalid_session_filter)
logging.getLogger('socketio.server').addFilter(_invalid_session_filter)


def parse_args():
    parser = argparse.ArgumentParser(description='ecoMAX Monitor')
    parser.add_argument('--host', default=None,
                        help='IP modulu EW11A (domyslnie z bazy lub 192.168.1.100)')
    parser.add_argument('--port', type=int, default=None,
                        help='Port TCP EW11A (domyslnie z bazy lub 8899)')
    parser.add_argument('--simulate', action='store_true',
                        help='Tryb symulacji - generuj losowe dane')
    parser.add_argument('--web-port', type=int, default=_FLASK_PORT,
                        help=f'Port serwera WWW (domyslnie: {_FLASK_PORT})')
    parser.add_argument('--debug', action='store_true',
                        help='Tryb debug')
    return parser.parse_args()


class Simulator:
    """Symulator danych z kotla (do testow bez sprzetu)."""

    def __init__(self, on_data_callback, interval=5, preprocess_callback=None, frame_callback=None):
        self.on_data_callback = on_data_callback
        self.preprocess_callback = preprocess_callback
        self.frame_callback = frame_callback
        self.interval = interval
        self._running = False
        self._thread = None
        self._connected = True
        self.frames_received = 0
        self.bytes_received = 0
        self.last_frame_time = None
        self.errors = 0
        self._lock = threading.Lock()
        self._last_data = None

    @property
    def connected(self):
        return self._connected

    @property
    def last_data(self):
        with self._lock:
            return self._last_data

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Symulator uruchomiony (dane co %ds)", self.interval)

    def stop(self):
        self._running = False

    def get_status(self):
        return {
            'connected': True,
            'host': 'simulator',
            'port': 0,
            'frames_received': self.frames_received,
            'bytes_received': self.bytes_received,
            'last_frame_time': self.last_frame_time,
            'errors': 0,
        }

    def _loop(self):
        base_temp = 55.0
        base_cwu = 45.0
        mode = 3  # Praca

        while self._running:
            data = BoilerData()
            data.timestamp = time.time()
            data.mode = mode
            data.mode_name = "Praca" if mode == 3 else "Nadzór"

            # Temperatury z lekkimi wahaniami
            data.temp_co = round(base_temp + random.uniform(-2, 2), 1)
            data.temp_feeder = round(45 + random.uniform(-3, 3), 1)
            data.temp_cwu = round(base_cwu + random.uniform(-1, 1), 1)
            data.temp_back = round(data.temp_co - 10 + random.uniform(-2, 2), 1)
            data.temp_flue_gas = round(150 + random.uniform(-20, 20), 1)
            data.temp_external = round(3 + random.uniform(-2, 2), 1)

            data.temp_co_set = 60
            data.temp_cwu_set = 50

            data.fan_power = round(50 + random.uniform(-10, 10), 1)
            data.boiler_power = round(65 + random.uniform(-15, 15))
            data.boiler_power_kw = round(data.boiler_power * 0.15, 1)
            data.fuel_level = round(70 + random.uniform(-5, 5))
            data.fuel_stream = round(2.5 + random.uniform(-0.5, 0.5), 2)
            data.flame = round(60 + random.uniform(-10, 10), 1)

            data.pump_co = True
            data.pump_cwu = random.random() > 0.5
            data.pump_circulation = random.random() > 0.7

            data.fuel_burned_kg = round(1250 + random.uniform(0, 50), 1)
            data.ignitions = 187
            data.work_time_100 = 450
            data.work_time_50 = 820
            data.work_time_33 = 1100

            # Bledy callbackow nie moga zatrzymac petli symulatora (jak w EW11AConnection).
            for callback in (self.frame_callback, self.preprocess_callback):
                if callback:
                    try:
                        callback(data)
                    except Exception as e:
                        logger.error("Symulator: blad callbacku: %s", e, exc_info=True)
            with self._lock:
                self._last_data = data
            self.frames_received += 1
            self.last_frame_time = time.time()

            if self.on_data_callback:
                try:
                    self.on_data_callback(data)
                except Exception as e:
                    logger.error("Symulator: blad on_data_callback: %s", e, exc_info=True)

            # Powolne zmiany
            base_temp += random.uniform(-0.3, 0.3)
            base_temp = max(40, min(80, base_temp))
            base_cwu += random.uniform(-0.2, 0.2)
            base_cwu = max(35, min(55, base_cwu))

            time.sleep(self.interval)


def main():
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Baza danych
    db = Database(_DB_PATH, _DB_RETENTION_DAYS)

    # Skonfiguruj logowanie do pliku (na podstawie ustawień w bazie)
    log_enabled = db.get_config('log_enabled', '1') == '1'
    if log_enabled:
        log_handler = setup_file_logging(enabled=True)
        if log_handler:
            root_logger = logging.getLogger()
            root_logger.addHandler(log_handler)
            logger.info(
                "Logowanie do pliku włączone dla wszystkich modułów aplikacji"
            )

    # Odczyt host/port EW11A z bazy (nadpisywane przez CLI jesli podano)
    ew11a_host = args.host or db.get_config('ew11a_host', '192.168.1.100')
    ew11a_port = args.port or int(db.get_config('ew11a_port', '8899'))

    # Interwaly z bazy (lub domyslne)
    read_interval = int(db.get_config('read_interval', '5'))
    db_save_interval = int(db.get_config('db_save_interval', str(_DB_SAVE_INTERVAL)))

    # Silnik alertow
    alert_engine = AlertEngine(db, emit_alert)

    # Callback przy nowych danych
    last_save_time = 0.0

    def on_new_data(boiler_data):
        nonlocal last_save_time
        # Wyslij przez WebSocket
        emit_data(boiler_data)

        # Sprawdz alerty
        alert_engine.check(boiler_data)

        # Zapisz do bazy (co db_save_interval sekund)
        now = time.time()
        if now - last_save_time >= db_save_interval:
            db.save_measurement(boiler_data.to_dict())
            last_save_time = now

    def preprocess_data(boiler_data):
        # Korekty zalezne od ustawien (poziom paliwa, moc %) - stosowane przed
        # opublikowaniem danych w connection.last_data, aby REST API, /api/ha,
        # /metrics i WebSocket widzialy te same wartosci.
        apply_config_overrides(boiler_data, db.get_all_config())

    # Analityka pracy kotla (z kazdej ramki, niezaleznie od read_interval). Symulator
    # generuje ramke co read_interval, wiec dopuszczalna przerwa musi byc od niego dluzsza.
    operation_tracker = OperationTracker(
        db,
        max_gap=max(60.0, read_interval * 2.5) if args.simulate else 60.0,
    )

    # Polaczenie z EW11A lub symulator
    if args.simulate:
        connection = Simulator(on_data_callback=on_new_data, interval=read_interval,
                               preprocess_callback=preprocess_data,
                               frame_callback=operation_tracker.on_frame)
        logger.info("=== Tryb SYMULACJI ===")
    else:
        connection = EW11AConnection(
            host=ew11a_host,
            port=ew11a_port,
            on_data_callback=on_new_data,
            read_interval=read_interval,
            preprocess_callback=preprocess_data,
            frame_callback=operation_tracker.on_frame,
        )
        logger.info("Laczenie z EW11A: %s:%d", ew11a_host, ew11a_port)

    logger.info(
        "Interwały: odczyt=%ss, zapis_do_bazy=%ss",
        read_interval,
        db_save_interval,
    )

    def on_new_controller_alarm(alarm):
        end = alarm.get('end')
        period = f"do {end:%Y-%m-%d %H:%M}" if end else "trwa"
        alert_engine.fire(
            'controller_alarm',
            f"{alarm['name']} (kod {alarm['code']}) od {alarm['start']:%Y-%m-%d %H:%M}, {period}",
        )

    controller_sync = ControllerSync(
        connection,
        db,
        on_parameters=update_boiler_limits_cache,
        on_new_alarm=on_new_controller_alarm,
    )

    # Flask app
    app = create_app()
    init_routes(connection, db, controller_sync, operation_tracker)

    # Start polaczenia/symulatora
    controller_sync.load_stored_regdata_schema()
    controller_sync.start()
    operation_tracker.start()
    connection.start()
    alert_engine.start()

    # Czyszczenie bazy i logów co godzine
    _stop_event = threading.Event()

    def db_cleanup_loop():
        while not _stop_event.wait(timeout=3600):
            db.cleanup()
            # Czyszczenie starych logów
            log_retention_days = int(db.get_config('log_retention_days', '14'))
            deleted = cleanup_old_logs(retention_days=log_retention_days)
            if deleted > 0:
                logger.debug("Usunięto %d starych plików logów", deleted)

    cleanup_thread = threading.Thread(target=db_cleanup_loop, daemon=True)
    cleanup_thread.start()

    # Graceful shutdown przy SIGTERM / SIGINT
    def _shutdown(signum, frame):
        logger.info("Otrzymano sygnal %d - zamykanie...", signum)
        _stop_event.set()
        controller_sync.stop()
        alert_engine.stop()
        connection.stop()
        operation_tracker.stop()
        db.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("=== ecoMAX Monitor startuje na http://%s:%d ===",
                _FLASK_HOST, args.web_port)

    # Start serwera
    socketio.run(
        app,
        host=_FLASK_HOST,
        port=args.web_port,
        debug=args.debug,
        use_reloader=False,
    )


if __name__ == '__main__':
    main()
