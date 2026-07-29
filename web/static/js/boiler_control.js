/**
 * ecoMAX Monitor - Sterowanie kotłem (MVP)
 */

function showBoilerControlMsg(text, type) {
    const el = document.getElementById('boiler-control-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'settings-msg ' + type;
    el.style.display = 'block';
    window.scrollTo({ top: 0, behavior: 'smooth' });
    setTimeout(() => { el.style.display = 'none'; }, 4500);
}

function buildControlHeaders() {
    const headers = {
        'Content-Type': 'application/json',
        'X-Requested-With': 'ecoMAX-Monitor',
    };

    try {
        if (window.localStorage) {
            const token = window.localStorage.getItem('boilerControlAccessToken');
            if (token) {
                headers['X-Boiler-Control-Token'] = String(token).trim();
            }

            const serviceToken = window.sessionStorage?.getItem('boilerServiceAccessToken');
            if (serviceToken) {
                headers['X-Boiler-Service-Token'] = String(serviceToken).trim();
            }
        }
    } catch (e) {
        // Brak dostępu do Web Storage (localStorage/sessionStorage) - pomijamy nagłówki z tokenami.
    }

    return headers;
}

let parameterProfile = 'user';
let serviceProfileEnabled = false;
let serviceProfileGranted = false;

const profileRank = {
    user: 1,
    installer: 2,
    service: 3,
};

function normalizeProfile(value) {
    return (value === 'service' || value === 'installer' || value === 'user') ? value : 'user';
}

function getModeFromStorage() {
    try {
        const raw = window.localStorage?.getItem('boilerControlProfile');
        if (raw === 'service' || raw === 'installer' || raw === 'user') return raw;

        // Kompatybilność wsteczna po migracji basic/advanced -> user/installer.
        const legacy = window.localStorage?.getItem('boilerControlViewMode');
        if (legacy === 'advanced') return 'installer';
        if (legacy === 'basic') return 'user';
    } catch (e) {
        // Brak localStorage - użyj domyślnego trybu.
    }
    return 'user';
}

function saveModeToStorage(profile) {
    try {
        if (window.localStorage) {
            window.localStorage.setItem('boilerControlProfile', profile);
        }
    } catch (e) {
        // Ignoruj błędy zapisu preferencji.
    }
}

function applyParameterViewMode() {
    const cards = document.querySelectorAll('#boiler-control-list .settings-field[data-key]');
    cards.forEach((card) => {
        const profile = normalizeProfile(card.dataset.uiProfile || 'installer');
        const currentRank = profileRank[parameterProfile] || profileRank.user;
        const neededRank = profileRank[profile] || profileRank.installer;
        const hide = neededRank > currentRank;
        card.classList.toggle('is-hidden-by-mode', hide);
    });

    const userBtn = document.getElementById('btn-profile-user');
    const installerBtn = document.getElementById('btn-profile-installer');
    const serviceBtn = document.getElementById('btn-profile-service');
    if (userBtn) userBtn.classList.toggle('is-active', parameterProfile === 'user');
    if (installerBtn) installerBtn.classList.toggle('is-active', parameterProfile === 'installer');
    if (serviceBtn) {
        serviceBtn.classList.toggle('is-active', parameterProfile === 'service');
        serviceBtn.style.display = serviceProfileEnabled ? '' : 'none';
        serviceBtn.disabled = !serviceProfileGranted;
    }

    const sections = document.querySelectorAll('.settings-section[data-min-profile]');
    sections.forEach((section) => {
        const minProfile = normalizeProfile(section.dataset.minProfile || 'user');
        const currentRank = profileRank[parameterProfile] || profileRank.user;
        const neededRank = profileRank[minProfile] || profileRank.user;
        section.classList.toggle('is-hidden-by-profile', neededRank > currentRank);
    });

    const servicePanel = document.getElementById('boiler-service-access');
    const clearBtn = document.getElementById('btn-service-clear');
    if (servicePanel) {
        servicePanel.style.display = serviceProfileEnabled && !serviceProfileGranted ? '' : 'none';
    }
    if (clearBtn) {
        clearBtn.style.display = serviceProfileEnabled && serviceProfileGranted ? '' : 'none';
    }
}

function setParameterViewMode(profile) {
    const normalized = normalizeProfile(profile);
    if (normalized === 'service' && !serviceProfileGranted) {
        showBoilerControlMsg('Profil serwisowy wymaga poprawnego tokenu.', 'warning');
        return;
    }

    parameterProfile = normalized;
    saveModeToStorage(parameterProfile);
    applyParameterViewMode();
}

function setupParameterViewModeControls() {
    const userBtn = document.getElementById('btn-profile-user');
    const installerBtn = document.getElementById('btn-profile-installer');
    const serviceBtn = document.getElementById('btn-profile-service');
    if (!userBtn || !installerBtn) return;

    userBtn.addEventListener('click', () => setParameterViewMode('user'));
    installerBtn.addEventListener('click', () => setParameterViewMode('installer'));
    if (serviceBtn) {
        serviceBtn.addEventListener('click', () => setParameterViewMode('service'));
    }
    applyParameterViewMode();
}

async function refreshServiceAccessState(showError = false) {
    try {
        const resp = await fetch('/api/boiler-control/service-access', {
            headers: buildControlHeaders(),
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            throw new Error(json.message || 'Nie udało się sprawdzić dostępu serwisowego.');
        }

        serviceProfileEnabled = !!json.data?.enabled;
        serviceProfileGranted = !!json.data?.granted;

        if (!serviceProfileEnabled && parameterProfile === 'service') {
            parameterProfile = 'installer';
            saveModeToStorage(parameterProfile);
        }
        if (serviceProfileEnabled && !serviceProfileGranted && parameterProfile === 'service') {
            parameterProfile = 'installer';
            saveModeToStorage(parameterProfile);
        }

        applyParameterViewMode();
    } catch (e) {
        if (showError) {
            showBoilerControlMsg('Błąd sprawdzania profilu serwisowego: ' + e.message, 'error');
        }
    }
}

async function unlockServiceProfile() {
    const input = document.getElementById('service_token_input');
    const token = String(input?.value || '').trim();
    if (!token) {
        showBoilerControlMsg('Wprowadź token serwisowy.', 'error');
        return;
    }

    try {
        if (window.sessionStorage) {
            window.sessionStorage.setItem('boilerServiceAccessToken', token);
        }
    } catch (e) {
        // Brak sessionStorage - kontynuuj bez zapisu.
    }

    await refreshServiceAccessState(true);
    if (serviceProfileGranted) {
        showBoilerControlMsg('Profil serwisowy odblokowany.', 'ok');
        setParameterViewMode('service');
        if (input) input.value = '';
    } else {
        showBoilerControlMsg('Nieprawidłowy token serwisowy.', 'error');
    }
}

function clearServiceProfileToken() {
    try {
        window.sessionStorage?.removeItem('boilerServiceAccessToken');
    } catch (e) {
        // Ignoruj błędy usuwania.
    }

    serviceProfileGranted = false;
    if (parameterProfile === 'service') {
        setParameterViewMode('installer');
    }
    refreshServiceAccessState(false);
    showBoilerControlMsg('Usunięto token serwisowy z bieżącej sesji przeglądarki.', 'ok');
}

function setupServiceAccessControls() {
    const unlockBtn = document.getElementById('btn-service-unlock');
    const clearBtn = document.getElementById('btn-service-clear');
    const input = document.getElementById('service_token_input');
    if (unlockBtn) {
        unlockBtn.addEventListener('click', unlockServiceProfile);
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', clearServiceProfileToken);
    }
    if (input) {
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                unlockServiceProfile();
            }
        });
    }
}

