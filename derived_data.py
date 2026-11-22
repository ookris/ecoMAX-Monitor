"""Wartosci pochodne i korekty danych kotla zalezne od ustawien aplikacji.

Funkcje dzialaja zarowno na obiekcie BoilerData, jak i na slowniku
(np. wierszu z bazy albo wyniku BoilerData.to_dict()).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

CONFIG_FUEL_LEVEL_NOT_SET = 'fuel_level_not_set'
CONFIG_BOILER_NOMINAL_POWER_KW = 'boiler_nominal_power_kw'


def _get(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data.get(key)
    return getattr(data, key, None)


def _set(data: Any, key: str, value: Any) -> None:
    if isinstance(data, dict):
        data[key] = value
    else:
        setattr(data, key, value)


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_nominal_power(cfg: Mapping[str, Any]) -> float | None:
    """Moc nominalna kotla z ustawien (kW) albo None, gdy nieustawiona/niepoprawna."""
    raw = str(cfg.get(CONFIG_BOILER_NOMINAL_POWER_KW, '') or '').strip().replace(',', '.')
    if not raw:
        return None
    value = _as_number(raw)
    if value is None or value <= 0:
        return None
    return value


def is_fuel_level_not_set(cfg: Mapping[str, Any]) -> bool:
    return str(cfg.get(CONFIG_FUEL_LEVEL_NOT_SET, '0') or '0').strip() == '1'


def apply_config_overrides(data: Any, cfg: Mapping[str, Any]) -> Any:
    """Zastosuj korekty zalezne od konfiguracji. Modyfikuje `data` i je zwraca.

    1. Poziom paliwa nieustawiony w sterowniku -> fuel_level = None,
       fuel_level_not_set = True (UI pokazuje "nie ustawiono", alert fuel_low nie dziala).
    2. Moc kotla w % (boiler_power):
       - sterownik podaje obciazenie > 0 -> zrodlo 'controller',
       - sterownik podaje 0 przy mocy kW > 0 (np. ecoMAX 920P1) -> % liczony z kW
         i mocy nominalnej z ustawien (zrodlo 'calculated') albo None, gdy moc
         nominalna nie jest ustawiona,
       - kociol nie pracuje (kW = 0) -> wartosc ze sterownika bez zmian.
    """
    if data is None:
        return data

    fuel_not_set = is_fuel_level_not_set(cfg)
    _set(data, 'fuel_level_not_set', fuel_not_set)
    if fuel_not_set:
        _set(data, 'fuel_level', None)

    load = _as_number(_get(data, 'boiler_power'))
    if _get(data, 'boiler_power_source') == 'calculated':
        # Ponowne zastosowanie (np. na danych juz skorygowanych) - przelicz od nowa;
        # bez mocy kW wyliczonego % nie da sie potwierdzic.
        load = 0.0
        if _as_number(_get(data, 'boiler_power_kw')) is None:
            _set(data, 'boiler_power', None)
            load = None
    power_kw = _as_number(_get(data, 'boiler_power_kw'))
    source = None
    if load is not None and load > 0:
        source = 'controller'
    elif power_kw is not None and power_kw > 0:
        nominal = parse_nominal_power(cfg)
        if nominal is not None:
            _set(data, 'boiler_power', round(min(100.0, power_kw / nominal * 100.0)))
            source = 'calculated'
        else:
            _set(data, 'boiler_power', None)
    elif load is not None:
        source = 'controller'
    _set(data, 'boiler_power_source', source)

    return data
