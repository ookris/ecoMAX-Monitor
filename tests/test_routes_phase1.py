"""Testy API dla poprawek z fazy 1."""

import subprocess

import pytest

from tests.helpers import make_test_app
from web import routes


class _FakeConnection:
    connected = False
    last_data = None

    def get_status(self):
        return {'connected': False}

    def get_device_info_cached(self):
        return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    with make_test_app(tmp_path, _FakeConnection(), monkeypatch) as (test_client, db):
        yield test_client, db


def test_status_uptime_is_duration(client):
    test_client, _ = client
    data = test_client.get('/api/status').get_json()
    assert 0 <= data['uptime'] < 24 * 3600
    assert data['started_at'] > 1_600_000_000


def test_restart_reports_systemctl_error(client, monkeypatch):
    test_client, _ = client

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout='', stderr='brak uprawnien')

    monkeypatch.setattr(routes.subprocess, 'run', fake_run)
    resp = test_client.post('/api/restart')
    assert resp.status_code == 500
    assert resp.get_json()['message'] == 'brak uprawnien'


def test_config_accepts_new_keys(client):
    test_client, db = client
    resp = test_client.post('/api/config', json={
        'fuel_level_not_set': '1',
        'boiler_nominal_power_kw': '15,5',
    })
    assert resp.get_json()['status'] == 'ok'
    assert db.get_config('fuel_level_not_set') == '1'
    assert db.get_config('boiler_nominal_power_kw') == '15.5'

    resp = test_client.post('/api/config', json={'boiler_nominal_power_kw': ''})
    assert resp.get_json()['status'] == 'ok'
    assert db.get_config('boiler_nominal_power_kw') == ''


def test_config_rejects_invalid_nominal_power(client):
    test_client, _ = client
    resp = test_client.post('/api/config', json={'boiler_nominal_power_kw': '-3'})
    assert resp.status_code == 400


def test_mode_name_normalized_from_code():
    row = {'mode': 6, 'mode_name': 'Czyszczenie'}
    routes._normalize_mode_name_in_row(row)
    assert row['mode_name'] == 'Czuwanie'

    legacy = {'mode': None, 'mode_name': 'pauza'}
    routes._normalize_mode_name_in_row(legacy)
    assert legacy['mode_name'] == 'Postój'


def test_fuel_info_without_controller(client):
    test_client, db = client
    db.set_config('fuel_level_not_set', '1')
    data = test_client.get('/api/fuel-info').get_json()['data']
    assert data['calorific_value_kwh_kg'] is None
    assert data['fuel_level_not_set'] is True
    assert data['boiler_nominal_power_kw'] is None


def test_api_data_applies_overrides_to_db_row(client):
    test_client, db = client
    db.set_config('fuel_level_not_set', '1')
    db.save_measurement({
        'timestamp': 1795334958.0, 'temp_co': 70.0, 'temp_feeder': None, 'temp_cwu': 50.0,
        'temp_external': None, 'temp_back': 50.0, 'temp_flue_gas': 140.0, 'temp_optical': None,
        'temp_co_set': 70, 'temp_cwu_set': 55, 'fan_power': 45.0, 'boiler_power': 0,
        'boiler_power_kw': 5.0, 'fuel_level': 0, 'fuel_stream': 1.0, 'mode': 1,
        'mode_name': 'Zatrzymany', 'pump_co': 1, 'pump_cwu': 0, 'pump_circulation': 0,
        'flame': None, 'blower': None, 'fuel_burned_kg': None, 'ignitions': None,
    })
    data = test_client.get('/api/data').get_json()['data']
    assert data['fuel_level'] is None
    assert data['mode_name'] == 'Stabilizacja'
    assert data['boiler_power'] is None


def test_range_error_uses_display_units(monkeypatch):
    monkeypatch.setattr(routes, '_get_ecomax_limits_cached', lambda force_refresh=False: {
        108: {'available': True, 'value': 20, 'min_value': 0, 'max_value': 40},
    })
    with pytest.raises(ValueError) as exc:
        routes._resolve_boiler_write_request({'key': 'HEATING_CURVE_SHIFT', 'value': 45})
    assert 'Wartość poza zakresem -20-20' in str(exc.value)
