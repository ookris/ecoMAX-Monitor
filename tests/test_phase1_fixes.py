"""Testy fazy 1: dekoder 0x35, tryby, katalog parametrow, wartosci pochodne."""

import struct

import pytest

from econet import decoder, protocol
from econet.boiler_control import PARAMETERS_BY_KEY, format_parameter_value
from derived_data import apply_config_overrides
from tests.fixtures import FRAME_35_WORKING


def _payload(frame_bytes):
    frames, _ = protocol.find_frames(frame_bytes)
    assert len(frames) == 1
    return frames[0]['payload']


# ---------------------------------------------------------------- dekoder 0x35

def test_decode_real_frame():
    data = decoder.decode_econet_data_frame(_payload(FRAME_35_WORKING))
    assert data.mode == 3
    assert data.mode_name == 'Praca'
    assert data.temp_co == pytest.approx(74.14)
    assert data.temp_cwu == pytest.approx(51.1)
    assert data.temp_back == pytest.approx(49.86)
    assert data.temp_flue_gas == pytest.approx(142.5)
    assert data.temp_co_set == 70
    assert data.temp_cwu_set == 55
    assert data.pump_co is True
    assert data.boiler_power_kw == pytest.approx(5.37)
    assert data.mixers_available == 6
    assert data.mixers_connected == 1
    assert data.module_a == '5.11.23.E1'
    assert data.module_panel == '5.11.15'


def test_frame_versions_table_is_parsed():
    versions, offset = decoder.parse_frame_versions(_payload(FRAME_35_WORKING))
    assert offset == 22
    assert versions[0x3D] == 25346
    assert versions[0x38] == 21
    assert decoder._find_econet_data_offset(_payload(FRAME_35_WORKING)) == 22


def _sensor_block(mode=3, temps=((0, 60.0),), fuel_level=50):
    block = bytes([mode]) + struct.pack('<II', 0, 0)
    block += bytes([len(temps)])
    for index, value in temps:
        block += bytes([index]) + struct.pack('<f', value)
    block += bytes([70, 0, 55, 0])            # zadane + statusy
    block += bytes([0])                        # brak alarmow
    block += bytes([fuel_level, 0])            # poziom paliwa, transmission
    block += struct.pack('<f', 40.0)           # wentylator
    block += bytes([0])                        # obciazenie
    block += struct.pack('<ff', 4.0, 0.8)      # kW, kg/h
    block += bytes([0])                        # termostat
    return block


def test_extended_temperature_index_does_not_reject_frame():
    payload = bytes([0]) + _sensor_block(temps=((0, 61.5), (14, 33.0)))
    data = decoder.decode_econet_data_frame(payload)
    assert data is not None
    assert data.temp_co == pytest.approx(61.5)
    assert data.temp_exchanger == pytest.approx(33.0)


@pytest.mark.parametrize('raw, expected', [(0, 0), (55, 55), (0xFF, None), (105, 4)])
def test_fuel_level_decoding(raw, expected):
    payload = bytes([0]) + _sensor_block(fuel_level=raw)
    data = decoder.decode_econet_data_frame(payload)
    assert data.fuel_level == expected


def test_mode_names_match_pyplumio():
    assert decoder.MODE_NAMES[1] == 'Stabilizacja'
    assert decoder.MODE_NAMES[6] == 'Czuwanie'


# ---------------------------------------------------------- katalog parametrow

def test_fuzzy_logic_catalog_entry():
    param = PARAMETERS_BY_KEY['FUZZY_LOGIC']
    assert param.index == 18
    assert (param.min_value, param.max_value) == (0, 1)
    assert format_parameter_value(param, 1) == 'Włączona'


def test_heating_curve_is_scaled():
    param = PARAMETERS_BY_KEY['HEATING_CURVE']
    assert param.index == 107
    assert param.to_display(8) == pytest.approx(0.8)
    assert format_parameter_value(param, 8) == '0,8'


def test_heating_curve_shift_offset():
    param = PARAMETERS_BY_KEY['HEATING_CURVE_SHIFT']
    assert param.to_display(20) == 0
    assert param.to_display(15) == -5
    assert format_parameter_value(param, 25) == '5 °C'


def test_fuel_calorific_value():
    param = PARAMETERS_BY_KEY['FUEL_CALORIFIC_VALUE']
    assert param.index == 88
    assert param.ui_profile == 'service'
    assert format_parameter_value(param, 52) == '5,2 kWh/kg'


def test_cwu_work_mode_map():
    param = PARAMETERS_BY_KEY['CWU_WORK_MODE']
    assert param.max_value == 2
    assert param.value_map == {0: 'Wyłączony', 1: 'Priorytet CWU', 2: 'Bez priorytetu'}


def test_removed_wrong_keys():
    assert 'CONTROL_MODE' not in PARAMETERS_BY_KEY
    assert 'PROGRAM_CONTROL_CO' not in PARAMETERS_BY_KEY


# ----------------------------------------------------------- wartosci pochodne

def test_fuel_level_not_set_hides_value():
    data = {'fuel_level': 0, 'boiler_power': 0, 'boiler_power_kw': 0}
    apply_config_overrides(data, {'fuel_level_not_set': '1'})
    assert data['fuel_level'] is None
    assert data['fuel_level_not_set'] is True


def test_fuel_level_kept_by_default():
    data = {'fuel_level': 40}
    apply_config_overrides(data, {})
    assert data['fuel_level'] == 40
    assert data['fuel_level_not_set'] is False


