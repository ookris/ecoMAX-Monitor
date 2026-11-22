"""Baza danych SQLite do przechowywania historii pomiarow."""

from __future__ import annotations

import sqlite3
import time
import os
import threading
import logging
from typing import Any, Optional

from sensors import DEFAULT_VISIBLE_SENSORS

logger = logging.getLogger(__name__)

# Predefiniowane zapytania statystyczne - eliminuje f-string w SQL
_STAT_QUERIES = {
    field: (
        f"SELECT MIN({field}) as min_val, MAX({field}) as max_val, "  # noqa: S608
        f"AVG({field}) as avg_val, COUNT({field}) as count "
        f"FROM measurements WHERE timestamp > ? AND {field} IS NOT NULL"
    )
    for field in (
        'temp_co', 'temp_cwu', 'temp_back', 'temp_flue_gas',
        'temp_feeder', 'temp_external',
        'boiler_power', 'fuel_stream'
    )
}


# Kolumny tabeli measurements zapisywane przez save_measurement (poza id).
_MEASUREMENT_COLUMNS: tuple[str, ...] = (
    'timestamp', 'temp_co', 'temp_feeder', 'temp_cwu',
    'temp_external', 'temp_back', 'temp_flue_gas', 'temp_optical',
    'temp_co_set', 'temp_cwu_set', 'fan_power', 'boiler_power',
    'boiler_power_kw', 'fuel_level', 'fuel_stream',
    'mode', 'mode_name', 'pump_co', 'pump_cwu', 'pump_circulation',
    'flame', 'blower', 'fuel_burned_kg', 'ignitions',
    'boiler_power_source',
)

# Kolumny dodane po pierwszej wersji schematu: migracja addytywna (ALTER TABLE ADD COLUMN).
_MEASUREMENT_ADDED_COLUMNS: dict[str, str] = {
    'boiler_power_source': 'TEXT',
}

_INSERT_MEASUREMENT_SQL = (
    f"INSERT INTO measurements ({', '.join(_MEASUREMENT_COLUMNS)}) "  # noqa: S608
    f"VALUES ({', '.join(':' + c for c in _MEASUREMENT_COLUMNS)})"
)


