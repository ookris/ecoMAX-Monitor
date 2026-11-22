/**
 * ecoMAX Monitor - boczny panel nawigacji.
 * Na szerokich ekranach panel jest stale widoczny; na wąskich
 * wysuwa się po kliknięciu przycisku menu w górnym pasku.
 */
document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.querySelector('[data-sidebar-toggle]');
    const closers = document.querySelectorAll('[data-sidebar-close]');
    const sidebar = document.getElementById('sidebar');
    if (!toggle || !sidebar) return;

    const setOpen = (open) => {
        document.body.classList.toggle('nav-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        toggle.setAttribute('aria-label', open ? 'Ukryj menu' : 'Pokaż menu');
    };

    toggle.addEventListener('click', () => {
        setOpen(!document.body.classList.contains('nav-open'));
    });

    closers.forEach((el) => el.addEventListener('click', () => setOpen(false)));
    sidebar.querySelectorAll('a').forEach((link) => {
        link.addEventListener('click', () => setOpen(false));
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') setOpen(false);
    });
});
