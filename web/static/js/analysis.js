let modeShareChart = null;
let fuelCostChart = null;
let deltaTrendChart = null;
let modeShareCompareChart = null;
let mixerTrendChart = null;
let dailyModesChart = null;

const MODE_COLORS = window.MODE_COLORS || {
    '-1': '#b0bec5',
    0: '#727272',
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

const ANALYSIS_RANGE_PRESETS = [
    { key: '1h', label: '1 godzina', hours: 1 },
    { key: '6h', label: '6 godzin', hours: 6 },
    { key: '12h', label: '12 godzin', hours: 12 },
    { key: '24h', label: '24 godziny', hours: 24 },
    { key: '7d', label: '7 dni', hours: 24 * 7 },
    { key: '14d', label: '14 dni', hours: 24 * 14 },
    { key: '30d', label: '30 dni', hours: 24 * 30 },
    { key: '90d', label: '90 dni', hours: 24 * 90 },
];

let analysisRetentionDays = 14;
let analysisSaveIntervalSec = 60;
let selectedAnalysisHours = 24;

const MODE_NAME_FALLBACKS = Object.freeze({
    0: 'Wyłączony',
    1: 'Stabilizacja',
    2: 'Rozpalanie',
    3: 'Praca',
    4: 'Nadzór',
    5: 'Postój',
    6: 'Czuwanie',
    7: 'Wygaszanie',
    8: 'Alarm',
    9: 'Ręczny',
    10: 'Odblokowanie',
    11: 'Inny',
});

const MODE_COMPARE_ORDER = Object.freeze([3, 4, 5, 7]);
const MODE_VISUAL_ORDER = Object.freeze([3, 4, 5, 7, 2, 1, 0, 6, 8, 9, 10, 11, -1]);

function modeVisualRank(mode) {
    const normalizedMode = Number(mode);
    const index = MODE_VISUAL_ORDER.indexOf(normalizedMode);
    if (index >= 0) return index;
    if (Number.isFinite(normalizedMode)) return 1000 + normalizedMode;
    return 9999;
}

function sortModeShareForVisualConsistency(items) {
    return [...items].sort((a, b) => {
        const rankDiff = modeVisualRank(a.mode) - modeVisualRank(b.mode);
        if (rankDiff !== 0) return rankDiff;
        return Number(a.mode) - Number(b.mode);
    });
}

function formatNumber(value, digits = 1) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return '--';
    }
    return number.toFixed(digits);
}

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}

function setChartEmptyState(id, isVisible, message = '') {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = message;
    element.classList.toggle('visible', Boolean(isVisible));
}

function formatDateLabel(dateString) {
    try {
        const date = new Date(dateString);
        if (Number.isNaN(date.getTime())) {
            return dateString;
        }
        return date.toLocaleDateString('pl-PL', {
            weekday: 'short',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
        });
    } catch {
        return dateString;
    }
}

function formatDurationMinutes(totalMinutes) {
    const minutes = Math.max(0, Math.round(Number(totalMinutes) || 0));
    const hours = Math.floor(minutes / 60);
    const restMinutes = minutes % 60;
    if (hours > 0) return `${hours}h ${restMinutes}m`;
    return `${restMinutes}m`;
}

function formatDaysLabel(days) {
    const number = Number(days);
    if (!Number.isFinite(number) || number <= 0) return '0 dni';

    const rounded = Math.round(number);
    if (Math.abs(number - rounded) < 0.01) {
        if (rounded === 1) return '1 dzień';
        return `${rounded} dni`;
    }

    return `${formatNumber(number, 1)} dnia`;
}

function formatHoursLabel(hours) {
    const number = Number(hours);
    if (!Number.isFinite(number) || number <= 0) return '0 godzin';

    const rounded = Math.round(number);
    if (Math.abs(number - rounded) < 0.01) {
        const mod10 = rounded % 10;
        const mod100 = rounded % 100;
        if (rounded === 1) return '1 godzina';
        if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) {
            return `${rounded} godziny`;
        }
        return `${rounded} godzin`;
    }

    return `${formatNumber(number, 1)} godz.`;
}

function formatLastDaysPhrase(days) {
    const number = Number(days);
    const isSingular = Number.isFinite(number) && Math.abs(number - 1) < 0.01;
    const adjective = isSingular ? 'ostatni' : 'ostatnie';
    return `${adjective} ${formatDaysLabel(number)}`;
}

function formatRangeShort(hours) {
    const h = Number(hours);
    if (!Number.isFinite(h) || h <= 0) return formatHoursLabel(24);
    if (h < 24) return formatHoursLabel(h);
    const days = h / 24;
    return formatDaysLabel(days);
}

function setRangeLabelText(id, text) {
    const element = document.getElementById(id);
    if (element) element.textContent = `(${text})`;
}

function updateRangeLabels(hours) {
    const label = formatRangeShort(hours);
    setRangeLabelText('ana-range-summary-label', label);
    setRangeLabelText('ana-range-modes-label', label);
    setRangeLabelText('ana-range-delta-label', label);
    setRangeLabelText('ana-range-mixer-label', label);
    setRangeLabelText('ana-range-anomalies-label', label);
    setRangeLabelText('ana-range-dayparts-label', label);
    setRangeLabelText('ana-range-compare-label', label);
}

function analysisMaxHours() {
    return Math.max(1, Number(analysisRetentionDays) * 24);
}

function historyLimitForHours(hours) {
    const seconds = Math.max(1, Number(hours) * 3600);
    const interval = Math.max(5, Number(analysisSaveIntervalSec) || 60);
    const estimated = Math.ceil(seconds / interval) + 200;
    return Math.min(Math.max(estimated, 500), 20000);
}

