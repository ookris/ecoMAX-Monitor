const REFRESH_INTERVAL_MS = 5000;
let refreshTimer = null;

function setConnectionStatus(connection) {
    const statusEl = document.getElementById('connection-status');
    const statusText = document.getElementById('status-text');

    if (!statusEl || !statusText) {
        return;
    }

    if (connection && connection.connected) {
        statusEl.className = 'status connected';
        statusText.textContent = 'Połączony';
    } else {
        statusEl.className = 'status disconnected';
        statusText.textContent = 'Rozłączony';
    }
}

function showMessage(text, type = 'info') {
    const msgEl = document.getElementById('parameters-msg');
    if (!msgEl) {
        return;
    }

    msgEl.textContent = text;
    msgEl.className = `settings-msg ${type}`;
    msgEl.style.display = text ? 'block' : 'none';
}

function formatValue(value, unit) {
    if (value === null || value === undefined) {
        return '---';
    }

    if (unit === 'unix' && typeof value === 'number') {
        const d = new Date(value * 1000);
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    }

    if (typeof value === 'boolean') {
        return value ? 'TAK' : 'NIE';
    }

    if (Array.isArray(value)) {
        return value.length ? value.join(', ') : 'brak';
    }

    if (typeof value === 'object') {
        const entries = Object.entries(value);
        return entries.length ? entries.map(([k, v]) => `${k}: ${v}`).join(', ') : 'brak';
    }

    if (typeof value === 'number') {
        if (!Number.isFinite(value)) return '---';
        const rounded = Math.round(value * 10) / 10;
        return rounded % 1 === 0 ? String(rounded) : rounded.toFixed(1);
    }

    return String(value);
}

function renderParameters(rows) {
    const tbody = document.getElementById('parameters-body');
    if (!tbody) {
        return;
    }

    if (!Array.isArray(rows) || rows.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="parameters-empty">Brak danych do wyświetlenia.</td></tr>';
        return;
    }

    // Grupuj wiersze według pola group (zachowując kolejność pierwszego wystąpienia)
    const groupOrder = [];
    const groupMap = Object.create(null);
    rows.forEach(row => {
        const g = row.group || 'Inne';
        if (!groupMap[g]) {
            groupMap[g] = [];
            groupOrder.push(g);
        }
        groupMap[g].push(row);
    });

    let html = '';
    groupOrder.forEach(groupName => {
        html += `<tr class="param-group-header"><td colspan="4">${groupName}</td></tr>`;
        groupMap[groupName].forEach(row => {
            const missingClass = row.available ? '' : 'param-missing';
            const isBool = row.unit === 'bool';
            const isUnix = row.unit === 'unix';
            const unit = (!row.unit || isBool || isUnix) ? '—' : row.unit;
            html += `
            <tr>
                <td>${row.label || row.key}</td>
                <td><code>${row.key}</code></td>
                <td class="${missingClass}">${formatValue(row.value, row.unit)}</td>
                <td>${unit}</td>
            </tr>`;
        });
    });

    tbody.innerHTML = html;
}

async function refreshParameters() {
    try {
        const response = await fetch('/api/parameters');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const payload = await response.json();
        setConnectionStatus(payload.connection);
        renderParameters(payload.data || []);

        if (payload.status === 'ok') {
            showMessage('');
        } else {
            showMessage('Brak aktualnych danych z kotła. Wyświetlane są dostępne pola bez wartości.', 'warning');
        }
    } catch (error) {
        showMessage(`Błąd pobierania parametrów: ${error.message}`, 'error');
    }
}

function startAutoRefresh() {
    if (refreshTimer) {
        clearInterval(refreshTimer);
    }

    refreshTimer = setInterval(() => {
        refreshParameters();
        refreshRegdata();
    }, REFRESH_INTERVAL_MS);
}

// ---- Dane regulatora (0x08) ----

// Własny komunikat sekcji: wspólny #parameters-msg jest czyszczony przy każdym odświeżeniu.
function showRegdataMessage(text, type = 'ok') {
    const el = document.getElementById('regdata-msg');
    if (!el) return;
    el.textContent = text;
    el.className = `settings-msg ${type}`;
    el.style.display = text ? 'block' : 'none';
    if (text) setTimeout(() => { el.style.display = 'none'; }, 8000);
}

