"use strict";

// The page's script: this file and the others in static/js/, joined in
// the order station_player/pagescript.py lists them and served as
// /app.js, one script (so each file's names are the others' too).

// ---------- small helpers ----------------------------------------------

// The player bar may be out in its own little window (see "the mini
// player"), so its parts are looked for there too.
let miniDoc = null;
const $ = (sel) => document.querySelector(sel) || (miniDoc && miniDoc.querySelector(sel));
// In the downloaded Mac app's own window (see "the Mac app's own window").
const NATIVE = Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.stationApp);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// Icons: line drawings on a 24 px grid. Each part is a path, or
// "c cx cy r" (circle) or "r x y w h rx" (rect); a leading "*" fills it.
const ICONS = {
  play: ["*M8 5.6v12.8a1 1 0 0 0 1.5.86l10.2-6.4a1 1 0 0 0 0-1.72L9.5 4.74A1 1 0 0 0 8 5.6z"],
  pause: ["*r 6.5 5 4 14 1.2", "*r 13.5 5 4 14 1.2"],
  next: ["*M5 6.3v11.4a.8.8 0 0 0 1.25.66L14.6 12.7a.8.8 0 0 0 0-1.4L6.25 5.64A.8.8 0 0 0 5 6.3z", "*r 16.5 5.5 2.6 13 1"],
  prev: ["*M19 6.3v11.4a.8.8 0 0 1-1.25.66L9.4 12.7a.8.8 0 0 1 0-1.4l8.35-5.66A.8.8 0 0 1 19 6.3z", "*r 4.9 5.5 2.6 13 1"],
  shuffle: ["M16 3h5v5", "M4 20L21 3", "M21 16v5h-5", "M15 15l6 6", "M4 4l5 5"],
  repeat: ["M17 2l4 4-4 4", "M3 11V9a3 3 0 0 1 3-3h15", "M7 22l-4-4 4-4", "M21 13v2a3 3 0 0 1-3 3H3"],
  repeat1: ["M17 2l4 4-4 4", "M3 11V9a3 3 0 0 1 3-3h15", "M7 22l-4-4 4-4", "M21 13v2a3 3 0 0 1-3 3H3", "M11 10.5l1.5-1v5"],
  volume: ["*M11 5L6.5 9H3.5a.5.5 0 0 0-.5.5v5a.5.5 0 0 0 .5.5h3L11 19z", "M15.5 8.5a5 5 0 0 1 0 7", "M18.5 5.5a9 9 0 0 1 0 13"],
  more: ["*c 5 12 1.7", "*c 12 12 1.7", "*c 19 12 1.7"],
  minus: ["M5 12h14"],
  globe: ["c 12 12 9", "M3 12h18", "M12 3a14 14 0 0 1 0 18", "M12 3a14 14 0 0 0 0 18"],
  home: ["M3.5 11L12 4l8.5 7", "M5.5 9.5V20h13V9.5", "M10 20v-5.5h4V20"],
  mic: ["M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z", "M5.5 11a6.5 6.5 0 0 0 13 0", "M12 17.5V21"],
  plus: ["M12 5v14", "M5 12h14"],
  folder: ["M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v6.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z"],
  list: ["M9 6h12", "M9 12h12", "M9 18h12", "*c 4.5 6 1.3", "*c 4.5 12 1.3", "*c 4.5 18 1.3"],
  music: ["M9 18V5.5l11-2V16", "c 6 18 3", "c 17 16 3"],
  sport: ["c 12 12 9", "M12 7.5l4 2.9-1.5 4.6h-5L8 10.4z", "M12 7.5V3", "M16 10.4l4.3-1.4", "M14.5 15l2.7 3.7", "M9.5 15l-2.7 3.7", "M8 10.4L3.7 9"],
  radio: ["c 12 12 2", "M16.24 7.76a6 6 0 0 1 0 8.48", "M7.76 16.24a6 6 0 0 1 0-8.48", "M19.07 4.93a10 10 0 0 1 0 14.14", "M4.93 19.07a10 10 0 0 1 0-14.14"],
  queue: ["M3 6h15", "M3 12h15", "M3 18h8", "*M15.5 15.2v5.6a.6.6 0 0 0 .9.5l4.3-2.8a.6.6 0 0 0 0-1l-4.3-2.8a.6.6 0 0 0-.9.5z"],
  settings: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1.5 14h5", "M9.5 8h5", "M17.5 16h5"],
  search: ["c 11 11 7", "M21 21l-4.35-4.35"],
  power: ["M18.36 6.64a9 9 0 1 1-12.73 0", "M12 2v10"],
  check: ["M20 6L9 17l-5-5"],
  record: ["*c 12 12 6"],
  stop: ["*r 6 6 12 12 2"],
  moon: ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"],
  waves: ["M2 8c2.5-2 4.5-2 7 0s4.5 2 7 0 4.5-2 6 0", "M2 13c2.5-2 4.5-2 7 0s4.5 2 7 0 4.5-2 6 0", "M2 18c2.5-2 4.5-2 7 0s4.5 2 7 0 4.5-2 6 0"],
  alert: ["M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
  edit: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  clock: ["c 12 12 9", "M12 7v5l3 2"],
  phone: ["r 6.5 2 11 20 2.5", "M11 18h2"],
  car: ["M4 12l1.8-4.6A2 2 0 0 1 7.7 6h8.6a2 2 0 0 1 1.9 1.4L20 12", "r 2.5 12 19 5.5 1.5", "M5 17.5v2", "M19 17.5v2", "*c 7 14.7 1.1", "*c 17 14.7 1.1"],
  download: ["M12 3v12", "M7 10l5 5 5-5", "M5 21h14"],
  app: ["r 3 4 18 16 2.5", "M3 9h18"],
  headphones: ["M3 18v-6a9 9 0 0 1 18 0v6", "M21 19a2 2 0 0 1-2 2h-1v-6h3z", "M3 19a2 2 0 0 0 2 2h1v-6H3z"],
  log: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M8 13h8", "M8 17h8"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M9 15h6"],
  sparkle: ["M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z", "M19 17l.6 1.4L21 19l-1.4.6L19 21l-.6-1.4L17 19l1.4-.6z"],
  refresh: ["M21 12a9 9 0 1 1-2.64-6.36L21 8", "M21 3v5h-5"],
  help: ["c 12 12 9", "M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 3.9", "*c 12 17 1.1"],
  locate: ["c 11 11 7", "M21 21l-4.35-4.35", "M11 8v6", "M8 11h6"],
  popout: ["M14 4h6v6", "M20 4l-8 8", "M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"],
  popin: ["M12 12l8-8", "M12 6v6h6", "M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"],
};
const SVG = "http://www.w3.org/2000/svg";

