/**
 * ecoMAX Monitor - Przełącznik motywu jasny/ciemny
 * Motyw domyślny: jasny (light).
 * Preferencja zapisywana w localStorage pod kluczem 'theme'.
 */

const THEME_DARK  = 'dark';
const THEME_LIGHT = 'light';

function _applyTheme(theme) {
    if (theme === THEME_DARK) {
        document.documentElement.setAttribute('data-theme', THEME_DARK);
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
    const btn = document.getElementById('btn-theme');
    if (btn) {
        if (theme === THEME_DARK) {
            btn.innerHTML = '<iconify-icon icon="mdi:weather-sunny"></iconify-icon>';
            btn.title = 'Przełącz na tryb jasny';
            btn.setAttribute('aria-label', btn.title);
        } else {
            btn.innerHTML = '<iconify-icon icon="mdi:weather-night"></iconify-icon>';
            btn.title = 'Przełącz na tryb ciemny';
            btn.setAttribute('aria-label', btn.title);
        }
    }
}

function toggleTheme() {
    const current = localStorage.getItem('theme') || THEME_LIGHT;
    const next = current === THEME_DARK ? THEME_LIGHT : THEME_DARK;
    localStorage.setItem('theme', next);
    _applyTheme(next);
}

// Zastosuj motyw przy DOMContentLoaded (aktualizuje ikonę przycisku)
document.addEventListener('DOMContentLoaded', function () {
    _applyTheme(localStorage.getItem('theme') || THEME_LIGHT);
});