function buildSelectOptions(param, minValue, maxValue) {
    const valueMap = param.value_map || {};
    const options = [];

    for (let value = minValue; value <= maxValue; value += 1) {
        options.push({
            value,
            label: valueMap[String(value)] || valueMap[value] || `Opcja ${value}`,
        });
    }

    return options;
}

function valueToText(param, value) {
    if (value === null || value === undefined) return '--';
    const intValue = parseInt(String(value), 10);
    if (Number.isNaN(intValue)) return String(value);

    if (param.value_map) {
        const mapped = param.value_map[String(intValue)] || param.value_map[intValue];
        if (mapped) return mapped;
    }

    if (param.unit) return `${intValue} ${param.unit}`;
    return String(intValue);
}

function renderParameterCard(param) {
    const wrapper = document.createElement('div');
    wrapper.className = 'settings-field';
    wrapper.dataset.key = param.key;
    wrapper.dataset.uiProfile = normalizeProfile(param.ui_profile || 'installer');

    const title = document.createElement('label');
    title.textContent = `${param.label} (${param.group})`;
    wrapper.appendChild(title);

    if (param.description) {
        const desc = document.createElement('span');
        desc.className = 'settings-hint';
        desc.style.marginTop = '-2px';
        desc.textContent = param.description;
        wrapper.appendChild(desc);
    }

    if (param.ha_entity_id) {
        const entity = document.createElement('span');
        entity.className = 'settings-hint';
        entity.textContent = `Encja HA: ${param.ha_entity_id}`;
        wrapper.appendChild(entity);
    }

    // Parametr niedostępny w firmware sterownika
    if (param.available === false) {
        const unavailable = document.createElement('span');
        unavailable.className = 'settings-hint';
        unavailable.style.color = 'var(--color-warning, #b45309)';
        unavailable.textContent = 'Parametr niedostępny w tym sterowniku.';
        wrapper.appendChild(unavailable);
        wrapper.style.opacity = '0.55';
        return wrapper;
    }

    const current = document.createElement('div');
    current.className = 'settings-hint';
    current.style.marginBottom = '0.5rem';
    current.textContent = `Aktualna wartość: ${valueToText(param, param.current_value)}`;
    wrapper.appendChild(current);

    let inputEl;
    if ((param.widget === 'switch' || param.widget === 'select') && param.value_map) {
        inputEl = document.createElement('select');
        inputEl.id = `param-${param.key}`;
        const options = buildSelectOptions(param, Number(param.min_value), Number(param.max_value));
        for (const optionDef of options) {
            const option = document.createElement('option');
            option.value = String(optionDef.value);
            option.textContent = optionDef.label;
            inputEl.appendChild(option);
        }
        if (param.current_value !== null && param.current_value !== undefined) {
            inputEl.value = String(param.current_value);
        }
    } else {
        inputEl = document.createElement('input');
        inputEl.id = `param-${param.key}`;
        inputEl.type = 'number';
        inputEl.min = String(param.min_value);
        inputEl.max = String(param.max_value);
        inputEl.step = String(param.step || 1);
        if (param.current_value !== null && param.current_value !== undefined) {
            inputEl.value = String(param.current_value);
        }
    }
    wrapper.appendChild(inputEl);

    const rangeHint = document.createElement('span');
    rangeHint.className = 'settings-hint';
    const unit = param.unit ? ` ${param.unit}` : '';
    rangeHint.textContent = `Zakres: ${param.min_value} - ${param.max_value}${unit}`;
    wrapper.appendChild(rangeHint);

    const btn = document.createElement('button');
    btn.className = 'btn-secondary';
    btn.style.marginTop = '0.75rem';
    btn.textContent = 'Zapisz parametr';
    btn.addEventListener('click', () => setParameter(param, inputEl, current));
    wrapper.appendChild(btn);

    return wrapper;
}