def test_boiler_power_from_controller_is_kept():
    data = {'boiler_power': 60, 'boiler_power_kw': 9.0}
    apply_config_overrides(data, {'boiler_nominal_power_kw': '15'})
    assert data['boiler_power'] == 60
    assert data['boiler_power_source'] == 'controller'


def test_boiler_power_calculated_from_nominal_power():
    data = {'boiler_power': 0, 'boiler_power_kw': 5.37}
    apply_config_overrides(data, {'boiler_nominal_power_kw': '15'})
    assert data['boiler_power'] == 36
    assert data['boiler_power_source'] == 'calculated'
    # Ponowne zastosowanie (np. w /api/data) nie zmienia zrodla ani wartosci.
    apply_config_overrides(data, {'boiler_nominal_power_kw': '15'})
    assert data['boiler_power'] == 36
    assert data['boiler_power_source'] == 'calculated'


def test_boiler_power_unknown_without_nominal_power():
    data = {'boiler_power': 0, 'boiler_power_kw': 5.37}
    apply_config_overrides(data, {'boiler_nominal_power_kw': ''})
    assert data['boiler_power'] is None
    assert data['boiler_power_source'] is None


def test_boiler_power_zero_when_idle():
    data = {'boiler_power': 0, 'boiler_power_kw': 0.0}
    apply_config_overrides(data, {'boiler_nominal_power_kw': '15'})
    assert data['boiler_power'] == 0
    assert data['boiler_power_source'] == 'controller'


def test_overrides_work_on_boiler_data_object():
    data = decoder.decode_econet_data_frame(_payload(FRAME_35_WORKING))
    apply_config_overrides(data, {'fuel_level_not_set': '1', 'boiler_nominal_power_kw': '20'})
    as_dict = data.to_dict()
    assert as_dict['fuel_level'] is None
    assert as_dict['fuel_level_not_set'] is True
    assert as_dict['boiler_power'] == 27
    assert as_dict['boiler_power_source'] == 'calculated'


# ------------------------------------------------------- poprawki z przegladu

def test_frame_without_prefix_and_mode_off_is_decoded_at_offset_zero():
    # Brak tabeli wersji: payload[0] = tryb 0, co wyglada jak count=0.
    payload = _sensor_block(mode=0, temps=((0, 45.0), (2, 40.0)))
    data = decoder.decode_econet_data_frame(payload)
    assert data.mode == 0
    assert data.temp_co == pytest.approx(45.0)
    assert data.temp_cwu == pytest.approx(40.0)


def test_aux_temperature_out_of_range_is_ignored_not_rejected():
    payload = bytes([0]) + _sensor_block(temps=((0, 60.0), (15, -150.0)))
    data = decoder.decode_econet_data_frame(payload)
    assert data.temp_air_in is None
    assert data.is_plausible()


def test_to_display_tolerates_bad_values():
    param = PARAMETERS_BY_KEY['HEATING_CURVE']
    assert param.to_display('12.0') == pytest.approx(1.2)
    assert param.to_display('abc') is None


def test_fuel_low_alert_cleared_when_level_unknown(tmp_path):
    from alerts import AlertEngine
    from database import Database

    db = Database(str(tmp_path / 'a.db'))
    fired = []
    engine = AlertEngine(db, fired.append)
    data = decoder.BoilerData()
    data.temp_co = 60.0
    data.fuel_level = 5
    engine.check(data)
    assert [a['type'] for a in fired] == ['fuel_low']

    data.fuel_level = None  # uzytkownik zaznaczyl "poziom paliwa nie ustawiony"
    engine.check(data)
    data.fuel_level = 5     # ...i odznaczyl: alert ma sie pojawic ponownie
    engine.check(data)
    assert [a['type'] for a in fired] == ['fuel_low', 'fuel_low']
    db.close()


def test_database_migrates_old_measurements_table(tmp_path):
    import sqlite3
    from database import Database

    path = str(tmp_path / 'old.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, "
                 "temp_co REAL, temp_feeder REAL, temp_cwu REAL, temp_external REAL, temp_back REAL, "
                 "temp_flue_gas REAL, temp_optical REAL, temp_co_set REAL, temp_cwu_set REAL, fan_power REAL, "
                 "boiler_power REAL, boiler_power_kw REAL, fuel_level REAL, fuel_stream REAL, mode INTEGER, "
                 "mode_name TEXT, pump_co INTEGER, pump_cwu INTEGER, pump_circulation INTEGER, flame REAL, "
                 "blower REAL, fuel_burned_kg REAL, ignitions INTEGER)")
    conn.commit()
    conn.close()

    db = Database(path)
    db.save_measurement({'timestamp': 1.0, 'boiler_power': 36, 'boiler_power_source': 'calculated'})
    row = db.get_latest()
    assert row['boiler_power_source'] == 'calculated'
    assert row['temp_co'] is None
    db.close()


def test_preprocess_runs_before_last_data_is_published():
    from econet.connection import EW11AConnection

    seen = {}
    conn = EW11AConnection('127.0.0.1', 1, read_interval=0,
                           preprocess_callback=lambda d: setattr(d, 'fuel_level', None))
    conn.on_data_callback = lambda d: seen.setdefault('last', conn.last_data.fuel_level)
    data = decoder.decode_econet_data_frame(_payload(FRAME_35_WORKING))
    conn._update_data(data)
    assert seen['last'] is None
