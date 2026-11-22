"""Testy fazy 4: mieszacze, parametry sterownika, UID, wlaczanie/wylaczanie kotla."""

import pytest

from econet import protocol
from econet.connection import EW11AConnection, format_uid
from econet.mixer_parameters import MIXER_PARAMETERS, parse_mixer_parameters_payload
from tests.helpers import make_test_app
from web import routes

# Fragment prawdziwej odpowiedzi 0xB2 (ecoMAX920P1): 14 parametrow x 6 mieszaczy, mieszacz 1 skonfigurowany.
B2_HEAD = bytes.fromhex("00000e06" "32143c" "14145a" "3c145a" "00001e" "000001" "080128" "140028"
                        "000032" "010003" "140028" "ffffff" "000001" "000001" "ffffff")
B2_PAYLOAD = B2_HEAD + b'\xff' * (5 * 14 * 3)


def test_parse_mixer_parameters():
    mixers = parse_mixer_parameters_payload(B2_PAYLOAD)
    assert sorted(mixers) == [0, 1, 2, 3, 4, 5]
    first = mixers[0]
    assert first[0] == {'available': True, 'value': 50, 'min_value': 20, 'max_value': 60}
    assert first[10]['available'] is False
    assert MIXER_PARAMETERS[5].to_display(first[5]['value']) == pytest.approx(0.8)
    assert MIXER_PARAMETERS[6].to_display(first[6]['value']) == 0
    assert MIXER_PARAMETERS[8].value_map[first[8]['value']] == 'Grzanie CO'
    assert not any(p['available'] for p in mixers[3].values())


def test_uid_matches_pyplumio_vector():
    # Wektor z testow PyPlumIO (tests/testdata/responses/uid.json, EM350P2).
    payload = bytes.fromhex("005A000B001600110D3833383655395A0000000A454D33353050322D5A46")
    info = EW11AConnection._parse_uid_payload(payload)
    assert info['uid'] == 'D251PAKR3GCPZ1K8G05G0'
    assert info['product_id'] == 90
    assert info['model'] == 'ecoMAX350P2-ZF'
    assert format_uid(bytes.fromhex("002500300E191932135831")) == 'CE71HB09J468P1ZZ00980'


def _frame(frame_type, payload, source=protocol.ADDR_ECOMAX, dest=protocol.ADDR_BROADCAST):
    """Ramka odpowiedzi tak, jak wysyla ja sterownik (nadawca 0x45)."""
    raw = bytearray(protocol.FrameBuilder.create_frame(dest, bytes([frame_type]) + payload))
    raw[4] = source
    raw[-2] = protocol.crc_xor(bytes(raw[:-2]))
    return protocol.find_frames(bytes(raw))[0][0]


def _connected(responses):
    conn = EW11AConnection('127.0.0.1', 1)
    conn._connected = True
    conn._socket = object()
    sent = []

    def fake_send(frame_bytes):
        sent.append(frame_bytes)
        frame_type, payload = responses[frame_bytes[7]]
        conn._handle_econet_frame(_frame(frame_type, payload))

    conn.send_frame = fake_send
    return conn, sent


def test_service_parameters_request():
    payload = bytes([3, 0]) + bytes.fromhex("0e0b14" "0a050d" "ffffff")
    conn, sent = _connected({0x61: (protocol.FRAME_GET_SERVICE_PARAMS_ANS, payload)})
    params = conn.request_service_parameters(timeout=1)
    assert sent[0][7:10] == bytes([0x61, 255, 0])
    assert params[0] == {'available': True, 'value': 14, 'min_value': 11, 'max_value': 20}
    assert params[2]['available'] is False


def test_response_for_other_device_is_ignored():
    conn = EW11AConnection('127.0.0.1', 1)
    conn._connected = True
    conn._socket = object()

    def fake_send(_frame_bytes):
        # ACK sterownika dla panelu (0x50) - nie jest odpowiedzia na nasze zapytanie.
        conn._handle_econet_frame(_frame(protocol.FRAME_SET_BOILER_CONTROL_ANS, b'', dest=0x50))

    conn.send_frame = fake_send
    with pytest.raises(TimeoutError):
        conn.set_boiler_power(True, timeout=0.3)


def test_boiler_power_request():
    conn, sent = _connected({0x3B: (protocol.FRAME_SET_BOILER_CONTROL_ANS, b'')})
    ack = conn.set_boiler_power(False, timeout=1)
    assert sent[0][7:9] == bytes([0x3B, 0x00])
    assert ack['ack_type'] == protocol.FRAME_SET_BOILER_CONTROL_ANS


class _ControllerConnection:
    connected = True
    last_data = None

    def __init__(self):
        self.power_calls = []
        self.mixer_writes = []

    def get_status(self):
        return {'connected': True}

    def request_mixer_parameters(self, timeout=2.0):
        return parse_mixer_parameters_payload(B2_PAYLOAD)

    def request_ecomax_parameters(self, start=0, count=255, timeout=2.0):
        return {'parameters': {
            47: {'available': True, 'value': 10, 'min_value': 1, 'max_value': 20},
            88: {'available': True, 'value': 52, 'min_value': 1, 'max_value': 250},
            110: {'available': False, 'value': None, 'min_value': None, 'max_value': None},
        }}

    def set_boiler_power(self, enabled, timeout=3.0):
        self.power_calls.append(enabled)
        return {'ack_type': 0xBB}

    def set_parameter(self, target_type, index, value, **kwargs):
        self.mixer_writes.append((target_type, kwargs.get('target_index'), index, value))
        return {'ack_type': 0xB4}