function datetimeLocalValue(tsMs) {
    const date = new Date(tsMs);
    const pad = value => String(value).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function getCustomRangeHours() {
    const startEl = document.getElementById('ana-range-start');
    const endEl = document.getElementById('ana-range-end');
    if (!startEl || !endEl || !startEl.value || !endEl.value) return null;

    const start = new Date(startEl.value).getTime();
    const end = new Date(endEl.value).getTime();
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return null;

    const maxSpanMs = analysisMaxHours() * 3600 * 1000;
    const spanMs = end - start;
    if (spanMs > maxSpanMs) return null;

    const now = Date.now();
    const minAllowed = now - maxSpanMs;
    if (start < minAllowed || end > now + 60 * 1000) return null;

    return spanMs / 3600000;
}

function getSelectedRangeHours() {
    const select = document.getElementById('ana-range-preset');
    if (!select) return 24;
    if (select.value === 'custom') {
        const customHours = getCustomRangeHours();
        return customHours || selectedAnalysisHours || 24;
    }
    const selected = ANALYSIS_RANGE_PRESETS.find(item => item.key === select.value);
    if (!selected) return 24;
    return Math.min(selected.hours, analysisMaxHours());
}

function renderRangeOptions() {
    const select = document.getElementById('ana-range-preset');
    if (!select) return;

    const maxHours = analysisMaxHours();
    const options = ANALYSIS_RANGE_PRESETS.filter(item => item.hours <= maxHours);

    select.innerHTML = '';
    options.forEach(item => {
        const option = document.createElement('option');
        option.value = item.key;
        option.textContent = item.label;
        select.appendChild(option);
    });

    const customOption = document.createElement('option');
    customOption.value = 'custom';
    customOption.textContent = 'Własny zakres';
    select.appendChild(customOption);

    const defaultPreset = options.find(item => item.key === '24h') || options[options.length - 1];
    select.value = defaultPreset ? defaultPreset.key : 'custom';
}

async function initAnalysisRangeControls() {
    try {
        const response = await fetch('/api/config');
        const json = await response.json();
        if (json && json.status === 'ok' && json.data) {
            const retention = Number(json.data.db_retention_days);
            const saveInterval = Number(json.data.db_save_interval);
            if (Number.isFinite(retention) && retention > 0) {
                analysisRetentionDays = Math.max(1, Math.floor(retention));
            }
            if (Number.isFinite(saveInterval) && saveInterval > 0) {
                analysisSaveIntervalSec = saveInterval;
            }
        }
    } catch (error) {
        console.warn('Nie udało się pobrać konfiguracji zakresów analizy:', error);
    }

    renderRangeOptions();

    const select = document.getElementById('ana-range-preset');
    const customWrap = document.getElementById('ana-range-custom');
    const startEl = document.getElementById('ana-range-start');
    const endEl = document.getElementById('ana-range-end');
    const applyBtn = document.getElementById('ana-range-apply');

    const now = Date.now();
    const min = now - analysisMaxHours() * 3600 * 1000;
    if (startEl && endEl) {
        startEl.min = datetimeLocalValue(min);
        endEl.min = datetimeLocalValue(min);
        startEl.max = datetimeLocalValue(now);
        endEl.max = datetimeLocalValue(now);
        endEl.value = datetimeLocalValue(now);
        startEl.value = datetimeLocalValue(now - 24 * 3600 * 1000);
    }

    if (select && customWrap) {
        select.addEventListener('change', () => {
            const isCustom = select.value === 'custom';
            customWrap.style.display = isCustom ? 'inline-flex' : 'none';
            if (!isCustom) {
                selectedAnalysisHours = getSelectedRangeHours();
                loadSummary24h(selectedAnalysisHours);
            }
        });
    }

    if (applyBtn) {
        applyBtn.addEventListener('click', () => {
            const customHours = getCustomRangeHours();
            if (!customHours) {
                alert(`Nieprawidłowy zakres. Maksymalny dozwolony zakres to ${formatDaysLabel(analysisRetentionDays)}.`);
                return;
            }
            selectedAnalysisHours = customHours;
            loadSummary24h(selectedAnalysisHours);
        });
    }

    selectedAnalysisHours = getSelectedRangeHours();
    updateRangeLabels(selectedAnalysisHours);
}

function modeLabel(modeCode, modeName) {
    const normalizedName = String(modeName || '').trim();
    if (normalizedName) {
        const normalized = normalizedName.toLowerCase();
        if (normalized === 'pauza') return 'Postój';

        const legacyMap = {
            wylaczony: 'Wyłączony',
            nadzor: 'Nadzór',
            reczny: 'Ręczny',
        };
        return legacyMap[normalized] || normalizedName;
    }
    return MODE_NAME_FALLBACKS[modeCode] || `Tryb ${modeCode}`;
}

function calculateMedian(values) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
}

function prepareHistoryRows(historyRows) {
    return Array.isArray(historyRows)
        ? historyRows
            .filter(r => r && Number.isFinite(Number(r.timestamp)))
            .map(r => ({ ...r, timestamp: Number(r.timestamp) }))
            .sort((a, b) => a.timestamp - b.timestamp)
        : [];
}

function computeSamplingMetrics(rows, windowHours = 24) {
    const intervals = [];
    for (let i = 1; i < rows.length; i++) {
        const diff = rows[i].timestamp - rows[i - 1].timestamp;
        if (diff > 0 && diff <= 3600) intervals.push(diff);
    }
    const medianGapSec = calculateMedian(intervals) || 60;
    const freshnessThresholdSec = Math.max(180, medianGapSec * 3);
    const nowTs = Math.floor(Date.now() / 1000);
    const windowSeconds = Math.max(1, Number(windowHours) * 3600);
    const windowStart = nowTs - windowSeconds;
    return { medianGapSec, freshnessThresholdSec, nowTs, windowStart, windowSeconds };
}

function computeModeShareForWindow(historyRows, windowHours = 24, modeWhitelist = null) {
    const rows = prepareHistoryRows(historyRows);
    if (!rows.length) return [];

    const {
        freshnessThresholdSec,
        nowTs,
        windowStart,
        windowSeconds,
    } = computeSamplingMetrics(rows, windowHours);

    const modeSeconds = new Map();
    const modeNames = new Map();

    for (let i = 0; i < rows.length; i++) {
        const current = rows[i];
        const next = rows[i + 1];
        const segStart = Math.max(current.timestamp, windowStart);
        const segEnd = next
            ? Math.min(next.timestamp, nowTs)
            : Math.min(nowTs, current.timestamp + freshnessThresholdSec);
        const duration = Math.max(0, segEnd - segStart);
        if (duration <= 0 || !Number.isFinite(Number(current.mode))) continue;

        const mode = Number(current.mode);
        if (Array.isArray(modeWhitelist) && !modeWhitelist.includes(mode)) continue;

        modeSeconds.set(mode, (modeSeconds.get(mode) || 0) + duration);
        if (!modeNames.has(mode)) {
            modeNames.set(mode, modeLabel(mode, current.mode_name));
        }
    }

    return sortModeShareForVisualConsistency([...modeSeconds.entries()]
        .map(([mode, seconds]) => ({
            mode,
            name: modeNames.get(mode) || `Tryb ${mode}`,
            seconds,
            percent: windowSeconds > 0 ? (seconds / windowSeconds) * 100 : 0,
            color: MODE_COLORS[mode] || '#777777',
        })));
}

