let allAlerts = [];

function setLimitInfo(text = '', visible = false, type = 'ok') {
    const el = document.getElementById('alerts-limit-info');
    if (!el) return;
    if (!visible || !text) {
        el.style.display = 'none';
        el.textContent = '';
        return;
    }
    el.textContent = text;
    el.className = 'settings-msg ' + type;
    el.style.display = 'block';
}

function showMsg(text, type = 'ok') {
    const el = document.getElementById('alerts-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'settings-msg ' + type;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 3500);
}

function getFilters() {
    const unreadOnlyEl = document.getElementById('alerts-unread-only');
    const typeEl = document.getElementById('alerts-type-filter');
    return {
        unreadOnly: !!(unreadOnlyEl && unreadOnlyEl.checked),
        type: typeEl ? String(typeEl.value || 'all') : 'all',
    };
}

function applyFilters(data) {
    const { type } = getFilters();
    let filtered = Array.isArray(data) ? [...data] : [];
    if (type !== 'all') {
        filtered = filtered.filter((a) => String(a.type || '') === type);
    }
    return filtered;
}

function rebuildTypeFilterOptions(data) {
    const select = document.getElementById('alerts-type-filter');
    if (!select) return;

    const current = String(select.value || 'all');
    const types = [...new Set((Array.isArray(data) ? data : [])
        .map(a => String(a.type || '').trim())
        .filter(Boolean))].sort();

    select.innerHTML = '<option value="all">Wszystkie</option>';
    types.forEach((type) => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = type;
        select.appendChild(option);
    });

    select.value = types.includes(current) || current === 'all' ? current : 'all';
}

function renderAlerts(data) {
    const list = document.getElementById('alerts-list');
    if (!list) return;
    list.innerHTML = '';

    if (!Array.isArray(data) || data.length === 0) {
        list.innerHTML = '<div class="alert-item-empty">Brak alertów w historii</div>';
        return;
    }

    const sorted = [...data].sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    sorted.forEach((alert) => {
        const dt = new Date((alert.timestamp || 0) * 1000);
        const timeStr = dt.toLocaleString('pl-PL', { dateStyle: 'short', timeStyle: 'short' });

        const item = document.createElement('div');
        item.className = 'alert-item';
        const isUnread = alert && (alert.read === 0 || alert.read === '0' || alert.read === false || alert.read === undefined || alert.read === null);
        item.classList.add(isUnread ? 'alert-item-unread' : 'alert-item-read');

        const colTime = document.createElement('span');
        colTime.className = 'alert-item-time';
        colTime.textContent = timeStr;

        const colLabel = document.createElement('span');
        colLabel.className = 'alert-item-label';
        colLabel.textContent = alert.label || '';

        const colMsg = document.createElement('span');
        colMsg.className = 'alert-item-msg';
        colMsg.textContent = alert.message || '';

        item.appendChild(colTime);
        item.appendChild(colLabel);
        item.appendChild(colMsg);
        list.appendChild(item);
    });
}

async function loadAllAlerts() {
    try {
        const { unreadOnly } = getFilters();
        const url = unreadOnly
            ? '/api/alerts?limit=all&unread=true'
            : '/api/alerts?limit=all';

        const resp = await fetch(url);
        const json = await resp.json();
        if (json.status !== 'ok') {
            setLimitInfo();
            showMsg('Błąd pobierania alertów', 'error');
            return;
        }

        const data = Array.isArray(json.data) ? json.data : [];
        const limited = !!json.limited;
        const total = Number.isFinite(Number(json.total)) ? Number(json.total) : null;
        const count = Number.isFinite(Number(json.count)) ? Number(json.count) : data.length;
        if (limited && total !== null && total > count) {
            setLimitInfo(`Pokazano ${count} z ${total} alertów (limit API).`, true, 'error');
        } else {
            setLimitInfo();
        }

        allAlerts = data;
        rebuildTypeFilterOptions(allAlerts);
        renderAlerts(applyFilters(allAlerts));
    } catch (e) {
        setLimitInfo();
        showMsg('Błąd połączenia: ' + e.message, 'error');
    }
}

async function markAllAlertsRead() {
    try {
        const resp = await fetch('/api/alerts/read', { method: 'POST' });
        const json = await resp.json();
        if (json.status === 'ok') {
            showMsg('Oznaczono wszystkie alerty jako przeczytane', 'ok');
            await loadAllAlerts();
            return;
        }
        showMsg('Nie udało się oznaczyć alertów', 'error');
    } catch (e) {
        showMsg('Błąd połączenia: ' + e.message, 'error');
    }
}

