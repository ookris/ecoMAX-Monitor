"""Dekoder danych ze sterownika ecoMAX 910/920.

Dwa tryby parsowania:
1. Tryb legacy/PoC - parsowanie bloku statusu znanego z ecoMax920-04.10.2019.py.
    W pasywnym nasluchu blok wystepowal jako surowa paczka z headerem 0x68 0x69 0x01,
    a przez EW11A pojawia sie takze jako payload ramki typu 0x08.
2. Tryb EcoNET (pelny protokol) - parsowanie ramek FRAME_DATA_ANS (0x35)
    z firmware econet (ecomax.py parseCurrentData)
"""

import datetime
import struct
import logging
import math
import time

logger = logging.getLogger(__name__)

# Zakresy fizyczne pol numerycznych (min, max) - wartosci spoza zakresu
# uznawane sa za uszkodzone dane z kotla.
_FIELD_BOUNDS: dict[str, tuple[float, float]] = {
    'temp_co':           (-30,  150),
    'temp_feeder':       (-30,  250),
    'temp_cwu':          (-30,  100),
    'temp_external':     (-60,   70),
    'temp_back':         (-30,  150),
    'temp_flue_gas':     (-30,  700),
    'temp_optical':      (-30,  700),
    'temp_upper_buffer': (-30,  150),
    'temp_lower_buffer': (-30,  150),
    'temp_upper_solar':  (-30,  200),
    'temp_lower_solar':  (-30,  200),
    'temp_fireplace':    (-30,  500),
    'total_gain':        (-1e6, 1e6),
    'boiler_power':      (0,    100),
    'boiler_power_kw':   (0,   1000),
    'fan_power':         (0,    100),
    'fuel_level':        (0,    100),
    'fuel_stream':       (0,    500),
    'fuel_burned_kg':    (0,    1e7),
    'work_time_100':     (0,    1e6),
    'work_time_50':      (0,    1e6),
    'work_time_33':      (0,    1e6),
    'feeder_work_time':  (0,    1e6),
    'ignitions':         (0, 100000),
}

# Publiczna stała do użycia w innych modułach – górna granica strumienia paliwa.
FUEL_STREAM_MAX: float = _FIELD_BOUNDS['fuel_stream'][1]

# Zbiór kluczy pól temperaturowych ze słownika _FIELD_BOUNDS (bez zadanych).
_TEMP_FIELDS: frozenset[str] = frozenset(k for k in _FIELD_BOUNDS if k.startswith('temp_'))

# Tryby pracy kotla (z ecomax.py Mode class, linia 311-323)
MODE_NAMES = {
    0: "Wyłączony",
    1: "Zatrzymany",
    2: "Rozpalanie",
    3: "Praca",
    4: "Nadzór",
    5: "Postój",
    6: "Czyszczenie",
    7: "Wygaszanie",
    8: "Alarm",
    9: "Ręczny",
    10: "Odblokowanie",
    11: "Inny",
}

# Nazwy temperatur (z ecomax.py ECOMAXDATA_TEMP_MEMBER_NAMES, linia 182)
TEMP_NAMES = {
    0: ('temp_co', 'Temperatura CO (zasilanie)'),
    1: ('temp_feeder', 'Temperatura podajnika'),
    2: ('temp_cwu', 'Temperatura CWU'),
    3: ('temp_external', 'Czujnik zewnetrzny'),
    4: ('temp_back', 'Temperatura powrotu'),
    5: ('temp_flue_gas', 'Temperatura spalin'),
    6: ('temp_optical', 'Czujnik optyczny (plomien)'),
    7: ('temp_upper_buffer', 'Bufor gorny'),
    8: ('temp_lower_buffer', 'Bufor dolny'),
    9: ('temp_upper_solar', 'Solar gorny'),
    10: ('temp_lower_solar', 'Solar dolny'),
    11: ('temp_fireplace', 'Kominek'),
    12: ('total_gain', 'Zysk calkowity'),
}

# Flagi wyjsc (z ecomax.py linia 1016-1021)
OUTPUT_FLAGS = {
    0x04: 'pump_co',
    0x08: 'pump_cwu',
    0x10: 'pump_circulation',
    0x800: 'pump_solar',
    0x1000: 'pump_fireplace',
    0x2000: 'contact_gzc',
}