function computeHistoryMetrics(historyRows, windowHours = 24) {
    const rows = prepareHistoryRows(historyRows);

    const {
        freshnessThresholdSec,
        nowTs,
        windowStart,
        windowSeconds,
    } = computeSamplingMetrics(rows, windowHours);

    const modeSeconds = new Map();
    const modeNames = new Map();
    let deltaSamples = 0;
    let deltaSum = 0;
    let deltaLowSeconds = 0;
    let deltaMin = null;
    let deltaMax = null;

    let minIgnitions = null;
    let maxIgnitions = null;
    let tempDropEvents = 0;
    let telemetryGapEvents = 0;
    let ignitionStartTs = null;
    const ignitionToWorkDurationsSec = [];
    let idleWorkSeconds = 0;

    let cwuCycleActive = false;
    let cwuCycleStartTs = null;
    let cwuCycleStartBoilerTemp = null;
    let cwuCycleDurationSec = 0;
    const cwuCycleDurationsSec = [];
    const cwuBoilerImpactValues = [];

    for (let i = 0; i < rows.length; i++) {
        const current = rows[i];
        const next = rows[i + 1];
        const prev = i > 0 ? rows[i - 1] : null;
        const currentMode = Number(current.mode);
        const prevMode = prev ? Number(prev.mode) : null;

        if (currentMode === 2 && prevMode !== 2 && ignitionStartTs === null) {
            ignitionStartTs = current.timestamp;
        }

        if (ignitionStartTs !== null && currentMode === 3 && prevMode !== 3) {
            const durationSec = current.timestamp - ignitionStartTs;
            if (durationSec > 0 && durationSec <= 6 * 3600) {
                ignitionToWorkDurationsSec.push(durationSec);
            }
            ignitionStartTs = null;
        }

        if (ignitionStartTs !== null && (current.timestamp - ignitionStartTs) > 6 * 3600) {
            ignitionStartTs = null;
        }

        if (next && (next.timestamp - current.timestamp) > 180) {
            telemetryGapEvents += 1;
        }
        const segStart = Math.max(current.timestamp, windowStart);
        const segEnd = next
            ? Math.min(next.timestamp, nowTs)
            : Math.min(nowTs, current.timestamp + freshnessThresholdSec);
        const duration = Math.max(0, segEnd - segStart);

        if (duration > 0 && Number.isFinite(Number(current.mode))) {
            const mode = Number(current.mode);
            modeSeconds.set(mode, (modeSeconds.get(mode) || 0) + duration);
            if (!modeNames.has(mode)) {
                modeNames.set(mode, modeLabel(mode, current.mode_name));
            }
        }

        if (Number.isFinite(Number(current.temp_co)) && Number.isFinite(Number(current.temp_back))) {
            const delta = Number(current.temp_co) - Number(current.temp_back);
            deltaSum += delta;
            deltaSamples += 1;
            deltaMin = deltaMin === null ? delta : Math.min(deltaMin, delta);
            deltaMax = deltaMax === null ? delta : Math.max(deltaMax, delta);
            if (delta < 8 && duration > 0) {
                deltaLowSeconds += duration;
            }
        }

        if (
            next
            && duration > 0
            && Number.isFinite(Number(current.temp_co))
            && Number.isFinite(Number(next.temp_co))
        ) {
            const dtSec = Math.max(1, next.timestamp - current.timestamp);
            const dTemp = Number(next.temp_co) - Number(current.temp_co);
            const risePerMinute = dTemp / (dtSec / 60);
            const hasActiveHeat = (Number(current.boiler_power) >= 25) || (Number(current.fan_power) >= 30);
            if (hasActiveHeat && risePerMinute < 0.03) {
                idleWorkSeconds += duration;
            }
        }

        if (Number.isFinite(Number(current.temp_cwu_set)) && Number.isFinite(Number(current.temp_cwu))) {
            const cwu = Number(current.temp_cwu);
            const cwuSet = Number(current.temp_cwu_set);
            const prevCwu = prev && Number.isFinite(Number(prev.temp_cwu)) ? Number(prev.temp_cwu) : null;
            const startThreshold = cwuSet - 2;
            const finishThreshold = cwuSet - 0.5;

            if (!cwuCycleActive) {
                const crossedDown = prevCwu !== null ? (prevCwu > startThreshold && cwu <= startThreshold) : (cwu <= startThreshold);
                if (crossedDown) {
                    cwuCycleActive = true;
                    cwuCycleStartTs = current.timestamp;
                    cwuCycleStartBoilerTemp = Number.isFinite(Number(current.temp_co)) ? Number(current.temp_co) : null;
                    cwuCycleDurationSec = 0;
                }
            }

            if (cwuCycleActive && duration > 0) {
                cwuCycleDurationSec += duration;
                const finished = cwu >= finishThreshold;
                const timeout = cwuCycleDurationSec > (4 * 3600);
                if (finished || timeout) {
                    if (finished) {
                        cwuCycleDurationsSec.push(cwuCycleDurationSec);
                        if (
                            cwuCycleStartBoilerTemp !== null
                            && Number.isFinite(Number(current.temp_co))
                        ) {
                            cwuBoilerImpactValues.push(Number(current.temp_co) - cwuCycleStartBoilerTemp);
                        }
                    }
                    cwuCycleActive = false;
                    cwuCycleStartTs = null;
                    cwuCycleStartBoilerTemp = null;
                    cwuCycleDurationSec = 0;
                }
            }
        }

        if (
            next
            && Number.isFinite(Number(current.temp_co))
            && Number.isFinite(Number(next.temp_co))
        ) {
            const tempDrop = Number(current.temp_co) - Number(next.temp_co);
            const dt = next.timestamp - current.timestamp;
            if (tempDrop >= 8 && dt > 0 && dt <= 1800) {
                tempDropEvents += 1;
            }
        }

        if (Number.isFinite(Number(current.ignitions))) {
            const value = Number(current.ignitions);
            minIgnitions = minIgnitions === null ? value : Math.min(minIgnitions, value);
            maxIgnitions = maxIgnitions === null ? value : Math.max(maxIgnitions, value);
        }
    }

    const workSeconds = modeSeconds.get(3) || 0;
    const workSharePct = (workSeconds / windowSeconds) * 100;
    const ignitionsDelta = (minIgnitions !== null && maxIgnitions !== null)
        ? Math.max(0, maxIgnitions - minIgnitions)
        : null;
    const deltaAvg = deltaSamples > 0 ? (deltaSum / deltaSamples) : null;
    const ignitionToWorkAvgMinutes = ignitionToWorkDurationsSec.length > 0
        ? ignitionToWorkDurationsSec.reduce((sum, value) => sum + value, 0) / ignitionToWorkDurationsSec.length / 60
        : null;
    const cwuHeatCycles = cwuCycleDurationsSec.length;
    const cwuHeatAvgMinutes = cwuHeatCycles > 0
        ? cwuCycleDurationsSec.reduce((sum, value) => sum + value, 0) / cwuHeatCycles / 60
        : null;
    const cwuHeatTotalMinutes = cwuCycleDurationsSec.reduce((sum, value) => sum + value, 0) / 60;
    const cwuBoilerImpactAvg = cwuBoilerImpactValues.length > 0
        ? cwuBoilerImpactValues.reduce((sum, value) => sum + value, 0) / cwuBoilerImpactValues.length
        : null;

    const modeShare = sortModeShareForVisualConsistency([...modeSeconds.entries()]
        .map(([mode, seconds]) => ({
            mode,
            name: modeNames.get(mode) || `Tryb ${mode}`,
            seconds,
            percent: (seconds / windowSeconds) * 100,
            color: MODE_COLORS[mode] || '#777777',
        })));

    return {
        workSharePct,
        ignitionsDelta,
        deltaAvg,
        deltaLowMinutes: deltaLowSeconds / 60,
        tempDropEvents,
        telemetryGapEvents,
        ignitionToWorkAvgMinutes,
        ignitionToWorkCycles: ignitionToWorkDurationsSec.length,
        deltaMin,
        deltaMax,
        cwuHeatCycles,
        cwuHeatAvgMinutes,
        cwuHeatTotalMinutes,
        cwuBoilerImpactAvg,
        idleWorkMinutes: idleWorkSeconds / 60,
        modeShare,
    };
}

