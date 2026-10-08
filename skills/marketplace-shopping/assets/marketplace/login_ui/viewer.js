"use strict";
const token = location.hash.slice(1);
history.replaceState(null, "", "/");
const viewport = document.querySelector("#viewport"), frame = document.querySelector("#frame"), statusLine = document.querySelector("#status");
const labels = {ozon: "Ozon", aliexpress: "AliExpress"};
let selected, busy = false, actionBusy = false, closed = false, frameUrl, pressed = false, queue = Promise.resolve();
async function api(path, data, binary=false) {
  const response = await fetch("/api/" + path, {method: data ? "POST" : "GET", cache: "no-store", headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"}, body: data ? JSON.stringify(data) : undefined});
  if (!response.ok) throw new Error("Не удалось выполнить действие. Проверь, действует ли ссылка и доступна ли площадка.");
  return binary ? response.blob() : response.json();
}
function summary(data) {
  return (data.sessions || [data]).map(s => (labels[s.provider] || s.provider || "") + ": " + (s.authenticated === true ? "вход подтверждён" : s.status === "login_required" ? "нужно войти" : "вход пока не подтверждён")).join(" · ");
}
async function updateFrame() {
  if (!selected || busy || actionBusy || closed) return;
  const provider = selected;
  busy = true;
  try {
    const blob = await api("frame/" + provider, undefined, true);
    if (provider !== selected || closed) return;
    const nextUrl = URL.createObjectURL(blob), nextImage = new Image();
    nextImage.src = nextUrl;
    try { await nextImage.decode(); } catch(e) { URL.revokeObjectURL(nextUrl); throw e; }
    if (provider !== selected || closed) { URL.revokeObjectURL(nextUrl); return; }
    const previousUrl = frameUrl;
    frameUrl = nextUrl; frame.src = nextUrl; frame.dataset.provider = provider;
    if (previousUrl) URL.revokeObjectURL(previousUrl);
    viewport.setAttribute("aria-busy", "false");
  } catch (e) { if(provider === selected && !actionBusy) statusLine.textContent = e.message; }
  finally { busy = false; if(provider !== selected && !closed) updateFrame(); }
}
function input(event) {
  if (!selected || closed) return;
  const provider = selected;
  queue = queue.then(() => api("input", {provider, event})).catch(e => { statusLine.textContent = e.message; });
}
function eventKey(event) {
  if((event.ctrlKey || event.metaKey || event.altKey) && /^Key[A-Z]$/.test(event.code)) return event.code.slice(3).toLowerCase();
  return event.key === " " ? "Space" : event.key;
}
function coordinates(event) {
  const rect = frame.getBoundingClientRect();
  return {x: Math.max(0, Math.min(1440, (event.clientX - rect.left) * 1440 / rect.width)), y: Math.max(0, Math.min(1000, (event.clientY - rect.top) * 1000 / rect.height))};
}
viewport.addEventListener("pointerdown", e => { if(e.button !== 0 || frame.dataset.provider !== selected || actionBusy) return; e.preventDefault(); viewport.focus({preventScroll:true}); pressed = true; viewport.setPointerCapture(e.pointerId); input({op: "down", ...coordinates(e)}); });
viewport.addEventListener("pointermove", e => { if(pressed) input({op: "move", ...coordinates(e)}); });
viewport.addEventListener("pointerup", e => { if(pressed) { pressed = false; input({op: "up", ...coordinates(e)}); } });
viewport.addEventListener("pointercancel", e => { if(pressed) { pressed = false; input({op: "up", ...coordinates(e)}); } });
viewport.addEventListener("wheel", e => { e.preventDefault(); input({op: "wheel", delta: e.deltaY, ...coordinates(e)}); }, {passive:false});
viewport.addEventListener("paste", e => { e.preventDefault(); input({op: "text", text: e.clipboardData.getData("text/plain")}); });
viewport.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") return;
  e.preventDefault();
  if(e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) input({op:"text", text:e.key});
  else if(e.key !== "Unidentified") input({op:"key_down", key:eventKey(e)});
});
viewport.addEventListener("keyup", e => { if(e.key.length !== 1 || e.ctrlKey || e.metaKey || e.altKey) input({op:"key_up", key:eventKey(e)}); });
async function action(button, operation) {
  if(actionBusy) return;
  actionBusy = true; button.disabled = true;
  statusLine.textContent = operation === "cancel" ? "Закрываю подключение…" : "Проверяю вход на площадке…";
  try { await queue; const data = await api(operation, operation === "verify" ? {provider:selected} : {}); statusLine.textContent = summary(data); if(operation === "cancel" || operation === "finish" && data.complete) { closed = true; statusLine.textContent = operation === "cancel" ? "Подключение закрыто." : "Аккаунты подключены. Можно вернуться к агенту."; } }
  catch(e) { statusLine.textContent = e.message; } finally { button.disabled = false; actionBusy = false; updateFrame(); }
}
for(const op of ["verify", "finish", "cancel"]) { const button = document.querySelector("#" + op); button.addEventListener("click", () => action(button, op)); }
(async () => {
  if(!token) throw new Error("Открой полную ссылку, которую дал агент. Эта ссылка уже закрыта или не содержит доступа.");
  const data = await api("status");
  statusLine.textContent = summary(data);
  document.querySelector("#expiry").textContent = "Ссылка действует до " + new Date(data.expires_at).toLocaleString();
  for(const provider of data.providers) {
    const button = document.createElement("button"); button.textContent = labels[provider] || provider; button.dataset.provider = provider;
    button.addEventListener("click", async () => {
      if(actionBusy || selected === provider) return;
      selected = provider; pressed = false; frame.dataset.provider = ""; frame.removeAttribute("src");
      viewport.setAttribute("aria-busy", "true");
      for(const b of document.querySelectorAll("nav button")) b.setAttribute("aria-pressed", String(b === button));
      await queue; await updateFrame();
    });
    document.querySelector("#providers").append(button);
  }
  document.querySelector("nav button").click();
  setInterval(updateFrame, 650);
})().catch(e => {statusLine.textContent = e.message;});
