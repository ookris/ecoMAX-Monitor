/**
 * ecoMAX Monitor - Dashboard
 * WebSocket real-time + Chart.js wykresy
 */

// ---- Połączenie WebSocket ----
const socket = io('/', {
    path: '/socket.io',
    transports: ['polling'],
    upgrade: false,
    rememberUpgrade: false,
    withCredentials: false,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 15000,
    timeout: 8000,
});

// Po restarcie serwera stara sesja jest nieprawidlowa.
// Socket.IO v4 automatycznie tworzy nowa sesje po odebraniu bledu sesji.
socket.io.on('error', (err) => {
    const message = String(
        (err && (err.message || err.description || err.reason || err.data)) || err || ''
    ).toLowerCase();
    if (!message.includes('invalid session')) {
        return;
    }

    const currentQuery = socket.io.opts.query;
    if (!currentQuery || typeof currentQuery !== 'object') {
        return;
    }

    const nextQuery = { ...currentQuery };
    delete nextQuery.sid;
    delete nextQuery.eio_sid;
    socket.io.opts.query = nextQuery;
});
let tempChart = null;
let lastData = null;
let _isLeavingPage = false;
const _intervals = [];
let restartMonitorTimer = null;
let restartStartedAt = 0;
const RESTART_MONITOR_TIMEOUT_MS = 90000;
const TIMELINE_WINDOW_HOURS = 14;
const WEATHER_REFRESH_MS = 300000;
const WEATHER_REFRESH_JITTER_MS = 30000;
const TEMP_STATS_WINDOW_HOURS = 24;
const TEMP_STATS_REFRESH_MS = 300000;

const TEMP_STATS_DEF = [
    { key: 'temp_co', id: 'tile-stats-temp-co' },
    { key: 'temp_cwu', id: 'tile-stats-temp-cwu' },
    { key: 'temp_back', id: 'tile-stats-temp-back' },
    { key: 'temp_feeder', id: 'tile-stats-temp-feeder' },
    { key: 'temp_flue_gas', id: 'tile-stats-temp-flue-gas' },
    { key: 'temp_external', id: 'tile-stats-temp-external' },
    { key: 'temp_upper_buffer', id: 'tile-stats-temp-upper-buffer' },
    { key: 'temp_lower_buffer', id: 'tile-stats-temp-lower-buffer' },
];

const SENSOR_DEFINITIONS = Array.isArray(window.SENSOR_DEFINITIONS)
    ? window.SENSOR_DEFINITIONS
    : [];

const SENSOR_TILE_MAP = SENSOR_DEFINITIONS.reduce((acc, sensor) => {
    if (sensor && sensor.key && sensor.tile_id) {
        acc[sensor.key] = sensor.tile_id;
    }
    return acc;
}, {});

const DEFAULT_VISIBLE_SENSORS = Object.keys(SENSOR_TILE_MAP);

function parseVisibleSensors(value) {
    if (value === undefined || value === null) return [...DEFAULT_VISIBLE_SENSORS];
    if (String(value).trim() === '') return [];
    const sensors = String(value)
        .split(',')
        .map(v => v.trim())
        .filter(Boolean)
        .filter(v => Object.prototype.hasOwnProperty.call(SENSOR_TILE_MAP, v));

    return sensors;
}

function applySensorVisibility(visibleSensors) {
    const selected = new Set(visibleSensors);
    let visibleCount = 0;
    for (const [sensorKey, tileId] of Object.entries(SENSOR_TILE_MAP)) {
        const tile = document.getElementById(tileId);
        if (!tile) continue;
        const isVisible = selected.has(sensorKey);
        tile.style.display = isVisible ? '' : 'none';
        if (isVisible) visibleCount += 1;
    }

    const emptyInfo = document.getElementById('no-sensors-info');
    if (emptyInfo) {
        emptyInfo.style.display = visibleCount === 0 ? '' : 'none';
    }
}

function weatherCodeToIcon(code) {
    if (code === 0) return 'mdi:weather-sunny';
    if ([1, 2].includes(code)) return 'mdi:weather-partly-cloudy';
    if (code === 3) return 'mdi:weather-cloudy';
    if ([45, 48].includes(code)) return 'mdi:weather-fog';
    if ([51, 53, 55, 56, 57].includes(code)) return 'mdi:weather-rainy';
    if ([61, 63, 65, 80, 81, 82].includes(code)) return 'mdi:weather-pouring';
    if ([66, 67].includes(code)) return 'mdi:weather-snowy-rainy';
    if ([71, 73, 75, 77, 85, 86].includes(code)) return 'mdi:weather-snowy';
    if ([95, 96, 99].includes(code)) return 'mdi:weather-lightning-rainy';
    return 'mdi:weather-cloudy';
}

function weatherCodeToLabel(code) {
    const labels = {
        0: 'Bezchmurnie',
        1: 'Prawie bezchmurnie',
        2: 'Częściowe zachmurzenie',
        3: 'Pochmurno',
        45: 'Mgła',
        48: 'Mgła szadź',
        51: 'Mżawka słaba',
        53: 'Mżawka umiarkowana',
        55: 'Mżawka silna',
        61: 'Deszcz słaby',
        63: 'Deszcz umiarkowany',
        65: 'Deszcz silny',
        71: 'Śnieg słaby',
        73: 'Śnieg umiarkowany',
        75: 'Śnieg silny',
        80: 'Przelotny deszcz',
        81: 'Przelotny deszcz',
        82: 'Ulewa',
        95: 'Burza',
        96: 'Burza z gradem',
        99: 'Silna burza z gradem',
    };
    return labels[code] || 'Pogoda';
}