function severityLabel(level) {
    if (level === 'Wysoki') return 'Wysoki';
    if (level === 'Umiarkowany') return 'Umiarkowany';
    return 'Informacja';
}

function buildDailyAnomalies(metrics) {
    const anomalies = [];

    if (Number.isFinite(Number(metrics.deltaLowMinutes)) && Number(metrics.deltaLowMinutes) >= 180) {
        anomalies.push({
            category: 'Niska sprawność obiegu',
            level: Number(metrics.deltaLowMinutes) >= 360 ? 'Wysoki' : 'Umiarkowany',
            details: `ΔT < 8°C przez ${formatDurationMinutes(metrics.deltaLowMinutes)}.`,
        });
    }

    if (Number.isFinite(Number(metrics.ignitionsDelta)) && Number(metrics.ignitionsDelta) >= 3) {
        anomalies.push({
            category: 'Częste rozpalenia',
            level: Number(metrics.ignitionsDelta) >= 6 ? 'Wysoki' : 'Umiarkowany',
            details: `${metrics.ignitionsDelta} rozpaleń w wybranym zakresie.`,
        });
    }

    if (Number.isFinite(Number(metrics.tempDropEvents)) && Number(metrics.tempDropEvents) >= 2) {
        anomalies.push({
            category: 'Spadki temperatury CO',
            level: Number(metrics.tempDropEvents) >= 4 ? 'Wysoki' : 'Umiarkowany',
            details: `${metrics.tempDropEvents} szybkich spadków (>=8°C).`,
        });
    }

    if (Number.isFinite(Number(metrics.telemetryGapEvents)) && Number(metrics.telemetryGapEvents) >= 3) {
        anomalies.push({
            category: 'Jakość danych',
            level: Number(metrics.telemetryGapEvents) >= 8 ? 'Wysoki' : 'Umiarkowany',
            details: `${metrics.telemetryGapEvents} luk telemetrii (>180 s) w wybranym zakresie.`,
        });
    }

    if (Number.isFinite(Number(metrics.rejectedFrames)) && Number(metrics.rejectedFrames) > 0) {
        anomalies.push({
            category: 'Odrzucone ramki',
            level: Number(metrics.rejectedFrames) >= 50 ? 'Wysoki' : 'Informacja',
            details: `Łącznie odrzuconych ramek: ${metrics.rejectedFrames}.`,
        });
    }

    if (Number.isFinite(Number(metrics.idleWorkMinutes)) && Number(metrics.idleWorkMinutes) >= 60) {
        anomalies.push({
            category: 'Jałowa praca',
            level: Number(metrics.idleWorkMinutes) >= 180 ? 'Wysoki' : 'Umiarkowany',
            details: `${formatDurationMinutes(metrics.idleWorkMinutes)} aktywnej pracy przy niskim przyroście temperatury CO.`,
        });
    }

    return anomalies;
}

