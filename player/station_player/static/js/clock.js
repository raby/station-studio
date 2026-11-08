// ---------- clock and alarms ------------------------------------------------

const MAX_ALARMS = 30;  // alarms and schedule switches together
const SNOOZE_MINUTES = 9;
const C = { editing: null, face: store("clock-face") || "digital", timer: null, draft: null };

const alarmsList = () => (S.lib.config.alarms || []);
// The list holds both: alarms ring, schedule switches change the station.
const isSwitch = (a) => a.action === "switch";
const pad2 = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
// The day a clock time ("17:00") next comes round: today, or tomorrow.
function nextDay(hm) {
  const now = new Date();
  const [h, m] = (hm || "00:00").split(":").map(Number);
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m);
  if (d <= now) d.setDate(d.getDate() + 1);
  return isoDay(d);
}
const hhmm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// "Weekdays", "Every day", "Sat, Sun"…
function daysText(days) {
  const set = [...days].sort().join("");
  if (set === "0123456") return "Every day";
  if (set === "01234") return "Weekdays";
  if (set === "56") return "Weekends";
  return days.map((d) => DAY_NAMES[d]).join(", ");
}

// "Weekdays", or "Once, Thu 23 Oct" for a one-off.
function whenText(a) {
  if (!a.date) return daysText(a.days);
  const [y, m, d] = a.date.split("-").map(Number);
  return `Once, ${new Date(y, m - 1, d).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}`;
}

function nextAlarmText() {
  const n = S.lib.next_alarm;
  const snooze = S.lib.alarm_state && S.lib.alarm_state.snoozed_until;
  if (snooze) return `Snoozed until ${hhmm(new Date(snooze * 1000))}`;
  if (!n) return alarmsList().some((a) => a.on && !isSwitch(a)) ? "" : "No alarms on";
  const at = new Date(n.at);
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const day = at.toDateString() === today.toDateString() ? "Today"
    : at.toDateString() === tomorrow.toDateString() ? "Tomorrow"
      : at - today < 6 * 86400000 ? at.toLocaleDateString([], { weekday: "long" })
        : at.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });  // a week away: not "Thursday" on a Thursday
  return `Next alarm: ${day} ${hhmm(at)}${n.label ? ` · ${n.label}` : ""}`;
}

async function saveAlarms(alarms, message) {
  return saveSetting({ alarms }, message);
}

// The big clock: digits, or a face with hands.
function clockFace(size = "big") {
  if (C.face === "analogue") {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 200 200");
    svg.setAttribute("class", `analogue ${size}`);
    svg.setAttribute("role", "img");
    const part = (tag, attrs) => {
      const n = document.createElementNS(SVG, tag);
      for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
      svg.append(n);
      return n;
    };
    part("circle", { cx: 100, cy: 100, r: 94, class: "rim" });
    for (let i = 0; i < 60; i++) {
      const major = i % 5 === 0;
      part("line", { x1: 100, y1: major ? 12 : 9, x2: 100, y2: major ? 24 : 15, class: major ? "tick major" : "tick",
        transform: `rotate(${i * 6} 100 100)` });
    }
    part("line", { x1: 100, y1: 104, x2: 100, y2: 52, class: "hand hour", "data-hand": "h" });
    part("line", { x1: 100, y1: 108, x2: 100, y2: 30, class: "hand minute", "data-hand": "m" });
    part("line", { x1: 100, y1: 114, x2: 100, y2: 22, class: "hand second", "data-hand": "s" });
    part("circle", { cx: 100, cy: 100, r: 4, class: "hub" });
    return svg;
  }
  return el("div", { class: `digits ${size}`, "data-digits": "" },
    el("span", { class: "hm" }), el("span", { class: "sec" }));
}