function normalizeModeLabel(modeName) {
    const text = String(modeName || '').trim();
    if (!text) return '';
    const normalized = text.toLowerCase();
    if (normalized === 'pauza') return 'Postój';

    const legacyMap = {
        wylaczony: 'Wyłączony',
        nadzor: 'Nadzór',
        reczny: 'Ręczny',
    };
    return legacyMap[normalized] || text;
}

function pad2(value) {
    return String(value).padStart(2, '0');
}

function formatControllerDateTime(data) {
    if (!data || typeof data !== 'object') {
        return { time: '--:--', date: '--.--.----' };
    }

    const hour = Number(data.controller_time_hour);
    const minute = Number(data.controller_time_minute);
    const day = Number(data.controller_time_day);
    const month = Number(data.controller_time_month);
    const year = Number(data.controller_time_year);

    const hasDiscreteParts = [hour, minute, day, month, year].every(Number.isFinite);
    if (hasDiscreteParts) {
        return {
            time: `${pad2(hour)}:${pad2(minute)}`,
            date: `${pad2(day)}.${pad2(month)}.${String(Math.trunc(year)).padStart(4, '0')}`,
        };
    }

    if (data.controller_time_iso) {
        const isoText = String(data.controller_time_iso).trim();
        const m = isoText.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/);
        if (m) {
            return {
                time: `${m[4]}:${m[5]}`,
                date: `${m[3]}.${m[2]}.${m[1]}`,
            };
        }
    }

    const ts = Number(data.timestamp);
    if (Number.isFinite(ts) && ts > 0) {
        const dt = new Date(ts * 1000);
        if (!Number.isNaN(dt.getTime())) {
            return {
                time: `${pad2(dt.getHours())}:${pad2(dt.getMinutes())}`,
                date: `${pad2(dt.getDate())}.${pad2(dt.getMonth() + 1)}.${String(dt.getFullYear()).padStart(4, '0')}`,
            };
        }
    }

    return { time: '--:--', date: '--.--.----' };
}

async function loadWeather() {
    const tempEl = document.getElementById('weather-temp');
    const iconEl = document.getElementById('weather-icon');
    const descEl = document.getElementById('weather-desc');
    const locationLocalityEl = document.getElementById('weather-location-locality');
    const locationAdminEl = document.getElementById('weather-location-admin');
    if (!tempEl || !iconEl || !descEl || !locationLocalityEl || !locationAdminEl) return;

    const setWeatherLocation = (locality, admin = '') => {
        locationLocalityEl.textContent = locality || 'Miejscowość';
        locationAdminEl.textContent = admin || '';
    };

    try {
        const resp = await fetch('/api/weather');
        const json = await resp.json();

        if (json.status === 'ok' && json.data) {
            const temp = Number(json.data.temperature);
            tempEl.textContent = Number.isFinite(temp) ? temp.toFixed(1) : '--';
            iconEl.setAttribute('icon', weatherCodeToIcon(json.data.weathercode));
            descEl.textContent = weatherCodeToLabel(json.data.weathercode);

            const locality = json.data.location || 'Miejscowość';
            const adminParts = [json.data.municipality, json.data.region].filter(Boolean);
            if (adminParts.length > 0) {
                setWeatherLocation(locality, adminParts.join(', '));
            } else if (json.data.location_label && String(json.data.location_label).includes(',')) {
                const [firstPart, ...restParts] = String(json.data.location_label).split(',').map(v => v.trim()).filter(Boolean);
                setWeatherLocation(firstPart || locality, restParts.join(', '));
            } else {
                setWeatherLocation(locality, '');
            }
            return;
        }

        if (json.status === 'not_configured') {
            tempEl.textContent = '--';
            iconEl.setAttribute('icon', 'mdi:map-marker-question-outline');
            descEl.textContent = 'Brak konfiguracji';
            setWeatherLocation('Ustaw współrzędne', 'lat/lon w Ustawieniach');
            return;
        }

        tempEl.textContent = '--';
        iconEl.setAttribute('icon', 'mdi:weather-cloudy-alert');
        descEl.textContent = 'Brak danych';
        setWeatherLocation('Nie udało się', 'pobrać pogody');
    } catch (e) {
        tempEl.textContent = '--';
        iconEl.setAttribute('icon', 'mdi:weather-cloudy-alert');
        descEl.textContent = 'Błąd połączenia';
        setWeatherLocation('Sprawdź połączenie', 'internetowe');
    }
}

async function loadSensorVisibilityConfig() {
    try {
        const resp = await fetch('/api/config');
        const json = await resp.json();
        if (json.status === 'ok') {
            const visibleSensors = parseVisibleSensors(json.data.visible_sensors);
            applySensorVisibility(visibleSensors);
        } else {
            applySensorVisibility(DEFAULT_VISIBLE_SENSORS);
        }
    } catch (e) {
        console.warn('loadSensorVisibilityConfig: błąd pobierania konfiguracji:', e);
        applySensorVisibility(DEFAULT_VISIBLE_SENSORS);
    }
}

socket.on('disconnect', () => {
    if (!_isLeavingPage) {
        updateConnectionStatus(false);
    }
});

socket.on('connect_error', (err) => {
    const message = String(err && err.message ? err.message : '').toLowerCase();
    const transportError = (
        message.includes('xhr poll error')
        || message.includes('transport error')
        || message.includes('websocket error')
    );

    if (transportError) {
        console.warn('Socket.IO: błąd transportu, przejście na sam polling HTTP /api/data');
        try {
            socket.io.reconnection(false);
            socket.disconnect();
        } catch (e) {
            // ignoruj - fallback i tak działa przez pollData
        }
    }
});

