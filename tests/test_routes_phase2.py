"""Testy API dla nowych pol z ramki 0x35."""

import pytest

from econet import decoder, protocol
from tests.fixtures import FRAME_35_WORKING
from tests.helpers import make_test_app


class _LiveConnection:
    connected = True

    def __init__(self):
        payload = protocol.find_frames(FRAME_35_WORKING)[0][0]['payload']
        self.last_data = decoder.decode_econet_data_frame(payload)

    def get_status(self):
        return {'connected': True}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    with make_test_app(tmp_path, _LiveConnection(), monkeypatch) as (test_client, db):
        yield test_client


def test_ha_endpoint_exposes_outputs_and_mixer(client):
    data = client.get('/api/ha').get_json()
    assert data['fan_on'] is True
    assert data['feeder_on'] is False
    assert data['mixer1_target'] == 50
    assert data['pending_alerts'] == []


def test_metrics_include_new_series(client):
    text = client.get('/metrics').get_data(as_text=True)
    assert 'ecomax_fan_on 1' in text
    assert 'ecomax_mixer1_pump 1' in text
    assert 'ecomax_pending_alerts 0' in text


def test_parameters_page_lists_outputs(client):
    rows = {r['key']: r for r in client.get('/api/parameters').get_json()['data']}
    assert rows['fan_on']['value'] is True
    assert rows['lighter_on']['value'] is False
    assert rows['frame_versions']['value']['0x3D'] == 25346
    assert rows['thermostat_contact']['value'] == 1