def _parse_float32(data):
    """Parsuj float32 LE z 4 bajtow (jak parseFloat z ecomax.py linia 485-491)."""
    if len(data) < 4:
        return None
    if data == b'\xFF\xFF\xFF\xFF':
        return None
    try:
        result = struct.unpack('<f', data)[0]
        # NaN i Inf check
        if not math.isfinite(result):
            return None
        return round(result, 2)
    except struct.error:
        return None


def _parse_uint8(data):
    """Parsuj uint8."""
    if isinstance(data, int):
        val = data
    else:
        val = data[0] if len(data) > 0 else None
    if val == 0xFF:
        return None
    return val


def _parse_uint16_le(data):
    """Parsuj uint16 LE."""
    if len(data) < 2:
        return None
    return struct.unpack('<H', data[:2])[0]


def _parse_int16_le(data):
    """Parsuj int16 LE (signed)."""
    if len(data) < 2:
        return None
    return struct.unpack('<h', data[:2])[0]


def _parse_uint32_le(data):
    """Parsuj uint32 LE."""
    if len(data) < 4:
        return None
    return struct.unpack('<I', data[:4])[0]


def _normalize_legacy_packet(packet):
    """Przygotuj blok statusu do dekodowania niezaleznie od zrodla.

    Obslugiwane warianty:
    - surowa paczka z naglowkiem 0x68 0x69 0x01 z dawnego PoC,
    - payload ramki 0x08 z EW11A, bez tego 3-bajtowego naglowka.
    """
    if packet.startswith(b'\x68\x69\x01'):
        return packet[3:]
    return packet


def _find_econet_data_offset(payload):
    """Znajdz poczatek wlasciwych danych w ramce 0x35.

    W strumieniu z EW11A ramka 0x35 moze miec prefiks przed blokiem danych.
    Szukamy miejsca, od ktorego uklad [mode][outputs][flags][num_temps] wyglada wiarygodnie.
    """
    max_prefix = min(32, max(0, len(payload) - 10))

    for idx in range(max_prefix + 1):
        mode = payload[idx]
        if mode not in MODE_NAMES:
            continue

        temps_pos = idx + 9
        if temps_pos >= len(payload):
            continue

        num_temps = payload[temps_pos]
        if num_temps == 0 or num_temps > len(TEMP_NAMES):
            continue

        temps_start = idx + 10
        temps_end = temps_start + (num_temps * 5)
        if temps_end > len(payload):
            continue

        valid_entries = 0
        for pos in range(temps_start, temps_end, 5):
            temp_index = payload[pos]
            if temp_index in TEMP_NAMES:
                valid_entries += 1

        if valid_entries == num_temps:
            return idx

    return None