socket.on('new_data', (data) => {
    lastData = data;
    updateDashboard(data);
});

// ---- Aktualizacja danych ----
function updateDashboard(data) {
    if (!data) return;

    // Tryb pracy
    const modeEl = document.getElementById('mode-name');
    const modeTimeEl = document.getElementById('mode-time');
    const modeDateEl = document.getElementById('mode-date');
    const modeCard = document.querySelector('.mode-card');
    modeEl.textContent = normalizeModeLabel(data.mode_name) || '---';

    const controllerDateTime = formatControllerDateTime(data);
    if (modeTimeEl) modeTimeEl.textContent = controllerDateTime.time;
    if (modeDateEl) modeDateEl.textContent = controllerDateTime.date;

    modeCard.classList.remove('alarm', 'off');
    if (data.mode === 8) modeCard.classList.add('alarm');
    else if (data.mode === 0) modeCard.classList.add('off');
    const modeColor = (window.MODE_COLORS || {})[data.mode];
    if (modeColor) modeCard.style.setProperty('--mode-color', modeColor);
    else modeCard.style.removeProperty('--mode-color');

    // Temperatury
    setVal('val-temp-co', formatTemp(data.temp_co));
    setVal('val-temp-cwu', formatTemp(data.temp_cwu));
    setVal('val-temp-back', formatTemp(data.temp_back));
    setVal('val-temp-feeder', formatTemp(data.temp_feeder));
    setVal('val-temp-flue-gas', formatTemp(data.temp_flue_gas));
    setVal('val-temp-external', formatTemp(data.temp_external));
    setVal('val-temp-upper-buffer', formatTemp(data.temp_upper_buffer));
    setVal('val-temp-lower-buffer', formatTemp(data.temp_lower_buffer));

    // Temperatury zadane
    setVal('val-temp-co-set', formatTemp(data.temp_co_set));
    setVal('val-temp-cwu-set', formatTemp(data.temp_cwu_set));

    // Parametry operacyjne
    updateBoilerPowerTile(data);
    setVal('val-fan-power', formatVal(data.fan_power));
    setVal('val-flame', formatVal(data.flame || data.temp_optical));
    setVal('val-fuel-stream', formatVal(data.fuel_stream));
    if (data.fuel_level_not_set) {
        setVal('val-fuel-level', 'nie ustawiono');
        setVal('val-fuel-level-unit', '');
    } else {
        setVal('val-fuel-level', formatVal(data.fuel_level));
        setVal('val-fuel-level-unit', '%');
    }

    // Wskaźniki: temperatura względem zadanej, procent pracy
    setGauge('gauge-temp-co', data.temp_co, data.temp_co_set, GAUGE_MAX_CO);
    setGauge('gauge-temp-cwu', data.temp_cwu, data.temp_cwu_set, GAUGE_MAX_CWU);
    setMeter('meter-boiler-power', data.boiler_power);
    setMeter('meter-fan-power', data.fan_power);

    // Sprawność obiegu
    updateEfficiency(data);

    // Pompy
    setPump('pump-co', data.pump_co);
    setPump('pump-cwu', data.pump_cwu);
    setPump('pump-circ', data.pump_circulation);

    // Statystyki PoC
    if (data.fuel_burned_kg !== null && data.fuel_burned_kg !== undefined) {
        document.getElementById('poc-stats').style.display = 'block';
        setVal('val-fuel-burned', formatVal(data.fuel_burned_kg));
        setVal('val-ignitions', data.ignitions || '--');
        setVal('val-work-100', data.work_time_100 || '--');
        setVal('val-work-50', data.work_time_50 || '--');
    }
}

// Kafel mocy: % (ze sterownika lub wyliczony z mocy nominalnej), a gdy % jest
// niedostępny - sama moc w kW. Moc w kW jest szacowana przez sterownik.
function updateBoilerPowerTile(data) {
    const hasPercent = data.boiler_power !== null && data.boiler_power !== undefined;
    const detailEl = document.getElementById('boiler-power-detail');
    if (hasPercent) {
        setVal('val-boiler-power', formatVal(data.boiler_power));
        setVal('val-boiler-power-unit', '%');
        setVal('val-boiler-power-kw', formatVal(data.boiler_power_kw));
        if (detailEl) detailEl.style.display = '';
    } else {
        setVal('val-boiler-power', formatVal(data.boiler_power_kw));
        setVal('val-boiler-power-unit', 'kW');
        if (detailEl) detailEl.style.display = 'none';
    }
    const tile = document.getElementById('tile-power');
    if (tile) {
        tile.title = data.boiler_power_source === 'calculated'
            ? 'Obciążenie % wyliczone z mocy w kW i mocy nominalnej kotła z ustawień'
            : '';
    }
}

async function loadFuelInfo() {
    try {
        const resp = await fetch('/api/fuel-info');
        const json = await resp.json();
        const value = json?.data?.calorific_value_kwh_kg;
        const el = document.getElementById('boiler-power-calorific');
        if (el && value !== null && value !== undefined) {
            setVal('val-calorific', Number(value).toLocaleString('pl-PL', { maximumFractionDigits: 1 }));
            el.style.display = '';
        }
    } catch (e) {
        // Informacja pomocnicza - brak nie blokuje dashboardu.
    }
}

const GAUGE_MAX_CO = 90;
const GAUGE_MAX_CWU = 70;

function _pct(value, max) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return null;
    return Math.max(0, Math.min(100, (Number(value) / max) * 100));
}

