"""Testy fazy 3: historia alarmow sterownika (0x3D -> 0xBD)."""

import datetime
import struct

import pytest

from controller_sync import CONFIG_ALARMS_IMPORTED, ControllerSync
from database import Database
from econet import alarms, protocol
from econet.connection import EW11AConnection
from tests.helpers import make_test_app


def _encode_ts(dt):
    """Odwrotnosc alarms.controller_seconds_to_datetime (uklad 31-dniowych miesiecy)."""
    return ((((dt.year - 2000) * 12 + (dt.month - 1)) * 31 + (dt.day - 1)) * 86400
            + dt.hour * 3600 + dt.minute * 60 + dt.second)


def _alert_payload(total, start, entries):
    payload = bytes([total, start, len(entries)])
    for code, begin, end in entries:
        payload += bytes([code]) + struct.pack('<II', _encode_ts(begin),
                                               0xFFFFFFFF if end is None else _encode_ts(end))
    return payload


D = datetime.datetime


def test_controller_time_conversion():
    # Wartosci z prawdziwej ramki 0xBD (ecoMAX920P1): 7 / 2026-11-18 20:57:59 -> 23:27:07
    assert alarms.controller_seconds_to_datetime(0x337F6D57) == D(2026, 11, 18, 20, 57, 59)
    assert alarms.controller_seconds_to_datetime(0x337F904B) == D(2026, 11, 18, 23, 27, 7)
    assert alarms.controller_seconds_to_datetime(0xFFFFFFFF) is None


def test_decode_alerts_payload():
    payload = _alert_payload(80, 0, [
        (7, D(2026, 11, 18, 20, 57, 59), D(2026, 11, 18, 23, 27, 7)),
        (0, D(2026, 10, 20, 11, 36), None),
    ])
    parsed = alarms.decode_alerts_payload(payload)
    assert parsed['total'] == 80
    assert parsed['alerts'][0]['name'] == 'Nieudana próba rozpalania'
    assert parsed['alerts'][1]['end'] is None
    assert alarms.alarm_name(200) == 'Alarm 200'


def _frame(payload):
    body = bytes([protocol.FRAME_ALARMS_ANS]) + payload
    frame = protocol.FrameBuilder.create_frame(0x00, body)
    return protocol.find_frames(frame)[0][0]


def test_request_alerts_matches_response_start():
    conn = EW11AConnection('127.0.0.1', 1)
    conn._connected = True
    conn._socket = object()

    def fake_send(_frame_bytes):
        # Najpierw odpowiedz na cudze zapytanie (start=20), potem nasza (start=10).
        conn._handle_econet_frame(_frame(_alert_payload(80, 20, [(7, D(2026, 1, 1), None)])))
        conn._handle_econet_frame(_frame(_alert_payload(80, 10, [(0, D(2026, 2, 2), None)])))

    conn.send_frame = fake_send
    page = conn.request_alerts(start=10, count=10, timeout=1)
    assert page['start'] == 10
    assert page['alerts'][0]['code'] == 0


class _AlarmConnection:
    connected = True

    def __init__(self, entries):
        self.entries = entries   # od najnowszego
        self.requests = []

    def add_frame_version_listener(self, callback):
        pass

    def request_alerts(self, start=0, count=10, timeout=3.0):
        self.requests.append(start)
        page = self.entries[start:start + count]
        return alarms.decode_alerts_payload(_alert_payload(len(self.entries), start, page))


def _entries(n, base=D(2026, 9, 1)):
    return [(7 if i % 2 else 0, base - datetime.timedelta(days=i), base - datetime.timedelta(days=i, minutes=-30))
            for i in range(n)]