class BoilerData:
    """Kontener na zdekodowane dane z kotla."""

    def __init__(self):
        self.timestamp = time.time()
        self.mode = None
        self.mode_name = "Nieznany"

        # Temperatury
        self.temp_co = None
        self.temp_feeder = None
        self.temp_cwu = None
        self.temp_external = None
        self.temp_back = None
        self.temp_flue_gas = None
        self.temp_optical = None
        self.temp_upper_buffer = None
        self.temp_lower_buffer = None
        self.temp_upper_solar = None
        self.temp_lower_solar = None
        self.temp_fireplace = None
        self.total_gain = None

        # Temperatury zadane
        self.temp_co_set = None
        self.temp_cwu_set = None

        # Parametry operacyjne
        self.fan_power = None
        self.boiler_power = None
        self.boiler_power_kw = None
        self.fuel_level = None
        self.fuel_stream = None  # kg/h

        # Pompy
        self.pump_co = False
        self.pump_cwu = False
        self.pump_circulation = False

        # Z PoC
        self.flame = None
        self.blower = None
        self.fuel_burned_kg = None
        self.ignitions = None
        self.work_time_100 = None
        self.work_time_50 = None
        self.work_time_33 = None
        self.feeder_work_time = None

        # Czas sterownika (ramka TIME / 0x89)
        self.controller_time_unix = None
        self.controller_time_iso = None
        self.controller_time_year = None
        self.controller_time_month = None
        self.controller_time_day = None
        self.controller_time_hour = None
        self.controller_time_minute = None
        self.controller_time_second = None

        # Mieszacze (z ramki 0x35 sekcja sensor data)
        self.mixers_available = 0
        self.mixers_connected = 0
        self.mixer_data: list[dict] = []  # [{temp, target, pump}, ...]

        # Termostaty (z ramki 0x35 sekcja sensor data)
        self.thermostats_available = 0
        self.thermostats_connected = 0
        self.thermostat_data: list[dict] = []  # [{state, current_temp, target_temp}, ...]

        # Wersje modułów firmware (z ramki 0x35)
        self.module_a: str | None = None
        self.module_b: str | None = None
        self.module_c: str | None = None
        self.module_ecolambda: str | None = None
        self.module_ecoster: str | None = None
        self.module_panel: str | None = None

    def is_plausible(self) -> bool:
        """Sprawdz czy zdekodowane dane sa fizycznie wiarygodne.

        Zwraca False jesli:
        - zadne z pol temperaturowych (zdefiniowanych w _FIELD_BOUNDS z prefiksem
          'temp_') nie ma wartosci – ramka jest calkowicie pusta,
                - temp_co ma wartosc 0, a kluczowe temperatury rdzeniowe
                    (temp_cwu/temp_back/temp_flue_gas/temp_feeder) sa puste lub zerowe,
        - jakakolwiek niepusta wartosc numeryczna wykracza poza tablice
          _FIELD_BOUNDS (co wskazuje na uszkodzona ramke).
        """
        d = self.to_dict()
        if not any(d.get(f) is not None for f in _TEMP_FIELDS):
            return False

        core_temp_fields = ('temp_cwu', 'temp_back', 'temp_flue_gas', 'temp_feeder')
        temp_co = d.get('temp_co')
        if temp_co in (0, 0.0) and all(d.get(field) in (None, 0, 0.0) for field in core_temp_fields):
            logger.debug(
                "is_plausible: odrzucono niekompletna ramke (temp_co=0, pozostale temperatury puste lub zerowe)"
            )
            return False

        for field, (lo, hi) in _FIELD_BOUNDS.items():
            val = d.get(field)
            if val is None:
                continue
            if not isinstance(val, (int, float)):
                continue
            if not math.isfinite(val) or not (lo <= val <= hi):
                logger.debug(
                    "is_plausible: pole '%s' = %s poza zakresem [%s, %s]",
                    field, val, lo, hi,
                )
                return False
        return True

    def to_dict(self):
        """Konwertuj na slownik (do JSON/bazy)."""
        return {
            'timestamp': self.timestamp,
            'mode': self.mode,
            'mode_name': self.mode_name,
            'temp_co': self.temp_co,
            'temp_feeder': self.temp_feeder,
            'temp_cwu': self.temp_cwu,
            'temp_external': self.temp_external,
            'temp_back': self.temp_back,
            'temp_flue_gas': self.temp_flue_gas,
            'temp_optical': self.temp_optical,
            'temp_upper_buffer': self.temp_upper_buffer,
            'temp_lower_buffer': self.temp_lower_buffer,
            'temp_upper_solar': self.temp_upper_solar,
            'temp_lower_solar': self.temp_lower_solar,
            'temp_fireplace': self.temp_fireplace,
            'total_gain': self.total_gain,
            'temp_co_set': self.temp_co_set,
            'temp_cwu_set': self.temp_cwu_set,
            'fan_power': self.fan_power,
            'boiler_power': self.boiler_power,
            'boiler_power_kw': self.boiler_power_kw,
            'fuel_level': self.fuel_level,
            'fuel_stream': self.fuel_stream,
            'pump_co': self.pump_co,
            'pump_cwu': self.pump_cwu,
            'pump_circulation': self.pump_circulation,
            'flame': self.flame,
            'blower': self.blower,
            'fuel_burned_kg': self.fuel_burned_kg,
            'ignitions': self.ignitions,
            'work_time_100': self.work_time_100,
            'work_time_50': self.work_time_50,
            'work_time_33': self.work_time_33,
            'feeder_work_time': self.feeder_work_time,
            'controller_time_unix': self.controller_time_unix,
            'controller_time_iso': self.controller_time_iso,
            'controller_time_year': self.controller_time_year,
            'controller_time_month': self.controller_time_month,
            'controller_time_day': self.controller_time_day,
            'controller_time_hour': self.controller_time_hour,
            'controller_time_minute': self.controller_time_minute,
            'controller_time_second': self.controller_time_second,
            'mixers_available': self.mixers_available,
            'mixers_connected': self.mixers_connected,
            'mixer_data': self.mixer_data,
            'thermostats_available': self.thermostats_available,
            'thermostats_connected': self.thermostats_connected,
            'thermostat_data': self.thermostat_data,
            'module_a': self.module_a,
            'module_b': self.module_b,
            'module_c': self.module_c,
            'module_ecolambda': self.module_ecolambda,
            'module_ecoster': self.module_ecoster,
            'module_panel': self.module_panel,
        }


