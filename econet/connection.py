"""Polaczenie TCP z modulem Elfin EW11A.

EW11A udostepnia port RS485 jako serwer TCP (domyslnie port 8899).
Dane ze sterownika sa przesylane jako surowy strumien bajtow.
"""

import socket
import threading
import time
import logging
import datetime
import queue

from econet import alarms, protocol, decoder

logger = logging.getLogger(__name__)


def _format_bytes_hex(data, limit=256):
    """Zamien bajty na czytelny zapis hex z limitem dlugosci."""
    if not data:
        return ''

    display = data[:limit]
    hex_data = ' '.join(f'{byte:02X}' for byte in display)
    if len(data) > limit:
        return f"{hex_data} ... (+{len(data) - limit} B)"
    return hex_data


class EW11AConnection:
    """Zarzadza polaczeniem TCP z EW11A i parsowaniem danych."""

    RECV_BUFFER_SIZE = 4096
    MAX_BUFFER_SIZE = 100000
    RECONNECT_DELAY_INITIAL = 1
    RECONNECT_DELAY_MAX = 30

    def __init__(self, host, port, on_data_callback=None, read_interval=0,
                 preprocess_callback=None):
        self.host = host
        self.port = port
        self.on_data_callback = on_data_callback
        # Wywolywany przed opublikowaniem danych w last_data (np. korekty z ustawien),
        # aby wszystkie endpointy widzialy juz skorygowane wartosci.
        self.preprocess_callback = preprocess_callback
        self.read_interval = max(0.0, float(read_interval or 0))

        self._socket = None
        self._thread = None
        self._running = False
        self._connected = False
        self._buffer = bytearray()
        self._reconnect_delay = self.RECONNECT_DELAY_INITIAL
        self._last_data = None
        self._last_callback_time = 0.0
        self._lock = threading.Lock()
        self._socket_send_lock = threading.Lock()
        self._transaction_lock = threading.Lock()
        self._response_queues = {
            protocol.FRAME_SET_PARAM_ANS: queue.Queue(maxsize=20),
            protocol.FRAME_SET_MIXER_PARAM_ANS: queue.Queue(maxsize=20),
            protocol.FRAME_SET_THERMOSTAT_PARAM_ANS: queue.Queue(maxsize=20),
            protocol.FRAME_GET_PARAMS_ANS: queue.Queue(maxsize=20),
            protocol.FRAME_SCHEDULES_ANS: queue.Queue(maxsize=20),
            protocol.FRAME_UID_ANS: queue.Queue(maxsize=5),
            protocol.FRAME_ALARMS_ANS: queue.Queue(maxsize=20),
        }
        self._device_info: dict | None = None

        # Tabela wersji ramek z 0x35 i nasluchujacy zmian (patrz add_frame_version_listener)
        self._frame_versions: dict[int, int] = {}
        self._frame_version_listeners: list = []

        # Statystyki
        self.frames_received = 0
        self.bytes_received = 0
        self.last_frame_time = None
        self.errors = 0

        # Licznik kolejnych odrzuconych ramek (reset po pierwszej poprawnej).
        self._consecutive_bad = 0

    @property
    def connected(self):
        return self._connected

    @property
    def last_data(self):
        with self._lock:
            return self._last_data

    def start(self):
        """Uruchom watek polaczenia."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._connection_loop, daemon=True)
        self._thread.start()
        logger.info("Uruchomiono watek polaczenia z EW11A %s:%d", self.host, self.port)

    def stop(self):
        """Zatrzymaj polaczenie."""
        self._running = False
        self._disconnect()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Zatrzymano polaczenie z EW11A")

    def _connect(self):
        """Nawiaz polaczenie TCP."""
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(10)
            self._socket.connect((self.host, self.port))
            self._socket.settimeout(2)  # timeout na recv
            self._connected = True
            self._reconnect_delay = self.RECONNECT_DELAY_INITIAL
            self._buffer = bytearray()
            logger.info("Polaczono z EW11A %s:%d", self.host, self.port)
            return True
        except (socket.error, OSError) as e:
            logger.warning("Nie mozna polaczyc z EW11A %s:%d: %s",
                           self.host, self.port, e)
            self._connected = False
            if self._socket:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
            return False

    def _disconnect(self):
        """Rozlacz."""
        self._connected = False
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None

    def _connection_loop(self):
        """Glowna petla polaczenia z reconnectem."""
        while self._running:
            if not self._connected:
                if not self._connect():
                    time.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2,
                        self.RECONNECT_DELAY_MAX
                    )
                    continue

            try:
                data = self._socket.recv(self.RECV_BUFFER_SIZE)
                if not data:
                    logger.warning("EW11A zamknelo polaczenie")
                    self._disconnect()
                    continue

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "RAW recv: %d B | %s",
                        len(data),
                        _format_bytes_hex(data),
                    )

                with self._lock:
                    self.bytes_received += len(data)
                self._buffer.extend(data)

                # Ogranicz rozmiar bufora zaczynajac od poczatku ramki
                if len(self._buffer) > self.MAX_BUFFER_SIZE:
                    self._buffer = self._trim_buffer(self.MAX_BUFFER_SIZE)

                self._process_buffer()

            except socket.timeout:
                continue
            except (socket.error, OSError) as e:
                if not self._running:
                    self._disconnect()
                    break
                logger.warning("Blad odczytu z EW11A: %s", e)
                with self._lock:
                    self.errors += 1
                self._disconnect()

    def _process_buffer(self):
        """Przetworz bufor - szukaj ramek/paczek."""
        # Probuj najpierw pelny protokol EcoNET
        frames, remaining = protocol.find_frames(self._buffer)
        if frames:
            self._buffer = bytearray(remaining)
            for frame in frames:
                self._handle_econet_frame(frame)
            return

        # Probuj tryb PoC (surowe paczki 361-bajtowe)
        packets, remaining = decoder.find_poc_packets(self._buffer)
        if packets:
            self._buffer = bytearray(remaining)
            for packet in packets:
                self._handle_poc_packet(packet)
            return

        # Jesli bufor jest duzy ale nic nie znaleziono, przycnij
        if len(self._buffer) > 2000:
            self._buffer = self._buffer[-1000:]

    def _trim_buffer(self, max_size):
        """Przytnij bufor zachowujac poczatek ostatniej ramki.

        Szuka bajtu BEGIN_FRAME (0x68) w ostatnich max_size bajtach.
        Jesli nie znajdzie - zwraca ostatnie max_size//2 bajtow.
        """
        tail = self._buffer[-max_size:]
        begin = tail.find(0x68)
        if begin >= 0:
            return bytearray(tail[begin:])
        return bytearray(tail[max_size // 2:])

    def _handle_econet_frame(self, frame):
        """Obsluz ramke EcoNET."""
        with self._lock:
            self.frames_received += 1
            self.last_frame_time = time.time()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "RAW EcoNET frame: len=%d type=0x%02X src=0x%02X dst=0x%02X | %s",
                len(frame['raw']),
                frame['type'],
                frame['source'],
                frame['dest'],
                _format_bytes_hex(frame['raw']),
            )

        frame_type = frame['type']

        if frame_type in self._response_queues:
            response_queue = self._response_queues[frame_type]
            try:
                response_queue.put_nowait(frame)
            except queue.Full:
                try:
                    response_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    response_queue.put_nowait(frame)
                except queue.Full:
                    logger.debug("Kolejka odpowiedzi 0x%02X jest pelna - pomijam", frame_type)

        if frame_type == protocol.FRAME_DATA_ANS:
            boiler_data = decoder.decode_econet_data_frame(frame['payload'])
            if boiler_data:
                self._update_data(boiler_data)

        elif frame_type == protocol.FRAME_CHECK_DEVICE:
            # Sterownik pyta czy jestesmy - odpowiadamy B0
            if self._socket and self._connected:
                try:
                    response = protocol.FrameBuilder.device_available_response(
                        frame['source']
                    )
                    self._socket.sendall(response)
                except (socket.error, OSError) as e:
                    logger.warning("Nie mozna wyslac odpowiedzi B0: %s", e)

        elif frame_type == protocol.FRAME_TIME:
            # Ramki TIME ze sterownika sa ignorowane jako zrodlo czasu.
            # Aplikacja uzywa czasu serwera, ustawianego w _apply_server_time.
            logger.debug("Odebrano ramke TIME (0x89) - pomijam czas sterownika")

        else:
            logger.debug("Ramka typu 0x%02X (nieobslugiwana)", frame_type)

    def add_frame_version_listener(self, callback):
        """Zarejestruj callback(changes) wywolywany przy zmianie wersji ramek.

        `changes` to dict {typ_ramki: (stara_wersja | None, nowa_wersja)}; przy
        pierwszej obserwacji stara wersja to None. Callback jest wywolywany w watku
        odczytu z EW11A, wiec NIE moze blokowac ani wysylac zapytan synchronicznie
        (odpowiedz odbiera ten sam watek) - powinien jedynie zlecic prace.
        """
        self._frame_version_listeners.append(callback)

    @property
    def frame_versions(self) -> dict[int, int]:
        with self._lock:
            return dict(self._frame_versions)

    def _process_frame_versions(self, versions):
        if not versions:
            return
        with self._lock:
            changes = {
                frame_type: (self._frame_versions.get(frame_type), version)
                for frame_type, version in versions.items()
                if self._frame_versions.get(frame_type) != version
            }
            self._frame_versions.update(versions)
        if not changes:
            return
        logger.debug("Zmiana wersji ramek: %s", {f'0x{k:02X}': v for k, v in changes.items()})
        for listener in list(self._frame_version_listeners):
            try:
                listener(changes)
            except Exception as e:
                logger.error("Blad listenera wersji ramek: %s", e, exc_info=True)

    def _drain_response_queue(self, frame_type):
        response_queue = self._response_queues.get(frame_type)
        if not response_queue:
            return

        while True:
            try:
                response_queue.get_nowait()
            except queue.Empty:
                return

    def send_frame(self, frame_bytes):
        """Wyslij surowa ramke po aktualnym polaczeniu TCP."""
        if not self._connected or not self._socket:
            raise RuntimeError('Brak aktywnego polaczenia z EW11A')

        with self._socket_send_lock:
            self._socket.sendall(frame_bytes)

    def set_ecomax_parameter(self, index, value, timeout=2.0):
        """Ustaw pojedynczy parametr ecoMAX i czekaj na ACK 0xB3."""
        try:
            index = int(index)
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError('Nieprawidlowy index lub wartosc parametru') from exc

        if not (0 <= index <= 255 and 0 <= value <= 255):
            raise ValueError('Index i wartosc musza byc z zakresu 0-255')

        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_SET_PARAM_ANS)
            frame = protocol.FrameBuilder.set_ecomax_parameter(
                dest_addr=protocol.ADDR_ECOMAX,
                index=index,
                value=value,
            )
            self.send_frame(frame)

            response_queue = self._response_queues[protocol.FRAME_SET_PARAM_ANS]
            try:
                ack_frame = response_queue.get(timeout=max(0.1, float(timeout)))
            except queue.Empty as exc:
                raise TimeoutError('Brak potwierdzenia zapisu parametru (0xB3)') from exc

        return {
            'ack_type': ack_frame.get('type'),
            'ack_source': ack_frame.get('source'),
            'ack_dest': ack_frame.get('dest'),
        }

    def set_mixer_parameter(self, device_index, index, value, timeout=2.0):
        """Ustaw pojedynczy parametr mieszacza i czekaj na ACK 0xB4."""
        try:
            device_index = int(device_index)
            index = int(index)
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError('Nieprawidlowy device_index/index/value') from exc

        if not (0 <= device_index <= 255 and 0 <= index <= 255 and 0 <= value <= 255):
            raise ValueError('device_index, index i value musza byc z zakresu 0-255')

        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_SET_MIXER_PARAM_ANS)
            frame = protocol.FrameBuilder.set_mixer_parameter(
                dest_addr=protocol.ADDR_ECOMAX,
                device_index=device_index,
                index=index,
                value=value,
            )
            self.send_frame(frame)

            response_queue = self._response_queues[protocol.FRAME_SET_MIXER_PARAM_ANS]
            try:
                ack_frame = response_queue.get(timeout=max(0.1, float(timeout)))
            except queue.Empty as exc:
                raise TimeoutError('Brak potwierdzenia zapisu parametru mieszacza (0xB4)') from exc

        return {
            'ack_type': ack_frame.get('type'),
            'ack_source': ack_frame.get('source'),
            'ack_dest': ack_frame.get('dest'),
        }

    def set_thermostat_parameter(self, index, value, offset=0, size=1, timeout=2.0):
        """Ustaw parametr termostatu i czekaj na ACK 0xDD."""
        try:
            index = int(index)
            value = int(value)
            offset = int(offset)
            size = int(size)
        except (TypeError, ValueError) as exc:
            raise ValueError('Nieprawidlowe index/value/offset/size') from exc

        if not (0 <= index <= 255 and 0 <= offset <= 255):
            raise ValueError('index i offset musza byc z zakresu 0-255')
        if not (1 <= size <= 4):
            raise ValueError('size musi byc z zakresu 1-4')
        max_value = (1 << (8 * size)) - 1
        if not (0 <= value <= max_value):
            raise ValueError(f'value poza zakresem dla size={size}: 0-{max_value}')

        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_SET_THERMOSTAT_PARAM_ANS)
            frame = protocol.FrameBuilder.set_thermostat_parameter(
                dest_addr=protocol.ADDR_ECOMAX,
                index=index,
                value=value,
                offset=offset,
                size=size,
            )
            self.send_frame(frame)

            response_queue = self._response_queues[protocol.FRAME_SET_THERMOSTAT_PARAM_ANS]
            try:
                ack_frame = response_queue.get(timeout=max(0.1, float(timeout)))
            except queue.Empty as exc:
                raise TimeoutError('Brak potwierdzenia zapisu parametru termostatu (0xDD)') from exc

        return {
            'ack_type': ack_frame.get('type'),
            'ack_source': ack_frame.get('source'),
            'ack_dest': ack_frame.get('dest'),
        }

    def set_parameter(self, target_type, index, value, *, target_index=0, offset=0, size=1, timeout=2.0):
        """Uniwersalny zapis parametru dla target_type: ecomax|mixer|thermostat."""
        target = str(target_type or '').strip().lower()
        if target == 'ecomax':
            return self.set_ecomax_parameter(index=index, value=value, timeout=timeout)
        if target == 'mixer':
            return self.set_mixer_parameter(
                device_index=target_index,
                index=index,
                value=value,
                timeout=timeout,
            )
        if target == 'thermostat':
            return self.set_thermostat_parameter(
                index=index,
                value=value,
                offset=offset,
                size=size,
                timeout=timeout,
            )
        raise ValueError('Nieobslugiwany target_type (dozwolone: ecomax, mixer, thermostat)')

    @staticmethod
    def _parse_uid_payload(payload: bytes) -> dict:
        """Parsuj payload ramki 0xB9 (UID/model sterownika).

        Format (zgodny z PyPlumIO ProductInfoStructure):
        [product_type:1][product_id:2 LE][uid_len:1][uid:uid_len]
        [logo:2 LE][image:2 LE][model_len:1][model:model_len ASCII]
        """
        if not payload or len(payload) < 4:
            return {}

        idx = 0
        product_type_raw = payload[idx]
        idx += 1
        product_type = 'ECOMAX_P' if product_type_raw == 0 else 'ECOMAX_I'

        if idx + 2 > len(payload):
            return {'product_type': product_type}
        product_id = payload[idx] | (payload[idx + 1] << 8)
        idx += 2

        if idx >= len(payload):
            return {'product_type': product_type, 'product_id': product_id}
        uid_len = payload[idx]
        idx += 1

        uid_str = ''
        if uid_len > 0 and idx + uid_len <= len(payload):
            uid_bytes = payload[idx:idx + uid_len]
            # UID jest kodowany w base5 w PyPlumIO - tutaj zwracamy hex
            uid_str = uid_bytes.hex()
            idx += uid_len

        # logo(2) + image(2)
        idx += 4

        model = ''
        if idx < len(payload):
            model_len = payload[idx]
            idx += 1
            if model_len > 0 and idx + model_len <= len(payload):
                raw_model = payload[idx:idx + model_len].decode('ascii', errors='replace').strip('\x00')
                # Firmware zwraca prefix "EM" zamiast "ecoMAX"
                model = raw_model.replace('EM', 'ecoMAX', 1) if raw_model.startswith('EM') else raw_model

        return {
            'product_type': product_type,
            'product_id': product_id,
            'uid': uid_str,
            'model': model,
        }

    def request_uid(self, timeout=3.0) -> dict:
        """Pobierz UID i model sterownika przez ramkę 0x39/0xB9."""
        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_UID_ANS)
            frame = protocol.FrameBuilder.request_uid(dest_addr=protocol.ADDR_ECOMAX)
            self.send_frame(frame)

            response_queue = self._response_queues[protocol.FRAME_UID_ANS]
            try:
                response_frame = response_queue.get(timeout=max(0.5, float(timeout)))
            except queue.Empty as exc:
                raise TimeoutError('Brak odpowiedzi na zapytanie UID (0xB9)') from exc

        payload = bytes(response_frame.get('payload') or b'')
        info = self._parse_uid_payload(payload)
        if info.get('model'):  # nie cachuj niepełnych wyników
            self._device_info = info
        return info

    def request_alerts(self, start=0, count=alarms.ALERTS_PAGE_SIZE, timeout=3.0) -> dict:
        """Pobierz strone historii alarmow (0x3D -> 0xBD).

        Odpowiedz jest dopasowywana po polu `start`, bo na magistrali moga pojawic sie
        ramki 0xBD bedace odpowiedzia na zapytania innych urzadzen.
        """
        start = int(start)
        count = int(count)
        if not (0 <= start <= 255 and 1 <= count <= 255):
            raise ValueError('start/count poza zakresem 0..255')

        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_ALARMS_ANS)
            self.send_frame(protocol.FrameBuilder.request_alerts(
                dest_addr=protocol.ADDR_ECOMAX, start=start, count=count))

            response_queue = self._response_queues[protocol.FRAME_ALARMS_ANS]
            deadline = time.time() + max(0.5, float(timeout))
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError('Brak odpowiedzi na zapytanie alarmow (0xBD)')
                try:
                    frame = response_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError('Brak odpowiedzi na zapytanie alarmow (0xBD)') from exc
                parsed = alarms.decode_alerts_payload(bytes(frame.get('payload') or b''))
                if parsed is not None and parsed['start'] == start:
                    return parsed

    def get_device_info_cached(self) -> dict | None:
        """Zwróć ostatnio odczytany device info (lub None jeśli nie pobrano)."""
        return self._device_info

    def request_schedules_raw(self, timeout=2.0):
        """Pobierz surowy payload harmonogramów (odpowiedź 0xB6)."""
        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_SCHEDULES_ANS)
            frame = protocol.FrameBuilder.request_schedules(dest_addr=protocol.ADDR_ECOMAX)
            self.send_frame(frame)

            response_queue = self._response_queues[protocol.FRAME_SCHEDULES_ANS]
            try:
                response_frame = response_queue.get(timeout=max(0.1, float(timeout)))
            except queue.Empty as exc:
                raise TimeoutError('Brak odpowiedzi harmonogramów (0xB6)') from exc

        payload = bytes(response_frame.get('payload') or b'')
        return {
            'payload': payload,
            'payload_hex': payload.hex(),
            'frame': response_frame,
        }

    def set_schedule_raw(self, payload_bytes):
        """Wyślij surowy payload harmonogramu przez ramkę 0x37."""
        frame = protocol.FrameBuilder.set_schedule_raw(
            dest_addr=protocol.ADDR_ECOMAX,
            raw_data=payload_bytes,
        )
        self.send_frame(frame)
        return {'sent': True, 'size': len(payload_bytes or b'')}

    @staticmethod
    def _parse_ecomax_parameters_payload(payload):
        """Parsuj payload 0xB1 do mapy index -> {value, min_value, max_value}."""
        if not payload or len(payload) < 3:
            return {}

        start = int(payload[1])
        count = int(payload[2])
        offset = 3
        result = {}

        for index in range(start, start + count):
            if offset + 2 >= len(payload):
                break

            value = int(payload[offset])
            min_value = int(payload[offset + 1])
            max_value = int(payload[offset + 2])
            # 0xFF/0xFF/0xFF oznacza parametr niezdefiniowany w firmware kontrolera
            if min_value == 0xFF and max_value == 0xFF:
                result[index] = {'available': False, 'value': None, 'min_value': None, 'max_value': None}
            else:
                result[index] = {
                    'available': True,
                    'value': value if value != 0xFF else None,
                    'min_value': min_value,
                    'max_value': max_value,
                }
            offset += 3

        return result

    def request_ecomax_parameters(self, start=0, count=255, timeout=2.0):
        """Pobierz tabelę parametrów ecoMAX przez 0x31/0xB1."""
        try:
            start = int(start)
            count = int(count)
        except (TypeError, ValueError) as exc:
            raise ValueError('Nieprawidlowy zakres zapytania o parametry') from exc

        if not (0 <= start <= 255 and 1 <= count <= 255):
            raise ValueError('Zakres start/count musi byc w przedziale 0..255')

        with self._transaction_lock:
            self._drain_response_queue(protocol.FRAME_GET_PARAMS_ANS)
            frame = protocol.FrameBuilder.request_ecomax_parameters(
                dest_addr=protocol.ADDR_ECOMAX,
                start=start,
                count=count,
            )
            self.send_frame(frame)

            response_queue = self._response_queues[protocol.FRAME_GET_PARAMS_ANS]
            try:
                response_frame = response_queue.get(timeout=max(0.1, float(timeout)))
            except queue.Empty as exc:
                raise TimeoutError('Brak odpowiedzi na zapytanie parametrów (0xB1)') from exc

        payload = response_frame.get('payload') or b''
        parsed = self._parse_ecomax_parameters_payload(payload)
        return {
            'frame': response_frame,
            'parameters': parsed,
        }

    def _handle_poc_packet(self, packet):
        """Obsluz paczke PoC."""
        with self._lock:
            self.frames_received += 1
            self.last_frame_time = time.time()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "RAW PoC packet: len=%d | %s",
                len(packet),
                _format_bytes_hex(packet),
            )

        boiler_data = decoder.decode_poc_packet(packet)
        if boiler_data:
            self._update_data(boiler_data)

    # Ile kolejnych złych ramek zanim pojawi się WARNING w logach.
    _BAD_FRAMES_WARN_THRESHOLD = 10

    @staticmethod
    def _apply_server_time(boiler_data, epoch_now=None):
        if epoch_now is None:
            epoch_now = time.time()

        dt = datetime.datetime.fromtimestamp(epoch_now)
        boiler_data.timestamp = epoch_now
        boiler_data.controller_time_unix = epoch_now
        boiler_data.controller_time_iso = dt.strftime('%Y-%m-%d %H:%M:%S')
        boiler_data.controller_time_year = dt.year
        boiler_data.controller_time_month = dt.month
        boiler_data.controller_time_day = dt.day
        boiler_data.controller_time_hour = dt.hour
        boiler_data.controller_time_minute = dt.minute
        boiler_data.controller_time_second = dt.second

    def _update_data(self, boiler_data):
        """Zaktualizuj ostatnie dane i powiadom callback."""
        if not boiler_data.is_plausible():
            with self._lock:
                self.errors += 1
                self._consecutive_bad += 1
                bad = self._consecutive_bad
            if bad == self._BAD_FRAMES_WARN_THRESHOLD:
                logger.warning(
                    "RS485: %d kolejnych blednych/niekompletnych ramek z rzędu "
                    "(łącznie odrzuconych: %d) – mozliwe zaklocenia na linii.",
                    bad, self.errors,
                )
            else:
                logger.debug(
                    "Odrzucono ramke z nieplauzybilnymi danymi (%d z rzędu).", bad
                )
            return

        # Wersje ramek tylko z ramek wiarygodnych (szum na RS485 nie wyzwala synchronizacji).
        self._process_frame_versions(boiler_data.frame_versions)

        # Ramka poprawna – reset licznika i ewentualne INFO o odbudowie
        with self._lock:
            bad_before = self._consecutive_bad
            self._consecutive_bad = 0
        if bad_before >= self._BAD_FRAMES_WARN_THRESHOLD:
            logger.info(
                "RS485: odebrano poprawna ramke po %d blednych – polaczenie odbudowane.",
                bad_before,
            )

        now = time.time()
        self._apply_server_time(boiler_data, now)

        if self.read_interval > 0 and (now - self._last_callback_time) < self.read_interval:
            return

        if self.preprocess_callback:
            try:
                self.preprocess_callback(boiler_data)
            except Exception as e:
                logger.error("Blad w preprocess_callback: %s", e, exc_info=True)

        with self._lock:
            self._last_data = boiler_data
            self._last_callback_time = now
        if self.on_data_callback:
            try:
                self.on_data_callback(boiler_data)
            except Exception as e:
                logger.error("Blad w callback: %s", e, exc_info=True)

    def get_status(self):
        """Zwroc status polaczenia."""
        return {
            'connected': self._connected,
            'host': self.host,
            'port': self.port,
            'frames_received': self.frames_received,
            'bytes_received': self.bytes_received,
            'last_frame_time': self.last_frame_time,
            'errors': self.errors,
            'controller_time_iso': self._last_data.controller_time_iso if self._last_data else None,
        }