function renderAnomalies(metrics) {
    const tbody = document.getElementById('anomalies-body');
    if (!tbody) return;

    const anomalies = buildDailyAnomalies(metrics);
    if (anomalies.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color: var(--hint-color);">Brak istotnych anomalii w wybranym zakresie</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    anomalies.forEach(item => {
        const tr = document.createElement('tr');
        const values = [item.category, severityLabel(item.level), item.details];
        values.forEach(value => {
            const td = document.createElement('td');
            td.textContent = String(value);
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
}

function dayPartFromHour(hour) {
    if (hour >= 6 && hour < 12) return 'rano';
    if (hour >= 12 && hour < 22) return 'popoludnie';
    return 'noc';
}

function dayPartLabel(key) {
    if (key === 'rano') return 'Rano (06:00–11:59)';
    if (key === 'popoludnie') return 'Popołudnie (12:00–21:59)';
    return 'Noc (22:00–05:59)';
}

function computeDayPartBreakdown(historyRows, pricePerTon, windowHours = 24) {
    const rows = prepareHistoryRows(historyRows);
    const { nowTs, windowStart, freshnessThresholdSec } = computeSamplingMetrics(rows, windowHours);

    const groups = {
        noc: { fuelKg: 0, samples: 0 },
        rano: { fuelKg: 0, samples: 0 },
        popoludnie: { fuelKg: 0, samples: 0 },
    };

    for (let i = 0; i < rows.length; i++) {
        const current = rows[i];
        const next = rows[i + 1];

        const segStart = Math.max(current.timestamp, windowStart);
        const segEnd = next
            ? Math.min(next.timestamp, nowTs)
            : Math.min(nowTs, current.timestamp + freshnessThresholdSec);
        const durationSec = Math.max(0, segEnd - segStart);
        if (durationSec <= 0) continue;

        const fuelStream = Number(current.fuel_stream);
        if (!Number.isFinite(fuelStream) || fuelStream < 0 || fuelStream > 500) continue;

        const part = dayPartFromHour(new Date(current.timestamp * 1000).getHours());
        groups[part].fuelKg += fuelStream * (durationSec / 3600);
        groups[part].samples += 1;
    }

    const totalFuel = Object.values(groups).reduce((sum, item) => sum + item.fuelKg, 0);
    const validPricePerTon = Number.isFinite(Number(pricePerTon)) ? Number(pricePerTon) : 1000;

    return ['rano', 'popoludnie', 'noc'].map(key => {
        const fuelKg = groups[key].fuelKg;
        const costPln = (fuelKg / 1000) * validPricePerTon;
        const sharePct = totalFuel > 0 ? (fuelKg / totalFuel) * 100 : 0;
        return {
            key,
            label: dayPartLabel(key),
            fuelKg,
            costPln,
            sharePct,
            samples: groups[key].samples,
        };
    });
}

function renderDayPartBreakdown(rows) {
    const tbody = document.getElementById('dayparts-body');
    if (!tbody) return;

    const items = Array.isArray(rows) ? rows : [];
    const hasData = items.some(item => Number(item.fuelKg) > 0);

    if (!hasData) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color: var(--hint-color);">Brak danych o paliwie do podziału na pory dnia</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    items.forEach(item => {
        const tr = document.createElement('tr');
        const values = [
            item.label,
            `${formatNumber(item.fuelKg, 2)}`,
            `${formatNumber(item.costPln, 2)}`,
            `${formatNumber(item.sharePct, 1)}%`,
        ];
        values.forEach(value => {
            const td = document.createElement('td');
            td.textContent = String(value);
            tr.appendChild(td);
        });
        tbody.appendChild(tr);
    });
}

function renderModeShareChart(modeShare) {
    const canvas = document.getElementById('mode-share-chart');
    if (!canvas || typeof Chart === 'undefined') {
        setChartEmptyState('mode-share-empty', true, 'Brak możliwości renderowania wykresu trybów.');
        return;
    }

    if (modeShareChart) {
        modeShareChart.destroy();
        modeShareChart = null;
    }

    if (!modeShare || modeShare.length === 0) {
        setChartEmptyState('mode-share-empty', true, 'Brak danych do wykresu udziału trybów w wybranym zakresie.');
        return;
    }

    setChartEmptyState('mode-share-empty', false);

    modeShareChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: [formatRangeShort(selectedAnalysisHours)],
            datasets: modeShare.map(item => ({
                label: `${item.name} (${formatNumber(item.percent, 1)}%)`,
                data: [Number(item.percent.toFixed(2))],
                backgroundColor: item.color,
                borderWidth: 0,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            indexAxis: 'y',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { boxWidth: 14 },
                },
            },
            scales: {
                x: {
                    stacked: true,
                    min: 0,
                    max: 100,
                    title: { display: true, text: 'Udział [%]' },
                },
                y: {
                    stacked: true,
                },
            },
        },
    });
}

function renderFuelCostChart(rows) {
    const canvas = document.getElementById('fuel-cost-chart');
    if (!canvas || typeof Chart === 'undefined') {
        setChartEmptyState('fuel-cost-empty', true, 'Brak możliwości renderowania wykresu paliwa i kosztu.');
        return;
    }

    if (fuelCostChart) {
        fuelCostChart.destroy();
        fuelCostChart = null;
    }

    if (!Array.isArray(rows) || rows.length === 0) {
        setChartEmptyState('fuel-cost-empty', true, 'Brak danych do wykresu paliwa i kosztu (7 dni).');
        return;
    }

    setChartEmptyState('fuel-cost-empty', false);

    const labels = rows.map(item => formatDateLabel(item.day || ''));
    const fuelData = rows.map(item => Number(item.fuel_kg) || 0);
    const costData = rows.map(item => Number(item.cost_pln) || 0);

    fuelCostChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Paliwo (kg)',
                    data: fuelData,
                    backgroundColor: '#e67e22',
                    yAxisID: 'yFuel',
                },
                {
                    label: 'Koszt (PLN)',
                    data: costData,
                    borderColor: '#4ecca3',
                    backgroundColor: 'rgba(78, 204, 163, 0.22)',
                    type: 'line',
                    tension: 0.25,
                    yAxisID: 'yCost',
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { position: 'bottom' } },
            scales: {
                yFuel: {
                    type: 'linear',
                    position: 'left',
                    title: { display: true, text: 'kg' },
                },
                yCost: {
                    type: 'linear',
                    position: 'right',
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: 'PLN' },
                },
            },
        },
    });
}

