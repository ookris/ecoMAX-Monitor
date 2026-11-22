"""Dane regulatora ecoMAX (ramka 0x08) i ich schemat (zapytanie 0x55 -> odpowiedz 0xD5).

Format zgodny z PyPlumIO (structures/regulator_data_schema.py, structures/regulator_data.py):
- schemat 0xD5: [liczba_blokow:uint16 LE] + n x [typ:1][id:uint16 LE],
- dane 0x08: [2 B][wersja: minor, major][tabela wersji ramek] + wartosci wg schematu.
Typy: 1 int8, 2 int16, 3 int32, 4 uint8, 5 uint16, 6 uint32, 7 float, 9 double,
10 bit (8 kolejnych bitow w jednym bajcie), 11/12 string zakonczony zerem,
13 int64, 14 uint64, 15 IPv4, 16 IPv6; 0 i 8 - typ niezdefiniowany (0 bajtow).
Identyfikatory pol moga sie w schemacie powtarzac, dlatego wynik to lista wg pozycji.
"""

from __future__ import annotations

import ipaddress
import math
import struct
from typing import Any

REGDATA_VERSION = '1.0'

# Typ ramki, ktorej wersja w tabeli wersji ramki 0x08 opisuje wersje schematu.
SCHEMA_FRAME_TYPE = 0x55

_STRUCT_TYPES: dict[int, tuple[str, int]] = {
    1: ('<b', 1), 2: ('<h', 2), 3: ('<i', 4), 4: ('<B', 1), 5: ('<H', 2), 6: ('<I', 4),
    7: ('<f', 4), 9: ('<d', 8), 13: ('<q', 8), 14: ('<Q', 8),
}
TYPE_BIT = 10
_STRING_TYPES = (11, 12)
TYPE_IPV4 = 15
TYPE_IPV6 = 16

TYPE_NAMES: dict[int, str] = {
    0: 'brak', 1: 'int8', 2: 'int16', 3: 'int32', 4: 'uint8', 5: 'uint16', 6: 'uint32',
    7: 'float', 8: 'brak', 9: 'double', 10: 'bit', 11: 'tekst', 12: 'tekst',
    13: 'int64', 14: 'uint64', 15: 'IPv4', 16: 'IPv6',
}

# Pola rozpoznane na ecoMAX920P1 przez porownanie z ramka 0x35 (verified=True)
# lub opisane na podstawie wartosci/kontekstu (verified=False - do potwierdzenia).
KNOWN_FIELDS: dict[int, tuple[str, bool]] = {
    1024: ('Temperatura CO', True),
    1025: ('Temperatura CWU', True),
    26: ('Temperatura powrotu', True),
    1030: ('Temperatura spalin', True),
    1031: ('Mieszacz 1 – temperatura', True),
    1280: ('Temperatura zadana CO', True),
    1281: ('Temperatura zadana CWU', True),
    1287: ('Mieszacz 1 – temperatura zadana', True),
    1288: ('Mieszacz 2 – temperatura zadana', False),
    1289: ('Mieszacz 3 – temperatura zadana', False),
    1290: ('Mieszacz 4 – temperatura zadana', False),
    1291: ('Mieszacz 5 – temperatura zadana', False),
    1292: ('Mieszacz 6 – temperatura zadana', False),
    1536: ('Wentylator (wyjście)', True),
    1538: ('Podajnik (wyjście)', True),
    1792: ('Stan regulatora (kod wewnętrzny)', False),
    1793: ('Moc kotła (kW)', True),
    1795: ('Moc wentylatora (%)', True),
    97: ('Strumień paliwa (kg/h)', True),
    140: ('Mieszacz 1 – otwarcie zaworu (%)', True),
    178: ('Sieć – adres IP', False),
    179: ('Sieć – maska', False),
    180: ('Sieć – brama', False),
    182: ('Sieć WLAN – adres IP', False),
    183: ('Sieć WLAN – maska', False),
    184: ('Sieć WLAN – brama', False),
    187: ('Sieć WLAN – jakość sygnału', False),
    190: ('Sieć WLAN – SSID', False),
}
for _adc_id in range(7, 17):
    KNOWN_FIELDS.setdefault(_adc_id, (f'Wejście analogowe czujnika (surowy odczyt) #{_adc_id}', False))


