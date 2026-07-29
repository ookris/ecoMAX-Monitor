/**
 * ecoMAX Monitor - Ustawienia
 */

const FIELDS = [
    'ew11a_host', 'ew11a_port',
    'weather_lat', 'weather_lon',
    'read_interval', 'db_save_interval',
    'alert_temp_flue_max', 'alert_temp_co_min',
    'alert_fuel_level_min', 'alert_no_connection_min',
    'alert_retention_mode', 'alert_retention_days', 'alert_retention_count',
    'fuel_price_per_ton', 'db_retention_days',
    'log_enabled', 'log_retention_days',
    'time_zone', 'time_ntp_server',
];

const FIELD_LABELS = {
    ew11a_host: 'Adres IP modułu EW11A',
    ew11a_port: 'Port TCP',
    weather_lat: 'Szerokość geograficzna (lat)',
    weather_lon: 'Długość geograficzna (lon)',
    read_interval: 'Częstotliwość odczytu danych (sekundy)',
    db_save_interval: 'Częstotliwość zapisu do bazy (sekundy)',
    alert_temp_flue_max: 'Maks. temperatura spalin',
    alert_temp_co_min: 'Min. temperatura CO',
    alert_fuel_level_min: 'Min. poziom paliwa',
    alert_no_connection_min: 'Brak połączenia (minuty)',
    alert_retention_mode: 'Tryb retencji alertów',
    alert_retention_days: 'Przechowywanie alertów (dni)',
    alert_retention_count: 'Maksymalna liczba alertów',
    fuel_price_per_ton: 'Cena paliwa (PLN / tona)',
    db_retention_days: 'Przechowywanie historii (dni)',
    log_enabled: 'Włącz logowanie do pliku',
    log_retention_days: 'Przechowywanie logów (dni)',
    time_zone: 'Strefa czasowa',
    time_ntp_server: 'Serwer NTP',
};

let restartMonitorTimer = null;
let restartStartedAt = 0;
const RESTART_MONITOR_TIMEOUT_MS = 90000;

function scrollToTop() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function updateAlertRetentionInputs() {
    const modeEl = document.getElementById('alert_retention_mode');
    const daysEl = document.getElementById('alert_retention_days');
    const countEl = document.getElementById('alert_retention_count');
    if (!modeEl || !daysEl || !countEl) return;

    const byDays = modeEl.value !== 'count';
    daysEl.disabled = !byDays;
    countEl.disabled = byDays;
    daysEl.style.opacity = byDays ? '1' : '0.6';
    countEl.style.opacity = byDays ? '0.6' : '1';
}

function normalizeDecimalString(value) {
    return String(value ?? '')
        .trim()
        .replace(/\s+/g, '')
        .replace(',', '.');
}

function getSensorOptionsFromDom() {
    return Array.from(document.querySelectorAll('.sensor-checkbox')).map((el) => el.value);
}

function parseVisibleSensors(value, sensorOptions) {
    const options = Array.isArray(sensorOptions) && sensorOptions.length > 0
        ? sensorOptions
        : getSensorOptionsFromDom();
    const defaultVisibleSensors = [...options];

    if (value === undefined || value === null) return defaultVisibleSensors;
    if (String(value).trim() === '') return [];
    const parsed = String(value)
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
        .filter(s => options.includes(s));

    return parsed;
}

function setVisibleSensorCheckboxes(selectedSensors) {
    const selected = new Set(selectedSensors);
    document.querySelectorAll('.sensor-checkbox').forEach((el) => {
        el.checked = selected.has(el.value);
    });
}

function getSelectedSensors() {
    const sensorOptions = getSensorOptionsFromDom();
    const selected = [];
    document.querySelectorAll('.sensor-checkbox:checked').forEach((el) => {
        if (sensorOptions.includes(el.value)) {
            selected.push(el.value);
        }
    });
    return selected;
}

async function loadSettings() {
    try {
        const resp = await fetch('/api/config');
        const json = await resp.json();
        if (json.status === 'ok') {
            const availableSensors = Array.isArray(json.data.available_visible_sensors)
                ? json.data.available_visible_sensors
                : getSensorOptionsFromDom();

            for (const key of FIELDS) {
                const el = document.getElementById(key);
                if (!el) continue;
                
                if (el.type === 'checkbox') {
                    // Dla checkboxów: '1' = zaznaczony, '0' = niezaznaczony
                    el.checked = (json.data[key] === '1' || json.data[key] === 1);
                } else if (json.data[key] !== undefined) {
                    if (key === 'weather_lat' || key === 'weather_lon') {
                        el.value = normalizeDecimalString(json.data[key]);
                    } else {
                        el.value = json.data[key];
                    }
                }
            }

            setVisibleSensorCheckboxes(parseVisibleSensors(json.data.visible_sensors, availableSensors));
            updateAlertRetentionInputs();
        }
    } catch (e) {
        showMsg('Błąd ładowania ustawień: ' + e.message, 'error');
        setVisibleSensorCheckboxes(getSensorOptionsFromDom());
        updateAlertRetentionInputs();
    }
}

