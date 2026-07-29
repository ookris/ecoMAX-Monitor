"""REST API i routing."""

import csv
import datetime
import hmac
import ipaddress
import io
import json
import logging
import math
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from typing import Optional
from zoneinfo import available_timezones

from flask import Blueprint, jsonify, render_template, request, Response, send_file

from logger import read_log_lines, get_log_file_path
from econet.decoder import FUEL_STREAM_MAX
from econet.boiler_control import PARAMETERS, PARAMETERS_BY_KEY, format_parameter_value
from sensors import DASHBOARD_TILE_DEFINITIONS, DASHBOARD_TILE_KEYS

bp = Blueprint('main', __name__)

try:
    from eventlet.green.urllib import request as green_urllib_request
    _urlopen = green_urllib_request.urlopen
except Exception:  # pragma: no cover - fallback when eventlet green urllib unavailable
    _urlopen = urllib.request.urlopen

try:
    import eventlet.tpool as _eventlet_tpool
except Exception:  # pragma: no cover - fallback when eventlet is unavailable
    _eventlet_tpool = None

_WEATHER_DATA_TTL_SEC = 300
_WEATHER_PLACE_TTL_SEC = 86400
_WEATHER_MIGRATION_RETRY_TTL_SEC = 900
_ALERTS_MAX_API_LIMIT = 100000
_weather_cache_lock = threading.Lock()
_weather_data_cache = {}
_weather_place_cache = {}
_weather_legacy_migration_fail_cache = {}
_live_fallback_cache_lock = threading.Lock()
_live_fallback_cache = {'cached_at': 0.0, 'data': None}


PARAMETERS_METADATA = [
    # ----- Czas serwera -----
    {'key': 'controller_time_iso',    'label': 'Czas serwera',                    'unit': None,   'group': 'Czas serwera'},
    {'key': 'controller_time_unix',   'label': 'Czas serwera (Unix)',             'unit': 'unix', 'group': 'Czas serwera'},
    {'key': 'controller_time_year',   'label': 'Rok serwera',                     'unit': None,   'group': 'Czas serwera'},
    {'key': 'controller_time_month',  'label': 'Miesiąc serwera',                 'unit': None,   'group': 'Czas serwera'},
    {'key': 'controller_time_day',    'label': 'Dzień serwera',                   'unit': None,   'group': 'Czas serwera'},
    {'key': 'controller_time_hour',   'label': 'Godzina serwera',                 'unit': None,   'group': 'Czas serwera'},
    {'key': 'controller_time_minute', 'label': 'Minuta serwera',                  'unit': None,   'group': 'Czas serwera'},
    {'key': 'controller_time_second', 'label': 'Sekunda serwera',                 'unit': None,   'group': 'Czas serwera'},

    # ----- Tryb pracy -----
    {'key': 'timestamp',         'label': 'Znacznik czasu',                    'unit': 'unix', 'group': 'Tryb pracy'},
    {'key': 'mode',              'label': 'Tryb pracy (kod)',                   'unit': None,   'group': 'Tryb pracy'},
    {'key': 'mode_name',         'label': 'Tryb pracy',                         'unit': None,   'group': 'Tryb pracy'},
    # ----- Temperatury -----
    {'key': 'temp_co',           'label': 'Temperatura CO (zasilanie)',          'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_feeder',       'label': 'Temperatura podajnika',               'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_cwu',          'label': 'Temperatura CWU',                     'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_external',     'label': 'Temperatura zewnętrzna',              'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_back',         'label': 'Temperatura powrotu',                 'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_flue_gas',     'label': 'Temperatura spalin',                  'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_optical',      'label': 'Temperatura optyczna płomienia',      'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_upper_buffer', 'label': 'Temperatura bufora górnego',          'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_lower_buffer', 'label': 'Temperatura bufora dolnego',          'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_upper_solar',  'label': 'Temperatura solarna górna',           'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_lower_solar',  'label': 'Temperatura solarna dolna',           'unit': '°C',  'group': 'Temperatury'},
    {'key': 'temp_fireplace',    'label': 'Temperatura kominka',                 'unit': '°C',  'group': 'Temperatury'},
    {'key': 'flame',             'label': 'Sygnał optyczny płomienia',           'unit': '°C',  'group': 'Temperatury'},
    {'key': 'total_gain',        'label': 'Zysk całkowity',                      'unit': None,   'group': 'Temperatury'},
    # ----- Temperatury zadane -----
    {'key': 'temp_co_set',       'label': 'Temperatura zadana CO',               'unit': '°C',  'group': 'Temperatury zadane'},
    {'key': 'temp_cwu_set',      'label': 'Temperatura zadana CWU',              'unit': '°C',  'group': 'Temperatury zadane'},
    # ----- Moc i paliwo -----
    {'key': 'fan_power',         'label': 'Moc wentylatora',                     'unit': '%',   'group': 'Moc i paliwo'},
    {'key': 'blower',            'label': 'Moc dmuchawy (raw)',                  'unit': '%',   'group': 'Moc i paliwo'},
    {'key': 'boiler_power',      'label': 'Moc kotła (%)',                       'unit': '%',   'group': 'Moc i paliwo'},
    {'key': 'boiler_power_kw',   'label': 'Moc kotła (kW)',                      'unit': 'kW',  'group': 'Moc i paliwo'},
    {'key': 'fuel_level',        'label': 'Poziom paliwa',                       'unit': '%',   'group': 'Moc i paliwo'},
    {'key': 'fuel_stream',       'label': 'Strumień paliwa',                     'unit': 'kg/h','group': 'Moc i paliwo'},
    # ----- Pompy i aktory -----
    {'key': 'pump_co',           'label': 'Pompa CO',                            'unit': 'bool','group': 'Pompy i aktory'},
    {'key': 'pump_cwu',          'label': 'Pompa CWU',                           'unit': 'bool','group': 'Pompy i aktory'},
    {'key': 'pump_circulation',  'label': 'Pompa cyrkulacji',                    'unit': 'bool','group': 'Pompy i aktory'},
    # ----- Statystyki -----
    {'key': 'fuel_burned_kg',    'label': 'Spalone paliwo',                      'unit': 'kg',  'group': 'Statystyki'},
    {'key': 'ignitions',         'label': 'Liczba rozpaleń',                     'unit': None,   'group': 'Statystyki'},
    {'key': 'work_time_100',     'label': 'Czas pracy 100%',                     'unit': 'h',   'group': 'Statystyki'},
    {'key': 'work_time_50',      'label': 'Czas pracy 50%',                      'unit': 'h',   'group': 'Statystyki'},
    {'key': 'work_time_33',      'label': 'Czas pracy 33%',                      'unit': 'h',   'group': 'Statystyki'},
    {'key': 'feeder_work_time',  'label': 'Czas pracy podajnika',                'unit': 'h',   'group': 'Statystyki'},
]


def _default_parameters_data():
    return {item['key']: None for item in PARAMETERS_METADATA}


def _fetch_json(url: str, timeout: int = 8, headers: Optional[dict] = None):
    req = urllib.request.Request(url, headers=headers or {})
    with _urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _parse_float_maybe_comma(value):
    normalized = str(value).strip().replace(' ', '').replace(',', '.')
    return float(normalized)


def _normalize_mode_name(value):
    text = str(value or '').strip()
    if not text:
        return value
    normalized = text.lower()
    if normalized == 'pauza':
        return 'Postój'

    legacy_map = {
        'wylaczony': 'Wyłączony',
        'nadzor': 'Nadzór',
        'reczny': 'Ręczny',
    }
    return legacy_map.get(normalized, text)


def _normalize_mode_name_in_row(row):
    if not isinstance(row, dict):
        return row
    if 'mode_name' in row:
        row['mode_name'] = _normalize_mode_name(row.get('mode_name'))
    return row


def _merge_missing_with_latest(
    data: dict,
    latest: Optional[dict],
    max_age_sec: int = 900,
    zero_fallback_keys: Optional[set[str]] = None,
) -> dict:
    if not isinstance(data, dict) or not isinstance(latest, dict):
        return data

    live_ts = data.get('timestamp')
    latest_ts = latest.get('timestamp')
    if isinstance(live_ts, (int, float)) and isinstance(latest_ts, (int, float)):
        if abs(live_ts - latest_ts) > max_age_sec:
            return data

    merged = dict(data)
    zero_fallback_keys = zero_fallback_keys or set()

    for key, value in latest.items():
        if key == 'id':
            continue
        current = merged.get(key)
        if current is None and value is not None:
            merged[key] = value
        elif key in zero_fallback_keys and current in (0, 0.0) and value not in (None, 0, 0.0):
            merged[key] = value

    return merged


def _looks_incomplete_live_row(data: Optional[dict]) -> bool:
    if not isinstance(data, dict):
        return True

    mode = data.get('mode')
    if mode in (2, 3):
        temp_co_set = data.get('temp_co_set')
        temp_cwu_set = data.get('temp_cwu_set')
        suspicious_setpoints = temp_co_set in (None, 0, 0.0) or temp_cwu_set in (None, 0, 0.0, 8, 8.0)
        if suspicious_setpoints:
            return True

    populated_count = sum(
        1 for k in ('temp_co', 'temp_back', 'temp_cwu', 'temp_flue_gas', 'fan_power', 'fuel_level')
        if data.get(k) not in (None, 0, 0.0)
    )
    return populated_count < 2


def _get_recent_valid_measurement() -> Optional[dict]:
    if not _database:
        return None

    now = time.time()
    with _live_fallback_cache_lock:
        cached = _live_fallback_cache.get('data')
        cached_at = _live_fallback_cache.get('cached_at', 0.0)
        if cached and (now - cached_at) <= 30:
            return dict(cached)

    rows = _database.get_history_desc(hours=2, limit=200)
    candidate = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        temp_co = row.get('temp_co')
        temp_co_set = row.get('temp_co_set')
        temp_cwu_set = row.get('temp_cwu_set')
        if temp_co is None or temp_co <= 5:
            continue
        if temp_co_set in (None, 0, 0.0) or temp_cwu_set in (None, 0, 0.0, 8, 8.0):
            continue
        candidate = row
        break

    with _live_fallback_cache_lock:
        _live_fallback_cache['cached_at'] = now
        _live_fallback_cache['data'] = dict(candidate) if candidate else None

    return dict(candidate) if candidate else None


def _get_cached_weather(location_key: str):
    entry = _weather_data_cache.get(location_key)
    if not entry:
        return None
    if time.time() - entry['cached_at'] > _WEATHER_DATA_TTL_SEC:
        _weather_data_cache.pop(location_key, None)
        return None
    return entry['data']


def _get_cached_place(location_key: str):
    entry = _weather_place_cache.get(location_key)
    if not entry:
        return None
    if time.time() - entry['cached_at'] > _WEATHER_PLACE_TTL_SEC:
        _weather_place_cache.pop(location_key, None)
        return None
    return entry['data']


