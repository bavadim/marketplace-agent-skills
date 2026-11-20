(() => {
    const code = new URLSearchParams(location.hash.slice(1)).get('invite');
    if (!code || !/^[A-Za-z0-9_-]{32}$/.test(code)) return;
    history.replaceState(null, '', location.pathname + location.search);
    const field = document.querySelector('input[name="key"]');
    field.type = 'hidden';
    field.value = code;
    document.querySelector('#hint').textContent = 'Это разовая ссылка. Подтверди, чтобы открыть Chromium на VPS.';
})();
