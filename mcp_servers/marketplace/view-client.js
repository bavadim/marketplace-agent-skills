import RFB from '/novnc/core/rfb.js';
const status = document.querySelector('#status'), done = document.querySelector('#done');
const keys = document.querySelector('#keys');
let rfb, closed = false;
const ws = new URL('ws', location.href);
ws.protocol = 'wss:';
function connect() {
    rfb = new RFB(document.querySelector('#screen'), ws.href);
    rfb.scaleViewport = true;
    rfb.addEventListener('connect', () => {
        status.textContent = 'Удалённый Chromium. Выполни ручную проверку на странице Ozon.';
        done.disabled = keys.disabled = false;
    });
    rfb.addEventListener('disconnect', () => {
        rfb = null;
        done.disabled = keys.disabled = true;
        keys.value = '';
    });
}
async function poll() {
    try {
        const r = await fetch('status', {cache:'no-store'});
        if (r.status === 410) {
            closed = true;
            rfb?.disconnect();
            done.disabled = keys.disabled = true;
            keys.value = '';
            status.textContent = 'Доступ к экрану закрыт. Вернись к агенту для проверки результата.';
            return;
        }
        if (r.ok && (await r.json()).status === 'ready' && !rfb) connect();
    } catch {
        status.textContent = 'Связь прервана. Переподключаемся…';
    } finally {
        if (!closed) setTimeout(poll, 900);
    }
}
keys.addEventListener('input', event => {
    if (!rfb) return;
    for (const char of (event.data ?? keys.value)) {
        const code = char.codePointAt(0);
        if (code >= 32 && code !== 127) rfb.sendKey(code <= 255 ? code : (0x01000000 | code));
    }
    keys.value = '';
});
keys.addEventListener('keydown', event => {
    const key = {Backspace:0xff08, Enter:0xff0d, Tab:0xff09}[event.key];
    if (key && rfb) { event.preventDefault(); rfb.sendKey(key); }
});
done.addEventListener('click', async () => {
    done.disabled = true;
    try {
        const response = await fetch('check', {method:'POST', cache:'no-store'});
        if (!response.ok) throw new Error('Unable to finish login');
        status.textContent = 'Закрываем браузер и сохраняем профиль…';
    } catch {
        status.textContent = 'Связь прервана.';
    } finally {
        if (rfb && !closed) done.disabled = false;
    }
});
poll();