async function setParameter(param, inputEl, currentEl) {
    const rawValue = inputEl.value;
    const value = parseInt(rawValue, 10);

    if (Number.isNaN(value)) {
        showBoilerControlMsg('Podaj poprawną wartość liczbową.', 'error');
        return;
    }

    if (value < param.min_value || value > param.max_value) {
        showBoilerControlMsg(`Wartość poza zakresem ${param.min_value}-${param.max_value}.`, 'error');
        return;
    }

    try {
        const resp = await fetch('/api/boiler-control/set-parameter', {
            method: 'POST',
            headers: buildControlHeaders(),
            body: JSON.stringify({ key: param.key, value }),
        });

        const json = await resp.json();
        if (json.status !== 'ok') {
            showBoilerControlMsg(json.message || 'Błąd zapisu parametru.', 'error');
            return;
        }

        currentEl.textContent = `Aktualna wartość: ${valueToText(param, json.data.value)}`;
        showBoilerControlMsg(`Zapisano: ${param.label} = ${valueToText(param, json.data.value)}`, 'ok');
    } catch (e) {
        showBoilerControlMsg('Błąd komunikacji: ' + e.message, 'error');
    }
}

async function loadBoilerControlParameters() {
    const listEl = document.getElementById('boiler-control-list');
    if (!listEl) return;

    listEl.innerHTML = '';

    try {
        const resp = await fetch('/api/boiler-control/parameters');
        const json = await resp.json();
        if (json.status !== 'ok' || !Array.isArray(json.data)) {
            showBoilerControlMsg('Nie udało się pobrać listy parametrów sterowania.', 'error');
            return;
        }

        for (const param of json.data) {
            listEl.appendChild(renderParameterCard(param));
        }
        applyParameterViewMode();
    } catch (e) {
        showBoilerControlMsg('Błąd ładowania parametrów: ' + e.message, 'error');
    }
}