def _resolve_place_data(lat: float, lon: float, location_key: str):
    with _weather_cache_lock:
        cached_place = _get_cached_place(location_key)
    if cached_place is not None:
        return cached_place

    query = urllib.parse.urlencode({
        'format': 'jsonv2',
        'lat': f'{lat:.6f}',
        'lon': f'{lon:.6f}',
        'zoom': 12,
        'addressdetails': 1,
        'accept-language': 'pl',
    })
    reverse_url = f'https://nominatim.openstreetmap.org/reverse?{query}'

    try:
        data = _fetch_json(
            reverse_url,
            timeout=8,
            headers={
                'User-Agent': 'ecoMAX-Monitor/1.0 (+self-hosted)',
                'Accept': 'application/json',
            }
        )
        address = data.get('address') or {}

        locality = (
            address.get('city')
            or address.get('town')
            or address.get('village')
            or address.get('hamlet')
            or data.get('name')
            or ''
        )
        municipality = (
            address.get('municipality')
            or address.get('city_district')
            or address.get('county')
            or ''
        )
        region = address.get('state') or ''
        country = address.get('country') or ''

        label_parts = [part for part in (locality, municipality, region) if part]
        location_label = ', '.join(label_parts) if label_parts else f'{lat:.5f}, {lon:.5f}'

        place_data = {
            'locality': locality,
            'municipality': municipality,
            'region': region,
            'country': country,
            'location_label': location_label,
        }
    except Exception as exc:
        logging.getLogger(__name__).info('Reverse geocoding niedostępny: %s', exc)
        place_data = {
            'locality': '',
            'municipality': '',
            'region': '',
            'country': '',
            'location_label': f'{lat:.5f}, {lon:.5f}',
        }

    with _weather_cache_lock:
        _weather_place_cache[location_key] = {
            'data': place_data,
            'cached_at': time.time(),
        }

    return place_data


def _resolve_coords_from_legacy_location(location: str):
    location_name = (location or '').strip()
    if not location_name:
        return None

    query = urllib.parse.urlencode({
        'name': location_name,
        'count': 1,
        'language': 'pl',
        'format': 'json',
    })
    geocode_url = f'https://geocoding-api.open-meteo.com/v1/search?{query}'

    data = _fetch_json(geocode_url, timeout=8)
    results = data.get('results') or []
    if not results:
        return None

    first = results[0] or {}
    lat = first.get('latitude')
    lon = first.get('longitude')
    if lat is None or lon is None:
        return None

    return float(lat), float(lon)


def _get_legacy_migration_retry_after_sec(location_name: str):
    with _weather_cache_lock:
        entry = _weather_legacy_migration_fail_cache.get(location_name)
        if not entry:
            return None
        elapsed = time.time() - entry.get('failed_at', 0)
        if elapsed >= _WEATHER_MIGRATION_RETRY_TTL_SEC:
            _weather_legacy_migration_fail_cache.pop(location_name, None)
            return None
        return int(_WEATHER_MIGRATION_RETRY_TTL_SEC - elapsed)


def _mark_legacy_migration_failed(location_name: str, reason: str):
    with _weather_cache_lock:
        _weather_legacy_migration_fail_cache[location_name] = {
            'failed_at': time.time(),
            'reason': reason,
        }

# Globalne referencje - ustawiane w run.py
_routes_lock = threading.Lock()
_connection = None
_database = None
_boiler_limits_cache_lock = threading.Lock()
_boiler_limits_cache = {
    'cached_at': 0.0,
    'parameters': {},
}
_BOILER_LIMITS_TTL_SEC = 5.0


def _is_local_or_private(remote_addr: Optional[str]) -> bool:
    if not remote_addr:
        return False
    try:
        ip = ipaddress.ip_address(remote_addr)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def _is_logs_access_allowed() -> bool:
    """Dostęp do logów: tylko przy włączonym logowaniu i z autoryzacją/tokenem/LAN."""
    if not _database:
        return False

    if _database.get_config('log_enabled', '1') != '1':
        return False

    token_expected = os.environ.get('LOGS_ACCESS_TOKEN', '').strip()
    token_provided = request.headers.get('X-Logs-Token', '').strip()
    if not token_provided:
        token_provided = request.args.get('token', '').strip()

    if token_expected:
        return hmac.compare_digest(token_provided, token_expected)

    return _is_local_or_private(request.remote_addr)


def _is_time_access_allowed() -> bool:
    """Dostęp do operacji czasu: token albo sieć lokalna."""
    token_expected = os.environ.get('TIME_ACCESS_TOKEN', '').strip()
    token_provided = request.headers.get('X-Time-Token', '').strip()
    if not token_provided:
        token_provided = request.args.get('token', '').strip()

    if token_expected:
        return hmac.compare_digest(token_provided, token_expected)

    return _is_local_or_private(request.remote_addr)


def _is_reset_access_allowed() -> bool:
    """Dostęp do resetu danych: token albo LAN + nagłówek wewnętrzny."""
    token_expected = os.environ.get('RESET_ACCESS_TOKEN', '').strip()
    token_provided = request.headers.get('X-Reset-Token', '').strip()

    if token_expected:
        return hmac.compare_digest(token_provided, token_expected)

    return (
        _is_local_or_private(request.remote_addr)
        and request.headers.get('X-Requested-With', '') == 'ecoMAX-Monitor'
    )


def _is_boiler_control_access_allowed() -> bool:
    """Dostęp do zmiany ustawień kotła: token albo LAN + nagłówek wewnętrzny."""
    token_expected = os.environ.get('BOILER_CONTROL_ACCESS_TOKEN', '').strip()
    token_provided = request.headers.get('X-Boiler-Control-Token', '').strip()

    if token_expected:
        return hmac.compare_digest(token_provided, token_expected)

    return (
        _is_local_or_private(request.remote_addr)
        and request.headers.get('X-Requested-With', '') == 'ecoMAX-Monitor'
    )


def _is_boiler_service_access_allowed() -> bool:
    """Dostęp do profilu serwisowego: wyłącznie poprawny token serwisowy."""
    token_expected = os.environ.get('BOILER_SERVICE_ACCESS_TOKEN', '').strip()
    token_provided = request.headers.get('X-Boiler-Service-Token', '').strip()

    if not token_expected:
        return False

    return bool(token_provided) and hmac.compare_digest(token_provided, token_expected)


def _resolve_request_source() -> str:
    """Zwraca faktyczne źródło autoryzacji: token, lan lub unknown."""
    token_expected = os.environ.get('BOILER_CONTROL_ACCESS_TOKEN', '').strip()
    token_provided = request.headers.get('X-Boiler-Control-Token', '').strip()

    if token_expected and token_provided and hmac.compare_digest(token_provided, token_expected):
        return 'token'

    if (
        not token_expected
        and _is_local_or_private(request.remote_addr)
        and request.headers.get('X-Requested-With', '') == 'ecoMAX-Monitor'
    ):
        return 'lan'

    return 'unknown'


def _execute_blocking(callable_obj, *args, **kwargs):
    """Uruchom blokujące I/O w tpool przy eventlet, aby nie blokować requestów."""
    if _eventlet_tpool is None:
        return callable_obj(*args, **kwargs)
    return _eventlet_tpool.execute(lambda: callable_obj(*args, **kwargs))


def _get_ecomax_limits_cached(force_refresh: bool = False) -> dict:
    if not (_connection and getattr(_connection, 'connected', False)):
        return {}

    now = time.time()
    with _boiler_limits_cache_lock:
        if not force_refresh and (now - _boiler_limits_cache['cached_at']) <= _BOILER_LIMITS_TTL_SEC:
            return dict(_boiler_limits_cache['parameters'])

    try:
        ctrl_response = _execute_blocking(
            _connection.request_ecomax_parameters,
            start=0,
            count=200,
            timeout=1.5,
        )
        parameters = ctrl_response.get('parameters') or {}
    except Exception:
        parameters = {}

    if parameters:
        with _boiler_limits_cache_lock:
            _boiler_limits_cache['cached_at'] = now
            _boiler_limits_cache['parameters'] = dict(parameters)

    return dict(parameters)