function icon(name) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  for (let part of ICONS[name] || []) {
    const filled = part.startsWith("*");
    if (filled) part = part.slice(1);
    const [kind, ...n] = part.split(" ");
    let node;
    if (kind === "c") {
      node = document.createElementNS(SVG, "circle");
      ["cx", "cy", "r"].forEach((k, i) => node.setAttribute(k, n[i]));
    } else if (kind === "r") {
      node = document.createElementNS(SVG, "rect");
      ["x", "y", "width", "height", "rx"].forEach((k, i) => node.setAttribute(k, n[i]));
    } else {
      node = document.createElementNS(SVG, "path");
      node.setAttribute("d", part);
    }
    if (filled) node.setAttribute("class", "f");
    svg.append(node);
  }
  return svg;
}

// A button with an icon before its label.
const iconLabel = (name, label) => [icon(name), el("span", {}, label)];

// The slim sliders fill up to their value (see style.css).
function fillRange(input) {
  const min = Number(input.min) || 0;
  const max = Number(input.max) || 100;
  const at = max > min ? ((Number(input.value) - min) / (max - min)) * 100 : 0;
  input.style.setProperty("--p", `${Math.max(0, Math.min(100, at))}%`);
}
document.addEventListener("input", (e) => { if (e.target.matches('input[type="range"]')) fillRange(e.target); });