// Move every clock on screen to now.
function tickClocks() {
  const now = new Date();
  for (const d of document.querySelectorAll("[data-digits]")) {
    d.querySelector(".hm").textContent = hhmm(now);
    d.querySelector(".sec").textContent = pad2(now.getSeconds());
  }
  for (const svg of document.querySelectorAll("svg.analogue")) {
    const s = now.getSeconds(), m = now.getMinutes() + s / 60, h = (now.getHours() % 12) + m / 60;
    svg.querySelector('[data-hand="h"]').setAttribute("transform", `rotate(${h * 30} 100 100)`);
    svg.querySelector('[data-hand="m"]').setAttribute("transform", `rotate(${m * 6} 100 100)`);
    svg.querySelector('[data-hand="s"]').setAttribute("transform", `rotate(${s * 6} 100 100)`);
    svg.setAttribute("aria-label", `The time is ${hhmm(now)}`);
  }
  for (const d of document.querySelectorAll("[data-today]")) {
    d.textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  }
  for (const d of document.querySelectorAll("[data-next-alarm]")) d.textContent = nextAlarmText();
}
setInterval(() => { if (document.querySelector("[data-digits], svg.analogue")) tickClocks(); }, 1000);
// Once the next alarm has gone by, ask the server which one is next.
setInterval(() => {
  const n = S.lib && S.lib.next_alarm;
  if (n && new Date(n.at) < Date.now() - 5000) loadState().then(() => { if (S.view.type === "clock" && !C.editing) renderView(); }).catch(() => {});
}, 15000);

function alarmEditor(a, isNew) {
  const sw = isSwitch(a);
  const what = sw ? "Switch" : "Alarm";
  const addButton = sw ? "#btn-add-switch" : "#btn-add-alarm";
  const time = el("input", { type: "time", id: "alarm-time", value: a.time, required: true });
  const label = el("input", { id: "alarm-label", value: a.label || "", maxlength: 60,
    placeholder: sw ? "Label (optional), e.g. The news" : "Label (optional), e.g. Work" });
  const days = DAY_NAMES.map((name, i) => el("input", { type: "checkbox", value: i, checked: a.days.includes(i) || null }));
  // Once: the next time this clock time comes round, unless a date was set.
  const date = el("input", { type: "date", id: "alarm-date", value: a.date || nextDay(a.time) });
  const repeat = el("select", { id: "alarm-repeat" },
    el("option", { value: "weekly" }, "Every week"), el("option", { value: "once" }, "Once"));
  repeat.value = a.date ? "once" : "weekly";
  const dateField = el("label", { class: "field" }, "Date", date);
  const daysField = el("fieldset", { class: "days" }, el("legend", {}, "Days"), ...days.map((d, i) => el("label", {}, d, DAY_NAMES[i])));
  const showRepeat = () => { dateField.hidden = repeat.value !== "once"; daysField.hidden = repeat.value === "once"; };
  repeat.addEventListener("change", showRepeat);
  time.addEventListener("change", () => { if (!a.date && repeat.value === "once") date.value = nextDay(time.value); });
  showRepeat();
  const source = el("select", { id: "alarm-source" },
    el("optgroup", { label: "Playlists" }, ...S.lib.playlists.map((p) => el("option", { value: `playlist:${p.id}` }, p.name))),
    el("optgroup", { label: "Radio" }, ...(S.lib.stations || []).map((st) => el("option", { value: `station:${st.id}` }, st.name))));
  if (a.source) source.value = `${a.source.kind}:${a.source.id}`;
  const msg = el("div", { class: "form-msg", role: "status" });
  const close = () => { C.editing = null; renderView(); refocus(isNew ? addButton : `#view [data-alarm="${a.id}"] .alarm-edit`); };
  const save = async (e) => {
    e.preventDefault();
    const once = repeat.value === "once";
    const chosen = once ? [] : days.filter((d) => d.checked).map((d) => Number(d.value));
    if (!once && !chosen.length) { msg.textContent = "Choose at least one day"; return; }
    if (once && !date.value) { msg.textContent = "Choose a date"; return; }
    if (once && new Date(`${date.value}T${time.value}`) <= new Date()) { msg.textContent = "That time has already gone by"; return; }
    if (!source.value) { msg.textContent = "Choose something to play"; return; }
    const [kind, ...rest] = source.value.split(":");
    const name = source.selectedOptions[0] ? source.selectedOptions[0].textContent : "";
    const updated = { ...a, on: true, time: time.value, label: label.value.trim(), days: chosen, date: once ? date.value : "",
      source: { kind, id: rest.join(":"), name } };
    const list = isNew ? [...alarmsList(), updated] : alarmsList().map((x) => (x.id === a.id ? updated : x));
    if (await saveAlarms(list, `${what} set for ${time.value}, ${whenText(updated).toLowerCase()}`)) {
      C.editing = null;
      renderView();
      const saved = alarmsList()[isNew ? alarmsList().length - 1 : alarmsList().findIndex((x) => x.id === a.id)];
      refocus(saved ? `#view [data-alarm="${saved.id}"] .alarm-edit` : addButton);
    }
  };
  const remove = async () => {
    if (!confirm(`Delete the ${a.time} ${what.toLowerCase()}${a.label ? ` (${a.label})` : ""}?`)) return;
    if (await saveAlarms(alarmsList().filter((x) => x.id !== a.id), `${what} deleted`)) {
      C.editing = null;
      renderView();
      refocus(addButton);
    }
  };
  return el("form", { class: "alarm-form", onsubmit: save,
    onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } } },
    el("div", { class: "fields" },
      el("label", { class: "field" }, "Time", time),
      el("label", { class: "field grow" }, "Label", label),
      el("label", { class: "field grow" }, sw ? "Switch to" : "Play", source)),
    el("div", { class: "fields" },
      el("label", { class: "field" }, "Repeat", repeat),
      dateField),
    daysField,
    msg,
    el("div", { class: "row" },
      el("button", { class: "btn primary" }, sw ? (isNew ? "Add to schedule" : "Save") : isNew ? "Set alarm" : "Save alarm"),
      el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel"),
      el("span", { class: "spacer" }),
      isNew ? null : el("button", { type: "button", class: "btn small danger", onclick: remove }, ...iconLabel("trash", "Delete"))));
}