def decode_time_frame(payload):
    """Dekoduj ramkę czasu sterownika (typ 0x89, payload zaczyna się od ASCII `TIME`).

    Zwraca słownik z polami czasu lub None jeśli payload jest nieczytelny.
    """
    if not payload or len(payload) < 14:
        return None

    marker = b'TIME'
    marker_pos = payload.find(marker)
    if marker_pos < 0:
        return None

    base = marker_pos + len(marker)
    if base + 9 > len(payload):
        return None

    # W zaobserwowanych ramkach po prefiksie TIME występują 3 bajty pomocnicze,
    # a dalej: year(uint16 LE), month, day, hour, minute, second.
    year = _parse_uint16_le(payload[base + 3:base + 5])
    month = payload[base + 5] if base + 5 < len(payload) else None
    day = payload[base + 6] if base + 6 < len(payload) else None
    hour = payload[base + 7] if base + 7 < len(payload) else None
    minute = payload[base + 8] if base + 8 < len(payload) else None
    second = payload[base + 9] if base + 9 < len(payload) else None

    if not all(isinstance(v, int) for v in (year, month, day, hour, minute, second)):
        return None
    if not (2000 <= year <= 2100):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None

    try:
        dt = datetime.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None

    return {
        'controller_time_unix': dt.timestamp(),
        'controller_time_iso': dt.strftime('%Y-%m-%d %H:%M:%S'),
        'controller_time_year': year,
        'controller_time_month': month,
        'controller_time_day': day,
        'controller_time_hour': hour,
        'controller_time_minute': minute,
        'controller_time_second': second,
    }


def decode_poc_packet(packet):
    """Dekoduj legacy blok statusu z PoC lub z payloadu ramki 0x08.

    W oryginalnym PoC licznik startowal po wykryciu 0x68 0x69 0x01,
    dlatego wszystkie offsety odnosza sie do danych ZA tym naglowkiem.
    """
    payload = _normalize_legacy_packet(packet)

    # Najwyzszy uzyty offset to 306:308 (ignitions) - minimalna bezpieczna dlugosc
    POC_PACKET_MIN_LEN = 308
    if len(payload) < POC_PACKET_MIN_LEN:
        logger.warning("Paczka za krotka: %d bajtow (wymagane min. %d)",
                       len(payload), POC_PACKET_MIN_LEN)
        return None

    data = BoilerData()

    # Temperatury (float32 LE)
    data.temp_co = _parse_float32(payload[86:90])
    data.temp_feeder = _parse_float32(payload[90:94])
    data.temp_back = _parse_float32(payload[106:110])
    data.flame = _parse_float32(payload[118:122])
    data.fuel_stream = _parse_float32(payload[234:238])
    data.boiler_power_kw = _parse_float32(payload[250:254])
    data.fuel_burned_kg = _parse_float32(payload[302:306])

    # Uint8
    data.blower = _parse_uint8(payload[279:280])
    data.fan_power = data.blower

    # Uint16 LE
    data.work_time_100 = _parse_uint16_le(payload[294:296])
    data.work_time_50 = _parse_uint16_le(payload[296:298])
    data.work_time_33 = _parse_uint16_le(payload[298:300])
    data.feeder_work_time = _parse_uint16_le(payload[300:302])
    data.ignitions = _parse_uint16_le(payload[306:308])

    # Mapowanie plomienia na temp_optical
    data.temp_optical = data.flame

    # Domyslny tryb (z PoC nie odczytujemy trybu)
    data.mode = 3 if data.temp_co and data.temp_co > 30 else 0
    data.mode_name = MODE_NAMES.get(data.mode, "Nieznany")

    logger.debug("Legacy decode: temp_co=%.1f, feeder=%.1f, back=%.1f, flame=%.1f",
                 data.temp_co or 0, data.temp_feeder or 0,
                 data.temp_back or 0, data.flame or 0)

    return data


