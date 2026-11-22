"""Testy fazy 2: pelny dekoder 0x35, wersje ramek, wykrywanie zmian z panelu."""

import struct
import time

import pytest

from controller_sync import ControllerSync, TASK_PARAMS
from database import Database
from econet import decoder, protocol
from econet.connection import EW11AConnection
from econet.ecomax_parameters import ECOMAX_P_PARAMETERS, get_parameter_info, parameter_key
from tests.fixtures import FRAME_35_WORKING


def _payload(frame_bytes):
    return protocol.find_frames(frame_bytes)[0][0]['payload']


def test_outputs_and_statuses_from_real_frame():
    data = decoder.decode_econet_data_frame(_payload(FRAME_35_WORKING))
    assert data.outputs_raw == 0x05
    assert data.fan_on is True
    assert data.feeder_on is False
    assert data.pump_co is True
    assert data.lighter_on is False
    assert data.output_flags_raw == 0x4FF
    assert data.heating_status == 0
    assert data.water_heater_status == 0
    assert data.pending_alerts == []
    assert data.thermostat_contact == 1
    assert data.lambda_state is None
    assert data.frame_versions[0x3D] == 25346

    as_dict = data.to_dict()
    assert as_dict['frame_versions']['0x38'] == 21
    assert as_dict['mixer1_temp'] == pytest.approx(51.84)
    assert as_dict['mixer1_target'] == 50
    assert as_dict['mixer1_pump'] is True
    assert as_dict['mixer_data'][0]['outputs_raw'] == 9
    assert as_dict['mixer_data'][0]['valve_position'] == 47
    assert as_dict['mixer_data'][0]['valve_opening'] is False
    assert as_dict['mixer1_valve'] == 47
    assert as_dict['mixer_data'][1]['valve_position'] is None
    assert as_dict['mixer_data'][1]['current_temp'] is None


def _block_with(outputs=0, alerts=(), lambda_bytes=b'\xff'):
    block = bytes([3]) + struct.pack('<II', outputs, 0xFFFF)
    block += bytes([1, 0]) + struct.pack('<f', 60.0)
    block += bytes([70, 1, 55, 2])
    block += bytes([len(alerts)]) + bytes(alerts)
    block += bytes([50, 0]) + struct.pack('<f', 40.0) + bytes([0]) + struct.pack('<ff', 4.0, 0.8) + bytes([0])
    block += bytes([1, 2, 3, ord('A'), 4]) + b'\xff' * 5     # moduly
    block += lambda_bytes
    block += b'\xff'                                          # brak termostatow
    block += bytes([0])                                       # 0 mieszaczy
    return bytes([1, 0x3D, 7, 0]) + block                     # tabela wersji: 0x3D = 7


def test_all_output_bits_and_alerts_and_lambda():
    payload = _block_with(outputs=(1 << 1) | (1 << 5) | (1 << 6), alerts=(7, 23),
                          lambda_bytes=bytes([3, 8]) + struct.pack('<H', 75))
    data = decoder.decode_econet_data_frame(payload)
    assert data.feeder_on and data.lighter_on and data.alarm_output
    assert not data.fan_on
    assert data.heating_status == 1
    assert data.water_heater_status == 2
    assert data.pending_alerts == [7, 23]
    assert data.to_dict()['pending_alerts_count'] == 2
    assert data.lambda_state == 3
    assert data.lambda_target == 8
    assert data.lambda_level == pytest.approx(7.5)
    assert data.to_dict()['lambda_state_name'] == 'Praca'
    assert data.frame_versions == {0x3D: 7}


def test_frame_version_listener_reports_changes_once():
    conn = EW11AConnection('127.0.0.1', 1)
    seen = []
    conn.add_frame_version_listener(seen.append)
    conn._process_frame_versions({0x38: 21, 0x3D: 5})
    conn._process_frame_versions({0x38: 21, 0x3D: 5})
    conn._process_frame_versions({0x38: 22, 0x3D: 5})
    assert seen == [{0x38: (None, 21), 0x3D: (None, 5)}, {0x38: (21, 22)}]
    assert conn.frame_versions == {0x38: 22, 0x3D: 5}


def test_parameter_map_matches_pyplumio_order():
    assert len(ECOMAX_P_PARAMETERS) == 139
    assert get_parameter_info(47).name == 'kindling_time'
    assert get_parameter_info(88).to_display(52) == pytest.approx(5.2)
    assert get_parameter_info(108).to_display(25) == 5
    assert parameter_key(200) == 'IDX_200'


class _ParamsConnection:
    connected = True

    def __init__(self):
        self.values = {98: 70, 47: 10, 119: 55}
        self.listeners = []

    def add_frame_version_listener(self, callback):
        self.listeners.append(callback)

    def request_ecomax_parameters(self, start=0, count=255, timeout=2.0):
        params = {i: {'available': True, 'value': v, 'min_value': 0, 'max_value': 250}
                  for i, v in self.values.items()}
        params[110] = {'available': False, 'value': None, 'min_value': None, 'max_value': None}
        return {'parameters': params}