// A short confirmation in the corner ("Saved"), also read out.
function toast(text, { error = false } = {}) {
  announce(text);
  const box = $("#toasts");
  if (!box) return;
  const t = el("div", { class: error ? "toast error" : "toast" }, icon(error ? "alert" : "check"), el("span", {}, text));
  box.append(t);
  while (box.children.length > 3) box.firstElementChild.remove();
  setTimeout(() => {
    t.classList.add("leaving");
    setTimeout(() => t.remove(), 300);
  }, 2600);
}

for (const slot of document.querySelectorAll("[data-icon]")) slot.replaceWith(icon(slot.dataset.icon));

function fmtTime(seconds) {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem("sp." + key);
    localStorage.setItem("sp." + key, value);
  } catch { return null; }
}

let pending = 0;

function setBusy(delta) {
  pending += delta;
  const busy = pending > 0;
  const status = $("#busy");
  status.hidden = !busy;
  document.body.classList.toggle("busy", busy);
  for (const b of document.querySelectorAll("[data-busy-disable]")) b.disabled = busy;
}

// Polite announcement for screen readers (visually hidden).
function announce(text) {
  const region = $("#announce");
  region.textContent = "";
  setTimeout(() => { region.textContent = text; }, 50);
}

// `quiet` is for background calls, which shouldn't show "Updating library…".
async function api(path, body, { quiet = false } = {}) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Station-Player": "1" },
    body: JSON.stringify(body),
  };
  // Settings changes rescan the library, which can take a while.
  const busy = body !== undefined && !quiet;
  if (busy) setBusy(1);
  try {
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `Request failed (${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  } finally {
    if (busy) setBusy(-1);
  }
}


function readJSON(key) {
  try { return JSON.parse(store(key) || "null"); } catch { return null; }
}

// ---------- state --------------------------------------------------------

// Two players for library tracks: while one plays, the other loads the
// next track, and starts the moment the first finishes (gapless). Radio
// has two of its own, never routed through volume levelling: one for the
// player's relay, which the sound wave listens to, and one for a station
// played straight from its own site (routed through the audio graph,
// another site's sound would be silenced).
const decks = [new Audio(), new Audio()];
for (const d of decks) d.preload = "auto";
const radioRelay = new Audio();
const radioDirect = new Audio();
const radios = [radioRelay, radioDirect];
for (const r of radios) r.preload = "none";
let radioEl = radioRelay;  // the radio player in use
// Whether the listener last asked for sound (play) or silence (pause).
// A stream that fails just stops, with no "pause", so this tells a
// listener's pause from a failure, which audio.paused can't.
let radioWanted = false;
let deckIdx = 0;
let audio = decks[0];  // the element playing (or paused) right now
const otherDeck = () => decks[1 - deckIdx];

const S = {
  lib: { playlists: [], stations: [], config: {}, errors: [] },
  view: { type: "settings" },
  detail: null,          // playlist (or search results) currently shown
  filter: "",
  queue: [],             // tracks (objects) in play order
  order: [],             // indexes into queue (shuffled or not)
  pos: -1,               // position in order
  queueSource: null,     // view id the queue came from
  queueLabel: "",        // shown under the track: playlist name or "Search"
  upNext: [],            // tracks queued with "Play next" / "Add to queue"
  extra: null,           // the queued track playing now, if any
  mode: "idle",          // "idle" | "track" | "radio"
  station: null,
  radioSrc: null,        // relay through the player, or the URL itself
  radioTitle: null,      // song the station says is playing
  reconnects: [],        // times the radio dropped and was reconnected
  shuffle: store("shuffle") === "1",
  repeat: store("repeat") || "off", // off | all | one
};

const current = () => S.extra || (S.pos >= 0 ? S.queue[S.order[S.pos]] : null);

