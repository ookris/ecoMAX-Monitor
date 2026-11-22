const _mode0Color = (() => {
    try {
        if (
            typeof window !== 'undefined'
            && window.getComputedStyle
            && window.document
            && window.document.documentElement
        ) {
            const computed = window
                .getComputedStyle(window.document.documentElement)
                .getPropertyValue('--text-secondary');
            if (computed && computed.trim() !== '') {
                return computed.trim();
            }
        }
    } catch (error) {
    }
    return '#727272';
})();

// Kolory trybów: ciepłe barwy = palnik pracuje, chłodne = postój, czerwień = alarm.
window.MODE_COLORS = Object.freeze({
    '-1': '#ced4da', // brak danych
    0: _mode0Color,  // Wyłączony
    1: '#868e96',    // Zatrzymany
    2: '#fab005',    // Rozpalanie
    3: '#e8590c',    // Praca
    4: '#f59f00',    // Nadzór
    5: '#4dabf7',    // Postój
    6: '#9775fa',    // Czyszczenie
    7: '#a5734a',    // Wygaszanie
    8: '#e03131',    // Alarm
    9: '#12b886',    // Ręczny
    10: '#7048e8',   // Odblokowanie
    11: '#495057',   // Inny
});
