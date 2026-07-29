function closeNavMenu(dropdown) {
    const menu = dropdown.querySelector('[data-nav-menu]');
    const toggle = dropdown.querySelector('[data-nav-toggle]');
    if (!menu || !toggle) return;
    menu.classList.remove('open');
    toggle.setAttribute('aria-expanded', 'false');
}

function openNavMenu(dropdown) {
    const menu = dropdown.querySelector('[data-nav-menu]');
    const toggle = dropdown.querySelector('[data-nav-toggle]');
    if (!menu || !toggle) return;
    menu.classList.add('open');
    toggle.setAttribute('aria-expanded', 'true');
}

document.addEventListener('DOMContentLoaded', () => {
    const dropdowns = Array.from(document.querySelectorAll('[data-nav-dropdown]'));
    if (!dropdowns.length) return;

    dropdowns.forEach((dropdown) => {
        const toggle = dropdown.querySelector('[data-nav-toggle]');
        const menu = dropdown.querySelector('[data-nav-menu]');
        if (!toggle || !menu) return;

        toggle.addEventListener('click', (event) => {
            event.preventDefault();
            const isOpen = menu.classList.contains('open');
            dropdowns.forEach(closeNavMenu);
            if (!isOpen) openNavMenu(dropdown);
        });

        menu.querySelectorAll('a').forEach((link) => {
            link.addEventListener('click', () => closeNavMenu(dropdown));
        });
    });

    document.addEventListener('click', (event) => {
        dropdowns.forEach((dropdown) => {
            if (!dropdown.contains(event.target)) {
                closeNavMenu(dropdown);
            }
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            dropdowns.forEach(closeNavMenu);
        }
    });
});