function setGauge(id, value, target, max) {
    const fill = document.getElementById(id);
    const marker = document.getElementById(id + '-target');
    const valuePct = _pct(value, max);
    const targetPct = _pct(target, max);
    if (fill) fill.style.width = valuePct === null ? '0%' : valuePct + '%';
    if (marker) {
        marker.style.display = targetPct === null ? 'none' : '';
        if (targetPct !== null) marker.style.left = targetPct + '%';
    }
}

function setMeter(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    const pct = _pct(value, 100);
    el.style.width = pct === null ? '0%' : pct + '%';
}

function setVal(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function formatTemp(val) {
    if (val === null || val === undefined) return '--';
    return Number(val).toFixed(1);
}

function formatVal(val) {
    if (val === null || val === undefined) return '--';
    return Number(val).toFixed(1);
}

function setTempStatsText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setTempStats(id, min, max, avg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = '';
    el.title = 'Ostatnie 24 h';
    [['min', min], ['max', max], ['śr.', avg]].forEach(([label, value]) => {
        const item = document.createElement('span');
        item.append(label + ' ');
        const strong = document.createElement('b');
        strong.textContent = formatTempStat(value);
        item.appendChild(strong);
        el.appendChild(item);
    });
}

function formatTempStat(val) {
    if (!Number.isFinite(val)) return '--';
    return Number(val).toFixed(1);
}

function setNoTempStats() {
    TEMP_STATS_DEF.forEach(({ id }) => {
        setTempStats(id, NaN, NaN, NaN);
    });
}

async function loadTempStats24h() {
    try {
        const resp = await fetch(`/api/statistics?hours=${TEMP_STATS_WINDOW_HOURS}`);
        const json = await resp.json();
        const statsData = json && json.status === 'ok' && json.data && typeof json.data === 'object'
            ? json.data
            : null;

        if (!statsData) {
            setNoTempStats();
            return;
        }

        TEMP_STATS_DEF.forEach(({ key, id }) => {
            const stats = statsData[key];

            if (!stats || !Number.isFinite(stats.min) || !Number.isFinite(stats.max) || !Number.isFinite(stats.avg)) {
                setTempStats(id, NaN, NaN, NaN);
                return;
            }

            setTempStats(id, stats.min, stats.max, stats.avg);
        });
    } catch (e) {
        console.warn('loadTempStats24h: błąd pobierania statystyk:', e);
        setNoTempStats();
    }
}

function setPump(id, active) {
    const el = document.getElementById(id);
    if (!el) return;
    const dot = el.querySelector('.pump-dot');
    if (dot) {
        dot.classList.toggle('on', !!active);
        dot.classList.toggle('off', !active);
    }
}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connection-status');
    const textEl = document.getElementById('status-text');

    statusEl.classList.toggle('connected', connected);
    statusEl.classList.toggle('disconnected', !connected);
    textEl.textContent = connected ? 'Połączony' : 'Rozłączony';
}

// ---- Wykres ----
function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v && v.trim() ? v.trim() : fallback;
}

const DATASETS_DEF = [
    { label: 'Zasilanie CO',  color: '#d9480f', cssColor: '--series-1', key: 'temp_co',      yAxis: 'yTemp' },
    { label: 'CWU',           color: '#1c7ed6', cssColor: '--series-2', key: 'temp_cwu',     yAxis: 'yTemp' },
    { label: 'Powrót',        color: '#0ca678', cssColor: '--series-3', key: 'temp_back',    yAxis: 'yTemp' },
    { label: 'Spaliny',       color: '#868e96', cssColor: '--series-4', key: 'temp_flue_gas',yAxis: 'yTemp' },
    { label: 'Moc kotła (%)', color: '#f59f00', cssColor: '--series-5', key: 'boiler_power', yAxis: 'yPct'  },
    { label: 'Wentylator (%)',color: '#7048e8', cssColor: '--series-6', key: 'fan_power',    yAxis: 'yPct'  },
    { label: 'Paliwo (kg/h)', color: '#a61e4d', cssColor: '--series-7', key: 'fuel_stream',  yAxis: 'yFuel' },
];

function chartGridColor() {
    return cssVar('--chart-grid', 'rgba(0,0,0,0.08)');
}

function chartTickColor() {
    return cssVar('--text-secondary', '#5e6b74');
}

function seriesColor(def) {
    return cssVar(def.cssColor, def.color);
}

function withAlpha(color, alpha) {
    const hex = String(color).trim();
    if (/^#[0-9a-f]{6}$/i.test(hex)) {
        const n = parseInt(hex.slice(1), 16);
        return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
    }
    return color;
}

function updateChartGridColors() {
    if (!tempChart) return;
    const grid = chartGridColor();
    const tick = chartTickColor();
    Object.values(tempChart.options.scales).forEach(scale => {
        if (scale.grid && scale.grid.drawOnChartArea !== false) scale.grid.color = grid;
        if (scale.ticks) scale.ticks.color = tick;
        if (scale.title) scale.title.color = tick;
    });
    tempChart.data.datasets.forEach((ds, i) => {
        const c = seriesColor(DATASETS_DEF[i]);
        ds.borderColor = c;
        ds.backgroundColor = withAlpha(c, 0.08);
    });
    tempChart.update('none');
}

new MutationObserver(updateChartGridColors)
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