function regdataValueText(row) {
    if (row.value === null || row.value === undefined) return '---';
    if (typeof row.value === 'boolean') return row.value ? 'TAK' : 'NIE';
    return String(row.value);
}

async function saveRegdataLabel(fieldId, currentName) {
    const label = prompt(`Nazwa pola ID ${fieldId} (puste = usuń własną nazwę):`, currentName || '');
    if (label === null) return;
    try {
        const resp = await fetch('/api/regdata/labels', {
            method: 'POST',
            headers: controlHeaders(),
            body: JSON.stringify({ id: fieldId, label }),
        });
        const json = await resp.json();
        if (json.status !== 'ok') {
            showRegdataMessage(json.message || 'Nie udało się zapisać nazwy pola.', 'error');
            return;
        }
        refreshRegdata();
    } catch (e) {
        showRegdataMessage('Błąd zapisu nazwy: ' + e.message, 'error');
    }
}

async function refreshRegdata() {
    const body = document.getElementById('regdata-body');
    const statusEl = document.getElementById('regdata-status');
    if (!body) return;
    try {
        const resp = await fetch('/api/regdata');
        const json = await resp.json();
        const namedOnly = !!document.getElementById('regdata-named-only')?.checked;
        if (statusEl) {
            const parts = [`Pól w schemacie: ${json.schema_fields}`];
            if (json.timestamp) parts.push(`ostatnia ramka: ${new Date(json.timestamp * 1000).toLocaleTimeString('pl-PL')}`);
            if (json.decode_errors) parts.push(`błędy dekodowania: ${json.decode_errors}`);
            if (json.status === 'no_schema') parts.push('brak schematu – zostanie pobrany automatycznie po połączeniu');
            statusEl.textContent = parts.join(' · ');
        }
        body.textContent = '';
        const rows = (json.data || []).filter((r) => !namedOnly || r.name);
        if (!rows.length) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 6;
            td.className = 'parameters-empty';
            td.textContent = json.status === 'ok' ? 'Brak pól do wyświetlenia.' : 'Brak danych regulatora.';
            tr.appendChild(td);
            body.appendChild(tr);
            return;
        }
        for (const row of rows) {
            const tr = document.createElement('tr');
            const nameText = row.name ? (row.name + (row.name_source === 'guess' ? ' ?' : '')) : '—';
            for (const text of [row.position, row.id, row.type_name, regdataValueText(row), nameText]) {
                const td = document.createElement('td');
                td.textContent = String(text);
                tr.appendChild(td);
            }
            const actionTd = document.createElement('td');
            const btn = document.createElement('button');
            btn.className = 'btn-ghost';
            btn.textContent = 'Nazwij';
            btn.addEventListener('click', () => saveRegdataLabel(row.id, row.name_source === 'user' ? row.name : ''));
            actionTd.appendChild(btn);
            tr.appendChild(actionTd);
            body.appendChild(tr);
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Błąd pobierania danych regulatora: ' + e.message;
    }
}

function controlHeaders() {
    const headers = { 'Content-Type': 'application/json', 'X-Requested-With': 'ecoMAX-Monitor' };
    try {
        const token = window.localStorage?.getItem('boilerControlAccessToken');
        if (token) headers['X-Boiler-Control-Token'] = String(token).trim();
    } catch (e) {
        // Brak localStorage - bez tokenu.
    }
    return headers;
}

async function refreshRegdataSchema() {
    try {
        const resp = await fetch('/api/regdata/refresh-schema', {
            method: 'POST',
            headers: controlHeaders(),
            body: '{}',
        });
        const json = await resp.json();
        showRegdataMessage(json.message || '', json.status === 'ok' ? 'ok' : 'error');
        setTimeout(refreshRegdata, 5000);
    } catch (e) {
        showRegdataMessage('Błąd: ' + e.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    refreshParameters();
    refreshRegdata();
    document.getElementById('regdata-named-only')?.addEventListener('change', refreshRegdata);
    startAutoRefresh();
});
