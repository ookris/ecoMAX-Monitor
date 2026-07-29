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

document.addEventListener('DOMContentLoaded', () => {
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
