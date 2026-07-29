"""Wspólne definicje czujników i kafelków dashboardu."""

SENSOR_DEFINITIONS = (
    {'key': 'temp_co', 'label': 'Temp. CO (zasilanie)', 'tile_id': 'tile-temp-co'},
    {'key': 'temp_cwu', 'label': 'Temp. CWU', 'tile_id': 'tile-temp-cwu'},
    {'key': 'temp_back', 'label': 'Temp. powrotu', 'tile_id': 'tile-temp-back'},
    {'key': 'temp_feeder', 'label': 'Temp. podajnika', 'tile_id': 'tile-temp-feeder'},
    {'key': 'temp_flue_gas', 'label': 'Temp. spalin', 'tile_id': 'tile-temp-flue'},
    {'key': 'temp_external', 'label': 'Czujnik zewnętrzny', 'tile_id': 'tile-temp-external'},
    {'key': 'temp_upper_buffer', 'label': 'Bufor górny', 'tile_id': 'tile-temp-upper-buffer'},
    {'key': 'temp_lower_buffer', 'label': 'Bufor dolny', 'tile_id': 'tile-temp-lower-buffer'},
)

SENSOR_KEYS = tuple(item['key'] for item in SENSOR_DEFINITIONS)

DASHBOARD_TILE_DEFINITIONS = (
    SENSOR_DEFINITIONS
    +
    (
    {'key': 'boiler_power', 'label': 'Moc kotła', 'tile_id': 'tile-power'},
    {'key': 'fan_power', 'label': 'Wentylator', 'tile_id': 'tile-fan'},
    {'key': 'flame', 'label': 'Płomień', 'tile_id': 'tile-flame'},
    {'key': 'fuel_stream', 'label': 'Paliwo', 'tile_id': 'tile-fuel'},
    {'key': 'efficiency', 'label': 'Sprawność obiegu', 'tile_id': 'tile-efficiency'},
    {'key': 'cost_day', 'label': 'Koszt dzienny', 'tile_id': 'tile-cost'},
    )
)

DASHBOARD_TILE_KEYS = tuple(item['key'] for item in DASHBOARD_TILE_DEFINITIONS)

DEFAULT_VISIBLE_SENSORS = ','.join(DASHBOARD_TILE_KEYS)
