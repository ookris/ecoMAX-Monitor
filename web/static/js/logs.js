/**
 * ecoMAX Monitor - Przeglądanie logów
 */

let isLoadingLogs = false;
let logsAbortController = null;

function parseLogLine(line) {
    const text = String(line || '').trimEnd();
    const re = /^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\[(DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\s+([^:]+):\s*(.*)$/;
    const m = text.match(re);

    if (!m) {
        return {
            ts: '',
            level: 'INFO',
            module: 'raw',
            message: text,
            raw: true,
        };
    }

    return {
        ts: m[1],
        level: m[2],
        module: m[3],
        message: m[4],
        raw: false,
    };
}

function escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function renderLogRows(lines) {
    const selectedLevel = document.getElementById('logsLevel')?.value || 'ALL';
    const parsed = lines.map(parseLogLine);
    const filtered = selectedLevel === 'ALL'
        ? parsed
        : parsed.filter(entry => entry.level === selectedLevel);

    if (filtered.length === 0) {
        return '<div class="log-row"><span class="log-ts">-</span><span class="log-level">-</span><span class="log-module">-</span><span class="log-msg">Brak wpisów dla wybranego filtra</span></div>';
    }

    return filtered.map(entry => `
        <div class="log-row">
            <span class="log-ts">${escapeHtml(entry.ts || '-')}</span>
            <span class="log-level ${escapeHtml(entry.level)}">${escapeHtml(entry.level)}</span>
            <span class="log-module">${escapeHtml(entry.module)}</span>
            <span class="log-msg">${escapeHtml(entry.message)}</span>
        </div>
    `).join('');
}

async function loadLogs() {
    if (isLoadingLogs) {
        return;
    }
    isLoadingLogs = true;

    const logsContent = document.getElementById('logsContent');
    const logsLines = parseInt(document.getElementById('logsLines').value, 10) || 200;

    if (logsAbortController) {
        logsAbortController.abort();
    }
    logsAbortController = new AbortController();
    
    logsContent.textContent = 'Ładowanie logów...';
    logsContent.classList.add('loading');
    
    try {
        const resp = await fetch(`/api/logs?lines=${logsLines}`, {
            signal: logsAbortController.signal,
        });
        const json = await resp.json();
        
        logsContent.classList.remove('loading');
        
        if (json.status === 'ok' && Array.isArray(json.data)) {
            logsContent.innerHTML = renderLogRows(json.data);
            
            // Pokaż statystykę
            const statsEl = document.querySelector('.logs-stats');
            const statsText = document.getElementById('logsStats');
            statsText.textContent = `Pobrano ${json.data.length} wierszy z ${json.total_lines} ogółem. Ostatnia aktualizacja: ${new Date().toLocaleTimeString('pl-PL')}`;
            statsEl.style.display = 'block';
            
            showMsg(`Załadowano ${json.data.length} wierszy logów`, 'ok');
        } else {
            logsContent.textContent = 'Błąd: nie udało się pobrać logów';
            showMsg('Błąd pobierania logów', 'error');
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            return;
        }
        logsContent.classList.remove('loading');
        logsContent.textContent = `Błąd pobierania logów:\n${e.message}`;
        showMsg('Błąd: ' + e.message, 'error');
    } finally {
        isLoadingLogs = false;
    }
}

function safeLoadLogs() {
    Promise.resolve()
        .then(() => loadLogs())
        .catch((e) => {
            if (console && console.error) {
                console.error('Błąd ładowania logów:', e);
            }
        });
}

function downloadLogsFile() {
    try {
        const link = document.createElement('a');
        link.href = '/api/logs/download';
        link.click();
        showMsg('Pobieranie logów...', 'ok');
    } catch (e) {
        showMsg('Błąd pobierania: ' + e.message, 'error');
    }
}

function clearLogsView() {
    document.getElementById('logsContent').innerHTML = '';
}

function scrollToTop() {
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showMsg(text, type) {
    const el = document.getElementById('logs-msg');
    el.textContent = text;
    el.className = 'settings-msg ' + type;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// Ładuj logi przy starcie strony
document.addEventListener('DOMContentLoaded', () => {
    const level = document.getElementById('logsLevel');
    if (level) {
        level.addEventListener('change', safeLoadLogs);
    }

    safeLoadLogs();
    
    // Auto-refresh co 10 sekund
    setInterval(safeLoadLogs, 10000);
});