function alarmCard(a) {
  const editing = C.editing === a.id;
  const what = isSwitch(a) ? "Switch" : "Alarm";
  const toggle = el("input", {
    type: "checkbox", checked: a.on || null, "data-key": `alarm-${a.id}`,
    "aria-label": `${what} at ${a.time}${a.label ? `, ${a.label}` : ""}`,
    onchange: async (e) => {
      const on = e.target.checked;
      // A one-off whose time has gone by moves on to the next time round.
      const stale = on && a.date && new Date(`${a.date}T${a.time}`) <= new Date();
      const list = alarmsList().map((x) => (x.id === a.id ? { ...x, on, ...(stale ? { date: nextDay(a.time) } : {}) } : x));
      await saveAlarms(list, on ? `${what} on for ${a.time}` : `${what} at ${a.time} off`);
      renderView();
      refocus(`#view input[data-key="alarm-${a.id}"]`);
    },
  });
  return el("li", { class: a.on ? "alarm-card" : "alarm-card off", "data-alarm": a.id },
    el("div", { class: "alarm-row" },
      el("div", { class: "alarm-time" }, a.time),
      el("div", { class: "info" },
        el("div", { class: "name" }, a.label || (isSwitch(a) ? `Switch to ${a.source ? a.source.name : ""}` : "Alarm")),
        el("div", { class: "facts" }, `${whenText(a)} · ${a.source ? a.source.name : ""}`)),
      el("button", {
        type: "button", class: "btn ghost icon-only alarm-edit", "aria-label": `Edit the ${a.time} ${what.toLowerCase()}`,
        onclick: () => { C.editing = a.id; renderView(); refocus("#alarm-time"); },
      }, icon("edit")),
      el("label", { class: "check" }, toggle)),
    editing ? alarmEditor(a, false) : null);
}