const NUMERIC_FIELDS = {
    'ew11a_port':             { min: 1,   max: 65535, integer: true  },
    'weather_lat':            { min: -90, max: 90,    integer: false },
    'weather_lon':            { min: -180,max: 180,   integer: false },
    'read_interval':          { min: 1,   max: 3600,  integer: true  },
    'db_save_interval':       { min: 1,   max: 3600,  integer: true  },
    'alert_temp_flue_max':    { min: 50,  max: 500,   integer: false },
    'alert_temp_co_min':      { min: 0,   max: 100,   integer: false },
    'alert_fuel_level_min':   { min: 0,   max: 100,   integer: false },
    'alert_no_connection_min':{ min: 1,   max: 60,    integer: false },
    'alert_retention_days':   { min: 1,   max: 3650,  integer: true  },
    'alert_retention_count':  { min: 1,   max: 50000, integer: true  },
    'fuel_price_per_ton':     { min: 1,   max: 100000,integer: false },
    'db_retention_days':      { min: 1,   max: 365,   integer: true  },
    'log_retention_days':     { min: 1,   max: 365,   integer: true  },
};

async function saveSettings() {
    const payload = {};
    for (const key of FIELDS) {
        const el = document.getElementById(key);
        if (!el) continue;
        
        if (el.type === 'checkbox') {
            // Checkbox: 1 jeśli zaznaczony, 0 jeśli nie
            payload[key] = el.checked ? '1' : '0';
        } else {
            const rule = NUMERIC_FIELDS[key];
            if (rule) {
                const rawValue = el.value;
                const normalizedValue = rule.integer ? String(rawValue).trim() : normalizeDecimalString(rawValue);
                if (!rule.integer && rawValue !== normalizedValue) {
                    el.value = normalizedValue;
                }

                const num = rule.integer
                    ? parseInt(normalizedValue, 10)
                    : parseFloat(normalizedValue);
                if (isNaN(num) || num < rule.min || num > rule.max) {
                    const fieldLabel = FIELD_LABELS[key] || key;
                    showMsg(`Nieprawidłowa wartość: ${fieldLabel} (zakres ${rule.min}–${rule.max})`, 'error');
                    return;
                }
                payload[key] = String(num);
            } else if (key === 'alert_retention_mode') {
                const mode = String(el.value || '').trim().toLowerCase();
                if (mode !== 'days' && mode !== 'count') {
                    showMsg('Nieprawidłowa wartość: Tryb retencji alertów', 'error');
                    return;
                }
                payload[key] = mode;
            } else {
                payload[key] = el.value;
            }
        }
    }

    const selectedSensors = getSelectedSensors();
    payload.visible_sensors = selectedSensors.join(',');

    try {
        const resp = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const json = await resp.json();

        if (json.status === 'ok') {
            showMsg('Ustawienia zostały zapisane. Zmiana logowania do pliku wymaga restartu aplikacji.', 'ok');
        } else if (json.status === 'partial') {
            const rejectedCount = Array.isArray(json.rejected) ? json.rejected.length : 0;
            showMsg(`Część ustawień została zapisana, a ${rejectedCount} odrzucono.`, 'error');
        } else {
            showMsg('Błąd: ' + (json.message || 'Nieznany błąd'), 'error');
        }
    } catch (e) {
        showMsg('Błąd zapisu: ' + e.message, 'error');
    }
}

function showMsg(text, type) {
    const el = document.getElementById('settings-msg');
    el.textContent = text;
    el.className = 'settings-msg ' + type;
    el.style.display = 'block';
    scrollToTop();
    setTimeout(() => { el.style.display = 'none'; }, 4000);
}