def _resolve_boiler_write_request(item_payload: dict, *, use_limits_cache: bool = True):
    """Ujednolicona walidacja zapisu parametru dla set-parameter i set-batch."""
    target_type = str(item_payload.get('target_type', 'ecomax') or 'ecomax').strip().lower()
    target_index = item_payload.get('target_index', 0)
    offset = item_payload.get('offset', 0)
    size = item_payload.get('size', 1)

    key = str(item_payload.get('key', '') or '').strip().upper()
    value_raw = item_payload.get('value', None)

    if target_type == 'ecomax':
        param = PARAMETERS_BY_KEY.get(key)
        if not param:
            raise ValueError('Nieznany parametr sterowania')
        index_value = param.index
    elif target_type in ('mixer', 'thermostat'):
        param = None
        index_raw = item_payload.get('index', None)
        try:
            index_value = int(index_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError('Dla target_type mixer/thermostat wymagane jest pole index') from exc
    else:
        raise ValueError('Nieobsługiwany target_type (dozwolone: ecomax, mixer, thermostat)')

    try:
        value = int(value_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Wartość parametru musi być liczbą całkowitą') from exc

    min_value = param.min_value if param else 0
    max_value = param.max_value if param else 255
    old_value = None

    if target_type == 'ecomax':
        controller_limits = {}
        if use_limits_cache:
            controller_limits = _get_ecomax_limits_cached(force_refresh=False)

        ctrl_limits = controller_limits.get(index_value)
        if not ctrl_limits:
            try:
                response = _execute_blocking(
                    _connection.request_ecomax_parameters,
                    start=index_value,
                    count=1,
                    timeout=1.0,
                )
                ctrl_limits = (response.get('parameters') or {}).get(index_value)
            except Exception:
                ctrl_limits = None

        if ctrl_limits:
            min_value = int(ctrl_limits.get('min_value', min_value))
            max_value = int(ctrl_limits.get('max_value', max_value))
            old_value = str(ctrl_limits.get('value'))

    if value < min_value or value > max_value:
        raise ValueError(f'Wartość poza zakresem {min_value}-{max_value}')

    return {
        'target_type': target_type,
        'target_index': int(target_index) if target_index is not None else None,
        'offset': int(offset),
        'size': int(size),
        'key': key,
        'param': param,
        'index_value': int(index_value),
        'value': int(value),
        'old_value': old_value,
        'min_value': int(min_value),
        'max_value': int(max_value),
    }


def _log_boiler_change(
    *,
    request_id: str,
    source: str,
    target_type: str,
    target_index: Optional[int],
    param_key: str,
    param_index: int,
    old_value: Optional[str],
    new_value: Optional[str],
    status: str,
    message: Optional[str],
) -> None:
    if not _database:
        return
    _database.log_boiler_setting_change(
        source=source,
        target_type=target_type,
        target_index=target_index,
        param_key=param_key,
        param_index=param_index,
        old_value=old_value,
        new_value=new_value,
        status=status,
        message=message,
        request_id=request_id,
    )


def _parse_hex_payload(value: str) -> bytes:
    text = str(value or '').strip().replace(' ', '').replace('\n', '').replace('\r', '')
    if text.startswith('0x'):
        text = text[2:]
    if not text:
        return b''
    if len(text) % 2 != 0:
        raise ValueError('Hex payload musi mieć parzystą liczbę znaków')
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError('Nieprawidłowy zapis hex payload') from exc


SCHEDULE_SIZE = 42  # 6 bajtow na dzien, 7 dni
SLOTS_PER_DAY = 48
SCHEDULE_DAY_NAMES = ('pon', 'wt', 'sr', 'czw', 'pt', 'sob', 'nd')
SCHEDULE_TYPES = (
    'heating',
    'water_heater',
    'circulation_pump',
    'boiler_work',
    'boiler_clean',
    'heat_exchanger_clean',
    'mixer_1',
    'mixer_2',
    'mixer_3',
    'mixer_4',
    'mixer_5',
    'mixer_6',
    'mixer_7',
    'mixer_8',
    'mixer_9',
    'mixer_10',
    'thermostat_1',
    'thermostat_2',
    'thermostat_3',
    'circuit_1',
    'circuit_2',
    'circuit_3',
    'circuit_4',
    'circuit_5',
    'circuit_6',
    'circuit_7',
    'panel_1',
    'panel_2',
    'panel_3',
    'panel_4',
)

SCHEDULE_TYPE_LABELS = {
    'heating':            'Ogrzewanie CO',
    'water_heater':       'Bojler CWU',
    'circulation_pump':   'Pompa cyrkulacji',
    'boiler_work':        'Praca kotła',
    'boiler_clean':       'Czyszczenie kotła',
    'heat_exchanger_clean': 'Czyszczenie wymiennika',
    'mixer_1':            'Mieszacz 1',
    'mixer_2':            'Mieszacz 2',
    'mixer_3':            'Mieszacz 3',
    'mixer_4':            'Mieszacz 4',
    'mixer_5':            'Mieszacz 5',
    'mixer_6':            'Mieszacz 6',
    'mixer_7':            'Mieszacz 7',
    'mixer_8':            'Mieszacz 8',
    'mixer_9':            'Mieszacz 9',
    'mixer_10':           'Mieszacz 10',
    'thermostat_1':       'Termostat 1',
    'thermostat_2':       'Termostat 2',
    'thermostat_3':       'Termostat 3',
    'circuit_1':          'Obieg 1',
    'circuit_2':          'Obieg 2',
    'circuit_3':          'Obieg 3',
    'circuit_4':          'Obieg 4',
    'circuit_5':          'Obieg 5',
    'circuit_6':          'Obieg 6',
    'circuit_7':          'Obieg 7',
    'panel_1':            'Panel 1',
    'panel_2':            'Panel 2',
    'panel_3':            'Panel 3',
    'panel_4':            'Panel 4',
}


def _schedule_type_name(schedule_type: int) -> str:
    if 0 <= schedule_type < len(SCHEDULE_TYPES):
        return SCHEDULE_TYPES[schedule_type]
    return f'type_{schedule_type}'


def _schedule_type_id(value) -> int:
    if isinstance(value, int):
        schedule_type = value
    else:
        text = str(value or '').strip().lower()
        if not text:
            raise ValueError('Brak typu harmonogramu')
        if text.isdigit():
            schedule_type = int(text)
        else:
            try:
                schedule_type = SCHEDULE_TYPES.index(text)
            except ValueError as exc:
                raise ValueError('Nieznany typ harmonogramu') from exc

    if not (0 <= schedule_type <= 255):
        raise ValueError('Typ harmonogramu musi byc w zakresie 0-255')
    return schedule_type


def _split_byte_bits(byte_value: int) -> list:
    byte_value = int(byte_value) & 0xFF
    return [bool(byte_value & (1 << bit)) for bit in reversed(range(8))]


def _join_bits(bits) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | int(bool(bit))
    return value & 0xFF


def _slot_to_time(slot: int) -> str:
    if slot < 0:
        slot = 0
    if slot > SLOTS_PER_DAY:
        slot = SLOTS_PER_DAY
    hour = slot // 2
    minute = 30 if slot % 2 else 0
    return f'{hour:02d}:{minute:02d}'


def _time_to_slot(value: str, *, allow_24_end: bool = False) -> int:
    text = str(value or '').strip()
    parts = text.split(':')
    if len(parts) != 2:
        raise ValueError(f'Nieprawidlowy format czasu: {text}')

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError(f'Nieprawidlowy format czasu: {text}') from exc

    if allow_24_end and hour == 24 and minute == 0:
        return SLOTS_PER_DAY

    if not (0 <= hour <= 23 and minute in (0, 30)):
        raise ValueError(f'Dozwolone godziny to HH:00 lub HH:30 (00:00-23:30), otrzymano: {text}')

    return hour * 2 + (1 if minute == 30 else 0)


def _slots_to_intervals(day_slots) -> list:
    intervals = []
    start = None
    for slot in range(SLOTS_PER_DAY):
        active = bool(day_slots[slot])
        if active and start is None:
            start = slot
        if (not active) and start is not None:
            intervals.append({'start': _slot_to_time(start), 'end': _slot_to_time(slot)})
            start = None

    if start is not None:
        intervals.append({'start': _slot_to_time(start), 'end': _slot_to_time(SLOTS_PER_DAY)})

    return intervals


def _intervals_to_slots(intervals) -> list:
    day_slots = [False] * SLOTS_PER_DAY
    if not intervals:
        return day_slots

    if not isinstance(intervals, list):
        raise ValueError('Lista interwalow dnia musi byc lista')

    for interval in intervals:
        if not isinstance(interval, dict):
            raise ValueError('Kazdy interwal musi byc obiektem z polami start i end')
        start_slot = _time_to_slot(interval.get('start'), allow_24_end=False)
        end_slot = _time_to_slot(interval.get('end'), allow_24_end=True)
        if not (0 <= start_slot < end_slot <= SLOTS_PER_DAY):
            raise ValueError('Nieprawidlowy zakres interwalu dnia')
        for slot in range(start_slot, end_slot):
            day_slots[slot] = True

    return day_slots


def _decode_schedule_bytes(buffer: bytes) -> list:
    if len(buffer) != SCHEDULE_SIZE:
        raise ValueError(f'Nieprawidlowy rozmiar danych harmonogramu: {len(buffer)} (oczekiwano {SCHEDULE_SIZE})')

    flat = []
    for byte_value in buffer:
        flat.extend(_split_byte_bits(byte_value))

    days = []
    for day_index in range(7):
        start = day_index * SLOTS_PER_DAY
        end = start + SLOTS_PER_DAY
        days.append(flat[start:end])
    return days


def _encode_schedule_bytes(days_slots) -> bytes:
    if not isinstance(days_slots, list) or len(days_slots) != 7:
        raise ValueError('Harmonogram musi zawierac dokladnie 7 dni')

    raw = bytearray()
    for day_slots in days_slots:
        if len(day_slots) != SLOTS_PER_DAY:
            raise ValueError('Kazdy dzien harmonogramu musi miec 48 polgodzinnych slotow')
        for i in range(0, SLOTS_PER_DAY, 8):
            raw.append(_join_bits(day_slots[i : i + 8]))
    return bytes(raw)


def _parse_schedule_parameter(raw_three_bytes: bytes) -> dict:
    if len(raw_three_bytes) != 3:
        return {'value': 0, 'min_value': 0, 'max_value': 255}
    return {
        'value': int(raw_three_bytes[0]),
        'min_value': int(raw_three_bytes[1]),
        'max_value': int(raw_three_bytes[2]),
    }


def _parse_schedules_payload(payload: bytes) -> dict:
    if not payload or len(payload) < 3:
        return {
            'total': 0,
            'start': 0,
            'count': 0,
            'schedules': [],
        }

    total = int(payload[0])
    start = int(payload[1])
    count = int(payload[2])
    offset = 3
    schedules = []

    for _ in range(count):
        if offset + 5 > len(payload):
            break

        schedule_type = int(payload[offset])
        switch_value = int(payload[offset + 1])
        parameter = _parse_schedule_parameter(payload[offset + 2 : offset + 5])
        offset += 5

        if offset + SCHEDULE_SIZE > len(payload):
            break

        schedule_bytes = bytes(payload[offset : offset + SCHEDULE_SIZE])
        offset += SCHEDULE_SIZE

        day_slots = _decode_schedule_bytes(schedule_bytes)
        days = []
        for day_index, slots in enumerate(day_slots):
            days.append({
                'day_index': day_index,
                'day_name': SCHEDULE_DAY_NAMES[day_index],
                'slots': [1 if x else 0 for x in slots],
                'intervals': _slots_to_intervals(slots),
            })

        schedules.append({
            'type': schedule_type,
            'type_name': _schedule_type_name(schedule_type),
            'switch': switch_value,
            'switch_on': bool(switch_value),
            'parameter': parameter,
            'days': days,
            'raw_schedule_hex': schedule_bytes.hex(),
        })

    return {
        'total': total,
        'start': start,
        'count': len(schedules),
        'schedules': schedules,
    }


def _find_schedule_by_type(parsed_payload: dict, schedule_type: int):
    for schedule in parsed_payload.get('schedules') or []:
        if int(schedule.get('type', -1)) == int(schedule_type):
            return schedule
    return None


def _build_schedule_write_payload(schedule_type: int, switch_value: int, parameter_value: int, days_data) -> bytes:
    if not (0 <= switch_value <= 1):
        raise ValueError('switch musi miec wartosc 0 lub 1')
    if not (0 <= parameter_value <= 255):
        raise ValueError('parameter musi byc z zakresu 0-255')

    if not isinstance(days_data, list) or len(days_data) != 7:
        raise ValueError('Pole days musi zawierac 7 elementow (pon-nd)')

    day_slots = []
    for day in days_data:
        if not isinstance(day, dict):
            raise ValueError('Kazdy element days musi byc obiektem')
        day_intervals = day.get('intervals', [])
        day_slots.append(_intervals_to_slots(day_intervals))

    schedule_bytes = _encode_schedule_bytes(day_slots)
    return bytes([
        0x01,
        schedule_type & 0xFF,
        switch_value & 0xFF,
        parameter_value & 0xFF,
    ]) + schedule_bytes


def _is_valid_time_zone(zone: str) -> bool:
    zone = str(zone or '').strip()
    if not zone or len(zone) > 64:
        return False
    if zone.startswith('/') or '\\' in zone or '..' in zone:
        return False
    return zone in available_timezones()


def _apply_process_timezone(zone: str) -> tuple[bool, str]:
    """Ustaw strefę czasową dla bieżącego procesu Pythona (wpływa na UI/WebSocket)."""
    zone = str(zone or '').strip()
    if not _is_valid_time_zone(zone):
        return False, 'Nieprawidłowa strefa czasowa procesu'

    os.environ['TZ'] = zone
    if hasattr(time, 'tzset'):
        time.tzset()
    return True, f'Ustawiono strefę procesu na {zone}'


def _parse_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _run_command(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        def _do_run():
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        if _eventlet_tpool is not None:
            result = _eventlet_tpool.execute(_do_run)
        else:
            result = _do_run()

        if result.returncode == 0:
            return True, (result.stdout or '').strip()
        return False, (result.stderr or result.stdout or '').strip()
    except subprocess.TimeoutExpired:
        return False, 'Timeout wykonania polecenia'
    except FileNotFoundError:
        return False, f'Nie znaleziono polecenia: {cmd[0] if cmd else "?"}'
    except Exception as e:
        return False, str(e)


def _sync_time_with_ntp(server: str) -> tuple[bool, str]:
    if not shutil.which('ntpdate'):
        return False, 'Polecenie ntpdate nie jest dostępne'

    for cmd in (
        ['sudo', 'ntpdate', '-u', server],
        ['ntpdate', '-u', server],
    ):
        ok, msg = _run_command(cmd, timeout=30)
        if ok:
            return True, msg or f'Zsynchronizowano czas przez {server}'
    return False, msg


def _set_system_timezone(zone: str) -> tuple[bool, str]:
    zone = str(zone or '').strip()
    if not _is_valid_time_zone(zone):
        return False, 'Nieprawidłowa strefa czasowa'

    if shutil.which('timedatectl'):
        for cmd in (
            ['sudo', 'timedatectl', 'set-timezone', zone],
            ['timedatectl', 'set-timezone', zone],
        ):
            ok, msg = _run_command(cmd)
            if ok:
                if hasattr(time, 'tzset'):
                    time.tzset()
                return True, msg or f'Ustawiono strefę czasową na {zone}'

    zone_base = os.path.realpath('/usr/share/zoneinfo')
    zone_path = os.path.realpath(os.path.join(zone_base, zone))
    if not zone_path.startswith(zone_base + os.sep):
        return False, 'Nieprawidłowa ścieżka strefy czasowej'

    if os.path.isfile(zone_path):
        for cmd in (
            ['sudo', 'ln', '-sf', zone_path, '/etc/localtime'],
            ['ln', '-sf', zone_path, '/etc/localtime'],
        ):
            ok, msg = _run_command(cmd)
            if ok:
                if hasattr(time, 'tzset'):
                    time.tzset()
                return True, msg or f'Ustawiono strefę czasową na {zone}'

    return False, f'Nie udało się ustawić strefy czasowej: {zone}'


def _set_system_date(date_value: datetime.date) -> tuple[bool, str]:
    now_local = datetime.datetime.now()
    merged = datetime.datetime(
        date_value.year,
        date_value.month,
        date_value.day,
        now_local.hour,
        now_local.minute,
        now_local.second,
    )
    return _set_system_time(merged)


def _set_system_time(value: datetime.datetime) -> tuple[bool, str]:
    dt_text = value.strftime('%Y-%m-%d %H:%M:%S')
    commands = []

    if shutil.which('timedatectl'):
        commands.extend([
            ['sudo', 'timedatectl', 'set-time', dt_text],
            ['timedatectl', 'set-time', dt_text],
        ])

    commands.extend([
        ['sudo', 'date', '-s', dt_text],
        ['date', '-s', dt_text],
    ])

    last_error = 'Nie udało się ustawić czasu systemowego'
    for cmd in commands:
        ok, msg = _run_command(cmd)
        if ok:
            return True, msg or f'Ustawiono czas na {dt_text}'
        last_error = msg or last_error

    return False, last_error


def _read_system_ntp_status() -> dict:
    status = {
        'ntp_enabled_system': None,
        'ntp_synchronized': None,
    }

    if not shutil.which('timedatectl'):
        return status

    ok, output = _run_command(['timedatectl', 'show', '--property=NTP', '--property=NTPSynchronized'])
    if not ok:
        return status

    for line in output.splitlines():
        if line.startswith('NTP='):
            status['ntp_enabled_system'] = (line.split('=', 1)[1].strip().lower() == 'yes')
        elif line.startswith('NTPSynchronized='):
            status['ntp_synchronized'] = (line.split('=', 1)[1].strip().lower() == 'yes')

    return status


def init_routes(connection, database):
    """Ustaw referencje do polaczenia i bazy."""
    global _connection, _database
    with _routes_lock:
        _connection = connection
        _database = database

    # Zastosuj zapisana strefe czasowa na poziomie procesu,
    # aby czas wysylany przez WebSocket byl zgodny z konfiguracja aplikacji.
    try:
        if _database:
            zone = str(_database.get_config('time_zone', 'Europe/Warsaw') or 'Europe/Warsaw')
            _apply_process_timezone(zone)
    except Exception:
        logging.getLogger(__name__).warning('Nie udalo sie ustawic strefy procesu podczas init_routes')


@bp.route('/')
def index():
    """Strona glowna - dashboard."""
    return render_template('index.html', sensor_definitions=DASHBOARD_TILE_DEFINITIONS)


@bp.route('/parameters')
def parameters():
    """Strona podglądu wszystkich dostępnych parametrów (read-only)."""
    return render_template('parameters.html')


@bp.route('/boiler-control')
def boiler_control_page():
    """Strona sterowania ustawieniami kotła (MVP)."""
    return render_template('boiler_control.html')


@bp.route('/analysis')
def analysis_page():
    """Strona analiz i raportów (miejsce pod rozbudowę)."""
    return render_template('analysis.html')


@bp.route('/api/data')
def get_current_data():
    """Pobierz aktualne dane z kotla."""
    if _connection and _connection.last_data:
        data = _connection.last_data.to_dict()

        zero_fallback_keys = {
            'temp_co', 'temp_back', 'temp_cwu', 'temp_flue_gas',
            'temp_co_set', 'temp_cwu_set', 'fan_power', 'fuel_level',
        }
        if _looks_incomplete_live_row(data):
            fallback = _get_recent_valid_measurement()
            if fallback is None and _database:
                fallback = _database.get_latest()
            data = _merge_missing_with_latest(
                data,
                fallback,
                max_age_sec=6 * 3600,
                zero_fallback_keys=zero_fallback_keys,
            )

        _normalize_mode_name_in_row(data)
        return jsonify({
            'status': 'ok',
            'data': data,
            'connection': _connection.get_status(),
        })

    latest = _database.get_latest() if _database else None
    if latest:
        _normalize_mode_name_in_row(latest)
        return jsonify({
            'status': 'ok',
            'data': latest,
            'connection': _connection.get_status() if _connection else None,
        })

    return jsonify({
        'status': 'no_data',
        'data': None,
        'connection': _connection.get_status() if _connection else None,
    })


@bp.route('/api/parameters')
def get_parameters():
    """Pobierz pełną listę dostępnych parametrów kotła (tylko odczyt)."""
    if _connection and _connection.last_data:
        data = _connection.last_data.to_dict()
        _normalize_mode_name_in_row(data)
        status = 'ok'
    else:
        data = _default_parameters_data()
        status = 'no_data'

    rows = []
    for item in PARAMETERS_METADATA:
        key = item['key']
        value = data.get(key)
        rows.append({
            'key': key,
            'label': item['label'],
            'unit': item['unit'],
            'group': item.get('group', ''),
            'value': value,
            'available': value is not None,
        })

    return jsonify({
        'status': status,
        'connection': _connection.get_status() if _connection else None,
        'count': len(rows),
        'data': rows,
    })


@bp.route('/api/boiler-control/parameters', methods=['GET'])
def get_boiler_control_parameters():
    """Pobierz katalog parametrów sterowania z bieżącymi wartościami."""
    live_data = None
    if _connection and _connection.last_data:
        try:
            live_data = _connection.last_data.to_dict()
        except Exception:
            live_data = None

    controller_params = _get_ecomax_limits_cached(force_refresh=False)

    rows = []
    for param in PARAMETERS:
        current_value = None
        if live_data and param.live_data_key:
            current_value = live_data.get(param.live_data_key)

        controller_limits = controller_params.get(param.index)
        min_value = param.min_value
        max_value = param.max_value
        controller_value = None
        available = True  # domyślnie dostępny, dopóki kontroler nie powie inaczej
        if controller_limits:
            if not controller_limits.get('available', True):
                available = False
            else:
                min_value = int(controller_limits.get('min_value', min_value))
                max_value = int(controller_limits.get('max_value', max_value))
                controller_value = controller_limits.get('value')
                if current_value is None and controller_value is not None:
                    current_value = controller_value

        rows.append({
            'key': param.key,
            'label': param.label,
            'description': param.description,
            'group': param.group,
            'index': param.index,
            'widget': param.widget,
            'unit': param.unit,
            'ha_entity_id': param.ha_entity_id,
            'ha_entity_type': param.ha_entity_type,
            'ui_profile': param.ui_profile,
            'ui_level': 'basic' if param.ui_profile == 'user' else 'advanced',
            'min_value': min_value,
            'max_value': max_value,
            'step': param.step,
            'value_map': param.value_map,
            'current_value': current_value,
            'current_value_text': format_parameter_value(param, current_value),
            'controller_value': controller_value,
            'available': available,
        })

    return jsonify({
        'status': 'ok',
        'connection': _connection.get_status() if _connection else None,
        'count': len(rows),
        'data': rows,
    })


@bp.route('/api/boiler-control/capabilities', methods=['GET'])
def get_boiler_control_capabilities():
    """Zwróć obsługiwane targety i funkcje sterowania."""
    return jsonify({
        'status': 'ok',
        'data': {
            'targets': ['ecomax', 'mixer', 'thermostat'],
            'supports_batch': True,
            'supports_history': True,
            'supports_dynamic_limits': True,
            'supports_schedule': True,
            'supports_schedule_raw': True,
            'supports_service_profile': True,
        },
    })


@bp.route('/api/boiler-control/device-info', methods=['GET'])
def get_boiler_control_device_info():
    """Pobierz informacje o modelu sterownika, liczbie mieszaczy i termostatów.

    Łączy dane z dwóch źródeł:
    - Ramka UID (0x39/0xB9): model, product_type, uid
    - Ostatnia ramka danych (0x35): mixers_available, thermostats_available
    """
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień'}), 403
    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    # Dane urządzenia (UID) - pobierz live lub zwróć z cache
    uid_info = _connection.get_device_info_cached()
    uid_error = None
    if uid_info is None:
        try:
            uid_info = _execute_blocking(_connection.request_uid, timeout=3.0)
        except TimeoutError:
            uid_error = 'Brak odpowiedzi sterownika na zapytanie UID (0xB9)'
            uid_info = {}
        except Exception as exc:
            uid_error = str(exc)
            uid_info = {}

    # Dane mieszaczy/termostatów z ostatniej ramki 0x35
    last_data = _connection.last_data
    mixers_available = getattr(last_data, 'mixers_available', 0) if last_data else 0
    mixers_connected = getattr(last_data, 'mixers_connected', 0) if last_data else 0
    thermostats_available = getattr(last_data, 'thermostats_available', 0) if last_data else 0
    thermostats_connected = getattr(last_data, 'thermostats_connected', 0) if last_data else 0
    mixer_data = getattr(last_data, 'mixer_data', []) if last_data else []
    thermostat_data = getattr(last_data, 'thermostat_data', []) if last_data else []
    modules = {
        'module_a': getattr(last_data, 'module_a', None),
        'module_b': getattr(last_data, 'module_b', None),
        'module_c': getattr(last_data, 'module_c', None),
        'module_ecolambda': getattr(last_data, 'module_ecolambda', None),
        'module_ecoster': getattr(last_data, 'module_ecoster', None),
        'module_panel': getattr(last_data, 'module_panel', None),
    }

    result = {
        'product_type': uid_info.get('product_type'),
        'product_id': uid_info.get('product_id'),
        'model': uid_info.get('model'),
        'uid': uid_info.get('uid'),
        'mixers_available': mixers_available,
        'mixers_connected': mixers_connected,
        'thermostats_available': thermostats_available,
        'thermostats_connected': thermostats_connected,
        'mixer_data': mixer_data,
        'thermostat_data': thermostat_data,
        'modules': modules,
    }
    if uid_error:
        result['uid_error'] = uid_error

    return jsonify({'status': 'ok', 'data': result})


@bp.route('/api/boiler-control/service-access', methods=['GET'])
def get_boiler_control_service_access():
    """Sprawdź, czy profil serwisowy jest skonfigurowany i odblokowany."""
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień'}), 403

    token_expected = os.environ.get('BOILER_SERVICE_ACCESS_TOKEN', '').strip()
    enabled = bool(token_expected)
    granted = _is_boiler_service_access_allowed() if enabled else False

    return jsonify({
        'status': 'ok',
        'data': {
            'enabled': enabled,
            'granted': granted,
        },
    })


@bp.route('/api/boiler-control/history', methods=['GET'])
def get_boiler_control_history():
    """Pobierz historię zmian ustawień kotła."""
    if not _database:
        return jsonify({'status': 'error', 'message': 'Brak bazy danych'}), 500

    limit = request.args.get('limit', 100, type=int)
    data = _database.get_boiler_setting_changes(limit=limit)
    return jsonify({'status': 'ok', 'count': len(data), 'data': data})


@bp.route('/api/boiler-control/set-parameter', methods=['POST'])
def set_boiler_control_parameter():
    """Ustaw pojedynczy parametr dla targetu ecomax/mixer/thermostat."""
    if not request.is_json:
        return jsonify({'status': 'error', 'message': 'Wymagany Content-Type: application/json'}), 415

    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień do zmiany ustawień kotła'}), 403

    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    payload = request.get_json(silent=True) or {}
    request_id = str(payload.get('request_id') or uuid.uuid4())
    source = _resolve_request_source()
    try:
        resolved = _resolve_boiler_write_request(payload, use_limits_cache=True)
    except ValueError as exc:
        err_msg = str(exc)
        err_status = 'rejected' if err_msg.startswith('Wartość poza zakresem') else 'validation_error'
        key = str(payload.get('key', '') or '').strip().upper()
        target_type = str(payload.get('target_type', 'ecomax') or 'ecomax').strip().lower()
        target_index = payload.get('target_index', 0)
        index_raw = payload.get('index')
        param = PARAMETERS_BY_KEY.get(key)
        index_for_log = int(index_raw) if (index_raw is not None and str(index_raw).isdigit()) else (param.index if param else -1)
        _log_boiler_change(
            request_id=request_id,
            source=source,
            target_type=target_type,
            target_index=int(target_index) if target_index is not None else None,
            param_key=key,
            param_index=index_for_log,
            old_value=None,
            new_value=str(payload.get('value')),
            status=err_status,
            message=err_msg,
        )
        return jsonify({'status': 'error', 'message': err_msg}), 400

    target_type = resolved['target_type']
    target_index = resolved['target_index']
    offset = resolved['offset']
    size = resolved['size']
    key = resolved['key']
    param = resolved['param']
    index_value = resolved['index_value']
    value = resolved['value']
    old_value = resolved['old_value']

    # Dodatkowa autoryzacja dla parametrów serwisowych.
    if getattr(param, 'ui_profile', None) == 'service' and not _is_boiler_service_access_allowed():
        err_msg = 'Brak uprawnień do zmiany parametrów serwisowych kotła'
        _log_boiler_change(
            request_id=request_id,
            source=source,
            target_type=target_type,
            target_index=int(target_index) if target_index is not None else None,
            param_key=key,
            param_index=index_value,
            old_value=old_value,
            new_value=str(value),
            status='forbidden',
            message=err_msg,
        )
        return jsonify({'status': 'error', 'message': err_msg}), 403

    try:
        ack = _execute_blocking(
            _connection.set_parameter,
            target_type=target_type,
            index=index_value,
            value=value,
            target_index=int(target_index) if target_index is not None else 0,
            offset=int(offset),
            size=int(size),
            timeout=2.0,
        )
    except TimeoutError as exc:
        _log_boiler_change(
            request_id=request_id,
            source=source,
            target_type=target_type,
            target_index=int(target_index) if target_index is not None else None,
            param_key=key,
            param_index=index_value,
            old_value=old_value,
            new_value=str(value),
            status='timeout',
            message=str(exc),
        )
        return jsonify({'status': 'error', 'message': str(exc)}), 504
    except RuntimeError as exc:
        _log_boiler_change(
            request_id=request_id,
            source=source,
            target_type=target_type,
            target_index=int(target_index) if target_index is not None else None,
            param_key=key,
            param_index=index_value,
            old_value=old_value,
            new_value=str(value),
            status='connection_error',
            message=str(exc),
        )
        return jsonify({'status': 'error', 'message': str(exc)}), 503
    except ValueError as exc:
        _log_boiler_change(
            request_id=request_id,
            source=source,
            target_type=target_type,
            target_index=int(target_index) if target_index is not None else None,
            param_key=key,
            param_index=index_value,
            old_value=old_value,
            new_value=str(value),
            status='validation_error',
            message=str(exc),
        )
        return jsonify({'status': 'error', 'message': str(exc)}), 400
    except Exception as exc:
        logging.getLogger(__name__).error('Blad zapisu parametru %s: %s', key, exc, exc_info=True)
        _log_boiler_change(
            request_id=request_id,
            source=source,
            target_type=target_type,
            target_index=int(target_index) if target_index is not None else None,
            param_key=key,
            param_index=index_value,
            old_value=old_value,
            new_value=str(value),
            status='error',
            message='Nie udało się zapisać parametru',
        )
        return jsonify({'status': 'error', 'message': 'Nie udało się zapisać parametru'}), 500

    _log_boiler_change(
        request_id=request_id,
        source=source,
        target_type=target_type,
        target_index=int(target_index) if target_index is not None else None,
        param_key=key,
        param_index=index_value,
        old_value=old_value,
        new_value=str(value),
        status='ok',
        message='Parametr zapisany',
    )

    return jsonify({
        'status': 'ok',
        'message': 'Parametr zapisany',
        'data': {
            'request_id': request_id,
            'target_type': target_type,
            'target_index': int(target_index) if target_index is not None else None,
            'key': key,
            'label': param.label if param else key,
            'index': index_value,
            'value': value,
            'value_text': format_parameter_value(param, value) if param else str(value),
            'old_value': old_value,
            'ack': ack,
        },
    })


@bp.route('/api/boiler-control/set-batch', methods=['POST'])
def set_boiler_control_batch():
    """Zapis wsadowy parametrów sterowania kotłem."""
    if not request.is_json:
        return jsonify({'status': 'error', 'message': 'Wymagany Content-Type: application/json'}), 415
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień do zmiany ustawień kotła'}), 403
    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    payload = request.get_json(silent=True) or {}
    operations = payload.get('operations')
    if not isinstance(operations, list) or not operations:
        return jsonify({'status': 'error', 'message': 'Pole operations musi być niepustą listą'}), 400

    source = _resolve_request_source()
    results = []
    for operation in operations[:100]:
        try:
            item_payload = {
                'request_id': operation.get('request_id') or uuid.uuid4().hex,
                'target_type': operation.get('target_type', 'ecomax'),
                'target_index': operation.get('target_index', 0),
                'offset': operation.get('offset', 0),
                'size': operation.get('size', 1),
                'key': operation.get('key', ''),
                'index': operation.get('index'),
                'value': operation.get('value'),
            }

            resolved = _resolve_boiler_write_request(item_payload, use_limits_cache=True)

            # Wymuszenie tokenu serwisowego dla parametrów serwisowych.
            param_meta = resolved.get('param')
            if getattr(param_meta, 'ui_profile', None) == 'service' and not _is_boiler_service_access_allowed():
                err_msg = 'Brak uprawnień do zmiany parametrów serwisowych kotła'
                _log_boiler_change(
                    request_id=item_payload['request_id'],
                    source=source,
                    target_type=resolved['target_type'],
                    target_index=resolved['target_index'],
                    param_key=resolved['key'],
                    param_index=resolved['index_value'],
                    old_value=resolved['old_value'],
                    new_value=str(resolved['value']),
                    status='forbidden',
                    message=err_msg,
                )
                results.append({
                    'request_id': item_payload['request_id'],
                    'status': 'error',
                    'key': resolved.get('key'),
                    'index': resolved.get('index_value'),
                    'message': err_msg,
                    'error_code': 'forbidden_service_param',
                })
                continue

            ack = _execute_blocking(
                _connection.set_parameter,
                target_type=resolved['target_type'],
                index=resolved['index_value'],
                value=resolved['value'],
                target_index=resolved['target_index'] if resolved['target_index'] is not None else 0,
                offset=resolved['offset'],
                size=resolved['size'],
                timeout=2.0,
            )

            _log_boiler_change(
                request_id=item_payload['request_id'],
                source=source,
                target_type=resolved['target_type'],
                target_index=resolved['target_index'],
                param_key=resolved['key'],
                param_index=resolved['index_value'],
                old_value=resolved['old_value'],
                new_value=str(resolved['value']),
                status='ok',
                message='Batch: parametr zapisany',
            )

            results.append({
                'request_id': item_payload['request_id'],
                'status': 'ok',
                'key': resolved['key'],
                'index': resolved['index_value'],
                'value': resolved['value'],
                'ack': ack,
            })

        except ValueError as exc:
            err_msg = str(exc)
            err_status = 'rejected' if err_msg.startswith('Wartość poza zakresem') else 'validation_error'
            item_request_id = (operation or {}).get('request_id') or uuid.uuid4().hex
            key = str((operation or {}).get('key', '') or '').strip().upper()
            target_type = str((operation or {}).get('target_type', 'ecomax') or 'ecomax').strip().lower()
            target_index = (operation or {}).get('target_index', 0)
            index_raw = (operation or {}).get('index')
            param = PARAMETERS_BY_KEY.get(key)
            index_for_log = int(index_raw) if (index_raw is not None and str(index_raw).isdigit()) else (param.index if param else -1)

            _log_boiler_change(
                request_id=item_request_id,
                source=source,
                target_type=target_type,
                target_index=int(target_index) if target_index is not None else None,
                param_key=key,
                param_index=index_for_log,
                old_value=None,
                new_value=str((operation or {}).get('value')),
                status=err_status,
                message=err_msg,
            )

            results.append({
                'request_id': item_request_id,
                'status': 'error',
                'message': err_msg,
                'operation': operation,
            })
        except Exception as exc:
            results.append({
                'request_id': operation.get('request_id'),
                'status': 'error',
                'message': str(exc),
                'operation': operation,
            })

    ok_count = len([r for r in results if r.get('status') == 'ok'])
    err_count = len(results) - ok_count
    status = 'ok' if err_count == 0 else 'partial'
    return jsonify({
        'status': status,
        'summary': {
            'total': len(results),
            'ok': ok_count,
            'error': err_count,
        },
        'results': results,
    })


@bp.route('/api/boiler-control/schedules/raw', methods=['GET'])
def get_boiler_control_schedules_raw():
    """Pobierz surowe dane harmonogramów (0x36/0xB6)."""
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień'}), 403
    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    try:
        data = _execute_blocking(_connection.request_schedules_raw, timeout=2.0)
        return jsonify({'status': 'ok', 'data': data})
    except TimeoutError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 504
    except Exception as exc:
        logging.getLogger(__name__).error('Blad pobierania harmonogramow raw: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'message': 'Nie udało się pobrać harmonogramów'}), 500


@bp.route('/api/boiler-control/schedules/raw', methods=['POST'])
def set_boiler_control_schedules_raw():
    """Wyślij surowy payload harmonogramu (0x37)."""
    if not request.is_json:
        return jsonify({'status': 'error', 'message': 'Wymagany Content-Type: application/json'}), 415
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień'}), 403
    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    payload = request.get_json(silent=True) or {}
    raw_hex = payload.get('payload_hex', '')

    try:
        raw_bytes = _parse_hex_payload(raw_hex)
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    if not raw_bytes:
        return jsonify({'status': 'error', 'message': 'Pusty payload harmonogramu'}), 400

    try:
        data = _execute_blocking(_connection.set_schedule_raw, raw_bytes)
        _log_boiler_change(
            request_id=str(payload.get('request_id') or uuid.uuid4()),
            source=_resolve_request_source(),
            target_type='schedule_raw',
            target_index=None,
            param_key='SCHEDULE_RAW',
            param_index=0,
            old_value=None,
            new_value=f'{len(raw_bytes)}B',
            status='ok',
            message='Wysłano surowy payload harmonogramu',
        )
        return jsonify({'status': 'ok', 'data': data})
    except Exception as exc:
        logging.getLogger(__name__).error('Blad zapisu harmonogramow raw: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'message': 'Nie udało się zapisać harmonogramu'}), 500


@bp.route('/api/boiler-control/schedules', methods=['GET'])
def get_boiler_control_schedules():
    """Pobierz harmonogramy w postaci semantycznej (dni/interwaly)."""
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień'}), 403
    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    try:
        data = _execute_blocking(_connection.request_schedules_raw, timeout=2.0)
        payload = bytes(data.get('payload') or b'')
        parsed = _parse_schedules_payload(payload)
        return jsonify({
            'status': 'ok',
            'data': {
                'payload_hex': payload.hex(),
                'parsed': parsed,
                'known_types': [
                    {
                        'type': i,
                        'type_name': name,
                        'label': SCHEDULE_TYPE_LABELS.get(name, name),
                    }
                    for i, name in enumerate(SCHEDULE_TYPES)
                ],
            },
        })
    except TimeoutError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 504
    except Exception as exc:
        logging.getLogger(__name__).error('Blad pobierania harmonogramow semantycznych: %s', exc, exc_info=True)
        return jsonify({'status': 'error', 'message': 'Nie udało się pobrać harmonogramów'}), 500


@bp.route('/api/boiler-control/schedules', methods=['POST'])
def set_boiler_control_schedule_semantic():
    """Zapisz pojedynczy harmonogram przez model dni/interwałów."""
    if not request.is_json:
        return jsonify({'status': 'error', 'message': 'Wymagany Content-Type: application/json'}), 415
    if not _is_boiler_control_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień'}), 403
    if not _connection:
        return jsonify({'status': 'error', 'message': 'Brak połączenia z modułem EW11A'}), 503

    payload = request.get_json(silent=True) or {}
    request_id = str(payload.get('request_id') or uuid.uuid4())

    try:
        schedule_type = _schedule_type_id(payload.get('schedule_type', payload.get('type')))
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    try:
        current_raw = _execute_blocking(_connection.request_schedules_raw, timeout=2.0)
        parsed_current = _parse_schedules_payload(bytes(current_raw.get('payload') or b''))
        current_schedule = _find_schedule_by_type(parsed_current, schedule_type)
    except Exception:
        current_schedule = None

    switch_raw = payload.get('switch')
    if switch_raw is None and current_schedule:
        switch_value = int(current_schedule.get('switch', 0))
    else:
        switch_value = 1 if _parse_bool(switch_raw, default=False) else 0

    parameter_raw = payload.get('parameter')
    if parameter_raw is None and current_schedule:
        parameter_raw = (current_schedule.get('parameter') or {}).get('value', 0)

    try:
        parameter_value = int(parameter_raw)
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'parameter musi byc liczba calkowita'}), 400

    if current_schedule:
        param_meta = current_schedule.get('parameter') or {}
        min_value = int(param_meta.get('min_value', 0))
        max_value = int(param_meta.get('max_value', 255))
        if not (min_value <= parameter_value <= max_value):
            return jsonify({'status': 'error', 'message': f'parameter poza zakresem {min_value}-{max_value}'}), 400

    days_data = payload.get('days')
    if days_data is None and current_schedule:
        days_data = current_schedule.get('days')

    try:
        write_payload = _build_schedule_write_payload(
            schedule_type=schedule_type,
            switch_value=switch_value,
            parameter_value=parameter_value,
            days_data=days_data,
        )
    except ValueError as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    try:
        result = _execute_blocking(_connection.set_schedule_raw, write_payload)
        _log_boiler_change(
            request_id=request_id,
            source=_resolve_request_source(),
            target_type='schedule',
            target_index=schedule_type,
            param_key=_schedule_type_name(schedule_type).upper(),
            param_index=schedule_type,
            old_value=None,
            new_value=f'switch={switch_value},parameter={parameter_value}',
            status='ok',
            message='Zapisano harmonogram semantyczny',
        )
        return jsonify({
            'status': 'ok',
            'message': 'Harmonogram zapisany',
            'data': {
                'schedule_type': schedule_type,
                'schedule_type_name': _schedule_type_name(schedule_type),
                'switch': switch_value,
                'parameter': parameter_value,
                'payload_hex': write_payload.hex(),
                'result': result,
            },
        })
    except Exception as exc:
        logging.getLogger(__name__).error('Blad zapisu harmonogramu semantycznego: %s', exc, exc_info=True)
        _log_boiler_change(
            request_id=request_id,
            source=_resolve_request_source(),
            target_type='schedule',
            target_index=schedule_type,
            param_key=_schedule_type_name(schedule_type).upper(),
            param_index=schedule_type,
            old_value=None,
            new_value=f'switch={switch_value},parameter={parameter_value}',
            status='error',
            message='Nie udalo sie zapisac harmonogramu semantycznego',
        )
        return jsonify({'status': 'error', 'message': 'Nie udało się zapisać harmonogramu'}), 500


@bp.route('/api/history')
def get_history():
    """Pobierz historie pomiarow."""
    hours = request.args.get('hours', 24, type=float)
    limit = request.args.get('limit', 500, type=int)
    if not hours or hours <= 0:
        return jsonify({'status': 'error', 'message': 'hours must be positive'}), 400
    limit = min(max(limit, 1), 200000)

    if _database:
        data = _database.get_history(hours=hours, limit=limit)
        for row in data:
            _normalize_mode_name_in_row(row)
        return jsonify({'status': 'ok', 'data': data, 'count': len(data)})
    return jsonify({'status': 'error', 'data': [], 'count': 0})


@bp.route('/api/statistics')
def get_statistics():
    """Pobierz statystyki."""
    hours = request.args.get('hours', 24, type=float)
    if not hours or hours <= 0:
        return jsonify({'status': 'error', 'message': 'hours must be positive'}), 400

    if _database:
        stats = _database.get_statistics(hours=hours)
        return jsonify({'status': 'ok', 'data': stats})
    return jsonify({'status': 'error', 'data': {}})


@bp.route('/api/export')
def export_csv():
    """Eksportuj historie pomiarow jako CSV."""
    hours = request.args.get('hours', 168, type=float)
    if not hours or hours <= 0:
        return jsonify({'status': 'error', 'message': 'hours must be positive'}), 400
    if not _database:
        return jsonify({'status': 'error'}), 500

    data = _database.get_history(hours=hours, limit=100000)

    columns = [
        'timestamp', 'temp_co', 'temp_cwu', 'temp_back', 'temp_flue_gas',
        'temp_feeder', 'temp_external', 'temp_co_set', 'temp_cwu_set',
        'boiler_power', 'boiler_power_kw', 'fan_power', 'fuel_stream',
        'fuel_level', 'mode_name', 'pump_co', 'pump_cwu', 'pump_circulation',
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for row in data:
        if row.get('timestamp'):
            row['timestamp'] = time.strftime(
                '%Y-%m-%d %H:%M:%S', time.localtime(row['timestamp'])
            )
        writer.writerow(row)

    filename = f"ecomax_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@bp.route('/api/costs')
def get_costs():
    """Oblicz koszty zuzycia paliwa."""
    hours = request.args.get('hours', 24, type=float)
    if not hours or hours <= 0:
        return jsonify({'status': 'error', 'message': 'hours must be positive'}), 400
    if not _database:
        return jsonify({'status': 'error'}), 500

    data = _database.get_history(hours=hours, limit=100000)
    try:
        price_per_ton = float(_database.get_config('fuel_price_per_ton', '1000'))
    except (ValueError, TypeError):
        price_per_ton = 1000.0

    # fuel_stream jest w kg/h, pomiary co ~60s -> wartosc srednia * przedzial w godzinach
    # Pojedynczy przebieg – bez alokowania dodatkowej listy.
    total_stream = 0.0
    valid_count = 0
    for r in data:
        v = r.get('fuel_stream')
        if v is not None and math.isfinite(v) and 0.0 <= v <= FUEL_STREAM_MAX:
            total_stream += v
            valid_count += 1
    interval_h = hours / max(valid_count, 1) if valid_count else 1
    total_kg = total_stream * interval_h
    total_ton = total_kg / 1000.0
    total_cost = total_ton * price_per_ton

    return jsonify({
        'status': 'ok',
        'data': {
            'hours': hours,
            'fuel_kg': round(total_kg, 2),
            'fuel_ton': round(total_ton, 4),
            'cost_pln': round(total_cost, 2),
            'price_per_ton': price_per_ton,
            'cost_per_day': round(total_cost / (hours / 24), 2) if hours >= 1 else 0,
        }
    })


@bp.route('/api/report')
def get_report():
    """Raport dzienny - zuzycie paliwa i liczba pomiarow per dzien."""
    days = request.args.get('days', 7, type=int)
    if not days or days <= 0:
        return jsonify({'status': 'error', 'message': 'days must be positive'}), 400
    if not _database:
        return jsonify({'status': 'error'}), 500

    data = _database.get_history(hours=days * 24, limit=100000)
    try:
        price_per_ton = float(_database.get_config('fuel_price_per_ton', '1000'))
    except (ValueError, TypeError):
        price_per_ton = 1000.0

    daily = defaultdict(lambda: {'fuel_kg': 0.0, 'samples': 0, 'ignitions_max': 0})

    prev_ts = None
    for row in data:
        ts = row.get('timestamp')
        if not ts:
            continue
        day = time.strftime('%Y-%m-%d', time.localtime(ts))
        interval_h = ((ts - prev_ts) / 3600.0) if prev_ts else (60 / 3600.0)
        prev_ts = ts

        fs = row.get('fuel_stream')
        if fs is not None and math.isfinite(fs) and 0.0 <= fs <= FUEL_STREAM_MAX:
            daily[day]['fuel_kg'] += fs * interval_h
        daily[day]['samples'] += 1
        if row.get('ignitions') and row['ignitions'] > daily[day]['ignitions_max']:
            daily[day]['ignitions_max'] = row['ignitions']

    result = []
    for day in sorted(daily.keys()):
        d = daily[day]
        cost = (d['fuel_kg'] / 1000.0) * price_per_ton
        result.append({
            'day': day,
            'fuel_kg': round(d['fuel_kg'], 2),
            'cost_pln': round(cost, 2),
            'samples': d['samples'],
        })

    return jsonify({'status': 'ok', 'data': result})


@bp.route('/api/alerts', methods=['GET'])
def get_alerts():
    """Pobierz ostatnie alerty."""
    limit_raw = (request.args.get('limit', '50') or '50').strip().lower()
    if limit_raw in ('all', 'none', '0'):
        limit = _ALERTS_MAX_API_LIMIT
    else:
        try:
            limit = int(limit_raw)
        except ValueError:
            return jsonify({'status': 'error', 'message': 'Nieprawidłowy parametr limit'}), 400
        limit = min(max(limit, 1), _ALERTS_MAX_API_LIMIT)

    unread_only = request.args.get('unread', 'false').lower() == 'true'
    if _database:
        filtered_total = _database.count_alerts(unread_only=unread_only)
        unread_total = filtered_total if unread_only else _database.count_alerts(unread_only=True)
        data = _database.get_alerts(limit=limit, unread_only=unread_only)
        return jsonify({
            'status': 'ok',
            'data': data,
            'count': len(data),
            'total': filtered_total,
            'unread_total': unread_total,
            'limited': len(data) < filtered_total,
        })
    return jsonify({'status': 'error', 'data': [], 'count': 0})


@bp.route('/api/alerts/read', methods=['POST'])
def mark_alerts_read():
    """Oznacz wszystkie alerty jako przeczytane."""
    if _database:
        _database.mark_alerts_read()
        return jsonify({'status': 'ok'})
    return jsonify({'status': 'error'}), 500


@bp.route('/settings')
def settings():
    """Strona ustawien."""
    return render_template('settings.html', sensor_definitions=DASHBOARD_TILE_DEFINITIONS)


@bp.route('/logs')
def logs():
    """Strona przeglądania logów."""
    return render_template('logs.html')


@bp.route('/alerts')
def alerts_page():
    """Strona pełnej historii alertów."""
    return render_template('alerts.html')


@bp.route('/api/config', methods=['GET'])
def get_config():
    """Pobierz wszystkie ustawienia."""
    if _database:
        data = _database.get_all_config()
        data['available_visible_sensors'] = list(DASHBOARD_TILE_KEYS)
        return jsonify({'status': 'ok', 'data': data})
    return jsonify({'status': 'error', 'data': {}})


@bp.route('/api/config', methods=['POST'])
def set_config():
    """Zapisz ustawienia (JSON: {key: value, ...})."""
    if not _database:
        return jsonify({'status': 'error', 'message': 'Brak bazy danych'}), 500

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'status': 'error', 'message': 'Nieprawidłowe dane'}), 400

    allowed = set(_database.CONFIG_DEFAULTS.keys())

    # Klucze numeryczne i ich dopuszczalne zakresy
    _INT_RANGES = {
        'ew11a_port':             (1, 65535),
        'read_interval':          (1, 3600),
        'db_save_interval':       (1, 3600),
        'db_retention_days':      (1, 3650),
        'alert_retention_days':   (1, 3650),
        'alert_retention_count':  (1, 50000),
        'log_retention_days':     (1, 365),
        'alert_no_connection_min':(1, 1440),
        'log_enabled':            (0, 1),
    }
    _FLOAT_RANGES = {
        'fuel_price_per_ton':   (0.0, 1_000_000.0),
        'alert_temp_flue_max':  (100.0, 600.0),
        'alert_temp_co_min':    (0.0, 100.0),
        'alert_fuel_level_min': (0.0, 100.0),
    }

    saved = []
    rejected = []

    for key, value in data.items():
        if key not in allowed:
            rejected.append({'key': key, 'reason': 'unknown key'})
            continue

        value_to_store = None

        if key in _INT_RANGES:
            try:
                v = int(value)
            except (ValueError, TypeError):
                rejected.append({'key': key, 'reason': 'expected integer'})
                continue
            lo, hi = _INT_RANGES[key]
            if not (lo <= v <= hi):
                rejected.append({'key': key, 'reason': f'value out of range [{lo}, {hi}]'})
                continue
            value_to_store = str(v)

        elif key in _FLOAT_RANGES:
            try:
                v = _parse_float_maybe_comma(value)
            except (ValueError, TypeError):
                rejected.append({'key': key, 'reason': 'expected number'})
                continue
            lo, hi = _FLOAT_RANGES[key]
            if not (lo <= v <= hi):
                rejected.append({'key': key, 'reason': f'value out of range [{lo}, {hi}]'})
                continue
            value_to_store = str(v)

        elif key == 'ew11a_host':
            v = str(value).strip()
            if not v or len(v) > 253:
                rejected.append({'key': key, 'reason': 'invalid hostname'})
                continue
            value_to_store = v

        elif key == 'weather_lat':
            v = str(value).strip()
            if v == '':
                value_to_store = ''
            else:
                try:
                    fv = _parse_float_maybe_comma(v)
                except (ValueError, TypeError):
                    rejected.append({'key': key, 'reason': 'latitude must be a number'})
                    continue
                if not (-90.0 <= fv <= 90.0):
                    rejected.append({'key': key, 'reason': 'latitude out of range [-90, 90]'})
                    continue
                value_to_store = str(fv)

        elif key == 'weather_lon':
            v = str(value).strip()
            if v == '':
                value_to_store = ''
            else:
                try:
                    fv = _parse_float_maybe_comma(v)
                except (ValueError, TypeError):
                    rejected.append({'key': key, 'reason': 'longitude must be a number'})
                    continue
                if not (-180.0 <= fv <= 180.0):
                    rejected.append({'key': key, 'reason': 'longitude out of range [-180, 180]'})
                    continue
                value_to_store = str(fv)

        elif key == 'visible_sensors':
            allowed_sensors = set(DASHBOARD_TILE_KEYS)

            tokens = [
                token.strip()
                for token in str(value).split(',')
                if token.strip()
            ]
            unique_tokens = []
            for token in tokens:
                if token not in unique_tokens:
                    unique_tokens.append(token)

            invalid = [token for token in unique_tokens if token not in allowed_sensors]
            if invalid:
                rejected.append({'key': key, 'reason': f'invalid sensors: {", ".join(invalid)}'})
                continue

            value_to_store = ','.join(unique_tokens)

        elif key == 'alert_retention_mode':
            v = str(value).strip().lower()
            if v not in ('days', 'count'):
                rejected.append({'key': key, 'reason': 'expected one of: days, count'})
                continue
            value_to_store = v

        elif key == 'time_ntp_server':
            v = str(value).strip()
            if not v or len(v) > 253:
                rejected.append({'key': key, 'reason': 'invalid NTP server'})
                continue
            value_to_store = v

        elif key == 'time_zone':
            v = str(value).strip()
            if not _is_valid_time_zone(v):
                rejected.append({'key': key, 'reason': 'invalid time zone'})
                continue
            value_to_store = v

        else:
            value_to_store = str(value)

        if _database.set_config(key, value_to_store):
            saved.append(key)
        else:
            rejected.append({'key': key, 'reason': 'database write failed'})

    if not saved and rejected:
        return jsonify({
            'status': 'error',
            'message': 'Żadna wartość nie została zapisana',
            'rejected': rejected,
        }), 400

    if rejected:
        if 'weather_lat' in saved or 'weather_lon' in saved:
            with _weather_cache_lock:
                _weather_data_cache.clear()
                _weather_place_cache.clear()
                _weather_legacy_migration_fail_cache.clear()
        return jsonify({
            'status': 'partial',
            'message': 'Część wartości została odrzucona',
            'saved': saved,
            'rejected': rejected,
        }), 200

    if 'weather_lat' in saved or 'weather_lon' in saved:
        with _weather_cache_lock:
            _weather_data_cache.clear()
            _weather_place_cache.clear()
            _weather_legacy_migration_fail_cache.clear()

    return jsonify({'status': 'ok', 'message': 'Zapisano', 'saved': saved})


@bp.route('/api/time/status', methods=['GET'])
def get_time_status():
    """Zwróć bieżący czas serwera i status NTP."""
    time_zone_cfg = 'Europe/Warsaw'
    ntp_cfg_server = 'pool.ntp.org'
    if _database:
        time_zone_cfg = str(_database.get_config('time_zone', 'Europe/Warsaw') or 'Europe/Warsaw')
        ntp_cfg_server = str(_database.get_config('time_ntp_server', 'pool.ntp.org') or 'pool.ntp.org')

    now = time.time()
    ntp_status = _read_system_ntp_status()

    return jsonify({
        'status': 'ok',
        'data': {
            'server_time_unix': now,
            'server_time_iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
            'timezone': time.tzname[0] if time.tzname else '',
            'time_zone_config': time_zone_cfg,
            'ntp_server_config': ntp_cfg_server,
            'ntp_enabled_system': ntp_status.get('ntp_enabled_system'),
            'ntp_synchronized': ntp_status.get('ntp_synchronized'),
            'capabilities': {
                'timedatectl': shutil.which('timedatectl') is not None,
                'ntpdate': shutil.which('ntpdate') is not None,
            },
        },
    })


@bp.route('/api/time/set', methods=['POST'])
def set_time_settings():
    """Ustaw strefę czasową, datę serwera i opcjonalnie synchronizację NTP."""
    if not _is_time_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień do zmiany czasu'}), 403

    if not _database:
        return jsonify({'status': 'error', 'message': 'Brak bazy danych'}), 500

    payload = request.get_json(silent=True) or {}
    zone = str(payload.get('time_zone', '') or '').strip()
    date_text = str(payload.get('date', '') or '').strip()
    ntp_sync = _parse_bool(payload.get('ntp_sync', False), default=False)
    ntp_server = str(payload.get('ntp_server', 'pool.ntp.org') or 'pool.ntp.org').strip()

    if not _is_valid_time_zone(zone):
        return jsonify({'status': 'error', 'message': 'Nieprawidłowa strefa czasowa'}), 400
    if not date_text:
        return jsonify({'status': 'error', 'message': 'Brak daty'}), 400
    if ntp_sync and (not ntp_server or len(ntp_server) > 253):
        return jsonify({'status': 'error', 'message': 'Nieprawidłowy adres serwera NTP'}), 400

    try:
        date_value = datetime.datetime.strptime(date_text, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Nieprawidłowy format daty (YYYY-MM-DD)'}), 400

    process_tz_ok, process_tz_msg = _apply_process_timezone(zone)

    tz_ok, tz_msg = _set_system_timezone(zone)
    date_ok, date_msg = _set_system_date(date_value)
    sync_ok = None
    sync_msg = None
    if ntp_sync:
        sync_ok, sync_msg = _sync_time_with_ntp(ntp_server)

    config_saved = _database.set_config_many({
        'time_zone': zone,
        'time_ntp_server': ntp_server,
    })

    status = 'ok'
    if not process_tz_ok or not tz_ok or not date_ok or (ntp_sync and not sync_ok) or (not config_saved):
        status = 'partial'

    return jsonify({
        'status': status,
        'message': 'Zastosowano ustawienia czasu',
        'details': {
            'time_zone': zone,
            'process_time_zone_ok': process_tz_ok,
            'process_time_zone_msg': process_tz_msg,
            'time_zone_ok': tz_ok,
            'time_zone_msg': tz_msg,
            'date': date_text,
            'date_ok': date_ok,
            'date_msg': date_msg,
            'ntp_sync_requested': ntp_sync,
            'ntp_server': ntp_server,
            'sync_ok': sync_ok,
            'sync_msg': sync_msg,
            'config_saved': config_saved,
        },
    })


@bp.route('/api/db/reset-runtime', methods=['POST'])
def reset_runtime_data():
    """Wyczysc pomiary i alerty, pozostawiajac konfiguracje."""
    if not _database:
        return jsonify({'status': 'error', 'message': 'Brak bazy danych'}), 500
    if not request.is_json:
        return jsonify({'status': 'error', 'message': 'Wymagany Content-Type: application/json'}), 415
    if not _is_reset_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień do resetu danych'}), 403

    try:
        details = _database.reset_runtime_data()
        return jsonify({
            'status': 'ok',
            'message': 'Wyczyszczono statystyki (pomiary i alerty). Konfiguracja została zachowana.',
            'details': details,
        })
    except Exception as e:
        logging.getLogger(__name__).error('Blad resetu danych runtime: %s', e)
        return jsonify({'status': 'error', 'message': 'Nie udało się wyczyścić statystyk'}), 500


@bp.route('/api/db/reset-all', methods=['POST'])
def reset_all_data_and_config():
    """Wyczysc pomiary, alerty i konfiguracje (pelny reset)."""
    if not _database:
        return jsonify({'status': 'error', 'message': 'Brak bazy danych'}), 500
    if not request.is_json:
        return jsonify({'status': 'error', 'message': 'Wymagany Content-Type: application/json'}), 415
    if not _is_reset_access_allowed():
        return jsonify({'status': 'error', 'message': 'Brak uprawnień do resetu danych'}), 403

    try:
        details = _database.reset_all_data_and_config()
        with _weather_cache_lock:
            _weather_data_cache.clear()
            _weather_place_cache.clear()
            _weather_legacy_migration_fail_cache.clear()

        return jsonify({
            'status': 'ok',
            'message': 'Wyczyszczono całą bazę danych oraz konfigurację.',
            'details': details,
        })
    except Exception as e:
        logging.getLogger(__name__).error('Blad pelnego resetu bazy: %s', e)
        return jsonify({'status': 'error', 'message': 'Nie udało się wykonać pełnego resetu'}), 500


@bp.route('/api/weather')
def get_weather():
    """Pobierz bieżącą pogodę dla współrzędnych z ustawień (Open-Meteo)."""
    if not _database:
        return jsonify({'status': 'error', 'message': 'Brak bazy danych'}), 500

    lat_raw = (_database.get_config('weather_lat', '') or '').strip()
    lon_raw = (_database.get_config('weather_lon', '') or '').strip()

    if not lat_raw or not lon_raw:
        legacy_location = (_database.get_config('weather_location', '') or '').strip()
        if legacy_location:
            retry_after_sec = _get_legacy_migration_retry_after_sec(legacy_location)
            if retry_after_sec is not None:
                return jsonify({
                    'status': 'not_configured',
                    'message': 'Wykryto starą konfigurację weather_location. Ustaw współrzędne lat/lon w Ustawieniach.',
                    'retry_after_sec': retry_after_sec,
                }), 200

            geocoding_had_exception = False
            try:
                resolved = _resolve_coords_from_legacy_location(legacy_location)
            except Exception as exc:
                logging.getLogger(__name__).warning('Błąd migracji weather_location -> lat/lon: %s', exc)
                _mark_legacy_migration_failed(legacy_location, 'geocoding_exception')
                geocoding_had_exception = True
                resolved = None

            if resolved:
                lat, lon = resolved
                lat_raw = str(lat)
                lon_raw = str(lon)

                migration_saved = _database.set_config_many({
                    'weather_lat': lat_raw,
                    'weather_lon': lon_raw,
                })
                if not migration_saved:
                    logging.getLogger(__name__).error(
                        'Nie udało się atomowo zapisać migracji weather_location -> lat/lon'
                    )
                    return jsonify({
                        'status': 'error',
                        'message': 'Nie udało się zapisać współrzędnych po migracji weather_location. Sprawdź bazę danych.',
                    }), 500

                with _weather_cache_lock:
                    _weather_data_cache.clear()
                    _weather_place_cache.clear()
                    _weather_legacy_migration_fail_cache.pop(legacy_location, None)
                logging.getLogger(__name__).info(
                    'Zmigrowano weather_location="%s" -> weather_lat=%s weather_lon=%s',
                    legacy_location,
                    lat_raw,
                    lon_raw,
                )
            else:
                if not geocoding_had_exception:
                    _mark_legacy_migration_failed(legacy_location, 'geocoding_no_result')
                return jsonify({
                    'status': 'not_configured',
                    'message': 'Wykryto starą konfigurację weather_location. Ustaw współrzędne lat/lon w Ustawieniach.',
                    'retry_after_sec': _WEATHER_MIGRATION_RETRY_TTL_SEC,
                }), 200
        else:
            return jsonify({'status': 'not_configured', 'message': 'Ustaw współrzędne pogody (lat/lon) w Ustawieniach'}), 200

    try:
        lat = _parse_float_maybe_comma(lat_raw)
        lon = _parse_float_maybe_comma(lon_raw)
    except (ValueError, TypeError):
        return jsonify({'status': 'error', 'message': 'Nieprawidłowe współrzędne pogody'}), 400

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return jsonify({'status': 'error', 'message': 'Współrzędne pogody poza zakresem'}), 400

    location_key = f"{lat:.5f},{lon:.5f}"

    with _weather_cache_lock:
        cached_weather = _get_cached_weather(location_key)
    if cached_weather is not None:
        return jsonify({'status': 'ok', 'data': cached_weather, 'cached': True})

    try:
        weather_url = (
            'https://api.open-meteo.com/v1/forecast'
            f'?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto'
        )
        weather_data = _fetch_json(weather_url, timeout=8)
        place_data = _resolve_place_data(lat, lon, location_key)

        current = weather_data.get('current_weather') or {}
        if 'temperature' not in current or 'weathercode' not in current:
            return jsonify({'status': 'error', 'message': 'Brak danych pogodowych'}), 502

        payload = {
            'location': place_data.get('locality') or f'{lat:.5f}, {lon:.5f}',
            'location_label': place_data.get('location_label') or f'{lat:.5f}, {lon:.5f}',
            'municipality': place_data.get('municipality'),
            'region': place_data.get('region'),
            'country': place_data.get('country'),
            'lat': round(lat, 5),
            'lon': round(lon, 5),
            'temperature': current.get('temperature'),
            'weathercode': current.get('weathercode'),
            'windspeed': current.get('windspeed'),
            'time': current.get('time'),
        }

        with _weather_cache_lock:
            _weather_data_cache[location_key] = {
                'data': payload,
                'cached_at': time.time(),
            }

        return jsonify({
            'status': 'ok',
            'data': payload,
            'cached': False,
        })
    except Exception as e:
        logging.getLogger(__name__).warning('Błąd pobierania pogody: %s', e)
        return jsonify({'status': 'error', 'message': 'Nie udało się pobrać pogody'}), 502


@bp.route('/api/restart', methods=['POST'])
def restart_app():
    """Zrestartuj usluge systemd ecomax-monitor."""
    try:
        result = subprocess.run(
            ['sudo', 'systemctl', 'restart', 'ecomax-monitor'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return jsonify({'status': 'ok', 'message': 'Restart zlecony'})
            return jsonify({'status': 'error', 'message': result.stderr.strip() or 'Błąd restartu'}), 500
    except subprocess.TimeoutExpired:
            return jsonify({'status': 'error', 'message': 'Timeout - brak odpowiedzi systemd'}), 500
    except FileNotFoundError:
            return jsonify({'status': 'error', 'message': 'Polecenie systemctl niedostępne na tym systemie'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@bp.route('/api/ha')
def ha_sensor():
    """Endpoint dla Home Assistant REST sensor.
    Konfiguracja w HA:
      sensor:
        - platform: rest
          resource: http://<IP>:5050/api/ha
          name: ecoMAX
          json_attributes: [temp_co, temp_cwu, temp_back, temp_flue_gas,
                             boiler_power, fuel_stream, mode_name]
          value_template: '{{ value_json.mode_name }}'
    """
    if _connection and _connection.last_data:
        d = _connection.last_data.to_dict()
        return jsonify({
            'state':         d.get('mode_name', 'unknown'),
            'temp_co':       d.get('temp_co'),
            'temp_cwu':      d.get('temp_cwu'),
            'temp_back':     d.get('temp_back'),
            'temp_flue_gas': d.get('temp_flue_gas'),
            'temp_external': d.get('temp_external'),
            'boiler_power':  d.get('boiler_power'),
            'boiler_power_kw': d.get('boiler_power_kw'),
            'fan_power':     d.get('fan_power'),
            'fuel_stream':   d.get('fuel_stream'),
            'fuel_level':    d.get('fuel_level'),
            'pump_co':       bool(d.get('pump_co')),
            'pump_cwu':      bool(d.get('pump_cwu')),
            'pump_circulation': bool(d.get('pump_circulation')),
            'mode':          d.get('mode'),
            'mode_name':     d.get('mode_name'),
            'timestamp':     d.get('timestamp'),
        })
    return jsonify({'state': 'unavailable'}), 503


@bp.route('/metrics')
def prometheus_metrics():
    """Endpoint Prometheus (format text/plain).
    Konfiguracja w prometheus.yml:
      scrape_configs:
        - job_name: ecomax
          static_configs:
            - targets: ['<IP>:5050']
          metrics_path: /metrics
    """
    if not _connection or not _connection.last_data:
        return Response('# No data\n', mimetype='text/plain; version=0.0.4')

    d = _connection.last_data.to_dict()
    lines = ['# HELP ecomax_temp_co Temperatura zasilania CO (Celsius)',
             '# TYPE ecomax_temp_co gauge']

    def metric(name, value, help_text='', mtype='gauge'):
        if value is None:
            return
        lines.append(f'# HELP ecomax_{name} {help_text}')
        lines.append(f'# TYPE ecomax_{name} {mtype}')
        lines.append(f'ecomax_{name} {value}')

    metric('temp_co',        d.get('temp_co'),        'Temperatura CO zasilanie (C)')
    metric('temp_cwu',       d.get('temp_cwu'),       'Temperatura CWU (C)')
    metric('temp_back',      d.get('temp_back'),      'Temperatura powrotu (C)')
    metric('temp_flue_gas',  d.get('temp_flue_gas'),  'Temperatura spalin (C)')
    metric('temp_feeder',    d.get('temp_feeder'),    'Temperatura podajnika (C)')
    metric('temp_external',  d.get('temp_external'),  'Temperatura zewnetrzna (C)')
    metric('boiler_power',   d.get('boiler_power'),   'Moc kotla (%)')
    metric('boiler_power_kw',d.get('boiler_power_kw'),'Moc kotla (kW)')
    metric('fan_power',      d.get('fan_power'),      'Moc wentylatora (%)')
    metric('fuel_stream',    d.get('fuel_stream'),    'Zuzycie paliwa (kg/h)')
    metric('fuel_level',     d.get('fuel_level'),     'Poziom paliwa (%)')
    metric('pump_co',        int(bool(d.get('pump_co'))),    'Pompa CO (0/1)')
    metric('pump_cwu',       int(bool(d.get('pump_cwu'))),   'Pompa CWU (0/1)')
    metric('pump_circulation',int(bool(d.get('pump_circulation'))),'Pompa cyrkulacji (0/1)')
    metric('mode',           d.get('mode'),           'Tryb pracy kotla')

    return Response('\n'.join(lines) + '\n', mimetype='text/plain; version=0.0.4')


@bp.route('/api/setup-status')
def setup_status():
    """Sprawdz czy polaczenie z EW11A zostalo skonfigurowane."""
    configured = _database.is_setup_complete() if _database else False
    return jsonify({'configured': configured})


@bp.route('/api/logs')
def get_logs():
    """Pobierz ostatnie linie z pliku logu z paginacją.
    
    Query params:
        lines: ile linii pobrać (default: 100, max: 1000)
        offset: offset od końca (w liniach)
    """
    if not _is_logs_access_allowed():
        return jsonify({'status': 'error', 'message': 'Dostęp do logów zablokowany'}), 403

    lines = request.args.get('lines', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    # Validacja
    lines = min(max(lines, 1), 1000)
    offset = max(offset, 0)
    
    log_lines, total = read_log_lines(lines=lines, offset=offset)
    
    return jsonify({
        'status': 'ok',
        'data': log_lines,
        'total_lines': total,
        'lines_returned': len(log_lines),
        'offset': offset,
    })


@bp.route('/api/logs/download')
def download_logs():
    """Pobierz plik logu."""
    if not _is_logs_access_allowed():
        return jsonify({'status': 'error', 'message': 'Dostęp do logów zablokowany'}), 403

    log_path = get_log_file_path()
    
    if not log_path:
        return jsonify({'status': 'error', 'message': 'Brak pliku logu'}), 404
    
    try:
        filename = f"ecomax_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        return send_file(
            log_path,
            mimetype='text/plain',
            as_attachment=True,
            download_name=filename,
            max_age=0,
        )
    except Exception as e:
        logging.getLogger(__name__).error("Błąd pobierania logu: %s", e)
        return jsonify({'status': 'error', 'message': 'Błąd odczytu pliku logu'}), 500


@bp.route('/api/status')
def get_status():
    """Status polaczenia i aplikacji."""
    return jsonify({
        'uptime': time.time(),
        'connection': _connection.get_status() if _connection else None,
    })



