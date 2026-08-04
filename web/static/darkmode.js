/**
 * API Sentinel dark mode.
 * Loaded before app.js so the theme applies before first paint logic runs.
 * Priority: saved preference > system preference > light.
 */
(function () {
    const STORAGE_KEY = 'apisentinel-theme';

    function checkSystemPreference() {
        return window.matchMedia &&
            window.matchMedia('(prefers-color-scheme: dark)').matches
            ? 'dark'
            : 'light';
    }

    function persistPreference(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (e) { /* private mode etc. — non-fatal */ }
    }

    function applyTheme(theme) {
        document.documentElement.classList.toggle('dark', theme === 'dark');
        const btn = document.getElementById('darkModeToggle');
        if (btn) {
            btn.textContent = theme === 'dark' ? 'Light mode' : 'Dark mode';
            btn.setAttribute('aria-label',
                theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
        }
    }

    function currentTheme() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved === 'dark' || saved === 'light') return saved;
        } catch (e) { /* ignore */ }
        return 'dark'; // dark is the default theme
    }

    function toggleDarkMode() {
        const next = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
        persistPreference(next);
        applyTheme(next);
    }

    // Apply immediately (before DOMContentLoaded) to avoid a theme flash
    applyTheme(currentTheme());

    document.addEventListener('DOMContentLoaded', () => {
        applyTheme(currentTheme()); // syncs the toggle icon once the DOM exists
        const btn = document.getElementById('darkModeToggle');
        if (btn) btn.addEventListener('click', toggleDarkMode);
    });

    window.darkMode = { checkSystemPreference, toggleDarkMode, persistPreference, applyTheme };
})();
