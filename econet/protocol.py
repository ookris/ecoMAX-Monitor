"""Parser protokolu EcoNET.

Format ramki:
[0x68][Dlugosc:2 bajty LE][DA][SA][Typ][Wersja][Payload...][CRC XOR][0x16]

Na podstawie firmware econet:
- comm.py (findFrameEm)
- ecomax.py (stale ramek, CRC)
- crcLib.py (CRC-CCITT)
"""

import struct
import logging

logger = logging.getLogger(__name__)

# Stale protokolu
BEGIN_FRAME = 0x68
END_FRAME = 0x16
ECONET_VERSION = 0x05

# Adresy
ADDR_ECONET = 0x56
ADDR_ECOSTER = 0x51
ADDR_BROADCAST = 0x00
ADDR_ECOMAX = 0x45

# Typy ramek - zapytania
FRAME_CHECK_DEVICE = 0x30
FRAME_GET_PARAMS = 0x31
FRAME_GET_MIXER_PARAMS = 0x32
FRAME_SET_PARAM = 0x33
FRAME_SET_MIXER_PARAM = 0x34
FRAME_DATA_ANS = 0x35
FRAME_GET_SETTINGS = 0x38
FRAME_GET_UID = 0x39
FRAME_SET_BOILER_CONTROL = 0x3B
FRAME_GET_ALARMS = 0x3D
FRAME_GET_SCHEDULES = 0x36
FRAME_SET_SCHEDULE = 0x37
FRAME_GET_THERMOSTAT_PARAMS = 0x5C
FRAME_GET_SERVICE_PARAMS = 0x61
FRAME_SET_THERMOSTAT_PARAM = 0x5D

# Typy ramek - odpowiedzi
FRAME_DEVICE_AVAILABLE = 0xB0
FRAME_GET_PARAMS_ANS = 0xB1
FRAME_GET_MIXER_PARAMS_ANS = 0xB2
FRAME_SET_PARAM_ANS = 0xB3
FRAME_SET_MIXER_PARAM_ANS = 0xB4
FRAME_GET_SETTINGS_ANS = 0xB8
FRAME_UID_ANS = 0xB9
FRAME_SET_BOILER_CONTROL_ANS = 0xBB
FRAME_SCHEDULES_ANS = 0xB6
FRAME_ALARMS_ANS = 0xBD
FRAME_SET_THERMOSTAT_PARAM_ANS = 0xDD
FRAME_GET_THERMOSTAT_PARAMS_ANS = 0xDC
FRAME_GET_SERVICE_PARAMS_ANS = 0xE1
FRAME_TIME = 0x89

# Minimalne/maksymalne dlugosci
MIN_FRAME_LENGTH = 10
MAX_FRAME_LENGTH = 10000
FRAME_HEADER_LEN = 9  # BEGIN + len(2) + DA + SA + type + version + ... + CRC + END


def crc_xor(data):
    """Oblicz CRC XOR ramki (jak w ecomax.py linia 229-233)."""
    result = 0
    for b in data:
        result ^= b
    return result & 0xFF


def crc_ccitt(data):
    """Oblicz CRC-CCITT (jak w crcLib.py)."""
    crc = 0xFFFF
    for c in data:
        c = c ^ (crc & 0xFF)
        c = c ^ ((c << 4) & 0xFF)
        crc = (((c & 0xFFFF) << 8) | ((crc >> 8) & 0xFF)) ^ \
              ((c & 0xFF) >> 4) ^ ((c & 0xFFFF) << 3)
    return crc & 0xFFFF


def is_valid_frame(frame_bytes):
    """Sprawdz czy ramka jest poprawna (CRC, BEGIN/END markers)."""
    if len(frame_bytes) < MIN_FRAME_LENGTH:
        return False
    if frame_bytes[0] != BEGIN_FRAME:
        return False
    if frame_bytes[-1] != END_FRAME:
        return False
    # CRC XOR: przedostatni bajt to XOR wszystkich bajtow od BEGIN do danych
    expected_crc = crc_xor(frame_bytes[:-2])
    actual_crc = frame_bytes[-2]
    return expected_crc == actual_crc


def parse_frame(frame_bytes):
    """Parsuj ramke EcoNET na slownik.

    Returns:
        dict z kluczami: length, dest, source, type_byte, version, payload
        lub None jesli ramka niepoprawna
    """
    if not is_valid_frame(frame_bytes):
        return None

    frame_len = frame_bytes[1] | (frame_bytes[2] << 8)
    dest = frame_bytes[3]
    source = frame_bytes[4]
    sender_type = frame_bytes[5]
    version = frame_bytes[6]
    frame_type = frame_bytes[7]
    payload = frame_bytes[8:-2]  # od bajtu 8 do CRC (wylaczajac CRC i END)

    return {
        'length': frame_len,
        'dest': dest,
        'source': source,
        'sender_type': sender_type,
        'version': version,
        'type': frame_type,
        'payload': payload,
        'raw': frame_bytes,
    }