function showStartupBanner(text, withDiagnostics = false) {
    const banner = document.getElementById('startup-banner');
    const textEl = document.getElementById('startup-banner-text');
    const helpEl = document.getElementById('startup-banner-help');
    if (!banner || !textEl) return;
    textEl.textContent = text;
    if (helpEl) helpEl.style.display = withDiagnostics ? 'block' : 'none';
    banner.style.display = 'flex';
    scrollToTop();
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
    try {
        const resp = await fetch('/api/data', { cache: 'no-store' });
        if (resp.ok) {
            const json = await resp.json();
            if (json.status === 'ok' || json.status === 'no_data') {
                stopRestartMonitor();
                dismissStartupBanner();
                showMsg('Aplikacja ponownie działa po restarcie.', 'ok');
                return;
            }
        }
    } catch (e) {
        // Serwer nadal może się uruchamiać.
    }

    if (restartStartedAt && (Date.now() - restartStartedAt) > RESTART_MONITOR_TIMEOUT_MS) {
        stopRestartMonitor();
        showStartupBanner('Restart nie powiódł się. Coś nie zadziałało podczas uruchamiania głównej aplikacji.', true);
    }
}

function startRestartMonitor() {
    stopRestartMonitor();
    restartStartedAt = Date.now();
    showStartupBanner('Trwa restart aplikacji... Jeśli uruchomienie się nie powiedzie, zobaczysz tu szczegóły.', false);
    restartMonitorTimer = setInterval(checkRestartReadiness, 3000);
}

function ensureSelectOption(selectEl, value) {
    if (!selectEl || !value) return;
    const existing = Array.from(selectEl.options || []).some((opt) => opt.value === value);
    if (existing) return;

    const option = document.createElement('option');
    option.value = value;
    option.textContent = `${value} (niestandardowa)`;
    selectEl.appendChild(option);
}

function buildAdminHeaders({ forTime = false, forReset = false } = {}) {
    const headers = {
        'Content-Type': 'application/json',
        'X-Requested-With': 'ecoMAX-Monitor',
    };

    // Tokeny sa opcjonalne i pobierane z localStorage,
    // aby nie eksponowac dodatkowych pol w UI.
    try {
        if (typeof window !== 'undefined' && window.localStorage) {
            if (forTime) {
                const timeToken = window.localStorage.getItem('timeAccessToken');
                if (timeToken) {
                    headers['X-Time-Token'] = String(timeToken).trim();
                }
            }

            if (forReset) {
                const resetToken = window.localStorage.getItem('resetAccessToken');
                if (resetToken) {
                    headers['X-Reset-Token'] = String(resetToken).trim();
                }
            }
        }
    } catch (e) {
        // localStorage moze byc niedostepne (np. privacy mode) - zachowaj fallback.
    }

    return headers;
}

async function downloadLogs() {
    try {
        const link = document.createElement('a');
        link.href = '/api/logs/download';
        link.click();
        showMsg('Pobieranie logów...', 'ok');
    } catch (e) {
        showMsg('Błąd pobierania logów: ' + e.message, 'error');
    }
}

async function viewLogs() {
    window.location.href = '/logs';
}

async function restartAppFromSettings() {
    if (!confirm('Zrestartować aplikację ecoMAX Monitor?')) return;

    dismissStartupBanner();

    try {
        const resp = await fetch('/api/restart', {
            method: 'POST',
            headers: buildAdminHeaders(),
            body: JSON.stringify({ action: 'restart' }),
        });

        if (resp.status === 403) {
            showMsg('Brak uprawnień do restartu aplikacji na tym środowisku.', 'error');
            return;
        }

        const json = await resp.json();
        if (json.status === 'ok') {
            startRestartMonitor();
            return;
        }
        showStartupBanner('Błąd restartu: ' + (json.message || 'nieznany błąd systemu'), true);
    } catch (e) {
        // Przy restarcie połączenie może zostać zerwane - traktujemy to jako normalne.
        startRestartMonitor();
    }
}

async function loadTimeStatus() {
    try {
        const resp = await fetch('/api/time/status');
        const json = await resp.json();
        if (json.status !== 'ok' || !json.data) return;

        const nowEl = document.getElementById('server-time-now');
        const tzEl = document.getElementById('server-timezone');
        const dateEl = document.getElementById('server_date');
        const zoneEl = document.getElementById('time_zone');
        const ntpServerEl = document.getElementById('time_ntp_server');

        if (nowEl) nowEl.textContent = json.data.server_time_iso || '--';
        if (tzEl) tzEl.textContent = json.data.timezone || '--';

        if (dateEl && json.data.server_time_iso) {
            dateEl.value = String(json.data.server_time_iso).slice(0, 10);
        }
        if (zoneEl && json.data.time_zone_config) {
            ensureSelectOption(zoneEl, json.data.time_zone_config);
            zoneEl.value = json.data.time_zone_config;
        }
        if (ntpServerEl && json.data.ntp_server_config) {
            ntpServerEl.value = json.data.ntp_server_config;
        }
    } catch (e) {
        console.warn('loadTimeStatus:', e);
    }
}

