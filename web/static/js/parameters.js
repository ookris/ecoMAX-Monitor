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

    refreshTimer = setInterval(refreshParameters, REFRESH_INTERVAL_MS);
}

document.addEventListener('DOMContentLoaded', () => {
    refreshParameters();
    startAutoRefresh();
});