function clockView() {
  const alarms = alarmsList().filter((a) => !isSwitch(a));
  const adding = C.editing === "new";
  const faceButton = (face, label) => el("button", {
    type: "button", class: "seg", "aria-pressed": String(C.face === face),
    onclick: () => { C.face = face; store("clock-face", face); renderView(); refocus(`#view .seg[aria-pressed="true"]`); },
  }, label);
  const blank = { id: "", on: true, time: "07:00", label: "", days: [0, 1, 2, 3, 4], date: "", action: "alarm", source: null };
  queueMicrotask(tickClocks);
  return el("div", { class: "clock-page" },
    el("div", { class: "view-head" },
      el("h1", {}, "Clock"),
      el("div", { class: "spacer" }),
      el("div", { class: "segmented", role: "group", "aria-label": "Clock style" },
        faceButton("digital", "Digits"), faceButton("analogue", "Clock face")),
      el("button", { type: "button", class: "btn", id: "btn-night", onclick: nightMode }, ...iconLabel("moon", "Night mode"))),
    el("div", { class: "clock-hero" },
      clockFace("big"),
      el("div", { class: "today", "data-today": "" }),
      el("div", { class: "next-alarm", "data-next-alarm": "", role: "status" }),
      S.lib.alarm_state && S.lib.alarm_state.snoozed_until ? el("button", {
        type: "button", class: "btn small", id: "btn-cancel-snooze",
        onclick: async () => { await stopAlarm({ keepPlaying: true, message: "Snooze cancelled" }); renderView(); refocus("#btn-add-alarm"); },
      }, "Cancel snooze") : null),
    el("section", { class: "alarms" },
      el("div", { class: "section-head" },
        el("h2", {}, "Alarms"),
        el("span", { class: "spacer" }),
        el("button", {
          type: "button", class: "btn primary", id: "btn-add-alarm", disabled: alarmsList().length >= MAX_ALARMS || adding || null,
          onclick: () => { C.editing = "new"; renderView(); refocus("#alarm-time"); },
        }, ...iconLabel("plus", "Add alarm"))),
      el("p", { class: "help" },
        "Alarms fade in over a minute. Station Player has to be running at the time (turn on Start at login under Settings › App) ",
        "and the computer awake: it can't wake a sleeping computer."),
      adding ? el("div", { class: "alarm-card new" }, alarmEditor(blank, true)) : null,
      alarms.length ? el("ul", { class: "alarm-list" }, [...alarms].sort((x, y) => x.time.localeCompare(y.time)).map(alarmCard))
        : adding ? null : el("div", { class: "empty-state" },
            el("div", { class: "glyph big" }, icon("clock")),
            el("h2", {}, "No alarms yet"),
            el("p", {}, "Wake up to a playlist or a radio station. Add an alarm to get started."))));
}

// ---------- alarm going off ---------------------------------------------------

let alarmScreenReturn = null;

// By the computer's own clock: an evening alarm doesn't say good morning.
function alarmGreeting(now = new Date()) {
  const h = now.getHours();
  if (h >= 5 && h < 12) return "Good morning";
  if (h >= 12 && h < 18) return "Good afternoon";
  if (h >= 18 && h < 23) return "Good evening";
  return "Hello";
}

function showAlarmScreen(req) {
  const box = $("#alarm-screen");
  alarmScreenReturn = document.activeElement;
  box.replaceChildren(
    el("div", { class: "alarm-inner" },
      clockFace("huge"),
      el("h2", { id: "alarm-screen-title" }, req.label || (req.snoozed ? "Alarm (snoozed)" : alarmGreeting())),
      el("p", { class: "alarm-playing" }, req.source && req.source.name ? `Playing ${req.source.name}` : ""),
      el("div", { class: "alarm-actions" },
        el("button", { type: "button", class: "btn big primary", id: "btn-snooze", onclick: () => snoozeAlarm() },
          ...iconLabel("moon", `Snooze ${SNOOZE_MINUTES} min`)),
        el("button", { type: "button", class: "btn big", id: "btn-stop-alarm", onclick: () => stopAlarm() },
          ...iconLabel("stop", "Stop"))),
      el("button", { type: "button", class: "btn ghost", id: "btn-keep-playing", onclick: () => stopAlarm({ keepPlaying: true }) },
        "Turn off the alarm, keep the music playing")));
  box.hidden = false;
  tickClocks();
  $("#btn-snooze").focus();
}

function hideAlarmScreen() {
  const box = $("#alarm-screen");
  if (box.hidden) return;
  box.hidden = true;
  box.replaceChildren();
  if (alarmScreenReturn && document.contains(alarmScreenReturn)) alarmScreenReturn.focus();
  alarmScreenReturn = null;
}

function endAlarmFade() {
  clearInterval(ALARM.timer);
  ALARM.timer = null;
  if (volMul.alarm !== 1) { volMul.alarm = 1; applyVolume(); }
}

