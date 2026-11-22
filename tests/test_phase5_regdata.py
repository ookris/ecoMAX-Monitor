"""Testy fazy 5: schemat i dane regulatora (0x55 -> 0xD5, ramka 0x08)."""

import json
import struct

import pytest

from controller_sync import CONFIG_REGDATA_SCHEMA, TASK_REGDATA_SCHEMA, ControllerSync
from database import Database
from econet import protocol, regdata
from econet.connection import EW11AConnection
from tests.helpers import make_test_app

SCHEMA = [(1536, 10), (1538, 10), (1024, 7), (140, 4), (5, 5), (178, 15), (190, 11), (5, 4)]


def _schema_payload(schema):
    return struct.pack('<H', len(schema)) + b''.join(bytes([t]) + struct.pack('<H', i) for i, t in schema)


def _regdata_payload(fan=True, feeder=False, temp=61.5, valve=67, schema_version=None):
    payload = b'\x00\x00' + bytes([0, 1])            # 2 bajty + wersja 1.0
    if schema_version is None:
        payload += bytes([1, 0x3D, 7, 0])            # tabela wersji: 1 wpis (bez 0x55)
    else:
        payload += bytes([2, 0x3D, 7, 0, 0x55]) + struct.pack('<H', schema_version)
    payload += bytes([(1 if fan else 0) | (2 if feeder else 0)])   # 2 bity w jednym bajcie
    payload += struct.pack('<f', temp) + bytes([valve]) + struct.pack('<H', 0)
    payload += bytes([10, 0, 0, 226]) + b'SIEC\x00' + bytes([9])
    return payload


def test_parse_schema_and_decode():
    schema = regdata.parse_schema_payload(_schema_payload(SCHEMA))
    assert schema == SCHEMA
    rows = regdata.decode_regdata_payload(_regdata_payload(), schema)
    values = [r['value'] for r in rows]
    assert values == [True, False, pytest.approx(61.5), 67, 0, '10.0.0.226', 'SIEC', 9]
    assert [r['position'] for r in rows] == list(range(8))
    assert rows[4]['id'] == rows[7]['id'] == 5        # powtorzone id zachowane wg pozycji


def test_decode_rejects_wrong_version_or_short_frame():
    payload = bytearray(_regdata_payload())
    payload[3] = 2                                     # wersja 2.0
    assert regdata.decode_regdata_payload(bytes(payload), SCHEMA) is None
    assert regdata.decode_regdata_payload(_regdata_payload()[:12], SCHEMA) is None
    assert regdata.decode_regdata_payload(_regdata_payload(), []) is None


def test_invalid_schema_type_gives_empty_schema():
    assert regdata.parse_schema_payload(_schema_payload([(1, 99)])) == []


def test_field_names():
    assert regdata.field_name(1024) == ('Temperatura CO', 'verified')
    assert regdata.field_name(178)[1] == 'guess'
    assert regdata.field_name(9999) == (None, None)
    assert regdata.field_name(9999, {'9999': 'Moje pole'}) == ('Moje pole', 'user')


def _frame(frame_type, payload, source=protocol.ADDR_ECOMAX):
    raw = bytearray(protocol.FrameBuilder.create_frame(0x00, bytes([frame_type]) + payload))
    raw[4] = source
    raw[-2] = protocol.crc_xor(bytes(raw[:-2]))
    return protocol.find_frames(bytes(raw))[0][0]


def test_connection_decodes_regdata_frames_with_schema():
    conn = EW11AConnection('127.0.0.1', 1)
    conn._handle_econet_frame(_frame(protocol.FRAME_REGDATA, _regdata_payload()))
    assert conn.last_regdata is None                   # brak schematu
    conn.set_regdata_schema(SCHEMA)
    conn._handle_econet_frame(_frame(protocol.FRAME_REGDATA, _regdata_payload(valve=55)))
    assert conn.last_regdata['rows'][3]['value'] == 55
    conn._handle_econet_frame(_frame(protocol.FRAME_REGDATA, _regdata_payload()[:10]))
    assert conn.regdata_decode_errors == 1
    conn._handle_econet_frame(_frame(protocol.FRAME_REGDATA, _regdata_payload(valve=1), source=0x50))
    assert conn.last_regdata['rows'][3]['value'] == 55  # ramka od innego urzadzenia pominieta


class _SchemaConnection:
    connected = True

    def __init__(self):
        self.schema = None
        self.frame_versions = {protocol.FRAME_GET_REGDATA_SCHEMA: 34841}
        self.requests = 0

    def add_frame_version_listener(self, callback):
        pass

    def request_regdata_schema(self, timeout=3.0):
        self.requests += 1
        return list(SCHEMA)

    def set_regdata_schema(self, schema, version=None):
        self.schema = schema
        self.schema_version = version


