"""Parametry mieszaczy sterownika ecoMAX typu P (ramka 0x32 -> 0xB2, zapis 0x34).

Kolejnosc i nazwy zgodne z PyPlumIO (pyplumio/parameters/mixer.py, ProductType.ECOMAX_P).
Format odpowiedzi 0xB2: [?][start][count][mixers] + mixers x count x [value][min][max];
zakres FF/FF oznacza parametr niedostepny.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MixerParameterInfo:
    index: int
    name: str
    label: str
    unit: str | None = None
    scale: float = 1.0
    offset: int = 0
    widget: str = 'number'
    value_map: dict[int, str] | None = field(default=None)

    @property
    def key(self) -> str:
        return f'MIXER_{self.name.upper()}'

    @property
    def display_offset(self) -> int:
        """Alias zgodny z BoilerControlParameter (formatowanie zakresow w API)."""
        return self.offset

    def to_display(self, raw: Any) -> float | int | None:
        if raw is None:
            return None
        try:
            raw_int = int(float(raw))
        except (TypeError, ValueError):
            return None
        value = (raw_int - self.offset) * self.scale
        return round(value, 3) if self.scale != 1 else int(value)


_SWITCH = {0: 'Wyłączone', 1: 'Włączone'}

MIXER_PARAMETERS: tuple[MixerParameterInfo, ...] = (
    MixerParameterInfo(0, 'mixer_target_temp', 'Temperatura zadana mieszacza', '°C'),
    MixerParameterInfo(1, 'min_target_temp', 'Min. temperatura zadana', '°C'),
    MixerParameterInfo(2, 'max_target_temp', 'Maks. temperatura zadana', '°C'),
    MixerParameterInfo(3, 'thermostat_decrease_target_temp', 'Obniżenie temperatury od termostatu', '°C'),
    MixerParameterInfo(4, 'weather_control', 'Sterowanie pogodowe', widget='switch', value_map=_SWITCH),
    MixerParameterInfo(5, 'heating_curve', 'Krzywa grzewcza', scale=0.1),
    MixerParameterInfo(6, 'heating_curve_shift', 'Przesunięcie krzywej grzewczej', '°C', offset=20),
    MixerParameterInfo(7, 'weather_factor', 'Współczynnik temperatury pokojowej'),
    MixerParameterInfo(8, 'work_mode', 'Obsługa mieszacza', widget='select', value_map={
        0: 'Wyłączony', 1: 'Grzanie CO', 2: 'Grzanie podłogowe', 3: 'Tylko pompa',
    }),
    MixerParameterInfo(9, 'mixer_input_dead_zone', 'Strefa nieczułości mieszacza', '°C', scale=0.1),
    MixerParameterInfo(10, 'thermostat_operation', 'Praca z termostatem pokojowym', widget='switch', value_map=_SWITCH),
    MixerParameterInfo(11, 'thermostat_mode', 'Tryb termostatu'),
    MixerParameterInfo(12, 'disable_pump_on_thermostat', 'Wyłączanie pompy przez termostat', widget='switch',
                       value_map=_SWITCH),
    MixerParameterInfo(13, 'summer_work', 'Praca w trybie lato', widget='switch', value_map=_SWITCH),
)

MIXER_PARAMETERS_BY_KEY: dict[str, MixerParameterInfo] = {p.key: p for p in MIXER_PARAMETERS}


def get_mixer_parameter_info(index: int) -> MixerParameterInfo | None:
    if 0 <= index < len(MIXER_PARAMETERS):
        return MIXER_PARAMETERS[index]
    return None


def parse_mixer_parameters_payload(payload: bytes) -> dict[int, dict[int, dict[str, Any]]]:
    """Parsuj payload 0xB2 do {mieszacz: {indeks: {available, value, min_value, max_value}}}."""
    if not payload or len(payload) < 4:
        return {}
    start, count, mixers = payload[1], payload[2], payload[3]
    offset = 4
    result: dict[int, dict[int, dict[str, Any]]] = {}
    for mixer_index in range(mixers):
        params: dict[int, dict[str, Any]] = {}
        for index in range(start, start + count):
            if offset + 3 > len(payload):
                break
            value, min_value, max_value = payload[offset], payload[offset + 1], payload[offset + 2]
            offset += 3
            # Zasady jak dla parametrow ecoMAX (0xB1): min = max = FF -> niedostepny,
            # wartosc FF przy poprawnym zakresie -> brak wartosci.
            if min_value == 0xFF and max_value == 0xFF:
                params[index] = {'available': False, 'value': None, 'min_value': None, 'max_value': None}
            else:
                params[index] = {'available': True, 'value': value if value != 0xFF else None,
                                 'min_value': min_value, 'max_value': max_value}
        result[mixer_index] = params
    return result