// ---- Alarmy sterownika (historia odczytana z ecoMAX) ----

function formatDuration(minutes) {
    const value = Number(minutes);
    if (!Number.isFinite(value)) return '--';
    const total = Math.round(value);
    if (total < 60) return `${total} min`;
    const hours = Math.floor(total / 60);
    const rest = total % 60;
    if (hours < 24) return `${hours} h ${rest} min`;
    return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

function controllerAlarmsMessage(body, text) {
    body.textContent = '';
    const tr = controllerAlarmsRow([text]);
    tr.firstChild.colSpan = 5;
    tr.firstChild.className = 'parameters-empty';
    body.appendChild(tr);
}

function controllerAlarmsRow(cells, className = '') {
    const tr = document.createElement('tr');
    if (className) tr.className = className;
    for (const cell of cells) {
        const td = document.createElement('td');
        td.textContent = cell;
        tr.appendChild(td);
    }
    return tr;
}

async function loadControllerAlarms() {
    const body = document.getElementById('controller-alarms-body');
    const summary = document.getElementById('controller-alarms-summary');
    if (!body) return;
    try {
        const resp = await fetch('/api/controller-alarms?limit=500');
        const json = await resp.json();
        body.textContent = '';
        if (json.status !== 'ok' || !Array.isArray(json.data)) {
            if (summary) summary.textContent = '';
            controllerAlarmsMessage(body, 'Nie udało się pobrać alarmów sterownika.');
            return null;
        }
        if (summary) {
            const ongoing = json.data.filter((a) => a.ongoing).length;
            summary.textContent = `Zapisanych alarmów: ${json.total}` + (ongoing ? ` · trwających: ${ongoing}` : '');
        }
        if (json.data.length === 0) {
            controllerAlarmsMessage(body, 'Brak alarmów – historia zostanie odczytana po połączeniu ze sterownikiem.');
            return json;
        }
        for (const alarm of json.data) {
            body.appendChild(controllerAlarmsRow([
                alarm.start || '--',
                alarm.ongoing ? 'trwa' : (alarm.end || '--'),
                formatDuration(alarm.duration_min),
                String(alarm.code ?? '--'),
                alarm.name || '--',
            ], alarm.ongoing ? 'is-ongoing' : ''));
        }
        return json;
    } catch (e) {
        if (summary) summary.textContent = '';
        controllerAlarmsMessage(body, 'Błąd: ' + e.message);
        return null;
    }
}

// Po zleceniu odczytu odświeżaj tabelę, dopóki zadanie jest w toku (maks. ~1 min).
async function pollControllerAlarms(attempt = 0) {
    const json = await loadControllerAlarms();
    if (json && json.refresh_pending && attempt < 20) {
        setTimeout(() => pollControllerAlarms(attempt + 1), 3000);
    }
}

function controllerRequestHeaders() {
    const headers = { 'Content-Type': 'application/json', 'X-Requested-With': 'ecoMAX-Monitor' };
    try {
        const token = window.localStorage?.getItem('boilerControlAccessToken');
        if (token) headers['X-Boiler-Control-Token'] = String(token).trim();
    } catch (e) {
        // Brak localStorage - wysyłamy bez tokenu.
    }
    return headers;
}

async function refreshControllerAlarms() {
    try {
        const resp = await fetch('/api/controller-alarms/refresh', {
            method: 'POST',
            headers: controllerRequestHeaders(),
            body: '{}',
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            showMsg(json.message || 'Nie udało się zlecić odczytu alarmów.', 'error');
            return;
        }
        showMsg('Zlecono odczyt alarmów ze sterownika.', 'ok');
        setTimeout(() => pollControllerAlarms(0), 1500);
    } catch (e) {
        showMsg('Błąd połączenia: ' + e.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadControllerAlarms();
    const unreadOnlyEl = document.getElementById('alerts-unread-only');
    const typeEl = document.getElementById('alerts-type-filter');

    if (unreadOnlyEl) unreadOnlyEl.addEventListener('change', loadAllAlerts);
    if (typeEl) {
        typeEl.addEventListener('change', () => {
            renderAlerts(applyFilters(allAlerts));
        });
    }

    loadAllAlerts();
});