def test_sync_stores_schema_and_skips_known_version(tmp_path):
    db = Database(str(tmp_path / 'r.db'))
    conn = _SchemaConnection()
    sync = ControllerSync(conn, db)
    assert sync.load_stored_regdata_schema() is False

    sync._on_frame_versions({0x55: (None, 34841)})
    assert TASK_REGDATA_SCHEMA in sync.pending_tasks()
    assert sync.sync_regdata_schema() == len(SCHEMA)
    stored = json.loads(db.get_config(CONFIG_REGDATA_SCHEMA))
    assert stored['version'] == 34841
    assert conn.schema == SCHEMA

    # Restart aplikacji: schemat z bazy, ta sama wersja -> bez zapytania 0x55.
    conn2 = _SchemaConnection()
    sync2 = ControllerSync(conn2, db)
    assert sync2.load_stored_regdata_schema() is True
    assert [tuple(x) for x in conn2.schema] == SCHEMA
    sync2._on_frame_versions({0x55: (None, 34841)})
    assert TASK_REGDATA_SCHEMA not in sync2.pending_tasks()
    sync2._on_frame_versions({0x55: (34841, 34842)})
    assert TASK_REGDATA_SCHEMA in sync2.pending_tasks()
    db.close()


HEADERS = {'X-Requested-With': 'ecoMAX-Monitor'}


class _LiveRegdataConnection:
    connected = True
    last_data = None
    regdata_decode_errors = 0

    def __init__(self):
        self.regdata_schema = SCHEMA
        self.last_regdata = {'timestamp': 1.0, 'rows': regdata.decode_regdata_payload(_regdata_payload(), SCHEMA)}

    def get_status(self):
        return {'connected': True}


def test_regdata_api_and_labels(tmp_path, monkeypatch):
    with make_test_app(tmp_path, _LiveRegdataConnection(), monkeypatch) as (client, db):
        data = client.get('/api/regdata').get_json()
        assert data['status'] == 'ok'
        rows = data['data']
        assert rows[2]['name'] == 'Temperatura CO'
        assert rows[4]['name'] is None

        resp = client.post('/api/regdata/labels', headers=HEADERS, json={'id': 5, 'label': 'Licznik X'})
        assert resp.get_json()['status'] == 'ok'
        rows = client.get('/api/regdata').get_json()['data']
        assert rows[4]['name'] == 'Licznik X' and rows[4]['name_source'] == 'user'
        assert rows[7]['name'] == 'Licznik X'

        client.post('/api/regdata/labels', headers=HEADERS, json={'id': 5, 'label': ''})
        assert client.get('/api/regdata').get_json()['data'][4]['name'] is None
        assert client.post('/api/regdata/labels', headers=HEADERS, json={'id': 'x', 'label': 'a'}).status_code == 400
        assert client.post('/api/regdata/labels', headers=HEADERS, json={'id': 1, 'label': 'a' * 81}).status_code == 400
        assert client.post('/api/regdata/labels', json={'id': 1, 'label': 'bez naglowka'}).status_code == 403

        # Klucze wewnetrzne nie trafiaja do /api/config.
        db.set_config(CONFIG_REGDATA_SCHEMA, '{"version": 1, "schema": []}')
        config = client.get('/api/config').get_json()['data']
        assert CONFIG_REGDATA_SCHEMA not in config and 'regdata_labels' not in config


def test_stale_schema_is_detected():
    payload = _regdata_payload(schema_version=34842)
    assert regdata.decode_regdata_payload(payload, SCHEMA, schema_version=34841) is None
    assert regdata.decode_regdata_payload(payload, SCHEMA, schema_version=34842) is not None
    # Nadmiarowe bajty = schemat nie opisuje calej ramki.
    assert regdata.decode_regdata_payload(_regdata_payload() + b'\x00', SCHEMA) is None


def test_schema_change_resets_decode_errors():
    conn = EW11AConnection('127.0.0.1', 1)
    conn.set_regdata_schema(SCHEMA, 34841)
    conn._handle_econet_frame(_frame(protocol.FRAME_REGDATA, _regdata_payload(schema_version=34842)))
    assert conn.regdata_decode_errors == 1 and conn.last_regdata is None
    conn.set_regdata_schema(SCHEMA, 34842)
    assert conn.regdata_decode_errors == 0
    conn._handle_econet_frame(_frame(protocol.FRAME_REGDATA, _regdata_payload(schema_version=34842)))
    assert conn.last_regdata is not None


def test_periodic_check_retries_missing_schema_only(tmp_path):
    db = Database(str(tmp_path / 'r.db'))
    conn = _SchemaConnection()
    conn.regdata_schema = []
    sync = ControllerSync(conn, db)
    assert sync.sync_regdata_schema(force=False) == len(SCHEMA)   # brak schematu -> pobierz
    conn.regdata_schema = SCHEMA
    assert sync.sync_regdata_schema(force=False) == 0             # aktualny -> bez zapytania
    assert conn.requests == 1
    conn.frame_versions[0x55] = 34842
    assert sync.sync_regdata_schema(force=False) == len(SCHEMA)   # nowa wersja -> pobierz
    db.close()


def test_regdata_api_sends_big_integers_as_strings(tmp_path, monkeypatch):
    conn = _LiveRegdataConnection()
    conn.last_regdata = {'timestamp': 1.0, 'rows': [{'position': 0, 'id': 1, 'type': 14, 'value': 2 ** 64 - 1}]}
    with make_test_app(tmp_path, conn, monkeypatch) as (client, _):
        assert client.get('/api/regdata').get_json()['data'][0]['value'] == str(2 ** 64 - 1)