async function applyTimeSettings() {
    const zoneEl = document.getElementById('time_zone');
    const dateEl = document.getElementById('server_date');
    const serverEl = document.getElementById('time_ntp_server');
    const syncEl = document.getElementById('time_ntp_sync');

    const timeZone = String(zoneEl?.value || '').trim();
    const date = String(dateEl?.value || '').trim();
    const ntpServer = String(serverEl?.value || '').trim();
    const ntpSync = !!syncEl?.checked;

    if (!timeZone) {
        showMsg('Podaj strefę czasową.', 'error');
        return;
    }
    if (!date) {
        showMsg('Podaj datę serwera.', 'error');
        return;
    }
    if (ntpSync && !ntpServer) {
        showMsg('Podaj serwer NTP.', 'error');
        return;
    }

    if (!confirm('Zastosować ustawienia czasu serwera?')) return;

    try {
        const resp = await fetch('/api/time/set', {
            method: 'POST',
            headers: buildAdminHeaders({ forTime: true }),
            body: JSON.stringify({
                time_zone: timeZone,
                date,
                ntp_sync: ntpSync,
                ntp_server: ntpServer,
            }),
        });
        if (resp.status === 403) {
            showMsg('Brak uprawnień do zmiany czasu na tym środowisku.', 'error');
            return;
        }
        const json = await resp.json();
        if (json.status === 'ok' || json.status === 'partial') {
            showMsg(
                json.status === 'ok'
                    ? (json.message || 'Ustawienia czasu zostały zastosowane.')
                    : 'Ustawienia czasu zapisano częściowo. Sprawdź uprawnienia systemowe.',
                json.status === 'ok' ? 'ok' : 'warning'
            );
            await loadTimeStatus();
            return;
        }
        showMsg('Błąd: ' + (json.message || 'Nie udało się zastosować ustawień czasu'), 'error');
    } catch (e) {
        showMsg('Błąd ustawiania czasu: ' + e.message, 'error');
    }
}

async function resetRuntimeData() {
    const confirmed = confirm(
        'To usunie wszystkie pomiary i alerty, ale pozostawi ustawienia.\n\nCzy kontynuować?'
    );
    if (!confirmed) return;

    try {
        const resp = await fetch('/api/db/reset-runtime', {
            method: 'POST',
            headers: buildAdminHeaders({ forReset: true }),
            body: JSON.stringify({ action: 'reset-runtime' }),
        });
        if (resp.status === 403) {
            showMsg('Brak uprawnień do resetu danych na tym środowisku.', 'error');
            return;
        }
        const json = await resp.json();

        if (json.status === 'ok') {
            showMsg(json.message || 'Statystyki zostały wyczyszczone.', 'ok');
            return;
        }
        showMsg('Błąd: ' + (json.message || 'Nie udało się wykonać operacji'), 'error');
    } catch (e) {
        showMsg('Błąd resetu statystyk: ' + e.message, 'error');
    }
}

async function resetAllDataAndConfig() {
    const confirmed = confirm(
        'UWAGA: To usunie wszystkie pomiary, alerty i całą konfigurację.\n'
        + 'Aplikacja wróci do ustawień domyślnych.\n\n'
        + 'Czy na pewno wykonać pełny reset?'
    );
    if (!confirmed) return;

    try {
        const resp = await fetch('/api/db/reset-all', {
            method: 'POST',
            headers: buildAdminHeaders({ forReset: true }),
            body: JSON.stringify({ action: 'reset-all' }),
        });
        if (resp.status === 403) {
            showMsg('Brak uprawnień do resetu danych na tym środowisku.', 'error');
            return;
        }
        const json = await resp.json();

        if (json.status === 'ok') {
            showMsg(json.message || 'Wykonano pełny reset.', 'warning');
            await loadSettings();
            return;
        }
        showMsg('Błąd: ' + (json.message || 'Nie udało się wykonać operacji'), 'error');
    } catch (e) {
        showMsg('Błąd pełnego resetu: ' + e.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', loadSettings);
document.addEventListener('DOMContentLoaded', () => {
    const modeEl = document.getElementById('alert_retention_mode');
    if (modeEl) modeEl.addEventListener('change', updateAlertRetentionInputs);
    updateAlertRetentionInputs();
    loadTimeStatus();
});
