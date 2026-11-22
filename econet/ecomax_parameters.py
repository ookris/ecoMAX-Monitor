"""Mapa parametrow sterownika ecoMAX typu P (ramka 0x31/0xB1).

Kolejnosc i nazwy zgodne z PyPlumIO (pyplumio/parameters/ecomax.py, ProductType.ECOMAX_P).
`scale` odpowiada polu `step` z PyPlumIO: wartosc = (surowa - offset) * scale.
Polskie etykiety opisuja znaczenie parametru; dokladne nazwy w menu sterownika moga sie roznic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EcomaxParameterInfo:
    index: int
    name: str
    label: str
    group: str
    unit: str | None = None
    scale: float = 1.0
    offset: int = 0

    @property
    def key(self) -> str:
        return self.name.upper()

    def to_display(self, raw: Any) -> float | int | None:
        if raw is None:
            return None
        try:
            raw_int = int(float(raw))
        except (TypeError, ValueError):
            return None
        value = (raw_int - self.offset) * self.scale
        return round(value, 3) if self.scale != 1 else int(value)


_G_POWER = 'Moc i nadmuch'
_G_FEED = 'Podawanie paliwa'
_G_REG = 'Regulacja'
_G_GRATE = 'Ruszt'
_G_KINDLING = 'Rozpalanie'
_G_STAB = 'Stabilizacja'
_G_SUPERVISION = 'Nadzór'
_G_BURN_OFF = 'Wygaszanie'
_G_CLEAN = 'Czyszczenie'
_G_LAMBDA = 'Sonda lambda'
_G_FUEL = 'Paliwo'
_G_FEEDER2 = 'Podajnik dodatkowy'
_G_CO = 'Obieg CO'
_G_THERM = 'Termostat'
_G_SAFETY = 'Zabezpieczenia'
_G_CWU = 'CWU'
_G_SUMMER = 'Lato/zima'
_G_CIRC = 'Cyrkulacja'
_G_BUFFER = 'Bufor'

_PCT = '%'
_C = '°C'
_S = 's'
_MIN = 'min'
_KW = 'kW'

# (name, label, group, unit, scale, offset)
_DEFINITIONS: tuple[tuple, ...] = (
    ('airflow_power_100', 'Nadmuch – moc 100%', _G_POWER, _PCT),
    ('airflow_power_50', 'Nadmuch – moc 50%', _G_POWER, _PCT),
    ('airflow_power_30', 'Nadmuch – moc 30%', _G_POWER, _PCT),
    ('boiler_power_100', 'Moc kotła – poziom 100%', _G_POWER, _KW),
    ('boiler_power_50', 'Moc kotła – poziom 50%', _G_POWER, _KW),
    ('boiler_power_30', 'Moc kotła – poziom 30%', _G_POWER, _KW),
    ('max_fan_boiler_power', 'Maks. nadmuch przy mocy kotła', _G_POWER, _PCT),
    ('min_fan_boiler_power', 'Min. nadmuch przy mocy kotła', _G_POWER, _PCT),
    ('fuel_feeding_work_100', 'Czas podawania – moc 100%', _G_FEED, _S),
    ('fuel_feeding_work_50', 'Czas podawania – moc 50%', _G_FEED, _S),
    ('fuel_feeding_work_30', 'Czas podawania – moc 30%', _G_FEED, _S),
    ('fuel_feeding_pause_100', 'Przerwa podawania – moc 100%', _G_FEED, _S),
    ('fuel_feeding_pause_50', 'Przerwa podawania – moc 50%', _G_FEED, _S),
    ('fuel_feeding_pause_30', 'Przerwa podawania – moc 30%', _G_FEED, _S),
    ('cycle_duration', 'Czas cyklu pracy', _G_FEED, _S),
    ('h2_hysteresis', 'Histereza H2', _G_REG, _C),
    ('h1_hysteresis', 'Histereza H1', _G_REG, _C),
    ('heating_hysteresis', 'Histereza kotła', _G_REG, _C),
    ('fuzzy_logic', 'Logika rozmyta (Fuzzy Logic)', _G_REG, None),
    ('min_fuzzy_logic_power', 'Fuzzy Logic – min. moc', _G_REG, _PCT),
    ('max_fuzzy_logic_power', 'Fuzzy Logic – maks. moc', _G_REG, _PCT),
    ('min_boiler_power', 'Min. moc kotła', _G_POWER, _KW),
    ('max_boiler_power', 'Maks. moc kotła', _G_POWER, _KW),
    ('min_fan_power', 'Min. moc wentylatora', _G_POWER, _PCT),
    ('max_fan_power', 'Maks. moc wentylatora', _G_POWER, _PCT),
    ('reduction_airflow_temp', 'Temperatura redukcji nadmuchu', _G_POWER, _C),
    ('fan_power_gain', 'Wzmocnienie nadmuchu', _G_POWER, _PCT),
    ('fuzzy_logic_fuel_flow_correction', 'Fuzzy Logic – korekta strumienia paliwa', _G_REG, _PCT),
    ('fuel_flow_correction', 'Korekta strumienia paliwa', _G_FEED, _PCT),
    ('airflow_correction_100', 'Korekta nadmuchu – moc 100%', _G_POWER, _PCT),
    ('feeder_correction_100', 'Korekta podawania – moc 100%', _G_FEED, _PCT),
    ('airflow_correction_50', 'Korekta nadmuchu – moc 50%', _G_POWER, _PCT),
    ('feeder_correction_50', 'Korekta podawania – moc 50%', _G_FEED, _PCT),
    ('airflow_correction_30', 'Korekta nadmuchu – moc 30%', _G_POWER, _PCT),
    ('feeder_correction_30', 'Korekta podawania – moc 30%', _G_FEED, _PCT),
    ('grate_airflow_power', 'Ruszt – moc nadmuchu', _G_GRATE, _PCT),
    ('grate_heating_hysteresis', 'Ruszt – histereza kotła', _G_GRATE, _C),
    ('grate_fan_work', 'Ruszt – czas pracy wentylatora', _G_GRATE, _S),
    ('grate_fan_pause', 'Ruszt – przerwa wentylatora', _G_GRATE, _MIN),
    ('grate_heating_temp', 'Ruszt – temperatura zadana kotła', _G_GRATE, _C),
    ('grate_fuel_detection_time', 'Ruszt – czas detekcji braku paliwa', _G_GRATE, _MIN),
    ('kindling_airflow_power', 'Rozpalanie – nadmuch', _G_KINDLING, _PCT),
    ('kindling_low_airflow_power', 'Rozpalanie – nadmuch niski', _G_KINDLING, _PCT),
    ('kindling_airflow_delay', 'Rozpalanie – opóźnienie nadmuchu', _G_KINDLING, _S),
    ('kindling_test_time', 'Rozpalanie – czas testu', _G_KINDLING, _S),
    ('kindling_feeder_work', 'Rozpalanie – czas podawania', _G_KINDLING, _S),
    ('kindling_feeder_dose', 'Rozpalanie – dawka paliwa', _G_KINDLING, 'g'),
    ('kindling_time', 'Rozpalanie – czas rozpalania', _G_KINDLING, _MIN),
    ('warming_up_time', 'Rozpalanie – czas rozgrzewania', _G_KINDLING, _MIN),
    ('kindling_finish_exhaust_temp', 'Rozpalanie – temp. spalin końca rozpalania', _G_KINDLING, _C),
    ('kindling_finish_threshold_temp', 'Rozpalanie – próg temperatury końca', _G_KINDLING, _C),
    ('kindling_fumes_delta_temp', 'Rozpalanie – przyrost temp. spalin', _G_KINDLING, _C),
    ('kindling_delta_temp', 'Rozpalanie – delta temperatury', _G_KINDLING, _C),
    ('kindling_min_power_time', 'Rozpalanie – czas pracy z mocą min.', _G_KINDLING, _MIN),
    ('stabilization_time', 'Stabilizacja – czas', _G_STAB, _MIN),
    ('stabilization_airflow_power', 'Stabilizacja – nadmuch', _G_STAB, _PCT),
    ('supervision_time', 'Nadzór – czas', _G_SUPERVISION, _MIN),
    ('supervision_feeder_work', 'Nadzór – czas podawania', _G_SUPERVISION, _S),
    ('supervision_feeder_dose', 'Nadzór – dawka paliwa', _G_SUPERVISION, 'g'),
    ('supervision_feeder_pause', 'Nadzór – przerwa podawania', _G_SUPERVISION, _MIN),
    ('supervision_cycle_duration', 'Nadzór – czas cyklu', _G_SUPERVISION, _S),
    ('supervision_airflow_power', 'Nadzór – nadmuch', _G_SUPERVISION, _PCT),
    ('supervision_fan_pause', 'Nadzór – przerwa wentylatora', _G_SUPERVISION, _MIN),
    ('supervision_fan_work', 'Nadzór – praca wentylatora', _G_SUPERVISION, _S),
    ('increase_fan_support_mode', 'Wspomaganie nadmuchu', _G_SUPERVISION, None),
    ('burning_off_max_time', 'Wygaszanie – maks. czas', _G_BURN_OFF, _MIN),
    ('burning_off_min_time', 'Wygaszanie – min. czas', _G_BURN_OFF, _MIN),
    ('burning_off_time', 'Wygaszanie – czas', _G_BURN_OFF, None),
    ('burning_off_airflow_power', 'Wygaszanie – nadmuch', _G_BURN_OFF, _PCT),
    ('burning_off_fan_work', 'Wygaszanie – praca wentylatora', _G_BURN_OFF, None),
    ('burning_off_fan_pause', 'Wygaszanie – przerwa wentylatora', _G_BURN_OFF, None),
    ('start_burning_off', 'Wygaszanie – próg startu', _G_BURN_OFF, _PCT),
    ('stop_burning_off', 'Wygaszanie – próg końca', _G_BURN_OFF, _PCT),
    ('cleaning_begin_time', 'Czyszczenie – czas na początku', _G_CLEAN, None),
    ('burning_off_cleaning_time', 'Czyszczenie – czas przy wygaszaniu', _G_CLEAN, None),
    ('cleaning_airflow_power', 'Czyszczenie – nadmuch', _G_CLEAN, _PCT),
    ('warming_up_pause_time', 'Rozgrzewanie – przerwa', _G_KINDLING, None),
    ('warming_up_cycle_time', 'Rozgrzewanie – czas cyklu', _G_KINDLING, None),
    ('remind_time', 'Czas przypomnienia', _G_CLEAN, None),
    ('lambda_control', 'Sterowanie sondą lambda', _G_LAMBDA, None),
    ('lambda_correction_range', 'Lambda – zakres korekty', _G_LAMBDA, None),
    ('oxygen_100', 'Tlen – moc 100%', _G_LAMBDA, None),
    ('oxygen_50', 'Tlen – moc 50%', _G_LAMBDA, None),
    ('oxygen_30', 'Tlen – moc 30%', _G_LAMBDA, None),
    ('fuzzy_logic_oxygen_correction', 'Fuzzy Logic – korekta tlenu', _G_LAMBDA, None),
    ('max_fuel_flow', 'Maks. strumień paliwa', _G_FUEL, 'kg/h', 0.2),
    ('feeder_calibration', 'Kalibracja podajnika', _G_FUEL, None),
    ('fuel_tank_capacity', 'Pojemność zasobnika', _G_FUEL, 'kg', 50.0),
    ('fuel_calorific_value', 'Kaloryczność paliwa', _G_FUEL, 'kWh/kg', 0.1),
    ('fuel_detection_time', 'Czas detekcji braku paliwa', _G_FUEL, _MIN),
    ('fuel_detection_exhaust_temp', 'Temp. spalin detekcji braku paliwa', _G_FUEL, _C),
    ('schedule_feeder_2', 'Podajnik 2 – harmonogram', _G_FEEDER2, None),
    ('feed2_h1', 'Podajnik 2 – godzina 1', _G_FEEDER2, None),
    ('feed2_h2', 'Podajnik 2 – godzina 2', _G_FEEDER2, None),
    ('feed2_h3', 'Podajnik 2 – godzina 3', _G_FEEDER2, None),
    ('feed2_h4', 'Podajnik 2 – godzina 4', _G_FEEDER2, None),
    ('feed2_work', 'Podajnik 2 – czas pracy', _G_FEEDER2, None),
    ('feed2_pause', 'Podajnik 2 – przerwa', _G_FEEDER2, None),
    ('heating_target_temp', 'Temperatura zadana CO', _G_CO, _C),
    ('min_heating_target_temp', 'Min. temperatura zadana CO', _G_CO, _C),
    ('max_heating_target_temp', 'Maks. temperatura zadana CO', _G_CO, _C),
    ('heating_pump_enable_temp', 'Temperatura załączenia pompy CO', _G_CO, _C),
    ('pause_heating_for_water_heater', 'Postój pompy CO przy ładowaniu CWU', _G_CO, _MIN),
    ('thermostat_pause', 'Termostat – przerwa', _G_THERM, None),
    ('thermostat_work', 'Termostat – praca', _G_THERM, None),
    ('increase_heating_temp_for_water_heater', 'Podwyższenie temp. kotła dla CWU', _G_CWU, _C),
    ('weather_control', 'Sterowanie pogodowe', _G_CO, None),
    ('heating_curve', 'Krzywa grzewcza', _G_CO, None, 0.1),
    ('heating_curve_shift', 'Przesunięcie krzywej grzewczej', _G_CO, _C, 1.0, 20),
    ('weather_factor', 'Współczynnik temperatury pokojowej', _G_CO, None),
    ('thermostat_operation', 'Termostat – działanie', _G_THERM, None),
    ('thermostat_mode', 'Termostat – tryb', _G_THERM, None),
    ('thermostat_decrease_target_temp', 'Termostat – obniżenie temp. zadanej', _G_THERM, _C),
    ('disable_pump_on_thermostat', 'Wyłączanie pompy przez termostat', _G_THERM, None),
    ('boiler_alert_temp', 'Temperatura alarmowa kotła', _G_SAFETY, _C),
    ('max_feeder_temp', 'Maks. temperatura podajnika', _G_SAFETY, _C),
    ('external_boiler_temp', 'Temperatura kotła zewnętrznego', _G_SAFETY, _C),
    ('alert_notify', 'Powiadomienie alarmowe', _G_SAFETY, None),
    ('pump_hysteresis', 'Histereza pompy', _G_CO, _C),
    ('water_heater_target_temp', 'Temperatura zadana CWU', _G_CWU, _C),
    ('min_water_heater_target_temp', 'Min. temperatura zadana CWU', _G_CWU, _C),
    ('max_water_heater_target_temp', 'Maks. temperatura zadana CWU', _G_CWU, _C),
    ('water_heater_work_mode', 'Tryb pracy CWU', _G_CWU, None),
    ('water_heater_hysteresis', 'Histereza CWU', _G_CWU, _C),
    ('water_heater_disinfection', 'Dezynfekcja CWU', _G_CWU, None),
    ('summer_mode', 'Tryb lato', _G_SUMMER, None),
    ('summer_mode_enable_temp', 'Temperatura włączenia trybu lato', _G_SUMMER, _C),
    ('summer_mode_disable_temp', 'Temperatura wyłączenia trybu lato', _G_SUMMER, _C),
    ('water_heater_work_extension', 'Wydłużenie pracy CWU', _G_CWU, _MIN),
    ('circulation_control', 'Sterowanie cyrkulacją', _G_CIRC, None),
    ('circulation_pause', 'Cyrkulacja – przerwa', _G_CIRC, _MIN),
    ('circulation_work', 'Cyrkulacja – praca', _G_CIRC, _MIN),
    ('circulation_start_temp', 'Cyrkulacja – temperatura startu', _G_CIRC, _C),
    ('buffer_control', 'Sterowanie buforem', _G_BUFFER, None),
    ('max_buffer_temp', 'Bufor – maks. temperatura', _G_BUFFER, _C),
    ('min_buffer_temp', 'Bufor – min. temperatura', _G_BUFFER, _C),
    ('buffer_hysteresis', 'Bufor – histereza', _G_BUFFER, _C),
    ('buffer_load_start', 'Bufor – start ładowania', _G_BUFFER, _C),
    ('buffer_load_stop', 'Bufor – koniec ładowania', _G_BUFFER, _C),
)


def _build() -> tuple[EcomaxParameterInfo, ...]:
    items = []
    for index, definition in enumerate(_DEFINITIONS):
        name, label, group = definition[0], definition[1], definition[2]
        unit = definition[3] if len(definition) > 3 else None
        scale = definition[4] if len(definition) > 4 else 1.0
        offset = definition[5] if len(definition) > 5 else 0
        items.append(EcomaxParameterInfo(index, name, label, group, unit, scale, offset))
    return tuple(items)


ECOMAX_P_PARAMETERS: tuple[EcomaxParameterInfo, ...] = _build()


def get_parameter_info(index: int) -> EcomaxParameterInfo | None:
    """Opis parametru ecoMAX P o danym indeksie albo None."""
    if 0 <= index < len(ECOMAX_P_PARAMETERS):
        return ECOMAX_P_PARAMETERS[index]
    return None


def parameter_key(index: int) -> str:
    """Klucz parametru do historii zmian (np. KINDLING_TIME) albo IDX_<n>."""
    info = get_parameter_info(index)
    return info.key if info else f'IDX_{index}'