function initChart() {
    const ctx = document.getElementById('temp-chart').getContext('2d');
    const tick = chartTickColor();
    const grid = chartGridColor();
    const font = { family: "'IBM Plex Sans', system-ui, sans-serif", size: 12 };
    tempChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: DATASETS_DEF.map(d => ({
                label: d.label,
                borderColor: seriesColor(d),
                backgroundColor: withAlpha(seriesColor(d), 0.08),
                borderWidth: 2,
                data: [],
                tension: 0.3,
                pointRadius: 0,
                pointHoverRadius: 4,
                yAxisID: d.yAxis,
                hidden: ['yPct','yFuel'].includes(d.yAxis),
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(23,32,38,0.92)',
                    titleFont: font,
                    bodyFont: font,
                    padding: 10,
                    cornerRadius: 6,
                    boxPadding: 4,
                },
                zoom: {
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: 'x',
                    },
                    pan: { enabled: true, mode: 'x' },
                },
            },
            scales: {
                x: {
                    ticks: { color: tick, maxTicksLimit: 10, font },
                    grid: { color: grid, drawTicks: false },
                    border: { display: false },
                },
                yTemp: {
                    type: 'linear', position: 'left',
                    ticks: { color: tick, font, padding: 8 },
                    grid: { color: grid, drawTicks: false },
                    border: { display: false },
                    title: { display: true, text: '°C', color: tick, font },
                },
                yPct: {
                    type: 'linear', position: 'right', display: false,
                    min: 0, max: 100,
                    ticks: { color: tick, font },
                    grid: { drawOnChartArea: false },
                    border: { display: false },
                    title: { display: true, text: '%', color: tick, font },
                },
                yFuel: {
                    type: 'linear', position: 'right', display: false,
                    ticks: { color: tick, font },
                    grid: { drawOnChartArea: false },
                    border: { display: false },
                    title: { display: true, text: 'kg/h', color: tick, font },
                },
            },
        },
    });
}

function toggleSeries(index) {
    const meta = tempChart.getDatasetMeta(index);
    meta.hidden = !meta.hidden;
    // Pokaz/ukryj os Y jesli wszystkie serie danej osi sa ukryte
    const yId = DATASETS_DEF[index].yAxis;
    if (yId !== 'yTemp') {
        const anyVisible = DATASETS_DEF.some((d, i) =>
            d.yAxis === yId && !tempChart.getDatasetMeta(i).hidden
        );
        tempChart.options.scales[yId].display = anyVisible;
    }
    tempChart.update();
}

function resetZoom() {
    if (tempChart) tempChart.resetZoom();
}

async function loadChart() {
    const hours = document.getElementById('chart-range').value;
    const chartHours = Number(hours);
    const timelineMayReuseChartData = timelineVisible && chartHours >= TIMELINE_WINDOW_HOURS;
    const chartHistoryLimit = timelineMayReuseChartData ? 2000 : 500;
    try {
        const resp = await fetch(`/api/history?hours=${hours}&limit=${chartHistoryLimit}`);
        const json = await resp.json();

        if (json.status === 'ok' && json.data.length > 0) {
            const labels = json.data.map(d => {
                const date = new Date(d.timestamp * 1000);
                return hours <= 24
                    ? date.toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' })
                    : date.toLocaleDateString('pl-PL', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
            });

            tempChart.data.labels = labels;
            DATASETS_DEF.forEach((def, i) => {
                tempChart.data.datasets[i].data = json.data.map(d => d[def.key]);
            });
            tempChart.update('none');
            if (timelineVisible) {
                const latestTs = json.data[json.data.length - 1]?.timestamp || 0;
                const nowTs = Math.floor(Date.now() / 1000);
                const hasTimelineCoverage = timelineMayReuseChartData && latestTs >= (nowTs - 300);

                if (hasTimelineCoverage) {
                    loadTimeline(json.data, TIMELINE_WINDOW_HOURS);
                } else {
                    loadTimeline(null, TIMELINE_WINDOW_HOURS);
                }
            }
        }
    } catch (e) {
        console.error('Błąd ładowania wykresu:', e);
    }
}

// ---- Historia trybów pracy ----
let timelineVisible = true;

function toggleTimeline() {
    timelineVisible = !timelineVisible;
    document.getElementById('timeline-content').style.display = timelineVisible ? 'block' : 'none';
    document.getElementById('timeline-toggle-icon').innerHTML = timelineVisible ? '<iconify-icon icon="mdi:chevron-up"></iconify-icon>' : '<iconify-icon icon="mdi:chevron-down"></iconify-icon>';
    if (timelineVisible) loadTimeline();
}

const MODE_COLORS = window.MODE_COLORS || {
    '-1': '#b0bec5',
    0: 'var(--text-secondary)',
    1: '#727272',
    2: '#f0a500',
    3: '#4ecca3',
    4: '#3498db',
    5: '#63adde',
    6: '#9b59b6',
    7: '#555',
    8: '#d84059',
    9: '#e67e22',
    10: '#8e44ad',
    11: '#2c3e50',
};

const MODE_VISUAL_ORDER = Object.freeze([3, 4, 5, 7, 2, 1, 0, 6, 8, 9, 10, 11, -1]);

function modeVisualRank(mode) {
    const normalizedMode = Number(mode);
    const index = MODE_VISUAL_ORDER.indexOf(normalizedMode);
    if (index >= 0) return index;
    if (Number.isFinite(normalizedMode)) return 1000 + normalizedMode;
    return 9999;
}

function formatTimelineTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString('pl-PL', {
        hour: '2-digit',
        minute: '2-digit',
    });
}

function timelineTickStepHours(windowHours) {
    if (windowHours <= 8) return 1;
    if (windowHours <= 18) return 2;
    return 3;
}

