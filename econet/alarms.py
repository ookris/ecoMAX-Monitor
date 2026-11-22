"""Alarmy sterownika ecoMAX: kody, nazwy i dekodowanie historii (ramka 0x3D -> 0xBD).

Kody i format zgodne z PyPlumIO (pyplumio/const.py AlertType, structures/alerts.py).
Odpowiedz na zapytanie 0x3D wysyla panel (adres 0x50) jako ramke rozgloszeniowa 0xBD:
[total][start][count] + count x [code:1][from:uint32 LE][to:uint32 LE]
Czas jest liczony w sekundach od 2000-01-01 w specjalnym ukladzie (miesiac = 31 dni,
rok = 12 x 31 dni), w czasie lokalnym sterownika. to = 0xFFFFFFFF -> alarm trwa.
"""

from __future__ import annotations

import datetime
import struct
from typing import Any

# Maksymalna liczba alarmow w jednej odpowiedzi (zweryfikowane na ecoMAX920P1 + panel 5.11).
ALERTS_PAGE_SIZE = 10

_UINT32_UNDEFINED = 0xFFFFFFFF

ALARM_NAMES: dict[int, str] = {
    0: 'Zanik zasilania',
    1: 'Uszkodzenie czujnika temperatury kotła',
    2: 'Przekroczenie maksymalnej temperatury kotła',
    3: 'Uszkodzenie czujnika temperatury podajnika',
    4: 'Przekroczenie maksymalnej temperatury podajnika',
    5: 'Uszkodzenie czujnika temperatury spalin',
    6: 'Przekroczenie maksymalnej temperatury spalin',
    7: 'Nieudana próba rozpalania',
    8: 'Brak paliwa',
    9: 'Wykryto wyciek',
    10: 'Uszkodzenie czujnika ciśnienia',
    11: 'Awaria wentylatora',
    12: 'Za niskie ciśnienie powietrza',
    13: 'Nieudane wygaszanie',
    14: 'Uszkodzenie czujnika płomienia',
    15: 'Zablokowany siłownik liniowy',
    16: 'Nieprawidłowe parametry',
    17: 'Ostrzeżenie o kondensacji',
    18: 'Zadziałał termik bezpieczeństwa kotła (STB)',
    19: 'Zadziałał termik bezpieczeństwa podajnika (STB)',
    20: 'Przekroczenie minimalnego ciśnienia wody',
    21: 'Przekroczenie maksymalnego ciśnienia wody',
    22: 'Zablokowany podajnik',
    23: 'Zgaśnięcie płomienia',
    24: 'Awaria wentylatora wyciągowego',
    25: 'Awaria podajnika zewnętrznego',
    26: 'Uszkodzenie czujnika temperatury kolektora solarnego',
    27: 'Uszkodzenie czujnika temperatury obiegu solarnego',
    28: 'Uszkodzenie czujnika temperatury obiegu H1',
    29: 'Uszkodzenie czujnika temperatury obiegu H2',
    30: 'Uszkodzenie czujnika temperatury obiegu H3',
    31: 'Uszkodzenie czujnika temperatury zewnętrznej',
    32: 'Uszkodzenie czujnika temperatury CWU',
    33: 'Uszkodzenie czujnika temperatury obiegu H0',
    34: 'Ochrona przed zamarzaniem (bez źródła ciepła)',
    35: 'Ochrona przed zamarzaniem (ze źródłem ciepła)',
    36: 'Przekroczenie maksymalnej temperatury kolektora solarnego',
    37: 'Przekroczenie maksymalnej temperatury ogrzewania podłogowego',
    38: 'Schładzanie kotła',
    39: 'Brak komunikacji z modułem ecoLAMBDA',
    40: 'Zablokowana przepustnica powietrza pierwotnego',
    41: 'Zablokowana przepustnica powietrza wtórnego',
    42: 'Przepełnienie podajnika',
    43: 'Przepełnienie paleniska',
    44: 'Brak komunikacji z modułem B',
    45: 'Awaria siłownika czyszczenia',
    46: 'Przekroczenie minimalnego ciśnienia',
    47: 'Przekroczenie maksymalnego ciśnienia',
    48: 'Uszkodzony czujnik ciśnienia',
    49: 'Przekroczenie maksymalnej temperatury głównego źródła ciepła',
    50: 'Przekroczenie maksymalnej temperatury dodatkowego źródła ciepła',
    51: 'Panel solarny niedostępny',
    52: 'Awaria sterowania podajnikiem',
    53: 'Zablokowany podajnik',
    54: 'Przekroczenie maksymalnej temperatury termopary',
    55: 'Uszkodzone okablowanie termopary',
    255: 'Nieznany błąd',
}

# Kod alarmu "Nieudana próba rozpalania" (uzywany w analityce skutecznosci rozpalen).
ALARM_KINDLING_FAILURE = 7


def alarm_name(code: int | None) -> str:
    if code is None:
        return 'Nieznany alarm'
    return ALARM_NAMES.get(int(code), f'Alarm {code}')


def controller_seconds_to_datetime(seconds: int) -> datetime.datetime | None:
    """Zamien czas alarmu sterownika na datetime (lokalny czas sterownika, naive)."""
    if seconds is None or seconds == _UINT32_UNDEFINED:
        return None
    parts = []
    remaining = int(seconds)
    for unit, offset in ((60 * 60 * 24 * 31 * 12, 2000), (60 * 60 * 24 * 31, 1),
                         (60 * 60 * 24, 1), (60 * 60, 0), (60, 0), (1, 0)):
        value = remaining // unit
        remaining -= value * unit
        parts.append(value + offset)
    try:
        return datetime.datetime(*parts)
    except ValueError:
        return None


def decode_alerts_payload(payload: bytes) -> dict[str, Any] | None:
    """Dekoduj payload ramki 0xBD.

    Zwraca {'total', 'start', 'count', 'alerts': [{'code', 'name', 'start', 'end', 'invalid'}]}
    gdzie start/end to naive datetime (end = None dla trwajacego alarmu),
    albo None, gdy payload jest za krotki. Alarmy sa uporzadkowane od najnowszego
    (start=0 to najnowszy alarm - zweryfikowane na ecoMAX920P1 z panelem 5.11).
    """
    if not payload or len(payload) < 3:
        return None
    total, start, count = payload[0], payload[1], payload[2]
    alerts = []
    offset = 3
    for _ in range(count):
        if offset + 9 > len(payload):
            break
        code = payload[offset]
        from_raw, to_raw = struct.unpack_from('<II', payload, offset + 1)
        offset += 9
        start_dt = controller_seconds_to_datetime(from_raw)
        end_dt = controller_seconds_to_datetime(to_raw)
        alerts.append({
            'code': code,
            'name': alarm_name(code),
            'start': start_dt,
            'end': end_dt,
            # Znacznik czasu, ktorego nie da sie zdekodowac (0xFFFFFFFF w `to` = alarm trwa).
            'invalid': start_dt is None or (end_dt is None and to_raw != _UINT32_UNDEFINED),
        })
    return {'total': total, 'start': start, 'count': count, 'alerts': alerts}