def parse_schema_payload(payload: bytes) -> list[tuple[int, int]]:
    """Parsuj payload 0xD5 do listy (id_pola, typ). Pusta lista, gdy format niepoprawny."""
    if not payload or len(payload) < 2:
        return []
    count = struct.unpack_from('<H', payload, 0)[0]
    offset = 2
    schema = []
    for _ in range(count):
        if offset + 3 > len(payload):
            return []
        field_type = payload[offset]
        field_id = struct.unpack_from('<H', payload, offset + 1)[0]
        if field_type not in TYPE_NAMES:
            return []
        schema.append((field_id, field_type))
        offset += 3
    return schema


def _parse_header(payload: bytes) -> tuple[int, dict[int, int]] | None:
    """(pozycja pierwszej wartosci, tabela wersji ramek) albo None, gdy format nieobslugiwany."""
    if len(payload) < 5:
        return None
    version = f'{payload[3]}.{payload[2]}'
    if version != REGDATA_VERSION:
        return None
    versions_count = payload[4]
    offset = 5 + versions_count * 3
    if offset > len(payload):
        return None
    versions = {
        payload[pos]: payload[pos + 1] | (payload[pos + 2] << 8)
        for pos in range(5, offset, 3)
    }
    return offset, versions


def decode_regdata_payload(
    payload: bytes,
    schema: list[tuple[int, int]],
    schema_version: int | None = None,
) -> list[dict[str, Any]] | None:
    """Dekoduj payload ramki 0x08 wg schematu.

    Zwraca liste {'position', 'id', 'type', 'value'} albo None, gdy ramka nie pasuje do schematu:
    inna wersja formatu, wersja schematu (0x55) w ramce rozna od `schema_version`
    albo dlugosc wartosci rozna od wynikajacej ze schematu (schemat nieaktualny).
    """
    if not schema:
        return None
    header = _parse_header(payload)
    if header is None:
        return None
    offset, versions = header
    frame_schema_version = versions.get(SCHEMA_FRAME_TYPE)
    if schema_version is not None and frame_schema_version is not None and frame_schema_version != schema_version:
        return None

    rows = []
    bit_index = 0
    try:
        for position, (field_id, field_type) in enumerate(schema):
            if field_type != TYPE_BIT and bit_index > 0:
                offset += 1        # niedokonczony bajt bitow
                bit_index = 0

            if field_type == TYPE_BIT:
                value: Any = bool(payload[offset] & (1 << bit_index))
                if bit_index == 7:
                    offset += 1
                    bit_index = 0
                else:
                    bit_index += 1
            elif field_type in _STRUCT_TYPES:
                fmt, size = _STRUCT_TYPES[field_type]
                value = struct.unpack_from(fmt, payload, offset)[0]
                offset += size
                if isinstance(value, float):
                    value = None if not math.isfinite(value) else round(value, 3)
            elif field_type in _STRING_TYPES:
                end = payload.index(0, offset)
                value = payload[offset:end].decode('latin-1')
                offset = end + 1
            elif field_type == TYPE_IPV4:
                value = str(ipaddress.IPv4Address(bytes(payload[offset:offset + 4])))
                offset += 4
            elif field_type == TYPE_IPV6:
                value = str(ipaddress.IPv6Address(bytes(payload[offset:offset + 16])))
                offset += 16
            else:
                value = None
            rows.append({'position': position, 'id': field_id, 'type': field_type, 'value': value})
    except (IndexError, ValueError, struct.error):
        return None

    if bit_index > 0:
        offset += 1
    if offset != len(payload):
        return None
    return rows


def field_name(field_id: int, labels: dict | None = None) -> tuple[str | None, str | None]:
    """(nazwa, zrodlo) pola: etykieta uzytkownika > znane pole > brak."""
    if labels:
        label = labels.get(str(field_id))
        if label:
            return label, 'user'
    known = KNOWN_FIELDS.get(field_id)
    if known:
        return known[0], 'verified' if known[1] else 'guess'
    return None, None