def _decode_extended_sensor_data(payload: bytes, idx: int, data: 'BoilerData') -> None:
    """Parsuj rozszerzone dane z ramki 0x35: moduły, lambda, mieszacze, termostaty.

    Format zgodny z firmware ecomax.py parseCurrentData (linia 1069-1158):
    1. module_a: STAŁE 5 bajtów (major, minor, patch, letter_chr, build)
    2. module_b..panel (5 modułów): 0xFF=skip(1) LUB 3 bajty (major, minor, patch)
    3. lambda data: 0xFF=skip(1) LUB 4 bajty (status, set, level:int16 LE)
    4. termostaty: 0xFF=skip(1) LUB contacts(1) + count(1) + 9 bajtów × count
    5. mieszacze: count(1) + 8 bajtów × count

    Parsowanie jest best-effort — wyjątek łapany przez wywołującego.
    """
    # --- Module A: stałe 5 bajtów ---
    if idx + 5 > len(payload):
        return
    t = struct.unpack('<BBBBB', payload[idx:idx + 5])
    idx += 5
    letter = chr(t[3]) if 0x41 <= t[3] <= 0x5A else None  # tylko wielkie litery A-Z
    if letter and all(t[i] < 256 for i in range(5)):
        data.module_a = f"{t[0]}.{t[1]}.{t[2]}.{letter}{t[4]}"

    # --- Moduły B, C, ecoLAMBDA, ecoSTER, Panel: 0xFF=skip LUB 3 bajty ---
    for attr in ('module_b', 'module_c', 'module_ecolambda', 'module_ecoster', 'module_panel'):
        if idx >= len(payload):
            return
        if payload[idx] == 0xFF:
            idx += 1
        else:
            if idx + 3 > len(payload):
                return
            t3 = struct.unpack('<BBB', payload[idx:idx + 3])
            setattr(data, attr, f"{t3[0]}.{t3[1]}.{t3[2]}")
            idx += 3

    # --- Dane lambda: 0xFF=skip LUB 4 bajty (status, set, level:int16 LE) ---
    if idx >= len(payload):
        return
    if payload[idx] == 0xFF:
        idx += 1
    else:
        idx += 4  # pomijamy wartości lambda (rzadko potrzebne)

    # --- Termostaty: 0xFF=skip LUB contacts(1) + count(1) + 9 bajtów × count ---
    if idx >= len(payload):
        return
    if payload[idx] == 0xFF:
        idx += 1
    else:
        # contacts = bitmask podłączonych termostatów
        idx += 1  # pomijamy contacts
        if idx >= len(payload):
            return
        thermostats_count = payload[idx]
        idx += 1
        if thermostats_count > 8:  # sanity check
            return
        thermostat_list = []
        for _ in range(thermostats_count):
            if idx + 9 > len(payload):
                break
            state = payload[idx]
            current_temp = _parse_float32(payload[idx + 1:idx + 5])
            target_temp = _parse_float32(payload[idx + 5:idx + 9])
            idx += 9
            thermostat_list.append({
                'state': state,
                'current_temp': current_temp,
                'target_temp': target_temp,
            })
        data.thermostats_available = thermostats_count
        data.thermostat_data = thermostat_list
        data.thermostats_connected = sum(1 for t in thermostat_list if t['current_temp'] is not None)

    # --- Mieszacze: count(1) + 8 bajtów × count ---
    # Format: mixerTemp:f32(4), mixerSetTemp:u8(1), reserved(1), mixerOutputs:u8(1), reserved(1)
    if idx >= len(payload):
        return
    mixers_count = payload[idx]
    idx += 1
    if mixers_count > 16:  # sanity check
        return
    mixer_list = []
    for _ in range(mixers_count):
        if idx + 8 > len(payload):
            break
        temp = _parse_float32(payload[idx:idx + 4])
        target = payload[idx + 4]
        outputs = payload[idx + 6]
        idx += 8
        pump_on = bool(outputs & 0x01)
        mixer_list.append({
            'current_temp': temp,
            'target_temp': target if target != 0xFF else None,
            'pump': pump_on,
        })
    data.mixers_available = mixers_count
    data.mixer_data = mixer_list
    data.mixers_connected = sum(1 for m in mixer_list if m['current_temp'] is not None)