def find_frames(buffer):
    """Znajdz ramki w buforze bajtow (wzorowane na findFrameEm z comm.py).

    Args:
        buffer: bytes lub bytearray

    Returns:
        (frames, remaining) - lista znalezionych ramek i pozostaly bufor
    """
    frames = []
    pos = 0

    while pos < len(buffer):
        # Szukaj BEGIN_FRAME
        idx = buffer.find(bytes([BEGIN_FRAME]), pos)
        if idx < 0:
            break

        # Sprawdz czy mamy wystarczajaco bajtow na dlugosc
        if idx + 3 > len(buffer):
            return frames, buffer[idx:]

        # Odczytaj dlugosc ramki
        frame_len = buffer[idx + 1] | (buffer[idx + 2] << 8)

        # Walidacja dlugosci
        if frame_len < MIN_FRAME_LENGTH or frame_len > MAX_FRAME_LENGTH:
            pos = idx + 1
            continue

        # Sprawdz czy mamy cala ramke
        if idx + frame_len > len(buffer):
            return frames, buffer[idx:]

        # Wyciagnij ramke
        frame_data = buffer[idx:idx + frame_len]

        # Waliduj
        if is_valid_frame(frame_data):
            parsed = parse_frame(frame_data)
            if parsed:
                frames.append(parsed)
            pos = idx + frame_len
        else:
            pos = idx + 1

    return frames, buffer[pos:] if pos < len(buffer) else b''


class FrameBuilder:
    """Budowanie ramek EcoNET do wysylania."""

    @staticmethod
    def create_frame(dest_addr, frame_body):
        """Utworz ramke EcoNET (jak createEcoNetFrame z ecomax.py linia 335-338)."""
        frame_len = len(frame_body) + FRAME_HEADER_LEN
        data = bytes([
            BEGIN_FRAME,
            frame_len & 0xFF,
            (frame_len >> 8) & 0xFF,
            dest_addr,
            ADDR_ECONET,
            0x30,  # sender type
            ECONET_VERSION,
        ]) + frame_body

        crc = crc_xor(data)
        return data + bytes([crc, END_FRAME])

    @staticmethod
    def device_available_response(dest_addr):
        """Odpowiedz B0 - device available (uproszczona)."""
        # Minimalna odpowiedz B0 z zerami zamiast danych sieciowych
        body = bytes([FRAME_DEVICE_AVAILABLE, 0x01])
        # IP/maska/gateway zerowe (4+4+4 = 12 bajtow) x2 (eth + wlan)
        body += bytes(24)
        # Statusy
        body += bytes([0, 0, 0, 0])
        # SSID puste
        body += bytes([0])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def request_ecomax_parameters(dest_addr=ADDR_ECOMAX, start=0, count=255):
        """Ramka 0x31 - pobierz zakres parametrów ecoMAX."""
        body = bytes([FRAME_GET_PARAMS, count & 0xFF, start & 0xFF])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def set_ecomax_parameter(dest_addr=ADDR_ECOMAX, index=0, value=0):
        """Ramka 0x33 - ustaw pojedynczy parametr ecoMAX."""
        body = bytes([FRAME_SET_PARAM, index & 0xFF, value & 0xFF])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def set_mixer_parameter(dest_addr=ADDR_ECOMAX, device_index=0, index=0, value=0):
        """Ramka 0x34 - ustaw parametr mieszacza."""
        body = bytes([
            FRAME_SET_MIXER_PARAM,
            device_index & 0xFF,
            index & 0xFF,
            value & 0xFF,
        ])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def set_thermostat_parameter(dest_addr=ADDR_ECOMAX, index=0, value=0, offset=0, size=1):
        """Ramka 0x5D - ustaw parametr termostatu.

        Format zgodny z PyPlumIO: [frame][index+offset][value_le(size)].
        """
        index_with_offset = (int(index) + int(offset)) & 0xFF
        value = int(value)
        size = max(1, int(size))
        body = bytes([FRAME_SET_THERMOSTAT_PARAM, index_with_offset]) + value.to_bytes(
            length=size,
            byteorder='little',
            signed=False,
        )
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def request_mixer_parameters(dest_addr=ADDR_ECOMAX, start=0, count=255):
        """Ramka 0x32 - pobierz parametry mieszaczy."""
        body = bytes([FRAME_GET_MIXER_PARAMS, count & 0xFF, start & 0xFF])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def request_service_parameters(dest_addr=ADDR_ECOMAX, start=0, count=255):
        """Ramka 0x61 - pobierz parametry serwisowe."""
        body = bytes([FRAME_GET_SERVICE_PARAMS, count & 0xFF, start & 0xFF])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def set_boiler_control(dest_addr=ADDR_ECOMAX, value=0):
        """Ramka 0x3B - wlacz (1) / wylacz (0) regulator kotla."""
        body = bytes([FRAME_SET_BOILER_CONTROL, 1 if value else 0])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def request_uid(dest_addr=ADDR_ECOMAX):
        """Ramka 0x39 - pobierz UID i model sterownika."""
        body = bytes([FRAME_GET_UID])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def request_alerts(dest_addr=ADDR_ECOMAX, start=0, count=10):
        """Ramka 0x3D - pobierz historie alarmow (odpowiada panel ramka 0xBD)."""
        body = bytes([FRAME_GET_ALARMS, start & 0xFF, count & 0xFF])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def request_schedules(dest_addr=ADDR_ECOMAX):
        """Ramka 0x36 - pobierz harmonogramy."""
        body = bytes([FRAME_GET_SCHEDULES])
        return FrameBuilder.create_frame(dest_addr, body)

    @staticmethod
    def set_schedule_raw(dest_addr=ADDR_ECOMAX, raw_data=b''):
        """Ramka 0x37 - ustaw harmonogramy (tryb raw payload)."""
        payload = bytes(raw_data or b'')
        body = bytes([FRAME_SET_SCHEDULE]) + payload
        return FrameBuilder.create_frame(dest_addr, body)