function renderDeltaTrendChart(historyRows) {
    const canvas = document.getElementById('delta-trend-chart');
    if (!canvas || typeof Chart === 'undefined') {
        setChartEmptyState('delta-trend-empty', true, 'Brak możliwości renderowania trendu ΔT.');
        return;
    }

    if (deltaTrendChart) {
        deltaTrendChart.destroy();
        deltaTrendChart = null;
    }

    const points = prepareHistoryRows(historyRows)
        .filter(row => Number.isFinite(Number(row.temp_co)) && Number.isFinite(Number(row.temp_back)))
        .map(row => ({
            x: row.timestamp * 1000,
            y: Number(row.temp_co) - Number(row.temp_back),
        }));

    if (!points.length) {
        setChartEmptyState('delta-trend-empty', true, 'Brak danych do trendu ΔT w wybranym zakresie.');
        return;
    }

    setChartEmptyState('delta-trend-empty', false);

    deltaTrendChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: points.map(point => new Date(point.x).toLocaleTimeString('pl-PL', { hour: '2-digit', minute: '2-digit' })),
            datasets: [
                {
                    label: 'ΔT CO–powrót [°C]',
                    data: points.map(point => Number(point.y.toFixed(2))),
                    borderColor: '#3498db',
                    backgroundColor: 'rgba(52, 152, 219, 0.18)',
                    fill: true,
                    tension: 0.2,
                    pointRadius: 0,
                },
                {
                    label: 'Próg 8°C',
                    data: points.map(() => 8),
                    borderColor: '#d84059',
                    borderDash: [6, 4],
                    pointRadius: 0,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { position: 'bottom' } },
            scales: {
                y: {
                    title: { display: true, text: '°C' },
                },
            },
        },
    });
}

function renderModeShareCompareChart(modeShare24h, modeShare7d, selectedHours = selectedAnalysisHours) {
    const canvas = document.getElementById('mode-share-compare-chart');
    if (!canvas || typeof Chart === 'undefined') {
        setChartEmptyState('mode-share-compare-empty', true, 'Brak możliwości renderowania porównania trybów.');
        return;
    }

    if (modeShareCompareChart) {
        modeShareCompareChart.destroy();
        modeShareCompareChart = null;
    }

    const map24 = new Map((modeShare24h || []).map(item => [item.mode, item]));
    const map7 = new Map((modeShare7d || []).map(item => [item.mode, item]));

    const datasets = MODE_COMPARE_ORDER.map(mode => {
        const from24 = map24.get(mode);
        const from7 = map7.get(mode);
        const preferredName = modeLabel(mode, (from24 && from24.name) || (from7 && from7.name));
        return {
            label: preferredName,
            data: [
                Number(((from24 && from24.percent) || 0).toFixed(2)),
                Number(((from7 && from7.percent) || 0).toFixed(2)),
            ],
            backgroundColor: MODE_COLORS[mode] || '#777777',
            borderWidth: 0,
        };
    });

    const hasAnyData = datasets.some(ds => ds.data.some(v => v > 0));
    if (!hasAnyData) {
        setChartEmptyState('mode-share-compare-empty', true, 'Brak danych do porównania czasu trybów.');
        return;
    }

    setChartEmptyState('mode-share-compare-empty', false);

    modeShareCompareChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: [formatRangeShort(selectedHours), '7 dni'],
            datasets,
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom' } },
            scales: {
                x: { stacked: true },
                y: {
                    stacked: true,
                    min: 0,
                    max: 100,
                    title: { display: true, text: 'Udział [%]' },
                },
            },
        },
    });
}

function renderWeeklyCompare(rows14) {
    const tbody = document.getElementById('weekly-compare-body');
    if (!tbody) return;

    const rows = Array.isArray(rows14) ? rows14 : [];
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color: var(--hint-color);">Brak danych do porównania tygodniowego</td></tr>';
        return;
    }

    const byDay = new Map(rows.map(item => [item.day, item]));
    const last7 = rows.slice(-7);

    if (!last7.length) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color: var(--hint-color);">Brak danych do porównania tygodniowego</td></tr>';
        return;
    }

    tbody.innerHTML = '';
    last7.forEach(item => {
        const dayDate = new Date(`${item.day}T00:00:00`);
        const prevDate = new Date(dayDate);
        prevDate.setDate(prevDate.getDate() - 7);
        const prevKey = `${prevDate.getFullYear()}-${String(prevDate.getMonth() + 1).padStart(2, '0')}-${String(prevDate.getDate()).padStart(2, '0')}`;
        const prev = byDay.get(prevKey);

        const currentFuel = Number(item.fuel_kg) || 0;
        const prevFuel = prev ? (Number(prev.fuel_kg) || 0) : null;
        const diff = prevFuel === null ? null : (currentFuel - prevFuel);

        const tr = document.createElement('tr');
        const values = [
            formatDateLabel(item.day),
            formatNumber(currentFuel, 2),
            prevFuel === null ? '--' : formatNumber(prevFuel, 2),
            diff === null ? '--' : `${diff > 0 ? '+' : ''}${formatNumber(diff, 2)} kg`,
        ];

        values.forEach(value => {
            const td = document.createElement('td');
            td.textContent = String(value);
            tr.appendChild(td);
        });

        tbody.appendChild(tr);
    });
}

