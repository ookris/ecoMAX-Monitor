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

window.MODE_COLORS = Object.freeze({
    '-1': '#b0bec5',
    0: _mode0Color,
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
});