HEADERS = {'X-Requested-With': 'ecoMAX-Monitor'}


@pytest.fixture()
def app(tmp_path, monkeypatch):
    conn = _ControllerConnection()
    monkeypatch.setattr(routes, '_mixer_params_cache', {'cached_at': 0.0, 'mixers': {}})
    monkeypatch.setattr(routes, '_boiler_limits_cache', {'cached_at': 0.0, 'parameters': {}})
    with make_test_app(tmp_path, conn, monkeypatch) as (client, db):
        yield client, db, conn


def test_mixers_endpoint(app):
    client, _, _ = app
    data = client.get('/api/boiler-control/mixers', headers=HEADERS).get_json()
    assert data['count'] == 1
    mixer = data['data'][0]
    rows = {p['key']: p for p in mixer['parameters']}
    assert rows['MIXER_HEATING_CURVE']['display_value'] == pytest.approx(0.8)
    assert rows['MIXER_WORK_MODE']['value_map']['1'] == 'Grzanie CO'   # klucze JSON to napisy
    assert rows['MIXER_THERMOSTAT_OPERATION']['available'] is False


def test_mixer_write_uses_controller_limits(app):
    client, _, conn = app
    resp = client.post('/api/boiler-control/set-parameter', headers=HEADERS, json={
        'target_type': 'mixer', 'target_index': 0, 'key': 'MIXER_MIXER_TARGET_TEMP', 'value': 65,
    })
    assert resp.status_code == 400
    assert 'zakresem 20-60' in resp.get_json()['message']

    resp = client.post('/api/boiler-control/set-parameter', headers=HEADERS, json={
        'target_type': 'mixer', 'target_index': 0, 'key': 'MIXER_MIXER_TARGET_TEMP', 'value': 45,
    })
    assert resp.get_json()['status'] == 'ok'
    assert conn.mixer_writes == [('mixer', 0, 0, 45)]

    resp = client.post('/api/boiler-control/set-parameter', headers=HEADERS, json={
        'target_type': 'mixer', 'target_index': 0, 'index': 10, 'value': 1,
    })
    assert resp.status_code == 400
    assert 'niedostępny' in resp.get_json()['message']


def test_controller_parameters_overview(app):
    client, _, _ = app
    rows = {r['index']: r for r in client.get('/api/boiler-control/controller-parameters').get_json()['data']}
    assert rows[47]['label'] == 'Rozpalanie – czas rozpalania'
    assert rows[47]['group'] == 'Rozpalanie'
    assert rows[88]['display_value'] == pytest.approx(5.2)
    assert rows[88]['editable_in_catalog'] is True
    assert 110 not in rows


def test_power_endpoint_logs_history(app):
    client, db, conn = app
    resp = client.post('/api/boiler-control/power', headers=HEADERS, json={'enabled': False})
    assert resp.get_json()['message'] == 'Kocioł wyłączony'
    assert conn.power_calls == [False]
    change = db.get_boiler_setting_changes()[0]
    assert change['param_key'] == 'BOILER_POWER'
    assert change['new_value'] == '0'

    assert client.post('/api/boiler-control/power', headers=HEADERS, json={}).status_code == 400
    assert client.post('/api/boiler-control/power', json={'enabled': True}).status_code == 403


def test_unavailable_ecomax_parameter_is_rejected(app):
    client, _, _ = app
    resp = client.post('/api/boiler-control/set-parameter', headers=HEADERS,
                       json={'key': 'TERM_BOILER_MODE', 'value': 1})
    assert resp.status_code == 400
    assert 'niedostępny' in resp.get_json()['message']


def test_service_parameters_require_token(app):
    client, _, _ = app
    assert client.get('/api/boiler-control/service-parameters', headers=HEADERS).status_code == 403


@pytest.mark.parametrize('value', [None, 'tak', 'ON ', 2, 0.0, [], {}])
def test_power_endpoint_rejects_ambiguous_values(app, value):
    client, _, conn = app
    resp = client.post('/api/boiler-control/power', headers=HEADERS, json={'enabled': value})
    assert resp.status_code == 400
    assert conn.power_calls == []


def test_mixer_write_refused_without_limits(app, monkeypatch):
    client, _, conn = app
    monkeypatch.setattr(conn, 'request_mixer_parameters', lambda timeout=2.0: {})
    resp = client.post('/api/boiler-control/set-parameter', headers=HEADERS, json={
        'target_type': 'mixer', 'target_index': 0, 'key': 'MIXER_MIXER_TARGET_TEMP', 'value': 45,
    })
    assert resp.status_code == 400
    assert 'wstrzymany' in resp.get_json()['message']
    assert conn.mixer_writes == []


def test_mixer_value_ff_with_valid_range_has_no_value():
    payload = bytes.fromhex("00000101") + bytes([0xFF, 20, 80])
    assert parse_mixer_parameters_payload(payload)[0][0] == {
        'available': True, 'value': None, 'min_value': 20, 'max_value': 80}