async function loadTimeline(historyData = null, windowHours = TIMELINE_WINDOW_HOURS) {
    try {
        if (!Array.isArray(historyData)) {
            const resp = await fetch(`/api/history?hours=${windowHours}&limit=2000`);
            const json = await resp.json();
            historyData = json.data;
        }

        const container = document.getElementById('mode-timeline');
        container.innerHTML = '';
        const nowTs = Math.floor(Date.now() / 1000);
        const windowEnd = nowTs;
        const windowStart = windowEnd - (windowHours * 3600);

        const sorted = (historyData || [])
            .filter(d => d && d.timestamp)
            .sort((a, b) => a.timestamp - b.timestamp);

        const beforeWindow = [...sorted].reverse().find(d => d.timestamp <= windowStart) || null;
        const inWindow = sorted.filter(d => d.timestamp > windowStart && d.timestamp <= windowEnd);
        const diffs = [];
        for (let i = 1; i < inWindow.length; i++) {
            const diff = inWindow[i].timestamp - inWindow[i - 1].timestamp;
            if (diff > 0 && diff <= 3600) diffs.push(diff);
        }
        diffs.sort((a, b) => a - b);
        const medianGapSec = diffs.length
            ? diffs[Math.floor(diffs.length / 2)]
            : 60;
        const freshnessThresholdSec = Math.max(180, medianGapSec * 3);

        const points = [];
        if (beforeWindow) {
            points.push({
                mode: beforeWindow.mode,
                mode_name: beforeWindow.mode_name,
                timestamp: windowStart,
            });
        }
        points.push(...inWindow);

        const segments = [];

        if (!points.length) {
            segments.push({ mode: -1, name: 'Brak danych', start: windowStart, end: windowEnd });
        } else {
            if (points[0].timestamp > windowStart) {
                segments.push({
                    mode: -1,
                    name: 'Brak danych',
                    start: windowStart,
                    end: points[0].timestamp,
                });
            }

            for (let i = 0; i < points.length; i++) {
                const current = points[i];
                const next = points[i + 1];
                const segStart = Math.max(current.timestamp, windowStart);
                const segEnd = next
                    ? Math.min(next.timestamp, windowEnd)
                    : Math.min(windowEnd, current.timestamp + freshnessThresholdSec);
                if (segEnd > segStart) {
                    segments.push({
                        mode: current.mode,
                        name: normalizeModeLabel(current.mode_name) || 'Nieznany',
                        start: segStart,
                        end: segEnd,
                    });
                }
            }

            const lastPointTs = points[points.length - 1].timestamp;
            const knownUntil = Math.min(windowEnd, lastPointTs + freshnessThresholdSec);
            if (knownUntil < windowEnd) {
                segments.push({
                    mode: -1,
                    name: 'Brak danych',
                    start: knownUntil,
                    end: windowEnd,
                });
            }
        }

        const mergedSegments = [];
        segments.forEach(seg => {
            const last = mergedSegments[mergedSegments.length - 1];
            if (
                last
                && last.mode === seg.mode
                && last.name === seg.name
                && seg.start <= (last.end + 1)
            ) {
                last.end = Math.max(last.end, seg.end);
            } else {
                mergedSegments.push({ ...seg });
            }
        });

        const total = Math.max(windowEnd - windowStart, 1);

        const bar = document.createElement('div');
        bar.className = 'timeline-bar';
        mergedSegments.forEach(seg => {
            const durationSec = Math.max(seg.end - seg.start, 1);
            const durationMin = Math.max(1, Math.round(durationSec / 60));
            const w = Math.max((durationSec / total) * 100, 0.2);
            const block = document.createElement('div');
            block.className = 'timeline-block';
            if (seg.mode === -1) block.classList.add('timeline-block-missing');
            block.style.width = w + '%';
            block.style.backgroundColor = MODE_COLORS[seg.mode] || '#555';
            block.title = `${seg.name} | ${formatTimelineTime(seg.start)} - ${formatTimelineTime(seg.end)} | ${durationMin} min`;
            bar.appendChild(block);
        });
        container.appendChild(bar);

        const axis = document.createElement('div');
        axis.className = 'timeline-axis';
        const stepHours = timelineTickStepHours(windowHours);
        const stepSec = stepHours * 3600;

        const tickTimestamps = [];
        for (let ts = windowStart; ts <= windowEnd; ts += stepSec) {
            tickTimestamps.push(ts);
        }
        if (tickTimestamps[tickTimestamps.length - 1] !== windowEnd) {
            tickTimestamps.push(windowEnd);
        }

        tickTimestamps.forEach(ts => {
            const tick = document.createElement('div');
            tick.className = 'timeline-axis-tick';
            tick.style.left = `${((ts - windowStart) / total) * 100}%`;

            const mark = document.createElement('span');
            mark.className = 'timeline-axis-mark';

            const label = document.createElement('span');
            label.className = 'timeline-axis-label';
            label.textContent = formatTimelineTime(ts);

            tick.appendChild(mark);
            tick.appendChild(label);
            axis.appendChild(tick);
        });
        container.appendChild(axis);

        // Legenda
        const legend = document.createElement('div');
        legend.className = 'timeline-legend';
        const legendByMode = new Map();
        mergedSegments.forEach(seg => {
            if (!legendByMode.has(seg.mode)) {
                legendByMode.set(seg.mode, seg.name || '');
            }
        });

        [...legendByMode.entries()]
            .sort((a, b) => {
                const rankDiff = modeVisualRank(a[0]) - modeVisualRank(b[0]);
                if (rankDiff !== 0) return rankDiff;
                return Number(a[0]) - Number(b[0]);
            })
            .forEach(([mode, name]) => {
                const item = document.createElement('span');
                item.className = 'timeline-legend-item';

                const dot = document.createElement('span');
                dot.className = 'timeline-dot';
                dot.style.background = MODE_COLORS[mode] || '#555';

                const label = document.createElement('span');
                label.textContent = name;

                item.appendChild(dot);
                item.appendChild(label);
                legend.appendChild(item);
            });
        container.appendChild(legend);
    } catch (e) {
        console.error('Błąd ładowania osi czasu:', e);
    }
}