async function loadSummary24h(hours = 24) {
    const boundedHours = Math.min(Math.max(Number(hours) || 24, 1), analysisMaxHours());
    const boundedDays = Math.max(1, Math.min(90, Math.ceil(boundedHours / 24), analysisRetentionDays));
    const historyLimit = historyLimitForHours(boundedHours);

    updateRangeLabels(boundedHours);

    const reportLabel = document.getElementById('ana-report-days-label');
    if (reportLabel) reportLabel.textContent = `(${formatDaysLabel(boundedDays)})`;
    const exportLabel = document.getElementById('ana-export-label');
    if (exportLabel) exportLabel.textContent = `Pobierz CSV (${formatDaysLabel(boundedDays)})`;
    const exportLink = document.getElementById('export-link');
    if (exportLink) exportLink.href = `/api/export?hours=${boundedDays * 24}`;

    try {
        const [statsResp, costsResp, historyResp, history7Resp, statusResp, report14Resp] = await Promise.all([
            fetch(`/api/statistics?hours=${boundedHours}`),
            fetch(`/api/costs?hours=${boundedHours}`),
            fetch(`/api/history?hours=${boundedHours}&limit=${historyLimit}`),
            fetch('/api/history?hours=168&limit=5000'),
            fetch('/api/status'),
            fetch(`/api/report?days=${Math.max(14, boundedDays)}`),
        ]);

        const statsJson = await statsResp.json();
        const costsJson = await costsResp.json();
        const historyJson = await historyResp.json();
        const history7Json = await history7Resp.json();
        const statusJson = await statusResp.json();
        const report14Json = await report14Resp.json();

        if (statsJson.status !== 'ok') {
            throw new Error(statsJson.message || 'Nie udało się pobrać statystyk');
        }
        if (costsJson.status !== 'ok') {
            throw new Error(costsJson.message || 'Nie udało się pobrać kosztów');
        }

        const statsData = statsJson.data || {};
        const costsData = costsJson.data || {};
        const historyRows = (historyJson && historyJson.status === 'ok' && Array.isArray(historyJson.data))
            ? historyJson.data
            : [];
        const historyRows7d = (history7Json && history7Json.status === 'ok' && Array.isArray(history7Json.data))
            ? history7Json.data
            : [];
        const reportRows14 = (report14Json && report14Json.status === 'ok' && Array.isArray(report14Json.data))
            ? report14Json.data
            : [];
        const rejectedFrames = statusJson && statusJson.connection
            ? Number(statusJson.connection.errors)
            : null;

        const avgPower = statsData.boiler_power?.avg;
        const avgFuelRate = statsData.fuel_stream?.avg;
        const fuelRange = Number.isFinite(Number(avgFuelRate)) ? Number(avgFuelRate) * boundedHours : null;
        const costPerDay = Number(costsData.cost_per_day);
        const costPerMonth = Number.isFinite(costPerDay) ? costPerDay * 30 : null;

        const metrics = computeHistoryMetrics(historyRows, boundedHours);
        const dayPartBreakdown = computeDayPartBreakdown(historyRows, costsData.price_per_ton, boundedHours);
        const modeShare7d = computeModeShareForWindow(historyRows7d, 168, MODE_COMPARE_ORDER);

        setText('ana-power-avg', `${formatNumber(avgPower, 1)}%`);
        setText('ana-fuel-24h', `${formatNumber(fuelRange, 2)} kg`);
        setText('ana-cost-day', `${formatNumber(costPerDay, 2)} PLN`);
        setText('ana-cost-month', `${formatNumber(costPerMonth, 2)} PLN`);
        setText('ana-work-share', `${formatNumber(metrics.workSharePct, 1)}%`);
        setText('ana-ignitions-24h', metrics.ignitionsDelta === null ? '--' : String(metrics.ignitionsDelta));
        setText(
            'ana-ignition-to-work',
            metrics.ignitionToWorkAvgMinutes === null ? '--' : formatDurationMinutes(metrics.ignitionToWorkAvgMinutes),
        );
        setText('ana-eff-avg', `${formatNumber(metrics.deltaAvg, 1)}°C`);
        setText('ana-eff-low-time', formatDurationMinutes(metrics.deltaLowMinutes));
        setText(
            'ana-delta-minmax',
            (metrics.deltaMin === null || metrics.deltaMax === null)
                ? '--'
                : `${formatNumber(metrics.deltaMin, 1)} / ${formatNumber(metrics.deltaMax, 1)}°C`,
        );
        setText('ana-cwu-cycles', String(metrics.cwuHeatCycles || 0));
        setText(
            'ana-cwu-heat-time',
            metrics.cwuHeatTotalMinutes > 0 ? formatDurationMinutes(metrics.cwuHeatTotalMinutes) : '--',
        );
        setText(
            'ana-cwu-impact',
            metrics.cwuBoilerImpactAvg === null ? '--' : `${formatNumber(metrics.cwuBoilerImpactAvg, 1)}°C`,
        );
        setText('ana-idle-work-time', formatDurationMinutes(metrics.idleWorkMinutes));
        setText('ana-telemetry-gaps', String(metrics.telemetryGapEvents || 0));
        setText('ana-rejected-frames', Number.isFinite(rejectedFrames) ? String(rejectedFrames) : '--');

        renderModeShareChart(metrics.modeShare);
        renderModeShareCompareChart(metrics.modeShare, modeShare7d, boundedHours);
        renderDeltaTrendChart(historyRows);
        renderMixerTrendChart(historyRows);
        renderAnomalies({ ...metrics, rejectedFrames });
        renderDayPartBreakdown(dayPartBreakdown);
        renderWeeklyCompare(reportRows14);
        await loadReport(boundedDays);
    } catch (error) {
        console.error('Błąd ładowania podsumowania 24h:', error);
        setText('ana-power-avg', '--');
        setText('ana-fuel-24h', '--');
        setText('ana-cost-day', '--');
        setText('ana-cost-month', '--');
        setText('ana-work-share', '--');
        setText('ana-ignitions-24h', '--');
        setText('ana-ignition-to-work', '--');
        setText('ana-eff-avg', '--');
        setText('ana-eff-low-time', '--');
        setText('ana-delta-minmax', '--');
        setText('ana-cwu-cycles', '--');
        setText('ana-cwu-heat-time', '--');
        setText('ana-cwu-impact', '--');
        setText('ana-idle-work-time', '--');
        setText('ana-telemetry-gaps', '--');
        setText('ana-rejected-frames', '--');
        renderModeShareCompareChart([], []);
        renderDeltaTrendChart([]);
        renderAnomalies({});
        renderDayPartBreakdown([]);
        renderWeeklyCompare([]);
        await loadReport(7);
    }
}

