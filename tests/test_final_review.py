"""Testy poprawek z koncowego przegladu calosci zmian."""

import datetime
import time

import pytest

from controller_sync import TASK_ALARMS, ControllerSync
from database import Database
from derived_data import apply_config_overrides
from econet import protocol
from econet.connection import EW11AConnection
from tests.test_phase2_decoder import _ParamsConnection
from tests.test_phase3_alarms import D, _AlarmConnection, _entries


@pytest.fixture()
def db(tmp_path):
    database = Database(str(tmp_path / 'f.db'))
    yield database
    database.close()


def test_new_alarm_reported_even_if_next_page_fails(db):
    conn = _AlarmConnection(_entries(25))
    fired = []
    sync = ControllerSync(conn, db, on_new_alarm=fired.append)
    sync.sync_alarms()
    conn.entries = [(8, D(2026, 9, 5), None)] + [(0, D(2026, 9, 4, h), None) for h in range(10)] + conn.entries
    original = conn.request_alerts

    def flaky(start=0, count=10, timeout=3.0):
        if start == 10:
            raise TimeoutError('panel zajety')
        return original(start=start, count=count)

    conn.request_alerts = flaky
    with pytest.raises(TimeoutError):
        sync.sync_alarms()
    assert 8 in [a['code'] for a in fired]


def test_own_write_older_than_window_but_after_snapshot_is_not_panel_change(db):
    conn = _ParamsConnection()
    sync = ControllerSync(conn, db)
    sync.sync_parameters()
    sync._param_snapshot_ts = time.time() - 15 * 60          # poprzedni odczyt 15 min temu
    db.log_boiler_setting_change(source='lan', target_type='ecomax', target_index=0,
                                 param_key='CO_TEMP_SET', param_index=98, old_value='70',
                                 new_value='65', status='ok')
    # zapis aplikacji sprzed 10 minut
    db._conn.execute("UPDATE boiler_setting_changes SET timestamp = ?", (time.time() - 600,))
    db._conn.commit()
    conn.values[98] = 65
    sync.sync_parameters()
    assert [c for c in db.get_boiler_setting_changes() if c['source'] == 'panel'] == []


def _b1_frame(start, count, dest=protocol.ADDR_BROADCAST):
    payload = bytes([0, start, count]) + bytes([10, 1, 20]) * count
    raw = bytearray(protocol.FrameBuilder.create_frame(dest, bytes([protocol.FRAME_GET_PARAMS_ANS]) + payload))
    raw[4] = protocol.ADDR_ECOMAX
    raw[-2] = protocol.crc_xor(bytes(raw[:-2]))
    return protocol.find_frames(bytes(raw))[0][0]


def test_params_response_must_match_request():
    conn = EW11AConnection('127.0.0.1', 1)
    conn._connected = True
    conn._socket = object()

    def fake_send(_frame_bytes):
        conn._handle_econet_frame(_b1_frame(98, 1))          # odpowiedz na cudze zapytanie
        conn._handle_econet_frame(_b1_frame(0, 5, dest=0x50))  # dla panelu
        conn._handle_econet_frame(_b1_frame(0, 3))           # nasza

    conn.send_frame = fake_send
    params = conn.request_ecomax_parameters(start=0, count=3, timeout=1)['parameters']
    assert sorted(params) == [0, 1, 2]


def test_task_due_while_disconnected_is_retried(db):
    conn = _AlarmConnection([])
    conn.connected = False
    sync = ControllerSync(conn, db)
    sync.request(TASK_ALARMS)
    assert sync.run_due_tasks() == [TASK_ALARMS]
    assert TASK_ALARMS in sync.pending_tasks()               # ponowione, nie zgubione


def test_calculated_power_without_kw_is_cleared():
    data = {'boiler_power': 40, 'boiler_power_source': 'calculated', 'boiler_power_kw': None}
    apply_config_overrides(data, {'boiler_nominal_power_kw': '15'})
    assert data['boiler_power'] is None
    assert data['boiler_power_source'] is None