// ---- Polling jako fallback ----
async function pollData() {
    try {
        const resp = await fetch('/api/data');
        const json = await resp.json();

        if (json.status === 'ok' && json.data) {
            updateDashboard(json.data);
        }

        // Aktualizuj stopke
        if (json.connection) {
            setVal('footer-frames', json.connection.frames_received || 0);
            const bytes = json.connection.bytes_received || 0;
            setVal('footer-bytes', bytes > 1024 ? (bytes / 1024).toFixed(1) + ' KB' : bytes + ' B');
            updateConnectionStatus(json.connection.connected);
        }
    } catch (e) {
        console.error('Błąd pollingu:', e);
    }
}

// ---- Analityka ----

function updateEfficiency(data) {
    if (data.temp_co !== null && data.temp_co !== undefined
            && data.temp_back !== null && data.temp_back !== undefined) {
        const delta = data.temp_co - data.temp_back;
        setVal('val-efficiency', delta.toFixed(1));
    }
}

async function loadCosts() {
    try {
        const resp = await fetch('/api/costs?hours=24');
        const json = await resp.json();
        if (json.status === 'ok') {
            setVal('val-cost-day', json.data.cost_per_day.toFixed(2));
        }
    } catch (e) {
        console.warn('loadCosts: błąd pobierania kosztów:', e);
    }
}

// ---- Alerty ----
const ALERTS_PREVIEW_LIMIT = 5;

socket.on('new_alert', (alert) => {
    showAlertBanner(alert.label + ': ' + alert.message);
    addAlertToList(alert);
    sendBrowserNotification(alert.label, alert.message);
    updateAlertsSection(true);
});

function showAlertBanner(text) {
    const banner = document.getElementById('alert-banner');
    document.getElementById('alert-banner-text').textContent = text;
    banner.style.display = 'flex';
}

function dismissAlerts() {
    document.getElementById('alert-banner').style.display = 'none';
    fetch('/api/alerts/read', { method: 'POST' });
    const badge = document.getElementById('alerts-count');
    if (badge) badge.textContent = '';

    const list = document.getElementById('alerts-list');
    if (list) {
        list.querySelectorAll('.alert-item').forEach((item) => {
            item.classList.remove('alert-item-unread');
            item.classList.add('alert-item-read');
        });
    }
}

async function loadRecentAlerts() {
    try {
        const resp = await fetch(`/api/alerts?limit=${ALERTS_PREVIEW_LIMIT}`);
        const json = await resp.json();
        const list = document.getElementById('alerts-list');
        list.innerHTML = '';
        if (json.data.length === 0) {
            list.innerHTML = '<div class="alert-item-empty">Brak alertów w historii</div>';
            return;
        }
        [...json.data].reverse().forEach(a => addAlertToList(a, list));
    } catch (e) {
        console.error('Błąd ładowania alertów:', e);
    }
}

function addAlertToList(alert, container) {
    const list = container || document.getElementById('alerts-list');
    if (!list) return;

    const empty = list.querySelector('.alert-item-empty');
    if (empty) empty.remove();

    const dt = new Date(alert.timestamp * 1000);
    const timeStr = dt.toLocaleString('pl-PL', { dateStyle: 'short', timeStyle: 'short' });

    const div = document.createElement('div');
    div.className = 'alert-item';
    const isUnread = alert && (alert.read === 0 || alert.read === '0' || alert.read === false || alert.read === undefined || alert.read === null);
    div.classList.add(isUnread ? 'alert-item-unread' : 'alert-item-read');

    const spanTime = document.createElement('span');
    spanTime.className = 'alert-item-time';
    spanTime.textContent = timeStr;

    const spanLabel = document.createElement('span');
    spanLabel.className = 'alert-item-label';
    spanLabel.textContent = alert.label || '';

    const spanMsg = document.createElement('span');
    spanMsg.className = 'alert-item-msg';
    spanMsg.textContent = alert.message || '';

    div.appendChild(spanTime);
    div.appendChild(spanLabel);
    div.appendChild(spanMsg);
    list.prepend(div);

    while (list.children.length > ALERTS_PREVIEW_LIMIT) {
        list.removeChild(list.lastElementChild);
    }
}

function updateAlertsSection(hasNew) {
    const section = document.getElementById('alerts-section');
    section.style.display = 'block';
    if (hasNew) {
        const badge = document.getElementById('alerts-count');
        const current = parseInt(badge.textContent) || 0;
        badge.textContent = current + 1;
    }
}

async function initAlerts() {
    try {
        const resp = await fetch(`/api/alerts?limit=${ALERTS_PREVIEW_LIMIT}&unread=true`);
        const json = await resp.json();
        const unreadTotal = Number(json.unread_total);
        if (Number.isFinite(unreadTotal) && unreadTotal > 0) {
            updateAlertsSection(false);
            const badge = document.getElementById('alerts-count');
            if (badge) badge.textContent = unreadTotal;
        } else if (json.count > 0) {
            updateAlertsSection(false);
            const badge = document.getElementById('alerts-count');
            if (badge) badge.textContent = json.count;
        }
        loadRecentAlerts();
    } catch (e) {
        console.warn('initAlerts: błąd inicjalizacji alertów:', e);
    }
}