async function loadReport(days = 7) {
    const tbody = document.getElementById('report-body');
    if (!tbody) {
        return;
    }

    try {
        const boundedDays = Math.max(1, Math.min(Number(days) || 7, analysisRetentionDays, 90));
        const response = await fetch(`/api/report?days=${boundedDays}`);
        const json = await response.json();

        if (json.status !== 'ok') {
            throw new Error(json.message || 'Nie udało się pobrać raportu');
        }

        const rows = json.data || [];
        if (rows.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color: var(--hint-color);">Brak danych za ${formatLastDaysPhrase(boundedDays)}</td></tr>`;
            renderFuelCostChart([]);
            return;
        }

        tbody.innerHTML = '';
        rows.forEach(item => {
            const tr = document.createElement('tr');
            const cells = [
                formatDateLabel(item.day || ''),
                formatNumber(item.fuel_kg, 2),
                formatNumber(item.cost_pln, 2),
                Number.isFinite(item.samples) ? item.samples : 0,
            ];
            cells.forEach(value => {
                const td = document.createElement('td');
                td.textContent = String(value);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        renderFuelCostChart(rows);
    } catch (error) {
        console.error('Błąd ładowania raportu:', error);
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color: var(--hint-color);">Błąd ładowania raportu</td></tr>';
        renderFuelCostChart([]);
    }
}

// ---- Mieszacz 1: zawór i temperatura ----

function renderMixerTrendChart(historyRows) {
    const canvas = document.getElementById('mixer-trend-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (mixerTrendChart) {
        mixerTrendChart.destroy();
        mixerTrendChart = null;
    }
    const rows = prepareHistoryRows(historyRows)
        .filter(row => Number.isFinite(Number(row.mixer1_temp)) || Number.isFinite(Number(row.mixer1_valve)));
    if (!rows.length) {
        setChartEmptyState('mixer-trend-empty', true, 'Brak danych mieszacza w wybranym zakresie (zapisywane od tej wersji aplikacji).');
        return;
    }
    setChartEmptyState('mixer-trend-empty', false);
    const num = (v) => (v === null || v === undefined || !Number.isFinite(Number(v)) ? null : Number(v));
    mixerTrendChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: rows.map(row => new Date(row.timestamp * 1000).toLocaleString('pl-PL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })),
            datasets: [
                { label: 'Temperatura mieszacza [°C]', data: rows.map(r => num(r.mixer1_temp)), borderColor: '#e8590c', pointRadius: 0, tension: 0.2, yAxisID: 'yTemp' },
                { label: 'Zadana [°C]', data: rows.map(r => num(r.mixer1_target)), borderColor: '#868e96', borderDash: [6, 4], pointRadius: 0, yAxisID: 'yTemp' },
                { label: 'Otwarcie zaworu [%]', data: rows.map(r => num(r.mixer1_valve)), borderColor: '#3498db', backgroundColor: 'rgba(52, 152, 219, 0.15)', fill: true, pointRadius: 0, stepped: true, yAxisID: 'yValve' },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { position: 'bottom' } },
            scales: {
                yTemp: { type: 'linear', position: 'left', title: { display: true, text: '°C' } },
                yValve: { type: 'linear', position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false }, title: { display: true, text: '%' } },
            },
        },
    });
}

// ---- Cykle pracy (dzienne, z każdej ramki) ----

function formatHoursMinutes(hours) {
    const total = Math.round(Number(hours || 0) * 60);
    const h = Math.floor(total / 60);
    const m = total % 60;
    return h ? `${h} h ${m} min` : `${m} min`;
}

function renderDailyModesChart(rows) {
    const canvas = document.getElementById('daily-modes-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (dailyModesChart) {
        dailyModesChart.destroy();
        dailyModesChart = null;
    }
    const withData = rows.filter(r => r.observed_hours > 0);
    if (!withData.length) {
        setChartEmptyState('daily-modes-empty', true, 'Brak danych – statystyki zbierane są od uruchomienia tej wersji aplikacji.');
        return;
    }
    setChartEmptyState('daily-modes-empty', false);
    const modes = [...new Set(withData.flatMap(r => Object.keys(r.modes).map(Number)))]
        .sort((a, b) => modeVisualRank(a) - modeVisualRank(b));
    dailyModesChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
            labels: withData.map(r => r.day),
            datasets: modes.map(mode => ({
                label: modeLabel(mode, (withData.find(r => r.modes[mode]) || {}).modes?.[mode]?.name),
                data: withData.map(r => Number(((r.modes[mode] || {}).hours || 0).toFixed(2))),
                backgroundColor: MODE_COLORS[mode] || '#777777',
                borderWidth: 0,
            })),
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom' } },
            scales: {
                x: { stacked: true },
                y: { stacked: true, suggestedMax: 24, title: { display: true, text: 'godziny' } },
            },
        },
    });
}

function renderDailyCyclesTable(rows) {
    const body = document.getElementById('daily-cycles-body');
    if (!body) return;
    body.textContent = '';
    if (!rows.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 13;
        td.textContent = 'Brak danych.';
        tr.appendChild(td);
        body.appendChild(tr);
        return;
    }
    for (const r of [...rows].reverse()) {
        const working = (r.modes['3'] || {}).hours || 0;
        const cells = [
            r.day,
            `${formatNumber(r.coverage_percent, 0)}%`,
            formatHoursMinutes(working),
            String(r.work_cycles),
            String(r.kindlings),
            String(r.kindling_failures),
            r.kindling_success_percent === null ? '--' : `${formatNumber(r.kindling_success_percent, 0)}%`,
            `${formatNumber(r.feeder_minutes, 1)} min`,
            formatHoursMinutes(r.fan_hours),
            `${formatNumber(r.energy_kwh, 1)} kWh`,
            `${formatNumber(r.fuel_kg, 1)} kg`,
            `${formatNumber(r.cost_pln, 2)} zł`,
            r.cost_per_kwh === null ? '--' : `${formatNumber(r.cost_per_kwh, 2)} zł`,
        ];
        const tr = document.createElement('tr');
        for (const text of cells) {
            const td = document.createElement('td');
            td.textContent = text;
            tr.appendChild(td);
        }
        body.appendChild(tr);
    }
}

async function loadDailyAnalytics() {
    try {
        const resp = await fetch('/api/analytics/daily?days=14');
        const json = await resp.json();
        const rows = json.status === 'ok' && Array.isArray(json.data) ? json.data : [];
        renderDailyModesChart(rows);
        renderDailyCyclesTable(rows);
    } catch (e) {
        setChartEmptyState('daily-modes-empty', true, 'Błąd pobierania statystyk dziennych: ' + e.message);
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    await initAnalysisRangeControls();
    await loadSummary24h(selectedAnalysisHours);
    await loadDailyAnalytics();
});