def decode_econet_data_frame(payload):
    """Dekoduj ramke FRAME_DATA_ANS (0x35) z pelnego protokolu EcoNET.

    Parsowanie na podstawie ecomax.py parseCurrentData (linia 1013+).
    Payload zaczyna sie od bajtu po type (0x35).
    """
    if len(payload) < 20:
        logger.warning("Payload za krotki: %d bajtow", len(payload))
        return None

    data = BoilerData()
    idx = _find_econet_data_offset(payload)
    if idx is None:
        logger.debug("Nie znaleziono poprawnego bloku danych w ramce 0x35")
        return None

    try:
        # Tryb pracy (1 bajt)
        data.mode = payload[idx]
        data.mode_name = MODE_NAMES.get(data.mode, "Nieznany")
        idx += 1

        # Wyjscia - outputs (4 bajty uint32 LE)
        if idx + 4 > len(payload):
            return data
        outputs = _parse_uint32_le(payload[idx:idx + 4])
        if outputs is not None:
            data.pump_co = bool(outputs & 0x04)
            data.pump_cwu = bool(outputs & 0x08)
            data.pump_circulation = bool(outputs & 0x10)
        idx += 4

        # Flagi wyjsc (4 bajty) - pomijamy, uzywamy outputs wyzej
        idx += 4

        # Liczba temperatur (1 bajt)
        if idx >= len(payload):
            return data
        num_temps = payload[idx]
        idx += 1

        # Temperatury: [index:1][value:4 float32] x num_temps
        for _ in range(num_temps):
            if idx + 5 > len(payload):
                break
            temp_index = payload[idx]
            temp_value = _parse_float32(payload[idx + 1:idx + 5])
            idx += 5

            if temp_index in TEMP_NAMES and temp_value is not None:
                attr_name = TEMP_NAMES[temp_index][0]
                if hasattr(data, attr_name):
                    setattr(data, attr_name, temp_value)

        # Temperatura CO zadana, status CO, temp CWU zadana, status CWU
        if idx + 4 <= len(payload):
            data.temp_co_set = _parse_uint8(payload[idx:idx + 1])
            # status_co = payload[idx + 1]
            data.temp_cwu_set = _parse_uint8(payload[idx + 2:idx + 3])
            # status_cwu = payload[idx + 3]
            idx += 4
        else:
            return data

        # Liczba alarmow (1 bajt) + dane alarmow
        if idx >= len(payload):
            return data
        alarms_no = payload[idx]
        idx += 1 + alarms_no

        # Fuel level, transmission, fan power, boiler power, power kW, fuel stream, thermostat
        if idx + 16 <= len(payload):
            data.fuel_level = _parse_uint8(payload[idx:idx + 1])
            # transmission = payload[idx + 1]
            data.fan_power = _parse_float32(payload[idx + 2:idx + 6])
            data.boiler_power = _parse_uint8(payload[idx + 6:idx + 7])
            data.boiler_power_kw = _parse_float32(payload[idx + 7:idx + 11])
            data.fuel_stream = _parse_float32(payload[idx + 11:idx + 15])
            # thermostat = payload[idx + 15]
            idx += 16
        else:
            logger.debug("EcoNET decode: brak danych operacyjnych")

        # === Dane rozszerzone: moduły, lambda, mieszacze, termostaty ===
        # Format zgodny z PyPlumIO SensorDataStructure.decode()
        # Wszystko opcjonalne - brak danych nie jest błędem.
        try:
            _decode_extended_sensor_data(payload, idx, data)
        except Exception as e:
            logger.debug("EcoNET decode: nie udało się sparsować danych rozszerzonych: %s", e)

        logger.debug("EcoNET decode: mode=%s, temp_co=%.1f, cwu=%.1f, power=%s%%, mixers=%d, thermostats=%d",
                     data.mode_name, data.temp_co or 0,
                     data.temp_cwu or 0, data.boiler_power or 0,
                     data.mixers_available, data.thermostats_available)

    except (IndexError, struct.error) as e:
        logger.error("Blad dekodowania ramki EcoNET: %s", e)

    return data


def find_poc_packets(buffer):
    """Znajdz paczki PoC w buforze (header 0x68 0x69 0x01, dlugosc ~361).

    Returns:
        (packets, remaining) - lista surowych paczek i pozostaly bufor
    """
    HEADER = bytes([0x68, 0x69, 0x01])
    PACKET_LEN = 361
    packets = []
    pos = 0

    while pos < len(buffer) - 3:
        idx = buffer.find(HEADER, pos)
        if idx < 0:
            break

        # Sprawdz czy mamy cala paczke
        if idx + PACKET_LEN > len(buffer):
            return packets, buffer[idx:]

        packets.append(bytes(buffer[idx:idx + PACKET_LEN]))
        pos = idx + PACKET_LEN

    remaining_start = pos
    # Szukaj ew. poczatku nastepnego headera na koncu
    last_header = buffer.find(HEADER, remaining_start)
    if last_header >= 0:
        return packets, buffer[last_header:]

    return packets, b''