function formatHistoryTimestamp(ts) {
    const num = Number(ts);
    if (!Number.isFinite(num) || num <= 0) return '--';
    return new Date(num * 1000).toLocaleString('pl-PL');
}

async function loadBoilerControlHistory() {
    const body = document.getElementById('boiler-control-history-body');
    if (!body) return;

    try {
        const resp = await fetch('/api/boiler-control/history?limit=100');
        const json = await resp.json();
        body.textContent = '';

        if (json.status !== 'ok' || !Array.isArray(json.data)) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 6;
            td.className = 'parameters-empty';
            td.textContent = 'Nie udało się pobrać historii.';
            tr.appendChild(td);
            body.appendChild(tr);
            return;
        }

        if (json.data.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 6;
            td.className = 'parameters-empty';
            td.textContent = 'Brak wpisów historii.';
            tr.appendChild(td);
            body.appendChild(tr);
            return;
        }

        for (const row of json.data) {
            const target = `${row.target_type || '--'}${row.target_index !== null && row.target_index !== undefined ? `:${row.target_index}` : ''}`;
            const change = `${row.old_value ?? '--'} → ${row.new_value ?? '--'}`;
            const tr = document.createElement('tr');

            const tdTimestamp = document.createElement('td');
            tdTimestamp.textContent = formatHistoryTimestamp(row.timestamp);
            tr.appendChild(tdTimestamp);

            const tdTarget = document.createElement('td');
            tdTarget.textContent = target;
            tr.appendChild(tdTarget);

            const tdParam = document.createElement('td');
            tdParam.textContent = `${row.param_key || '--'} (#${row.param_index ?? '--'})`;
            tr.appendChild(tdParam);

            const tdChange = document.createElement('td');
            tdChange.textContent = change;
            tr.appendChild(tdChange);

            const tdStatus = document.createElement('td');
            tdStatus.textContent = row.status || '--';
            tr.appendChild(tdStatus);

            const tdMessage = document.createElement('td');
            tdMessage.textContent = row.message || '';
            tr.appendChild(tdMessage);

            body.appendChild(tr);
        }
    } catch (e) {
        body.textContent = '';
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.className = 'parameters-empty';
        td.textContent = 'Błąd: ' + ((e && typeof e.message === 'string') ? e.message : String(e));
        tr.appendChild(td);
        body.appendChild(tr);
    }
}