class Database:
    """Zarzadza baza SQLite z pomiarami."""

    def __init__(self, db_path: str, retention_days: int = 30) -> None:
        self.db_path = db_path
        self.retention_days = retention_days
        self._lock = threading.RLock()  # RLock: cleanup() wywoluje downsample_old_data()

        if db_path != ':memory:':
            os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    # Domyslne wartosci konfiguracji
    CONFIG_DEFAULTS = {
        'ew11a_host':             '192.168.1.100',
        'ew11a_port':             '8899',
        'weather_lat':            '',
        'weather_lon':            '',
        'read_interval':          '5',
        'db_save_interval':       '60',
        'visible_sensors':        DEFAULT_VISIBLE_SENSORS,
        'fuel_price_per_ton':     '1000',
        'alert_temp_flue_max':    '250',
        'alert_temp_co_min':      '30',
        'alert_fuel_level_min':   '15',
        'fuel_level_not_set':     '0',
        'boiler_nominal_power_kw': '',
        'alert_no_connection_min': '5',
        'alert_retention_mode':   'days',
        'alert_retention_days':   '30',
        'alert_retention_count':  '1000',
        'db_retention_days':      '30',
        'log_enabled':            '1',
        'log_retention_days':     '14',
        'time_zone':              'Europe/Warsaw',
        'time_ntp_server':        'pool.ntp.org',
    }

    def _init_db(self):
        """Utworz tabele jesli nie istnieja."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                temp_co REAL,
                temp_feeder REAL,
                temp_cwu REAL,
                temp_external REAL,
                temp_back REAL,
                temp_flue_gas REAL,
                temp_optical REAL,
                temp_co_set REAL,
                temp_cwu_set REAL,
                fan_power REAL,
                boiler_power REAL,
                boiler_power_kw REAL,
                fuel_level REAL,
                fuel_stream REAL,
                mode INTEGER,
                mode_name TEXT,
                pump_co INTEGER,
                pump_cwu INTEGER,
                pump_circulation INTEGER,
                flame REAL,
                blower REAL,
                fuel_burned_kg REAL,
                ignitions INTEGER
            )
        """)
        self._ensure_columns('measurements', _MEASUREMENT_ADDED_COLUMNS)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON measurements(timestamp)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                type      TEXT NOT NULL,
                label     TEXT NOT NULL,
                message   TEXT NOT NULL,
                read      INTEGER DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alerts_timestamp
            ON alerts(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_alerts_read
            ON alerts(read)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS boiler_setting_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                source TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_index INTEGER,
                param_key TEXT NOT NULL,
                param_index INTEGER,
                old_value TEXT,
                new_value TEXT,
                status TEXT NOT NULL,
                message TEXT,
                request_id TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_boiler_setting_changes_timestamp
            ON boiler_setting_changes(timestamp DESC)
        """)
        self._conn.commit()

    def _ensure_columns(self, table: str, columns: dict[str, str]) -> None:
        """Dodaj brakujace kolumny (migracja addytywna istniejacych baz)."""
        existing = {row['name'] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        for name, col_type in columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
                logger.info("Migracja bazy: dodano kolumne %s.%s", table, name)

    def save_measurement(self, data: dict[str, Any]) -> None:
        """Zapisz pomiar do bazy.

        Args:
            data: BoilerData.to_dict() lub slownik z danymi; brakujace klucze
                zapisywane sa jako NULL.
        """
        params = {column: data.get(column) for column in _MEASUREMENT_COLUMNS}
        with self._lock:
            try:
                self._conn.execute(_INSERT_MEASUREMENT_SQL, params)
                self._conn.commit()
            except sqlite3.Error as e:
                logger.error("Blad zapisu do bazy: %s", e)

    def get_latest(self) -> Optional[dict[str, Any]]:
        """Pobierz ostatni pomiar."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT * FROM measurements ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                return dict(row) if row else None
            except sqlite3.Error as e:
                logger.error("Blad odczytu z bazy: %s", e)
                return None

    def get_history(self, hours: float = 24, limit: int = 1000) -> list[dict[str, Any]]:
        """Pobierz historie pomiarow.

        Args:
            hours: ile godzin wstecz
            limit: maksymalna liczba rekordow
        """
        since = time.time() - (hours * 3600)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT * FROM measurements
                       WHERE timestamp > ?
                       ORDER BY timestamp ASC
                       LIMIT ?""",
                    (since, limit)
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error as e:
                logger.error("Blad odczytu historii: %s", e)
                return []

    def get_history_desc(self, hours: float = 24, limit: int = 200) -> list[dict[str, Any]]:
        """Pobierz historię pomiarów w kolejności malejącej (najnowsze pierwsze)."""
        since = time.time() - (hours * 3600)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT * FROM measurements
                       WHERE timestamp > ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (since, limit)
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error as e:
                logger.error("Blad odczytu historii (desc): %s", e)
                return []

    def get_statistics(self, hours: float = 24) -> dict[str, Any]:
        """Oblicz statystyki (min/max/avg) dla wybranych parametrow."""
        since = time.time() - (hours * 3600)
        stats = {}
        with self._lock:
            try:
                for field, query in _STAT_QUERIES.items():
                    row = self._conn.execute(query, (since,)).fetchone()
                    if row and row['count'] > 0:
                        stats[field] = {
                            'min': round(row['min_val'], 1),
                            'max': round(row['max_val'], 1),
                            'avg': round(row['avg_val'], 1),
                            'count': row['count'],
                        }
            except sqlite3.Error as e:
                logger.error("Blad obliczania statystyk: %s", e)
        return stats

    def save_alert(self, alert: dict[str, Any]) -> None:
        """Zapisz alert do bazy."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO alerts(timestamp, type, label, message) VALUES(?,?,?,?)",
                    (alert['timestamp'], alert['type'], alert['label'], alert['message'])
                )
                self._conn.commit()
            except sqlite3.Error as e:
                logger.error("Blad zapisu alertu: %s", e)

    def get_alerts(self, limit: Optional[int] = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        """Pobierz ostatnie alerty."""
        with self._lock:
            try:
                query = "SELECT * FROM alerts"
                if unread_only:
                    query += " WHERE read = 0"
                query += " ORDER BY timestamp DESC"
                if limit is not None:
                    query += " LIMIT ?"
                    rows = self._conn.execute(query, (limit,)).fetchall()
                else:
                    rows = self._conn.execute(query).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error as e:
                logger.error("Blad odczytu alertow: %s", e)
                return []

    def count_alerts(self, unread_only: bool = False) -> int:
        """Policz alerty (opcjonalnie tylko nieprzeczytane)."""
        with self._lock:
            try:
                if unread_only:
                    row = self._conn.execute(
                        "SELECT COUNT(*) AS total FROM alerts WHERE read = 0"
                    ).fetchone()
                else:
                    row = self._conn.execute(
                        "SELECT COUNT(*) AS total FROM alerts"
                    ).fetchone()
                return int(row['total']) if row and row['total'] is not None else 0
            except sqlite3.Error as e:
                logger.error("Blad zliczania alertow: %s", e)
                return 0

    def mark_alerts_read(self) -> None:
        """Oznacz wszystkie alerty jako przeczytane."""
        with self._lock:
            try:
                self._conn.execute("UPDATE alerts SET read = 1 WHERE read = 0")
                self._conn.commit()
            except sqlite3.Error as e:
                logger.error("Blad oznaczania alertow: %s", e)

    def get_config(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Pobierz wartosc konfiguracji. Jezeli brak - zwroc default z CONFIG_DEFAULTS lub podany."""
        fallback = self.CONFIG_DEFAULTS.get(key, default)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT value FROM config WHERE key = ?", (key,)
                ).fetchone()
                return row['value'] if row else fallback
            except sqlite3.Error as e:
                logger.error("Blad odczytu config: %s", e)
                return fallback

    def set_config(self, key: str, value: str) -> bool:
        """Zapisz lub zaktualizuj wartosc konfiguracji.

        Returns:
            True gdy zapis sie udal, False w przypadku bledu bazy danych.
        """
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO config(key, value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value))
                )
                self._conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error("Blad zapisu config: %s", e)
                return False

    def set_config_many(self, items: dict[str, str]) -> bool:
        """Atomowo zapisz wiele kluczy konfiguracji.

        Returns:
            True gdy caly zapis zakonczyl sie sukcesem, False gdy nastapil rollback.
        """
        if not items:
            return True

        with self._lock:
            try:
                self._conn.execute("BEGIN")
                for key, value in items.items():
                    self._conn.execute(
                        "INSERT INTO config(key, value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, str(value))
                    )
                self._conn.commit()
                return True
            except sqlite3.Error as e:
                self._conn.rollback()
                logger.error("Blad atomowego zapisu config: %s", e)
                return False

    def get_all_config(self) -> dict[str, str]:
        """Zwroc slownik wszystkich ustawien (z domyslnymi jako podkladem)."""
        result = dict(self.CONFIG_DEFAULTS)
        with self._lock:
            if self._conn is None:
                return result
            try:
                rows = self._conn.execute("SELECT key, value FROM config").fetchall()
                for row in rows:
                    result[row['key']] = row['value']
            except sqlite3.Error as e:
                logger.error("Blad odczytu config: %s", e)
        return result

    def downsample_old_data(self):
        """Kompaktuj stare dane - zachowaj jedna probe na przedzial czasowy.

        Zakresy (wzgledem teraz):
          30-90 dni  -> 1 probka co 5 minut (bucket 300s)
          90-180 dni -> 1 probka co 10 minut (bucket 600s)
          180+ dni   -> 1 probka co 15 minut (bucket 900s)
        """
        now = time.time()
        tiers = [
            (now - 90  * 86400, now - 30  * 86400, 300),
            (now - 180 * 86400, now - 90  * 86400, 600),
            (now - 365 * 86400, now - 180 * 86400, 900),
        ]
        total_deleted = 0
        try:
            with self._lock:
                for t_from, t_to, bucket in tiers:
                    result = self._conn.execute(
                        """DELETE FROM measurements
                           WHERE timestamp BETWEEN ? AND ?
                           AND id NOT IN (
                               SELECT MIN(id) FROM measurements
                               WHERE timestamp BETWEEN ? AND ?
                               GROUP BY CAST(timestamp / ? AS INTEGER)
                           )""",
                        (t_from, t_to, t_from, t_to, bucket)
                    )
                    total_deleted += result.rowcount
                self._conn.commit()
                if total_deleted > 0:
                    logger.info("Downsampling: usunieto %d zduplikowanych probek", total_deleted)
        except sqlite3.Error as e:
            logger.error("Blad downsamplingu: %s", e)

    def is_setup_complete(self):
        """Zwroc True jesli uzytkownik jawnie skonfigurowal polaczenie (ew11a_host w tabeli config)."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT 1 FROM config WHERE key = 'ew11a_host' LIMIT 1"
                ).fetchone()
                return row is not None
            except sqlite3.Error:
                return False

    def cleanup(self):
        """Usun stare rekordy i skompaktuj archiwalne dane."""
        self.downsample_old_data()

        try:
            retention_days = int(self.get_config('db_retention_days',
                                                  str(self.retention_days)))
        except (ValueError, TypeError):
            retention_days = self.retention_days

        cutoff = time.time() - (retention_days * 86400)
        try:
            with self._lock:
                result = self._conn.execute(
                    "DELETE FROM measurements WHERE timestamp < ?",
                    (cutoff,)
                )
                self._conn.commit()
                if result.rowcount > 0:
                    logger.info("Usunieto %d starych rekordow", result.rowcount)
        except sqlite3.Error as e:
            logger.error("Blad czyszczenia bazy: %s", e)

        try:
            alert_mode = str(self.get_config('alert_retention_mode', 'days') or 'days').strip().lower()
            if alert_mode not in ('days', 'count'):
                alert_mode = 'days'

            with self._lock:
                if alert_mode == 'count':
                    try:
                        keep_count = int(self.get_config('alert_retention_count', '1000') or '1000')
                    except (ValueError, TypeError):
                        keep_count = 1000
                    keep_count = min(max(keep_count, 1), 50000)

                    result = self._conn.execute(
                        """
                        DELETE FROM alerts
                        WHERE id IN (
                            SELECT id FROM alerts
                            ORDER BY timestamp DESC, id DESC
                            LIMIT -1 OFFSET ?
                        )
                        """,
                        (keep_count,),
                    )
                else:
                    try:
                        keep_days = int(self.get_config('alert_retention_days', '30') or '30')
                    except (ValueError, TypeError):
                        keep_days = 30
                    keep_days = min(max(keep_days, 1), 3650)
                    alert_cutoff = time.time() - (keep_days * 86400)
                    result = self._conn.execute(
                        "DELETE FROM alerts WHERE timestamp < ?",
                        (alert_cutoff,),
                    )

                self._conn.commit()
                if result.rowcount > 0:
                    logger.info("Usunieto %d alertow zgodnie z retencja (%s)", result.rowcount, alert_mode)
        except sqlite3.Error as e:
            logger.error("Blad czyszczenia alertow: %s", e)

    def reset_runtime_data(self) -> dict[str, int]:
        """Wyczysc dane runtime (pomiary i alerty), zachowujac konfiguracje."""
        with self._lock:
            try:
                measurements_deleted = self._conn.execute(
                    "DELETE FROM measurements"
                ).rowcount
                alerts_deleted = self._conn.execute(
                    "DELETE FROM alerts"
                ).rowcount

                # Reset licznikow AUTOINCREMENT dla tabel runtime.
                self._conn.execute(
                    "DELETE FROM sqlite_sequence WHERE name IN ('measurements', 'alerts')"
                )
                self._conn.commit()

                logger.info(
                    "Reset runtime data: usunieto %d pomiarow i %d alertow",
                    measurements_deleted,
                    alerts_deleted,
                )
                return {
                    'measurements_deleted': int(measurements_deleted),
                    'alerts_deleted': int(alerts_deleted),
                }
            except sqlite3.Error as e:
                self._conn.rollback()
                logger.error("Blad resetu danych runtime: %s", e)
                raise

    def reset_all_data_and_config(self) -> dict[str, int]:
        """Wyczysc pomiary, alerty i konfiguracje (pelny reset bazy)."""
        with self._lock:
            try:
                measurements_deleted = self._conn.execute(
                    "DELETE FROM measurements"
                ).rowcount
                alerts_deleted = self._conn.execute(
                    "DELETE FROM alerts"
                ).rowcount
                config_deleted = self._conn.execute(
                    "DELETE FROM config"
                ).rowcount

                self._conn.execute(
                    "DELETE FROM sqlite_sequence WHERE name IN ('measurements', 'alerts')"
                )
                self._conn.commit()

                logger.warning(
                    "Pelny reset bazy: usunieto %d pomiarow, %d alertow i %d wpisow konfiguracji",
                    measurements_deleted,
                    alerts_deleted,
                    config_deleted,
                )
                return {
                    'measurements_deleted': int(measurements_deleted),
                    'alerts_deleted': int(alerts_deleted),
                    'config_deleted': int(config_deleted),
                }
            except sqlite3.Error as e:
                self._conn.rollback()
                logger.error("Blad pelnego resetu bazy: %s", e)
                raise

    def log_boiler_setting_change(
        self,
        *,
        source: str,
        target_type: str,
        target_index: Optional[int],
        param_key: str,
        param_index: Optional[int],
        old_value: Optional[str],
        new_value: Optional[str],
        status: str,
        message: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> bool:
        """Zapisz wpis historii zmian ustawień kotła."""
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO boiler_setting_changes(
                        timestamp, source, target_type, target_index,
                        param_key, param_index, old_value, new_value,
                        status, message, request_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        time.time(),
                        source,
                        target_type,
                        target_index,
                        param_key,
                        param_index,
                        old_value,
                        new_value,
                        status,
                        message,
                        request_id,
                    ),
                )
                self._conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error("Blad zapisu historii zmian kotla: %s", e)
                return False

    def get_boiler_setting_changes(self, limit: int = 100) -> list[dict[str, Any]]:
        """Pobierz historię zmian ustawień kotła (najnowsze pierwsze)."""
        limit = min(max(int(limit), 1), 1000)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT * FROM boiler_setting_changes
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error as e:
                logger.error("Blad odczytu historii zmian kotla: %s", e)
                return []

    def close(self) -> None:
        """Zamknij polaczenie z baza danych."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except sqlite3.Error as e:
                    logger.error("Blad zamykania bazy: %s", e)
                finally:
                    self._conn = None