// The server is told first: if it says no (the alarm had already ended),
// the screen and the music stay as they are and the message says why.
async function snoozeAlarm() {
  const seq = ALARM.seq;
  ALARM.seq = null;  // so the inbox check meanwhile doesn't end it a second time
  try {
    await loadState(await api("/api/alarm/snooze", { minutes: SNOOZE_MINUTES }, { quiet: true }));
  } catch (err) {
    ALARM.seq = seq;
    toast(err.status === 409 ? "The alarm had already stopped" : err.message, { error: true });
    return;
  }
  endAlarm({ pause: true });
  const until = S.lib.alarm_state && S.lib.alarm_state.snoozed_until;
  toast(until ? `Snoozed until ${hhmm(new Date(until * 1000))}` : "Snoozed");
}

async function stopAlarm({ keepPlaying = false, message = "" } = {}) {
  const seq = ALARM.seq;
  ALARM.seq = null;
  try {
    await loadState(await api("/api/alarm/stop", {}, { quiet: true }));
  } catch (err) {
    ALARM.seq = seq;
    toast(err.message, { error: true });
    return;
  }
  endAlarm({ pause: !keepPlaying });
  toast(message || (keepPlaying ? "Alarm off. The music plays on." : "Alarm stopped"));
}

// This window's alarm is over: snoozed or stopped here, from the phone or
// another window, or it rang unanswered for half an hour.
function endAlarm({ pause }) {
  ALARM.seq = null;
  if (pause) pauseAll();
  endAlarmFade();
  hideAlarmScreen();
  if (S.view.type === "clock" && !C.editing) renderView();
}

// Called on every inbox check while this window's alarm is ringing.
async function alarmEndedElsewhere() {
  try { await loadState(); } catch { /* still says what happened */ }
  endAlarm({ pause: true });
  const until = S.lib.alarm_state && S.lib.alarm_state.snoozed_until;
  toast(until ? `Snoozed until ${hhmm(new Date(until * 1000))}` : "Alarm stopped");
}

$("#alarm-screen").addEventListener("keydown", (e) => {
  e.stopPropagation();  // the player's own shortcuts (Space plays and pauses) wait
  if (e.key === "Escape") { e.preventDefault(); stopAlarm({ keepPlaying: true }); return; }
  if (e.key !== "Tab") return;
  // Focus stays on the alarm screen while it's up.
  const items = [...$("#alarm-screen").querySelectorAll("button")];
  const at = items.indexOf(document.activeElement);
  e.preventDefault();
  items[(at + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus();
});

// ---------- night mode ----------------------------------------------------------

let wakeLock = null;
// Full screen takes a moment to come on, and night mode can be left
// before it has (a quick second click, or a key): leaveNight waits for it,
// so the window isn't left full screen on nothing.
let nightOpening = null;

async function nightMode() {
  const box = $("#night");
  box.replaceChildren(
    clockFace("huge"),
    el("div", { class: "today", "data-today": "" }),
    el("div", { class: "next-alarm", "data-next-alarm": "" }),
    el("p", { class: "night-hint" }, "Click or press any key to leave night mode"));
  box.hidden = false;
  tickClocks();
  box.focus();
  nightOpening = (async () => {
    try { if (box.requestFullscreen) await box.requestFullscreen(); } catch { /* fine in a window */ }
  })();
  await nightOpening;
  if (box.hidden) return;  // left meanwhile
  try {
    const lock = navigator.wakeLock ? await navigator.wakeLock.request("screen") : null;
    // Left while asking: let the screen sleep after all.
    if (box.hidden) lock?.release().catch(() => {});
    else wakeLock = lock;
  } catch { wakeLock = null; }
}

// Resolves once full screen has closed (which moves focus), so focus can
// be put back afterwards: on the Night mode button, or wherever the caller says.
async function leaveNight({ restoreFocus = true } = {}) {
  const box = $("#night");
  if (box.hidden) return;
  box.hidden = true;
  box.replaceChildren();
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  await nightOpening;  // full screen still coming on
  if (!box.hidden) return;  // opened again meanwhile: that's the one showing
  if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
  if (restoreFocus) refocus("#btn-night");
}
$("#night").addEventListener("click", () => leaveNight());
$("#night").addEventListener("keydown", (e) => { e.preventDefault(); e.stopPropagation(); leaveNight(); });
document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && !$("#night").hidden) leaveNight(); });