function sendBrowserNotification(title, body) {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') {
        new Notification('ecoMAX: ' + title, { body, icon: '/static/favicon.ico' });
    } else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then(perm => {
            if (perm === 'granted') new Notification('ecoMAX: ' + title, { body });
        });
    }
}

// ---- Restart aplikacji ----
function showStartupBanner(text, withDiagnostics = false) {
    const banner = document.getElementById('startup-banner');
    const textEl = document.getElementById('startup-banner-text');
    const helpEl = document.getElementById('startup-banner-help');
    if (!banner || !textEl) return;
    textEl.textContent = text;
    if (helpEl) helpEl.style.display = withDiagnostics ? 'block' : 'none';
    banner.style.display = 'flex';
}

function dismissStartupBanner() {
    const banner = document.getElementById('startup-banner');
    const helpEl = document.getElementById('startup-banner-help');
    if (helpEl) helpEl.style.display = 'none';
    if (banner) banner.style.display = 'none';
}

function stopRestartMonitor() {
    if (restartMonitorTimer) {
        clearInterval(restartMonitorTimer);
        restartMonitorTimer = null;
    }
    restartStartedAt = 0;
}

async function checkRestartReadiness() {
    const btn = document.getElementById('btn-restart');

    try {
        const resp = await fetch('/api/data', { cache: 'no-store' });
        if (resp.ok) {
            const json = await resp.json();
            if (json.status === 'ok' || json.status === 'no_data') {
                stopRestartMonitor();
                dismissStartupBanner();
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = '↺ Restart';
                }
                return;
            }
        }
    } catch (e) {
        // Serwer jeszcze startuje - sprawdzimy ponownie w kolejnym cyklu.
    }

    if (restartStartedAt && (Date.now() - restartStartedAt) > RESTART_MONITOR_TIMEOUT_MS) {
        stopRestartMonitor();
        showStartupBanner('Restart nie powiódł się. Coś nie zadziałało podczas uruchamiania głównej aplikacji.', true);
        if (btn) {
            btn.disabled = false;
            btn.textContent = '↺ Restart';
        }
    }
}

function startRestartMonitor() {
    stopRestartMonitor();
    restartStartedAt = Date.now();
    showStartupBanner('Trwa restart aplikacji... Jeśli uruchomienie się nie powiedzie, zobaczysz tu szczegóły.', false);
    restartMonitorTimer = setInterval(checkRestartReadiness, 3000);
}

async function restartApp() {
    if (!confirm('Zrestartować aplikację ecoMAX Monitor?')) return;

    const btn = document.getElementById('btn-restart');
    btn.disabled = true;
    btn.textContent = 'Restartowanie...';

    try {
        const resp = await fetch('/api/restart', { method: 'POST' });
        const json = await resp.json();

        if (json.status === 'ok') {
            btn.textContent = 'Uruchamiam ponownie...';
            startRestartMonitor();
        } else {
            showStartupBanner('Błąd restartu: ' + (json.message || 'nieznany błąd systemu'), true);
            btn.disabled = false;
            btn.textContent = '↺ Restart';
        }
    } catch (e) {
        // Serwer się zresetował - normalne zachowanie
        btn.textContent = 'Oczekiwanie na restart...';
        startRestartMonitor();
    }
}

// Przywróć przycisk po ponownym podłączeniu
socket.on('connect', () => {
    updateConnectionStatus(true);
    stopRestartMonitor();
    dismissStartupBanner();
    const btn = document.getElementById('btn-restart');
    if (btn) {
        btn.disabled = false;
        btn.textContent = '↺ Restart';
    }
});

// ---- Sprawdzenie konfiguracji (first-run) ----
async function checkSetup() {
    try {
        const r = await fetch('/api/setup-status');
        const d = await r.json();
        document.getElementById('setup-banner').style.display = d.configured ? 'none' : 'flex';
    } catch (e) {
        console.warn('checkSetup: błąd sprawdzania konfiguracji:', e);
    }
}

// ---- Inicjalizacja ----
document.addEventListener('DOMContentLoaded', () => {
    loadSensorVisibilityConfig();
    checkSetup();
    loadWeather();
    const weatherRefreshIntervalMs = WEATHER_REFRESH_MS + Math.floor(Math.random() * (WEATHER_REFRESH_JITTER_MS + 1));
    _intervals.push(setInterval(loadWeather, weatherRefreshIntervalMs));
    initChart();
    loadChart();
    if (timelineVisible) loadTimeline();
    initAlerts();
    loadCosts();
    loadTempStats24h();
    loadFuelInfo();
    _intervals.push(setInterval(loadCosts, 300000));
    _intervals.push(setInterval(loadTempStats24h, TEMP_STATS_REFRESH_MS));

    // Poll co 10s jako backup
    _intervals.push(setInterval(pollData, 10000));
    // Odśwież wykres co 5 minut
    _intervals.push(setInterval(loadChart, 300000));

    // Pierwszy poll od razu
    pollData();
});

function cleanupRealtime() {
    _isLeavingPage = true;
    _intervals.forEach((id) => clearInterval(id));
    _intervals.length = 0;
    stopRestartMonitor();

    try {
        socket.removeAllListeners();
        socket.disconnect();
    } catch (e) {
        // ignoruj błąd zamykania przy opuszczaniu strony
    }
}

window.addEventListener('beforeunload', cleanupRealtime);
window.addEventListener('pagehide', cleanupRealtime);
