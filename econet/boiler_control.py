"""Katalog parametrów sterowania kotłem (MVP)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BoilerControlParameter:
    key: str
    label: str
    description: str
    group: str
    index: int
    min_value: int
    max_value: int
    step: int
    unit: str | None
    widget: str
    ha_entity_id: str
    ha_entity_type: str
    ui_profile: str = "installer"
    value_map: dict[int, str] | None = None
    live_data_key: str | None = None
    # Wartosc wyswietlana = (surowa - display_offset) * scale.
    # Sterownik przyjmuje i zwraca wylacznie wartosci surowe (0-255).
    scale: float = 1.0
    display_offset: int = 0

    def to_display(self, raw: Any) -> float | int | None:
        """Zamien surowa wartosc sterownika na wartosc wyswietlana."""
        if raw is None:
            return None
        try:
            raw_int = int(float(raw))
        except (TypeError, ValueError):
            return None
        value = (raw_int - self.display_offset) * self.scale
        return round(value, 3) if self.scale != 1 else int(value)


def _switch_map(off: str = "Wyłączone", on: str = "Włączone") -> dict[int, str]:
    return {0: off, 1: on}


def _cwu_work_mode_map() -> dict[int, str]:
    """Tryb pracy CWU (PyPlumIO water_heater_work_mode, sterownik: zakres 0-2).

    Nieznane wartości są obsługiwane po stronie UI jako "Opcja <n>".
    """
    return {
        0: "Wyłączony",
        1: "Priorytet CWU",
        2: "Bez priorytetu",
    }


def _thermostat_boiler_mode_map() -> dict[int, str]:
    return {
        0: "Bez wpływu termostatu",
        1: "Termostat redukuje",
        2: "Termostat wyłącza",
    }


# Indeksy pochodzą z analizy firmware opisanej w PLAN-boiler-control.md.
# W MVP udostępniamy tylko bezpieczne i najczęściej używane ustawienia użytkownika.
PARAMETERS: tuple[BoilerControlParameter, ...] = (
    BoilerControlParameter(
        key="CO_TEMP_SET",
        label="Temperatura zadana CO",
        description="Docelowa temperatura obiegu centralnego ogrzewania.",
        group="Kocioł",
        index=98,
        min_value=40,
        max_value=85,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.co_temp_set",
        ha_entity_type="number",
        ui_profile="user",
        live_data_key="temp_co_set",
    ),
    BoilerControlParameter(
        key="MIN_SET_CO_TEMP",
        label="Minimalna temperatura CO",
        description="Dolna granica możliwej temperatury zadanej CO.",
        group="Kocioł",
        index=99,
        min_value=30,
        max_value=80,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.min_set_co_temp",
        ha_entity_type="number",
        ui_profile="service",
    ),
    BoilerControlParameter(
        key="MAX_SET_CO_TEMP",
        label="Maksymalna temperatura CO",
        description="Górna granica możliwej temperatury zadanej CO.",
        group="Kocioł",
        index=100,
        min_value=30,
        max_value=90,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.max_set_co_temp",
        ha_entity_type="number",
        ui_profile="service",
    ),
    BoilerControlParameter(
        key="FUZZY_LOGIC",
        label="Logika rozmyta (Fuzzy Logic)",
        description="Automatyczny dobór mocy palnika przez algorytm Fuzzy Logic zamiast trybu standardowego.",
        group="Tryby pracy",
        index=18,
        min_value=0,
        max_value=1,
        step=1,
        unit=None,
        widget="switch",
        ha_entity_id="switch.fuzzy_logic",
        ha_entity_type="switch",
        ui_profile="service",
        value_map=_switch_map("Wyłączona", "Włączona"),
    ),
    BoilerControlParameter(
        key="CWU_SET_TEMP",
        label="Temperatura zadana CWU",
        description="Docelowa temperatura ciepłej wody użytkowej.",
        group="CWU",
        index=119,
        min_value=25,
        max_value=70,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.cwu_set_temp",
        ha_entity_type="number",
        ui_profile="user",
        live_data_key="temp_cwu_set",
    ),
    BoilerControlParameter(
        key="MIN_CWU_SET_TEMP",
        label="Minimalna temperatura CWU",
        description="Dolna granica temperatury zadanej CWU.",
        group="CWU",
        index=120,
        min_value=20,
        max_value=55,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.min_cwu_set_temp",
        ha_entity_type="number",
        ui_profile="service",
    ),
    BoilerControlParameter(
        key="MAX_CWU_SET_TEMP",
        label="Maksymalna temperatura CWU",
        description="Górna granica temperatury zadanej CWU.",
        group="CWU",
        index=121,
        min_value=25,
        max_value=80,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.max_cwu_set_temp",
        ha_entity_type="number",
        ui_profile="service",
    ),
    BoilerControlParameter(
        key="PUMP_HYSTERESIS",
        label="Histereza pompy",
        description="Różnica temperatury dla załączania/wyłączania pompy.",
        group="Pompy i obiegi",
        index=118,
        min_value=1,
        max_value=20,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.pump_hysteresis",
        ha_entity_type="number",
        ui_profile="installer",
    ),
    BoilerControlParameter(
        key="CWU_HYSTERESIS",
        label="Histereza CWU",
        description="Różnica temperatury dla dogrzewania CWU.",
        group="CWU",
        index=123,
        min_value=1,
        max_value=30,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.cwu_hysteresis",
        ha_entity_type="number",
        ui_profile="user",
    ),
    BoilerControlParameter(
        key="CWU_WORK_MODE",
        label="Tryb pracy CWU",
        description="Tryb przygotowania ciepłej wody użytkowej.",
        group="CWU",
        index=122,
        min_value=0,
        max_value=2,
        step=1,
        unit=None,
        widget="select",
        ha_entity_id="select.cwu_work_mode",
        ha_entity_type="select",
        ui_profile="installer",
        value_map=_cwu_work_mode_map(),
    ),
    BoilerControlParameter(
        key="WEATHER_CONTROL",
        label="Sterowanie pogodowe",
        description="Temperatura zadana CO wyliczana z krzywej grzewczej i czujnika zewnętrznego.",
        group="Tryby pracy",
        index=106,
        min_value=0,
        max_value=1,
        step=1,
        unit=None,
        widget="switch",
        ha_entity_id="switch.weather_control",
        ha_entity_type="switch",
        ui_profile="installer",
        value_map=_switch_map("Wyłączone", "Włączone"),
    ),
    BoilerControlParameter(
        key="HEATING_CURVE",
        label="Krzywa grzewcza",
        description="Nachylenie krzywej grzewczej (używane przy sterowaniu pogodowym).",
        group="Tryby pracy",
        index=107,
        min_value=1,
        max_value=40,
        step=1,
        unit=None,
        widget="number",
        ha_entity_id="number.heating_curve",
        ha_entity_type="number",
        ui_profile="installer",
        scale=0.1,
    ),
    BoilerControlParameter(
        key="HEATING_CURVE_SHIFT",
        label="Przesunięcie krzywej grzewczej",
        description="Równoległe przesunięcie krzywej grzewczej (sterowanie pogodowe).",
        group="Tryby pracy",
        index=108,
        min_value=0,
        max_value=40,
        step=1,
        unit="°C",
        widget="number",
        ha_entity_id="number.heating_curve_shift",
        ha_entity_type="number",
        ui_profile="installer",
        display_offset=20,
    ),
    BoilerControlParameter(
        key="TERM_BOILER_MODE",
        label="Wpływ termostatu pokojowego",
        description="Jak termostat pokojowy ma wpływać na pracę kotła.",
        group="Tryby pracy",
        index=110,
        min_value=0,
        max_value=2,
        step=1,
        unit=None,
        widget="select",
        ha_entity_id="select.thermostat_boiler_mode",
        ha_entity_type="select",
        ui_profile="service",
        value_map=_thermostat_boiler_mode_map(),
    ),
    BoilerControlParameter(
        key="CWU_DISINFECTION",
        label="Dezynfekcja CWU",
        description="Włącza cykl dezynfekcji zasobnika CWU.",
        group="CWU",
        index=124,
        min_value=0,
        max_value=1,
        step=1,
        unit=None,
        widget="switch",
        ha_entity_id="switch.cwu_disinfection",
        ha_entity_type="switch",
        ui_profile="installer",
        value_map=_switch_map("Wyłączona", "Włączona"),
    ),
    BoilerControlParameter(
        key="AUTO_SUMMER",
        label="Automatyczny tryb lato",
        description="Automatyczne przełączanie trybu lato/zima zależnie od temperatury.",
        group="Tryby pracy",
        index=125,
        min_value=0,
        max_value=1,
        step=1,
        unit=None,
        widget="switch",
        ha_entity_id="switch.auto_summer",
        ha_entity_type="switch",
        ui_profile="user",
        value_map=_switch_map("Wyłączony", "Włączony"),
    ),
    BoilerControlParameter(
        key="ALARM_NOTIF",
        label="Powiadomienia alarmowe",
        description="Włącza/wyłącza alarmowe powiadomienia dźwiękowe i systemowe.",
        group="Alarmy",
        index=117,
        min_value=0,
        max_value=1,
        step=1,
        unit=None,
        widget="switch",
        ha_entity_id="switch.alarm_notifications",
        ha_entity_type="switch",
        ui_profile="user",
        value_map=_switch_map("Wyłączone", "Włączone"),
    ),
    BoilerControlParameter(
        key="FUEL_CALORIFIC_VALUE",
        label="Kaloryczność paliwa",
        description="Wartość opałowa paliwa ustawiona w sterowniku; na jej podstawie sterownik szacuje moc kotła w kW.",
        group="Paliwo",
        index=88,
        min_value=1,
        max_value=250,
        step=1,
        unit="kWh/kg",
        widget="number",
        ha_entity_id="number.fuel_calorific_value",
        ha_entity_type="number",
        ui_profile="service",
        scale=0.1,
    ),
)

# Indeks parametru kalorycznosci paliwa (uzywany takze poza katalogiem sterowania).
FUEL_CALORIFIC_VALUE_INDEX = 88

PARAMETERS_BY_KEY: dict[str, BoilerControlParameter] = {item.key: item for item in PARAMETERS}


def format_parameter_value(param: BoilerControlParameter, value: Any) -> str:
    if value is None:
        return "--"

    try:
        int_value = int(value)
    except (TypeError, ValueError):
        return str(value)

    if param.value_map and int_value in param.value_map:
        return param.value_map[int_value]

    display = param.to_display(int_value)
    if display is None:
        return str(value)
    text = f"{display:g}".replace('.', ',') if isinstance(display, float) else str(display)
    if param.unit:
        return f"{text} {param.unit}"

    return text