async function saveExpertParameter() {
    const targetType = document.getElementById('expert_target_type')?.value;
    const targetIndex = parseInt(document.getElementById('expert_target_index')?.value || '0', 10);
    const index = parseInt(document.getElementById('expert_index')?.value || '0', 10);
    const value = parseInt(document.getElementById('expert_value')?.value || '0', 10);
    const offset = parseInt(document.getElementById('expert_offset')?.value || '0', 10);
    const size = parseInt(document.getElementById('expert_size')?.value || '1', 10);

    if (!['mixer', 'thermostat'].includes(targetType || '')) {
        showBoilerControlMsg('Nieprawidłowy typ celu.', 'error');
        return;
    }
    if ([targetIndex, index, value, offset, size].some((x) => Number.isNaN(x))) {
        showBoilerControlMsg('Wszystkie pola eksperckie muszą być liczbami.', 'error');
        return;
    }

    if (!confirm('Potwierdź zapis parametru w trybie eksperckim.')) {
        return;
    }

    try {
        const resp = await fetch('/api/boiler-control/set-parameter', {
            method: 'POST',
            headers: buildControlHeaders(),
            body: JSON.stringify({
                target_type: targetType,
                target_index: targetIndex,
                key: `${targetType.toUpperCase()}_${index}`,
                index,
                value,
                offset,
                size,
            }),
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            showBoilerControlMsg(json.message || 'Błąd zapisu eksperckiego.', 'error');
            return;
        }
        showBoilerControlMsg('Zapisano parametr w trybie eksperckim.', 'ok');
        loadBoilerControlHistory();
    } catch (e) {
        showBoilerControlMsg('Błąd komunikacji: ' + e.message, 'error');
    }
}

async function readScheduleRaw() {
    const textarea = document.getElementById('schedule_payload_hex');
    if (!textarea) return;

    try {
        const resp = await fetch('/api/boiler-control/schedules/raw', {
            headers: buildControlHeaders(),
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            showBoilerControlMsg(json.message || 'Błąd odczytu harmonogramów raw.', 'error');
            return;
        }

        const payloadHex = String(json.data?.payload_hex || '').toUpperCase();
        textarea.value = payloadHex;
        showBoilerControlMsg(`Pobrano payload harmonogramów (${payloadHex.length / 2} B).`, 'ok');
    } catch (e) {
        showBoilerControlMsg('Błąd komunikacji: ' + e.message, 'error');
    }
}

async function writeScheduleRaw() {
    const textarea = document.getElementById('schedule_payload_hex');
    if (!textarea) return;

    const payloadHex = String(textarea.value || '').trim();
    if (!payloadHex) {
        showBoilerControlMsg('Podaj payload HEX do wysłania.', 'error');
        return;
    }

    if (!confirm('Potwierdź wysłanie surowego payloadu harmonogramu.')) {
        return;
    }

    try {
        const resp = await fetch('/api/boiler-control/schedules/raw', {
            method: 'POST',
            headers: buildControlHeaders(),
            body: JSON.stringify({ payload_hex: payloadHex }),
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            showBoilerControlMsg(json.message || 'Błąd zapisu harmonogramów raw.', 'error');
            return;
        }

        showBoilerControlMsg('Wysłano surowy payload harmonogramu.', 'ok');
        loadBoilerControlHistory();
    } catch (e) {
        showBoilerControlMsg('Błąd komunikacji: ' + e.message, 'error');
    }
}

// Semantyka pola "parametr" dla każdego type_name harmonogramu.
// null = typ nie obsługuje parametru (pole wyłączone).
const scheduleParameterLabels = {
    'heating':              'Obniżenie temp. CO (°C)',
    'water_heater':         'Obniżenie temp. CWU (°C)',
    'circulation_pump':     null,
    'boiler_work':          null,
    'boiler_clean':         null,
    'heat_exchanger_clean': null,
    'mixer_1':              'Obniżenie temp. mieszacza 1 (°C)',
    'mixer_2':              'Obniżenie temp. mieszacza 2 (°C)',
    'mixer_3':              'Obniżenie temp. mieszacza 3 (°C)',
    'mixer_4':              'Obniżenie temp. mieszacza 4 (°C)',
    'mixer_5':              'Obniżenie temp. mieszacza 5 (°C)',
    'mixer_6':              'Obniżenie temp. mieszacza 6 (°C)',
    'mixer_7':              'Obniżenie temp. mieszacza 7 (°C)',
    'mixer_8':              'Obniżenie temp. mieszacza 8 (°C)',
    'mixer_9':              'Obniżenie temp. mieszacza 9 (°C)',
    'mixer_10':             'Obniżenie temp. mieszacza 10 (°C)',
    'thermostat_1':         null,
    'thermostat_2':         null,
    'thermostat_3':         null,
    'circuit_1':            'Obniżenie temp. obiegu 1 (°C)',
    'circuit_2':            'Obniżenie temp. obiegu 2 (°C)',
    'circuit_3':            'Obniżenie temp. obiegu 3 (°C)',
    'circuit_4':            'Obniżenie temp. obiegu 4 (°C)',
    'circuit_5':            'Obniżenie temp. obiegu 5 (°C)',
    'circuit_6':            'Obniżenie temp. obiegu 6 (°C)',
    'circuit_7':            'Obniżenie temp. obiegu 7 (°C)',
    'panel_1':              null,
    'panel_2':              null,
    'panel_3':              null,
    'panel_4':              null,
};

function updateScheduleParameterUI(scheduleType) {
    const labelEl = document.querySelector('label[for="schedule_parameter"]');
    const inputEl = document.getElementById('schedule_parameter');
    const rangeEl = document.getElementById('schedule_parameter_range');
    if (!inputEl) return;

    // Znajdź type_name dla wybranego numeru
    const typeInfo = (scheduleStore.knownTypes || []).find((t) => t.type === scheduleType);
    const typeName = typeInfo?.type_name || null;
    const paramLabel = typeName !== null ? scheduleParameterLabels[typeName] : undefined;
    const supported = paramLabel !== null && paramLabel !== undefined;

    if (labelEl) labelEl.textContent = supported ? paramLabel : 'Parametr harmonogramu';
    inputEl.disabled = !supported;
    inputEl.style.opacity = supported ? '' : '0.45';
    if (rangeEl) {
        if (!supported) {
            rangeEl.textContent = 'Ten typ harmonogramu nie obsługuje parametru.';
        }
        // Zakres aktualizuje applyScheduleToForm na podstawie danych z kotła
    }
}

const scheduleDayLabels = ['pon', 'wt', 'sr', 'czw', 'pt', 'sob', 'nd'];
const scheduleDayFullLabels = ['Pon', 'Wt', 'Śr', 'Czw', 'Pt', 'Sob', 'Nd'];
const slotsPerDay = 48;
let scheduleStore = {
    schedulesByType: new Map(),
    knownTypes: [],
};
let scheduleGridState = Array.from({ length: 7 }, () => Array.from({ length: slotsPerDay }, () => false));

function slotToTime(slot) {
    const hour = Math.floor(slot / 2);
    const minute = slot % 2 === 0 ? '00' : '30';
    return `${String(hour).padStart(2, '0')}:${minute}`;
}

function slotRangeLabel(slot) {
    return `${slotToTime(slot)}-${slotToTime(slot + 1)}`;
}

function timeToSlot(value, allow24End = false) {
    const text = String(value || '').trim();
    const parts = text.split(':');
    if (parts.length !== 2) {
        throw new Error(`Nieprawidłowy format czasu: ${text}`);
    }

    const hour = parseInt(parts[0], 10);
    const minute = parseInt(parts[1], 10);
    if (Number.isNaN(hour) || Number.isNaN(minute)) {
        throw new Error(`Nieprawidłowy format czasu: ${text}`);
    }
    if (allow24End && hour === 24 && minute === 0) {
        return slotsPerDay;
    }
    if (hour < 0 || hour > 23 || ![0, 30].includes(minute)) {
        throw new Error(`Dozwolone HH:00 lub HH:30 (00:00-23:30), otrzymano: ${text}`);
    }
    return hour * 2 + (minute === 30 ? 1 : 0);
}

function intervalsToSlots(intervals) {
    const slots = Array.from({ length: slotsPerDay }, () => false);
    if (!Array.isArray(intervals)) return slots;

    for (const interval of intervals) {
        const start = timeToSlot(interval?.start, false);
        const end = timeToSlot(interval?.end, true);
        if (!(start >= 0 && end > start && end <= slotsPerDay)) {
            throw new Error(`Nieprawidłowy zakres interwału: ${interval?.start}-${interval?.end}`);
        }
        for (let i = start; i < end; i++) {
            slots[i] = true;
        }
    }
    return slots;
}

function slotsToIntervals(slots) {
    const intervals = [];
    let start = null;
    for (let i = 0; i < slotsPerDay; i++) {
        const active = !!slots[i];
        if (active && start === null) start = i;
        if (!active && start !== null) {
            intervals.push({ start: slotToTime(start), end: slotToTime(i) });
            start = null;
        }
    }
    if (start !== null) {
        intervals.push({ start: slotToTime(start), end: slotToTime(slotsPerDay) });
    }
    return intervals;
}

function refreshScheduleGrid() {
    const buttons = document.querySelectorAll('.schedule-slot');
    buttons.forEach((btn) => {
        const day = parseInt(btn.dataset.day || '', 10);
        const slot = parseInt(btn.dataset.slot || '', 10);
        if (Number.isNaN(day) || Number.isNaN(slot)) return;
        btn.classList.toggle('active', !!scheduleGridState[day][slot]);
    });
}

function toggleScheduleSlot(day, slot) {
    scheduleGridState[day][slot] = !scheduleGridState[day][slot];
    refreshScheduleGrid();
}

function setScheduleDaySlots(day, value) {
    for (let i = 0; i < slotsPerDay; i++) {
        scheduleGridState[day][i] = !!value;
    }
    refreshScheduleGrid();
}

function clearScheduleGrid() {
    scheduleGridState = Array.from({ length: 7 }, () => Array.from({ length: slotsPerDay }, () => false));
    refreshScheduleGrid();
}

function ensureScheduleGridBuilt() {
    const root = document.getElementById('schedule-grid-root');
    if (!root || root.dataset.ready === '1') return;

    const header = document.createElement('div');
    header.className = 'schedule-grid-header';
    const dayHeader = document.createElement('div');
    dayHeader.className = 'schedule-grid-day';
    dayHeader.textContent = 'Dzień';
    header.appendChild(dayHeader);

    const hours = document.createElement('div');
    hours.className = 'schedule-grid-hours';
    for (let h = 0; h < 24; h++) {
        const item = document.createElement('div');
        item.className = 'schedule-grid-hour';
        item.textContent = String(h).padStart(2, '0');
        hours.appendChild(item);
    }
    header.appendChild(hours);
    header.appendChild(document.createElement('div'));
    root.appendChild(header);

    for (let day = 0; day < 7; day++) {
        const row = document.createElement('div');
        row.className = 'schedule-grid-row';

        const label = document.createElement('div');
        label.className = 'schedule-grid-day';
        label.textContent = scheduleDayFullLabels[day];
        row.appendChild(label);

        const track = document.createElement('div');
        track.className = 'schedule-grid-track';
        for (let slot = 0; slot < slotsPerDay; slot++) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'schedule-slot';
            button.dataset.day = String(day);
            button.dataset.slot = String(slot);
            button.title = slotRangeLabel(slot);
            button.addEventListener('click', () => toggleScheduleSlot(day, slot));
            track.appendChild(button);
        }
        row.appendChild(track);

        const actions = document.createElement('div');
        actions.className = 'schedule-grid-actions';
        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'btn-secondary';
        clearBtn.textContent = '0';
        clearBtn.title = 'Wyczyść dzień';
        clearBtn.addEventListener('click', () => setScheduleDaySlots(day, false));
        actions.appendChild(clearBtn);
        row.appendChild(actions);

        root.appendChild(row);
    }

    root.dataset.ready = '1';
    refreshScheduleGrid();
}

function getSelectedScheduleTypeId() {
    const select = document.getElementById('schedule_type_select');
    if (!select) return null;
    const value = parseInt(select.value || '', 10);
    return Number.isNaN(value) ? null : value;
}

function applyScheduleToForm(schedule) {
    if (!schedule) return;

    const switchEl = document.getElementById('schedule_switch');
    const parameterEl = document.getElementById('schedule_parameter');
    const rangeEl = document.getElementById('schedule_parameter_range');
    if (switchEl) switchEl.checked = !!schedule.switch_on;

    updateScheduleParameterUI(schedule.type);

    if (parameterEl) parameterEl.value = String(schedule.parameter?.value ?? 0);
    if (rangeEl && !parameterEl?.disabled) {
        const min = schedule.parameter?.min_value ?? 0;
        const max = schedule.parameter?.max_value ?? 255;
        rangeEl.textContent = `Zakres: ${min}–${max} °C`;
    }

    scheduleGridState = Array.from({ length: 7 }, (_, i) => {
        const day = (schedule.days || []).find((d) => d.day_index === i);
        if (Array.isArray(day?.slots) && day.slots.length === slotsPerDay) {
            return day.slots.map((x) => !!x);
        }
        return intervalsToSlots(day?.intervals || []);
    });
    ensureScheduleGridBuilt();
    refreshScheduleGrid();
}

function getScheduleDaysFromGrid() {
    return scheduleGridState.map((slots, i) => ({
        day_index: i,
        day_name: scheduleDayLabels[i],
        intervals: slotsToIntervals(slots),
    }));
}

function validateParameterRangeAgainstCurrentSchedule(parameter) {
    const selectedType = getSelectedScheduleTypeId();
    if (selectedType === null) return null;
    const current = scheduleStore.schedulesByType.get(selectedType);
    if (!current || !current.parameter) return null;
    const min = Number(current.parameter.min_value);
    const max = Number(current.parameter.max_value);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    if (parameter < min || parameter > max) {
        return `Parametr poza zakresem ${min}-${max}.`;
    }
    return null;
}

function renderScheduleSelector(selectTypeId = null) {
    const select = document.getElementById('schedule_type_select');
    if (!select) return;

    // Zachowaj poprzedni wybór (lub użyj nadanego)
    const prevValue = selectTypeId !== null ? String(selectTypeId) : select.value;
    select.innerHTML = '';

    const types = scheduleStore.knownTypes || [];
    for (const item of types) {
        const schedule = scheduleStore.schedulesByType.get(item.type);
        const isActive = schedule ? !!schedule.switch_on : false;
        const label = item.label || item.type_name;
        const option = document.createElement('option');
        option.value = String(item.type);
        option.textContent = isActive ? `${label} ✓` : label;
        select.appendChild(option);
    }

    if (prevValue && [...select.options].some((o) => o.value === prevValue)) {
        select.value = prevValue;
    }
    // Nie wywołujemy applyScheduleToForm — odpowiedzialność wywołującego
}

async function loadScheduleSemantic() {
    try {
        const resp = await fetch('/api/boiler-control/schedules', {
            headers: buildControlHeaders(),
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            showBoilerControlMsg(json.message || 'Błąd pobierania harmonogramów.', 'error');
            return;
        }

        const parsed = json.data?.parsed || {};
        const knownTypes = json.data?.known_types || [];
        scheduleStore = {
            schedulesByType: new Map(),
            knownTypes,
        };

        for (const schedule of parsed.schedules || []) {
            scheduleStore.schedulesByType.set(schedule.type, schedule);
        }

        // Wyznacz co pokazać: zachowaj poprzedni wybór lub wybierz pierwszy aktywny
        const prevSelectedType = getSelectedScheduleTypeId();
        let toShow = null;
        if (prevSelectedType !== null && scheduleStore.schedulesByType.has(prevSelectedType)) {
            toShow = scheduleStore.schedulesByType.get(prevSelectedType);
        } else {
            toShow = parsed.schedules.find((s) => s.switch_on) || parsed.schedules[0] || null;
        }

        renderScheduleSelector(toShow?.type ?? null);

        if (toShow) {
            applyScheduleToForm(toShow);
        }

        showBoilerControlMsg('Pobrano harmonogramy do edycji.', 'ok');
    } catch (e) {
        showBoilerControlMsg('Błąd komunikacji: ' + e.message, 'error');
    }
}

async function saveScheduleSemantic() {
    const scheduleType = getSelectedScheduleTypeId();
    if (scheduleType === null) {
        showBoilerControlMsg('Wybierz typ harmonogramu.', 'error');
        return;
    }

    const switchEl = document.getElementById('schedule_switch');
    const parameterEl = document.getElementById('schedule_parameter');
    const parameter = parseInt(parameterEl?.value || '', 10);
    if (Number.isNaN(parameter)) {
        showBoilerControlMsg('Parametr harmonogramu musi być liczbą całkowitą.', 'error');
        return;
    }

    const rangeError = validateParameterRangeAgainstCurrentSchedule(parameter);
    if (rangeError) {
        showBoilerControlMsg(rangeError, 'error');
        return;
    }

    const days = getScheduleDaysFromGrid();

    if (!confirm('Potwierdź zapis harmonogramu.')) {
        return;
    }

    try {
        const resp = await fetch('/api/boiler-control/schedules', {
            method: 'POST',
            headers: buildControlHeaders(),
            body: JSON.stringify({
                schedule_type: scheduleType,
                switch: !!switchEl?.checked,
                parameter,
                days,
            }),
        });

        const json = await resp.json();
        if (json.status !== 'ok') {
            showBoilerControlMsg(json.message || 'Błąd zapisu harmonogramu.', 'error');
            return;
        }

        showBoilerControlMsg('Harmonogram zapisany.', 'ok');
        await loadScheduleSemantic();
        loadBoilerControlHistory();
    } catch (e) {
        showBoilerControlMsg('Błąd komunikacji: ' + e.message, 'error');
    }
}

function onScheduleTypeChanged() {
    const scheduleType = getSelectedScheduleTypeId();
    if (scheduleType === null) return;
    const schedule = scheduleStore.schedulesByType.get(scheduleType);
    if (!schedule) {
        updateScheduleParameterUI(scheduleType);
        const rangeEl = document.getElementById('schedule_parameter_range');
        const parameterEl = document.getElementById('schedule_parameter');
        if (rangeEl && !parameterEl?.disabled) {
            rangeEl.textContent = 'Brak danych harmonogramu dla wybranego typu. Możesz ustawić ręcznie i zapisać.';
        }
        return;
    }
    applyScheduleToForm(schedule);
}

window.clearScheduleGrid = clearScheduleGrid;

document.addEventListener('DOMContentLoaded', () => {
    parameterProfile = normalizeProfile(getModeFromStorage());
    setupParameterViewModeControls();
    setupServiceAccessControls();

    loadBoilerControlParameters();
    loadBoilerControlHistory();
    ensureScheduleGridBuilt();
    refreshServiceAccessState(false);

    const scheduleTypeSelect = document.getElementById('schedule_type_select');
    if (scheduleTypeSelect) {
        scheduleTypeSelect.addEventListener('change', onScheduleTypeChanged);
        loadScheduleSemantic();
    }
});