def test_initial_import_reads_all_pages_without_alerts(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    conn = _AlarmConnection(_entries(25))
    fired = []
    sync = ControllerSync(conn, db, on_new_alarm=fired.append)
    stats = sync.sync_alarms()
    assert conn.requests == [0, 10, 20]
    assert stats['new'] == 25
    assert db.count_controller_alarms() == 25
    assert fired == []
    assert db.get_config(CONFIG_ALARMS_IMPORTED) == '1'
    db.close()


def test_new_alarm_after_import_fires_alert_and_stops_paging(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    conn = _AlarmConnection(_entries(25))
    fired = []
    sync = ControllerSync(conn, db, on_new_alarm=fired.append)
    sync.sync_alarms()

    conn.entries.insert(0, (8, D(2026, 9, 5, 6, 0), None))   # nowy, trwajacy "brak paliwa"
    conn.requests.clear()
    stats = sync.sync_alarms()
    assert conn.requests == [0, 10]          # strona 2 bez zmian -> koniec
    assert stats['new'] == 1
    assert [a['code'] for a in fired] == [8]

    conn.entries[0] = (8, D(2026, 9, 5, 6, 0), D(2026, 9, 5, 7, 15))  # alarm sie zakonczyl
    stats = sync.sync_alarms()
    assert stats['updated'] == 1
    row = db.get_controller_alarms(limit=1)[0]
    assert row['end_text'] == '2026-09-05 07:15:00'
    assert len(fired) == 1
    db.close()


def test_first_alarm_on_empty_controller_is_reported(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    conn = _AlarmConnection([])
    fired = []
    sync = ControllerSync(conn, db, on_new_alarm=fired.append)
    sync.sync_alarms()                         # pusty sterownik - import wykonany
    conn.entries.append((23, D(2026, 9, 6), None))
    sync.sync_alarms()
    assert [a['code'] for a in fired] == [23]
    db.close()


def test_alarm_version_change_schedules_sync(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    sync = ControllerSync(_AlarmConnection([]), db)
    sync._on_frame_versions({0x3D: (25346, 25347)})
    assert 'alarms' in sync.pending_tasks()
    db.close()


class _NoConnection:
    connected = False
    last_data = None

    def get_status(self):
        return {'connected': False}


def test_controller_alarms_api(tmp_path, monkeypatch):
    with make_test_app(tmp_path, _NoConnection(), monkeypatch) as (client, db):
        start = D(2026, 11, 18, 20, 57, 59)
        db.upsert_controller_alarm(code=7, start_text='2026-11-18 20:57:59', start_ts=start.timestamp(),
                                   end_text='2026-11-18 23:27:07',
                                   end_ts=D(2026, 11, 18, 23, 27, 7).timestamp())
        data = client.get('/api/controller-alarms').get_json()
        assert data['total'] == 1
        alarm = data['data'][0]
        assert alarm['name'] == 'Nieudana próba rozpalania'
        assert alarm['ongoing'] is False
        assert alarm['duration_min'] == pytest.approx(149.1)

        resp = client.post('/api/controller-alarms/refresh', json={},
                           headers={'X-Requested-With': 'ecoMAX-Monitor'})
        assert resp.status_code == 503


def test_reset_runtime_clears_controller_alarms(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    db.upsert_controller_alarm(code=0, start_text='2026-01-01 00:00:00', start_ts=1.0,
                               end_text=None, end_ts=None)
    db.set_config(CONFIG_ALARMS_IMPORTED, '1')
    db.reset_runtime_data()
    assert db.count_controller_alarms() == 0
    assert db.get_config(CONFIG_ALARMS_IMPORTED) is None
    db.close()


def test_duplicate_start_and_corrupted_timestamp(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    # Jak w prawdziwym sterowniku: dwa zaniki zasilania o tym samym poczatku
    # i wpis z uszkodzonym poczatkiem (rok 2133).
    conn = _AlarmConnection([
        (0, D(2133, 2, 10, 2, 8), D(2020, 1, 6, 19, 29, 17)),
        (0, D(2020, 1, 6, 19, 29), D(2020, 1, 16, 18, 58, 17)),
        (0, D(2020, 1, 6, 19, 29), D(2020, 1, 6, 19, 29, 42)),
    ])
    sync = ControllerSync(conn, db)
    stats = sync.sync_alarms()
    assert (stats['new'], stats['updated'], stats['invalid']) == (2, 0, 1)
    stats = sync.sync_alarms()
    assert (stats['new'], stats['updated'], stats['unchanged']) == (0, 0, 2)
    db.close()


def test_interrupted_initial_import_is_resumed(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    conn = _AlarmConnection(_entries(30))
    original = conn.request_alerts

    def flaky(start=0, count=10, timeout=3.0):
        if start == 10:
            raise TimeoutError('brak odpowiedzi')
        return original(start=start, count=count)

    conn.request_alerts = flaky
    sync = ControllerSync(conn, db)
    with pytest.raises(TimeoutError):
        sync.sync_alarms()
    assert db.count_controller_alarms() == 10
    assert db.get_config(CONFIG_ALARMS_IMPORTED) is None

    conn.request_alerts = original
    stats = sync.sync_alarms()
    assert stats['new'] == 20
    assert db.count_controller_alarms() == 30
    db.close()


def test_ongoing_alarm_beyond_first_page_gets_its_end(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    fuel = (8, D(2026, 9, 1, 6, 0), None)
    conn = _AlarmConnection([fuel])
    sync = ControllerSync(conn, db)
    sync.sync_alarms()

    # 12 nowszych alarmow + zakonczenie alarmu "brak paliwa" (teraz na stronie 2).
    newer = [(0, D(2026, 9, 2) + datetime.timedelta(hours=i), D(2026, 9, 2, 0, 5) + datetime.timedelta(hours=i))
             for i in reversed(range(12))]
    conn.entries = newer + [fuel]
    sync.sync_alarms()                       # import nowszych, "brak paliwa" nadal trwa
    conn.entries = newer + [(8, D(2026, 9, 1, 6, 0), D(2026, 9, 1, 9, 0))]
    conn.requests.clear()
    stats = sync.sync_alarms()               # strona 0 bez zmian, ale trzeba doczytac strone 1
    assert conn.requests == [0, 10]
    assert stats['updated'] == 1
    row = [r for r in db.get_controller_alarms(limit=None) if r['code'] == 8][0]
    assert row['end_text'] == '2026-09-01 09:00:00'

    conn.requests.clear()
    sync.sync_alarms()                       # brak trwajacych alarmow -> tylko strona 0
    assert conn.requests == [0]
    db.close()


def test_invalid_end_timestamp_is_skipped(tmp_path):
    db = Database(str(tmp_path / 'a.db'))
    conn = _AlarmConnection([(7, D(2026, 9, 1), D(2120, 1, 1))])
    stats = ControllerSync(conn, db).sync_alarms()
    assert stats['invalid'] == 1
    assert db.count_controller_alarms() == 0
    db.close()