def test_controller_sync_logs_changes_made_outside_app(tmp_path):
    db = Database(str(tmp_path / 's.db'))
    conn = _ParamsConnection()
    cache = []
    sync = ControllerSync(conn, db, on_parameters=cache.append)

    sync.sync_parameters()                  # pierwszy odczyt: tylko zapamietanie
    assert db.get_boiler_setting_changes() == []

    # Zmiana z aplikacji (zalogowana wczesniej) i zmiana z panelu.
    db.log_boiler_setting_change(source='lan', target_type='ecomax', target_index=0,
                                 param_key='CO_TEMP_SET', param_index=98, old_value='70',
                                 new_value='72', status='ok')
    conn.values.update({98: 72, 47: 12})
    sync.sync_parameters()

    changes = db.get_boiler_setting_changes()
    panel = [c for c in changes if c['source'] == 'panel']
    assert len(panel) == 1
    assert panel[0]['param_key'] == 'KINDLING_TIME'
    assert (panel[0]['old_value'], panel[0]['new_value']) == ('10', '12')
    assert len(cache) == 2
    db.close()


def test_controller_sync_debounces_version_changes(tmp_path):
    db = Database(str(tmp_path / 's.db'))
    conn = _ParamsConnection()
    calls = []
    conn.request_ecomax_parameters = lambda **kw: calls.append(kw) or {'parameters': {}}
    sync = ControllerSync(conn, db, debounce_sec=10)

    sync._on_frame_versions({0x38: (21, 22)})
    first_due = sync.pending_tasks()[TASK_PARAMS]
    sync._on_frame_versions({0x3D: (1, 2)})      # nie dotyczy parametrow
    time.sleep(0.01)
    sync._on_frame_versions({0x38: (22, 23)})    # kolejna zmiana przesuwa termin
    assert sync.pending_tasks()[TASK_PARAMS] > first_due

    assert sync.run_due_tasks(now=time.time()) == []          # jeszcze za wczesnie
    assert sync.run_due_tasks(now=time.time() + 11) == [TASK_PARAMS]
    assert len(calls) == 1
    assert sync.pending_tasks() == {}
    db.close()


def test_controller_sync_periodic_task(tmp_path):
    db = Database(str(tmp_path / 's.db'))
    conn = _ParamsConnection()
    sync = ControllerSync(conn, db)
    sync._periodic_next[TASK_PARAMS] = time.time() - 1
    assert sync.run_due_tasks() == [TASK_PARAMS]
    assert sync._periodic_next[TASK_PARAMS] > time.time() + 60
    db.close()


def test_timed_out_app_write_is_not_reported_as_panel_change(tmp_path):
    db = Database(str(tmp_path / 's.db'))
    conn = _ParamsConnection()
    sync = ControllerSync(conn, db)
    sync.sync_parameters()
    db.log_boiler_setting_change(source='lan', target_type='ecomax', target_index=0,
                                 param_key='CO_TEMP_SET', param_index=98, old_value='70',
                                 new_value='65', status='timeout')
    conn.values[98] = 65
    sync.sync_parameters()
    assert [c for c in db.get_boiler_setting_changes() if c['source'] == 'panel'] == []
    db.close()


def test_thermostat_contact_bits_only_for_first_three():
    from econet.decoder import BoilerData, _decode_extended_sensor_data
    body = bytes([1, 2, 3, ord('A'), 4]) + b'\xff' * 5 + b'\xff'          # moduly + brak lambdy
    body += bytes([0b00001000, 4])                                          # contacts, 4 termostaty
    for _ in range(4):
        body += bytes([1]) + struct.pack('<ff', 21.0, 22.0)
    body += bytes([0])
    data = BoilerData()
    _decode_extended_sensor_data(body, 0, data)
    assert data.thermostat_data[0]['schedule'] is True
    assert data.thermostat_data[0]['contact'] is False
    assert data.thermostat_data[3]['contact'] is None
    assert data.thermostat_data[3]['schedule'] is None


def test_database_stores_new_columns(tmp_path):
    db = Database(str(tmp_path / 'm.db'))
    data = decoder.decode_econet_data_frame(_payload(FRAME_35_WORKING)).to_dict()
    db.save_measurement(data)
    row = db.get_latest()
    assert row['fan_on'] == 1
    assert row['mixer1_temp'] == pytest.approx(51.84, abs=0.01)
    assert row['thermostat_contact'] == 1
    assert db.has_recent_boiler_change(target_type='ecomax', param_index=1, new_value='1',
                                       since=time.time() - 10) is False
    db.close()
