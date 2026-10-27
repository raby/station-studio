"use strict";

// ---------- small helpers ----------------------------------------------

const $ = (sel) => document.querySelector(sel);
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

// ---------- library + navigation ----------------------------------------

// Studio tools (station-studio's playlists, recording, the play log) are
// for people making a station; without them it's the plain player.
const studio = () => S.lib?.config?.studio !== false;
const STUDIO_HELP = ["record", "log"];

async function loadState(newState) {
  S.lib = newState || (await api("/api/state"));
  document.body.classList.toggle("public", !studio());
  renderNav();
  renderErrors();
  renderUpdate();
  syncRecordings();
}

function renderErrors(extra) {
  const box = $("#errors");
  // A folder or library that has gone is shown (with a way to find it)
  // on the Music settings page, so here it's one line pointing there.
  const gone = (S.lib.sources || []).filter((s) => !s.exists);
  const errors = S.lib.errors.filter((e) => !gone.some((s) => e.includes(s.path)));
  const onMusicPage = S.view.type === "settings" && (S.view.tab || "music") === "music";
  const goneLine = gone.length && !onMusicPage ? el("div", {},
    gone.length === 1 ? `The ${gone[0].kind === "music" ? "music folder" : "playlist library"} “${gone[0].name || gone[0].path}” can't be found. ` : `${gone.length} of your music sources can't be found. `,
    el("button", {
      type: "button", class: "btn small",
      onclick: () => show({ type: "settings", tab: "music" }),
    }, "Show me")) : null;
  const all = extra ? [extra, ...errors] : errors;
  box.replaceChildren(...all.map((e) => el("div", {}, e)), goneLine || "");
  box.hidden = all.length === 0 && !goneLine;
}

// "A new version is out", until dismissed for that version; while
// updating, how far it's got.
function renderUpdate() {
  const box = $("#update");
  const latest = S.lib.update;
  const u = S.lib.updating;
  if (u && u.stage !== "failed") {
    box.replaceChildren(el("div", {}, updateStageText(u)));
    box.hidden = false;
    return;
  }
  if (!latest || store("dismissed-update") === latest) {
    box.hidden = true;
    return;
  }
  box.replaceChildren(...[  // (replaceChildren would show a null as "null")
    el("div", {}, `Station Player ${latest} is out.`),
    u && u.stage === "failed" && u.version === latest ? updateFailure(u) : null,
    el("div", { class: "row" },
      el("button", { type: "button", class: "btn small primary", onclick: () => startUpdate(latest) }, "Update now"),
      el("button", {
        type: "button", class: "btn small",
        onclick: () => { store("dismissed-update", latest); renderUpdate(); },
      }, "Not now")),
  ].filter(Boolean));
  box.hidden = false;
}

function updateStageText(u) {
  if (u.stage === "downloading") return `Downloading Station Player ${u.version}… ${u.percent || 0}%`;
  if (u.stage === "installing") return `Installing Station Player ${u.version}…`;
  return `Restarting Station Player… ${u.version} opens here by itself.`;
}

function updateFailure(u) {
  return el("div", { class: "update-failed", role: "alert" }, u.error, " ",
    el("a", { href: u.page, target: "_blank", rel: "noopener" }, "Download it yourself"), ".");
}

// Update now: the player downloads and installs the new version, quits,
// and the new one starts; this page waits for it and reloads into it.
async function startUpdate(version) {
  if (activeRecordings().length && !confirm("Updating stops your recordings (what's recorded so far is saved). Update now?")) return;
  const from = S.lib.version;
  try {
    await loadState(await api("/api/update", { version }, { quiet: true }));
  } catch (err) {
    toast(err.message, { error: true });
    return;
  }
  announce(`Updating Station Player to ${version}`);
  if (S.view.type === "settings") renderView();
  const until = Date.now() + 10 * 60 * 1000;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 1000));
    let state = null;
    try {
      const res = await fetch("/api/state");
      if (res.ok) state = await res.json();
    } catch { /* restarting */ }
    if (!state) {
      S.lib.updating = { ...(S.lib.updating || { version }), stage: "restarting" };
      renderUpdate();
      continue;
    }
    if (state.version !== from) {
      location.reload();
      return;
    }
    S.lib.updating = state.updating;
    renderUpdate();
    if (S.view.type === "settings") syncUpdateSettings();
    if (!state.updating || state.updating.stage === "failed") return;
  }
  S.lib.updating = { version, stage: "failed", page: `https://github.com/raby/station-studio/releases/tag/player-v${version}`,
    error: "Station Player didn't come back after updating. Open it again from Applications or the Start menu." };
  renderUpdate();
}

// Run a settings action from a button; failures show in the error box.
async function act(path, body, done = "Done") {
  try {
    await loadState(await api(path, body));
    renderView();
    focusHeading();
    toast(done);
  } catch (err) {
    renderErrors(err.message);
  }
}

// After a view is rebuilt, put focus somewhere sensible, not <body>.
function focusHeading() {
  const h = $("#view h1");
  if (h) { h.tabIndex = -1; h.focus(); }
}

function navButton(label, count, view, active, iconName = "list") {
  return el("li", {},
    el("button", {
      type: "button", class: "nav-item", "aria-current": active ? "true" : "false",
      onclick: () => show(view),
    }, icon(iconName), el("span", {}, label), count === null ? null : el("span", { class: "count" }, count)));
}

function renderNav() {
  const isActive = (pl) => S.view.type === "playlist" && S.view.id === pl.id;
  const itunes = S.lib.playlists.filter((p) => p.kind === "itunes");
  const folders = S.lib.playlists.filter((p) => p.kind === "folder");
  const mine = myPlaylists();
  $("#nav-mine").replaceChildren(...(mine.length
    ? mine.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p), "music"))
    : [el("li", { class: "nav-empty" }, "Make one with +")]));
  $("#nav-itunes").replaceChildren(...(itunes.length
    ? itunes.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p)))
    : [el("li", { class: "nav-empty" }, "None yet")]));
  $("#nav-folders").replaceChildren(...(folders.length
    ? folders.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p), "folder"))
    : [el("li", { class: "nav-empty" }, "None yet")]));
  for (const b of document.querySelectorAll(".nav-item[data-view]")) {
    b.setAttribute("aria-current", S.view.type === b.dataset.view ? "true" : "false");
  }
  $("#btn-add-music").setAttribute("aria-current", S.view.type === "welcome" ? "true" : "false");
  const count = $("#upnext-count");
  count.textContent = S.upNext.length || "";
  const next = S.lib && S.lib.next_alarm;
  $("#nav-next-alarm").textContent = next ? hhmm(new Date(next.at)) : "";
  $("#nav-next-alarm").title = next ? `Next alarm ${hhmm(new Date(next.at))}` : "";
}

let showToken = 0;

async function show(view) {
  // Only the latest click may render: a slow response for an earlier
  // click must not replace the playlist the user has since opened.
  const token = ++showToken;
  S.view = view;
  editingStation = null;
  renderErrors();
  if (view.type !== "search") store("view", JSON.stringify(view));
  if (view.type !== "search") try { sessionStorage.setItem("sp.view", JSON.stringify(view)); } catch { /* private window */ }
  if (view.type === "playlist" || view.type === "search") {
    let detail = null;
    try {
      detail = view.type === "playlist"
        ? await api(`/api/playlists/${view.id}`)
        : searchResults(view.q, await api(`/api/search?q=${encodeURIComponent(view.q)}`));
    } catch {
      detail = null;
    }
    if (token !== showToken) return;
    if (detail) {
      S.detail = detail;
    } else {
      S.view = { type: "settings" };
      S.detail = null;
    }
    S.filter = "";
  }
  if (view.type === "recordings") loadRecordings();
  renderNav();
  renderView();
  $("#main").scrollTop = 0;
}

function searchResults(q, found) {
  return {
    id: `search:${q}`, name: `Search: ${q}`, search: true, tracks: found.tracks,
    count: found.tracks.length, total: found.total,
    missing: found.tracks.filter((t) => !t.available).length,
  };
}

function renderView() {
  const root = $("#view");
  if (!studio() && (S.view.type === "recordings" || (S.view.type === "settings" && S.view.tab === "log"))) {
    S.view = { type: "radio" };  // studio tools were turned off
    renderNav();
  }
  if ((S.view.type === "playlist" || S.view.type === "search") && S.detail) root.replaceChildren(playlistView(S.detail));
  else if (S.view.type === "home") root.replaceChildren(homeView());
  else if (S.view.type === "discover") root.replaceChildren(discoverView());
  else if (S.view.type === "radio") root.replaceChildren(radioView());
  else if (S.view.type === "recordings") root.replaceChildren(recordingsView());
  else if (S.view.type === "upnext") root.replaceChildren(upNextView());
  else if (S.view.type === "welcome") root.replaceChildren(welcomeView());
  else if (S.view.type === "clock") root.replaceChildren(clockView());
  else if (S.view.type === "help") root.replaceChildren(helpView());
  else root.replaceChildren(settingsView());
  for (const r of root.querySelectorAll('input[type="range"]')) fillRange(r);
}

// ---------- playlist view -----------------------------------------------

function matches(t, q) {
  if (!q) return true;
  return [t.name, t.artist, t.album, t.genre].some((v) => v && v.toLowerCase().includes(q));
}

function playlistView(pl) {
  // Rows are built once per view. Filtering hides rows and track changes
  // move the "playing" class, so a 10k-track library stays responsive.
  // Keyboard: one row is in the tab order at a time (roving tabindex);
  // arrow keys move between rows and Enter plays the focused one.
  const mine = isMine(pl);
  const rows = pl.tracks.map((t, i) => el("tr", {
    "data-index": i,
    "data-id": t.id,
    tabindex: "-1",
    draggable: mine ? "true" : null,
    class: t.available ? null : "missing",
    title: t.available ? "Double-click or press Enter to play" : "The audio file for this track wasn't found",
  },
    el("td", { class: "num" }, i + 1),
    el("td", { class: "title" }, t.name),
    el("td", { class: "artist" }, t.artist || ""),
    el("td", { class: "album" }, t.album || ""),
    el("td", { class: "dur" }, t.duration_ms ? fmtTime(t.duration_ms / 1000) : ""),
    el("td", { class: "more" }, t.available || mine
      ? el("button", { type: "button", class: "row-more", tabindex: "-1", "aria-label": `More for ${t.name}`, title: "Play next, add to a playlist and more" }, icon("more"))
      : null),
  ));
  const visibleRows = () => rows.filter((r) => !r.hidden);
  const focusRow = (tr) => {
    for (const r of rows) if (r.tabIndex === 0) r.tabIndex = -1;
    tr.tabIndex = 0;
    tr.focus();
  };
  const trackOf = (tr) => pl.tracks[Number(tr.dataset.index)];

  // Selecting several tracks: Cmd/Ctrl-click adds or takes away one,
  // Shift-click (or Shift+arrows) a run, Cmd/Ctrl+A all those showing.
  const sel = new Set();
  let anchor = null;
  const selected = () => [...sel].sort((a, b) => a - b).map((i) => pl.tracks[i]);
  const bar = el("div", {
    class: "select-bar", hidden: true, role: "toolbar", "aria-label": "Selected tracks",
    onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); clearSelection(); } },
  });
  const marked = new Set();  // rows shown as selected (only those change)
  const syncSelection = () => {
    const want = sel.size > 1 ? sel : new Set();
    for (const i of [...marked]) {
      if (want.has(i)) continue;
      rows[i].classList.remove("selected");
      rows[i].querySelector(".sel-note")?.remove();
      marked.delete(i);
    }
    for (const i of want) {
      if (marked.has(i)) continue;
      rows[i].classList.add("selected");
      rows[i].querySelector("td.title").append(el("span", { class: "sr-only sel-note" }, " (selected)"));
      marked.add(i);
    }
    if (sel.size < 2) { bar.hidden = true; return; }
    const group = selected();
    const playable = group.filter((t) => t.available);
    const what = plural(playable.length, "track");
    const addBtn = el("button", {
      type: "button", class: "btn small primary", disabled: playable.length ? null : true, "aria-haspopup": "menu",
      onclick: (e) => playlistChooser(playable.map((t) => t.id), what, e.currentTarget, e.currentTarget, null, pl.id),
    }, ...iconLabel("plus", "Add to playlist…"));
    bar.replaceChildren(...[  // (replaceChildren would show a null as "null")
      el("span", { class: "count" }, `${plural(sel.size, "track")} selected`),
      addBtn,
      el("button", { type: "button", class: "btn small", disabled: playable.length ? null : true, onclick: () => queueTracks(playable, pl, "next") }, "Play next"),
      el("button", { type: "button", class: "btn small", disabled: playable.length ? null : true, onclick: () => queueTracks(playable, pl, "end") }, "Add to queue"),
      mine ? el("button", { type: "button", class: "btn small danger", onclick: () => removeManyFromPlaylist(pl, [...sel]) }, "Remove") : null,
      el("span", { class: "spacer" }),
      el("button", { type: "button", class: "btn small ghost", onclick: () => { clearSelection(); } }, "Clear"),
    ].filter(Boolean));
    bar.hidden = false;
  };
  const clearSelection = () => {
    const back = bar.contains(document.activeElement);
    sel.clear();
    anchor = null;
    syncSelection();
    if (back) rows.find((r) => r.tabIndex === 0 && !r.hidden)?.focus();
  };
  const selectRun = (to) => {
    const vis = visibleRows();
    const a = vis.indexOf(rows[anchor ?? Number(to.dataset.index)]);
    const b = vis.indexOf(to);
    sel.clear();
    if (a < 0) { sel.add(Number(to.dataset.index)); } else {
      for (const r of vis.slice(Math.min(a, b), Math.max(a, b) + 1)) sel.add(Number(r.dataset.index));
    }
    syncSelection();
  };
  const pick = (tr, e) => {
    const i = Number(tr.dataset.index);
    if (e.shiftKey) { selectRun(tr); return; }
    if (e.metaKey || e.ctrlKey) {
      if (sel.has(i)) sel.delete(i); else sel.add(i);
      anchor = i;
      syncSelection();
      return;
    }
    sel.clear();
    sel.add(i);
    anchor = i;
    syncSelection();
  };
  const groupFor = (tr) => (sel.size > 1 && sel.has(Number(tr.dataset.index)) ? selected() : null);

  const tbody = el("tbody", {
    ondblclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (tr && !e.target.closest(".row-more") && !e.shiftKey && !e.metaKey && !e.ctrlKey) playPlaylist(pl, Number(tr.dataset.index));
    },
    onclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      focusRow(tr);
      if (e.target.closest(".row-more")) {
        openTrackMenu(trackOf(tr), pl, e.target.closest(".row-more"), tr, undefined, groupFor(tr));
        return;
      }
      pick(tr, e);
    },
    onmousedown: (e) => { if (e.shiftKey) e.preventDefault(); },  // no text selection on Shift-click
    oncontextmenu: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      const group = groupFor(tr);
      const useful = group ? mine || group.some((t) => t.available) : trackOf(tr).available || mine;
      if (!useful) return;  // nothing to offer: leave the browser's own menu
      e.preventDefault();
      focusRow(tr);
      openTrackMenu(trackOf(tr), pl, tr, tr, { x: e.clientX, y: e.clientY }, group);
    },
    onkeydown: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      if ((e.metaKey || e.ctrlKey) && (e.key === "a" || e.key === "A")) {
        e.preventDefault();
        for (const r of visibleRows()) sel.add(Number(r.dataset.index));
        syncSelection();
        announce(`${plural(sel.size, "track")} selected`);
        return;
      }
      if (e.key === "Escape" && sel.size > 1) {
        e.preventDefault();
        clearSelection();
        announce("Selection cleared");
        return;
      }
      if (e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && !e.altKey) {
        e.preventDefault();
        const vis = visibleRows();
        const next = vis[vis.indexOf(tr) + (e.key === "ArrowUp" ? -1 : 1)];
        if (!next) return;
        // A run starts from this track, unless it extends one it belongs to.
        if (anchor === null || rows[anchor].hidden || !sel.has(Number(tr.dataset.index))) anchor = Number(tr.dataset.index);
        focusRow(next);
        selectRun(next);
        announce(`${plural(sel.size, "track")} selected`);
        return;
      }
      if (e.key === " " && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        e.stopPropagation();  // not play/pause as well
        pick(tr, { metaKey: true });
        return;
      }
      // In my playlists: Alt+arrows move a track, Delete removes it.
      if (mine && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const i = Number(tr.dataset.index);
        moveInPlaylist(pl, i, i + (e.key === "ArrowUp" ? -1 : 1));
        return;
      }
      if (mine && (e.key === "Delete" || e.key === "Backspace")) {
        e.preventDefault();
        if (groupFor(tr)) removeManyFromPlaylist(pl, [...sel]);
        else removeFromPlaylist(pl, Number(tr.dataset.index));
        return;
      }
      const vis = visibleRows();
      const at = vis.indexOf(tr);
      let next = null;
      if (e.key === "ArrowDown") next = vis[at + 1];
      else if (e.key === "ArrowUp") next = vis[at - 1];
      else if (e.key === "Home") next = vis[0];
      else if (e.key === "End") next = vis[vis.length - 1];
      else if (e.key === "PageDown") next = vis[Math.min(at + 15, vis.length - 1)];
      else if (e.key === "PageUp") next = vis[Math.max(at - 15, 0)];
      else if (e.key === "Enter") {
        e.preventDefault();
        playPlaylist(pl, Number(tr.dataset.index));
        return;
      } else if ((e.key === "n" || e.key === "N") && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        if (groupFor(tr)) queueTracks(selected(), pl, "next"); else queueTrack(trackOf(tr), pl, "next");
        return;
      } else if ((e.key === "q" || e.key === "Q") && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        if (groupFor(tr)) queueTracks(selected(), pl, "end"); else queueTrack(trackOf(tr), pl, "end");
        return;
      } else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
        e.preventDefault();
        if (groupFor(tr) || trackOf(tr).available || mine) openTrackMenu(trackOf(tr), pl, tr, tr, undefined, groupFor(tr));
        return;
      } else return;
      e.preventDefault();
      if (!next) return;
      focusRow(next);
      if (sel.size <= 1) {  // nothing picked yet: the selection follows focus
        sel.clear();
        sel.add(Number(next.dataset.index));
        anchor = Number(next.dataset.index);
      }
    },
  }, rows);
  if (mine) dragToReorder(tbody, pl);
  const empty = el("div", { class: "empty" },
    pl.search ? "Nothing in your library matches." : pl.count ? "No tracks match."
      : mine ? "This playlist is empty. Add tracks with the ⋯ button on any track, or by right-clicking it." : "This playlist is empty.");
  const table = el("table", { class: "tracks" },
    el("caption", { class: "sr-only" },
      `${pl.name}. Use the arrow keys to move between tracks and Enter to play. ` +
      "N plays the track next, Q adds it to the queue. Shift with the arrows selects several tracks, Control or Command A selects all." +
      (mine ? " Alt with the up or down arrow moves a track, Delete removes it." : "")),
    el("thead", {}, el("tr", {},
      el("th", { class: "num" }, "#"), el("th", { class: "title" }, "Title"),
      el("th", { class: "artist" }, "Artist"), el("th", { class: "album" }, "Album"),
      el("th", { class: "dur" }, "Time"), el("th", { class: "more" }, el("span", { class: "sr-only" }, "Actions")))),
    tbody);

  const applyFilter = () => {
    const q = S.filter.toLowerCase();
    let first = null;
    let shown = 0;
    pl.tracks.forEach((t, i) => {
      const hit = matches(t, q);
      rows[i].hidden = !hit;
      rows[i].tabIndex = -1;
      if (hit) { shown++; first = first || rows[i]; } else sel.delete(i);  // only what's showing stays selected
    });
    if (anchor !== null && rows[anchor].hidden) anchor = null;
    syncSelection();
    if (first) first.tabIndex = 0;
    table.hidden = shown === 0;
    empty.hidden = shown !== 0;
  };

  const playable = pl.tracks.filter((t) => t.available).length;
  let meta = `${pl.count} tracks` + (pl.missing ? ` · ${pl.missing} missing` : "");
  if (pl.search) {
    meta = pl.total > pl.count ? `Showing the first ${pl.count} of ${pl.total} tracks` : `${pl.count} tracks`;
  }
  const search = pl.search ? null : el("input", {
    type: "search", class: "search", placeholder: "Filter tracks", value: S.filter,
    "aria-label": "Filter tracks",
    oninput: (e) => { S.filter = e.target.value; applyFilter(); },
  });

  const ids = () => pl.tracks.filter((t) => t.available).map((t) => t.id);
  const small = (label, onclick, extra = {}) => el("button", { type: "button", class: "btn small", onclick, ...extra }, label);
  const exportLink = pl.search ? null : el("a", {
    class: "btn small", href: `/api/playlists/${encodeURIComponent(pl.id)}.m3u`, download: "",
    title: "Save this playlist as an .m3u file other players can open",
  }, "Export M3U");
  const actions = el("div", { class: "head-actions" },
    mine ? small("Rename", () => renamePlaylist(pl)) : null,
    mine ? small("Delete", () => deletePlaylist(pl)) : null,
    !mine && playable ? small(pl.search ? "Save as a playlist" : "Copy to My playlists", async () => {
      const pid = await newPlaylist(ids(), pl.search ? pl.name.replace(/^Search: /, "") : pl.name);
      if (pid) show({ type: "playlist", id: pid });
    }) : null,
    exportLink);

  const view = el("div", {},
    el("div", { class: "view-head" },
      el("div", {}, el("h1", {}, pl.name), el("div", { class: "meta" }, meta), actions),
      el("div", { class: "spacer" }),
      search,
      el("button", {
        type: "button", class: "btn primary", disabled: playable ? null : true,
        onclick: () => playPlaylist(pl, S.shuffle ? null : 0),
      }, ...iconLabel("play", "Play")),
    ),
    !pl.search && pl.missing && S.lib.missing && pl.kind !== "folder" ? missingBanner(pl.missing === pl.count
      ? "None of these tracks were found on this computer"
      : `${pl.missing} of these tracks weren't found on this computer`) : null,
    table,
    bar,
    empty,
  );
  applyFilter();
  queueMicrotask(markPlaying);
  return view;
}

// Highlight the playing row in the visible playlist, if it's the queue's.
function markPlaying() {
  for (const tr of document.querySelectorAll("#view tr.playing")) tr.classList.remove("playing");
  const t = current();
  if (S.mode !== "track" || !t || !S.detail || !S.view.type.match(/^(playlist|search)$/)) return;
  if (t._source !== S.detail.id) return;
  const tr = document.querySelector(`#view tr[data-index="${t._index}"]`);
  if (tr && tr.dataset.id === t.id) tr.classList.add("playing");
}

// Drag a row of one of my playlists to a new place.
function dragToReorder(tbody, pl) {
  let from = null;
  const clear = () => {
    for (const r of tbody.querySelectorAll(".drop-before, .drop-after")) r.classList.remove("drop-before", "drop-after");
  };
  const spot = (e) => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr) return null;
    const box = tr.getBoundingClientRect();
    return { tr, after: e.clientY > box.top + box.height / 2 };
  };
  tbody.addEventListener("dragstart", (e) => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr) return;
    from = Number(tr.dataset.index);
    tr.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", pl.tracks[from].name);
  });
  tbody.addEventListener("dragover", (e) => {
    if (from === null) return;
    const at = spot(e);
    if (!at) return;
    e.preventDefault();
    clear();
    at.tr.classList.add(at.after ? "drop-after" : "drop-before");
  });
  tbody.addEventListener("dragleave", (e) => { if (!tbody.contains(e.relatedTarget)) clear(); });
  tbody.addEventListener("drop", (e) => {
    const at = spot(e);
    clear();
    if (from === null || !at) return;
    e.preventDefault();
    let to = Number(at.tr.dataset.index) + (at.after ? 1 : 0);
    if (to > from) to -= 1;
    const start = from;
    from = null;
    moveInPlaylist(pl, start, to);
  });
  tbody.addEventListener("dragend", () => {
    from = null;
    clear();
    for (const r of tbody.querySelectorAll(".dragging")) r.classList.remove("dragging");
  });
}

// ---------- my playlists ---------------------------------------------------

const isMine = (pl) => Boolean(pl && pl.kind === "mine");
const myPlaylists = () => S.lib.playlists.filter((p) => p.kind === "mine");

// Ask for a name and make a playlist of `ids`. Returns its id, or null.
async function newPlaylist(ids = [], suggested = "") {
  const name = prompt("Name for the new playlist:", suggested);
  if (name === null || !name.trim()) return null;
  try {
    const res = await api("/api/my-playlists", { name, ids }, { quiet: true });
    await loadState(res);
    announce(ids.length ? `Saved ${ids.length} track${ids.length === 1 ? "" : "s"} as ${name.trim()}` : `Made the playlist ${name.trim()}`);
    return res.created;
  } catch (err) {
    renderErrors(err.message);
    return null;
  }
}

async function addToPlaylist(pid, ids, what) {
  const target = S.lib.playlists.find((p) => p.id === pid);
  try {
    await loadState(await api(`/api/my-playlists/${pid}/add`, { ids }, { quiet: true }));
    toast(`Added ${what} to ${target ? target.name : "the playlist"}`);
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  await refreshDetail(undefined, pid);
}

// Save a new order (or fewer tracks) for one of my playlists. Each edit
// is worked out from the list on screen, so further edits wait until the
// list has been shown again.
let savingPlaylist = false;
async function setPlaylistTracks(pid, ids, focusIndex, message) {
  savingPlaylist = true;
  try {
    await loadState(await api(`/api/my-playlists/${pid}/set`, { ids }, { quiet: true }));
    announce(message);
  } catch (err) {
    renderErrors(err.message);
  }
  try {
    await refreshDetail(focusIndex, pid);
  } finally {
    savingPlaylist = false;
  }
}

function moveInPlaylist(pl, from, to) {
  if (savingPlaylist || to < 0 || to >= pl.tracks.length || from === to) return;
  const ids = pl.tracks.map((t) => t.id);
  const [moved] = ids.splice(from, 1);
  ids.splice(to, 0, moved);
  setPlaylistTracks(pl.id, ids, to, `${pl.tracks[from].name} moved to number ${to + 1}`);
}

function removeFromPlaylist(pl, index) {
  if (savingPlaylist) return;
  const ids = pl.tracks.map((t) => t.id);
  ids.splice(index, 1);
  setPlaylistTracks(pl.id, ids, Math.min(index, ids.length - 1), `Removed ${pl.tracks[index].name} from ${pl.name}`);
}

function removeManyFromPlaylist(pl, indexes) {
  if (savingPlaylist || !indexes.length) return;
  const gone = new Set(indexes);
  const ids = pl.tracks.map((t) => t.id).filter((_, i) => !gone.has(i));
  const first = Math.min(...indexes);
  setPlaylistTracks(pl.id, ids, Math.min(first, ids.length - 1), `Removed ${plural(gone.size, "track")} from ${pl.name}`);
}

async function renamePlaylist(pl) {
  const name = prompt("New name for this playlist:", pl.name);
  if (name === null || !name.trim() || name.trim() === pl.name) return;
  try {
    await loadState(await api(`/api/my-playlists/${pl.id}/rename`, { name }, { quiet: true }));
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  await refreshDetail(undefined, pl.id);
  focusHeading();
  announce(`Renamed to ${name.trim()}`);
}

async function deletePlaylist(pl) {
  if (!confirm(`Delete the playlist ${pl.name}? The music itself stays where it is.`)) return;
  try {
    await loadState(await api(`/api/my-playlists/${pl.id}/delete`, {}, { quiet: true }));
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  announce(`Deleted ${pl.name}`);
  const first = S.lib.playlists[0];
  await show(first ? { type: "playlist", id: first.id } : { type: "settings" });
  focusHeading();
}

// Fetch playlist `pid` again after a change, if it's still the one on
// show, keeping the scroll position and (optionally) focusing a row.
async function refreshDetail(focusIndex, pid) {
  if (S.view.type !== "playlist" || S.view.id !== pid) return;
  const token = ++showToken;
  let detail = null;
  try { detail = await api(`/api/playlists/${S.view.id}`); } catch { detail = null; }
  if (token !== showToken || S.view.id !== pid) return;
  if (!detail) { show({ type: "settings" }); return; }
  const top = $("#main").scrollTop;
  S.detail = detail;
  renderView();
  $("#main").scrollTop = top;
  if (focusIndex === undefined) return;
  // That row, else the nearest one showing, else the heading.
  const rows = [...document.querySelectorAll("#view tbody tr")].filter((r) => !r.hidden);
  const tr = rows.find((r) => Number(r.dataset.index) >= focusIndex) || rows[rows.length - 1];
  if (!tr) { focusHeading(); return; }
  for (const r of document.querySelectorAll("#view tbody tr")) r.tabIndex = -1;
  tr.tabIndex = 0;
  tr.focus();
}

// ---------- track menu ------------------------------------------------------

let menuReturn = null;

function closeTrackMenu() {
  const menu = $("#track-menu");
  if (menu.hidden) return;
  menu.hidden = true;
  if (menuReturn && document.contains(menuReturn)) menuReturn.focus();
  menuReturn = null;
}

function menuItem(label, fn) {
  return el("button", {
    type: "button", role: "menuitem", tabindex: "-1",
    onclick: () => { closeTrackMenu(); fn(); },
  }, label);
}

// Show the menu with `items`, by `anchor` (or at the pointer, `at`).
function showMenu(items, label, anchor, returnTo, at) {
  const menu = $("#track-menu");
  menuReturn = returnTo;
  menu.replaceChildren(...items);
  menu.setAttribute("aria-label", label);
  menu.hidden = false;
  const box = anchor.getBoundingClientRect();
  const x = at ? at.x : box.right - menu.offsetWidth;
  const y = at ? at.y : box.bottom;
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  menu.querySelector("button").focus();
}

// The list of my playlists to add `ids` to (`what` names them).
function playlistChooser(ids, what, anchor, returnTo, at, exclude = null) {
  showMenu([
    el("div", { class: "menu-label", "aria-hidden": "true" }, "Add to playlist"),
    ...myPlaylists().filter((p) => p.id !== exclude).map((p) => menuItem(p.name, () => addToPlaylist(p.id, ids, what))),
    menuItem("New playlist…", () => newPlaylist(ids)),
  ], `Add ${what} to a playlist`, anchor, returnTo, at);
}

// The ⋯ menu for one track, or for `group` (several selected tracks).
function openTrackMenu(track, pl, anchor, returnTo, at, group = null) {
  if (group && group.length > 1) return openGroupMenu(group, pl, anchor, returnTo, at);
  const index = pl.tracks.indexOf(track);
  const items = [];
  if (track.available) {
    items.push(menuItem("Play next", () => queueTrack(track, pl, "next")),
      menuItem("Add to queue", () => queueTrack(track, pl, "end")),
      el("button", {
        type: "button", role: "menuitem", tabindex: "-1", "aria-haspopup": "menu",
        onclick: () => playlistChooser([track.id], track.name, anchor, returnTo, at, pl.id),
      }, "Add to playlist…"));
  }
  if (isMine(pl)) {
    if (items.length) items.push(el("hr", { role: "separator" }));
    if (index > 0) items.push(menuItem("Move up", () => moveInPlaylist(pl, index, index - 1)));
    if (index < pl.tracks.length - 1) items.push(menuItem("Move down", () => moveInPlaylist(pl, index, index + 1)));
    items.push(menuItem("Remove from this playlist", () => removeFromPlaylist(pl, index)));
  }
  if (!items.length) return;
  showMenu(items, track.name, anchor, returnTo, at);
}

function openGroupMenu(group, pl, anchor, returnTo, at) {
  const playable = group.filter((t) => t.available);
  const what = plural(playable.length, "track");
  const items = [];
  if (playable.length) {
    items.push(menuItem(`Play ${what} next`, () => queueTracks(playable, pl, "next")),
      menuItem(`Add ${what} to the queue`, () => queueTracks(playable, pl, "end")),
      el("button", {
        type: "button", role: "menuitem", tabindex: "-1", "aria-haspopup": "menu",
        onclick: () => playlistChooser(playable.map((t) => t.id), what, anchor, returnTo, at, pl.id),
      }, `Add ${what} to a playlist…`));
  }
  if (isMine(pl)) {
    if (items.length) items.push(el("hr", { role: "separator" }));
    items.push(menuItem(`Remove ${plural(group.length, "track")} from this playlist`,
      () => removeManyFromPlaylist(pl, group.map((t) => pl.tracks.indexOf(t)))));
  }
  if (!items.length) return;
  showMenu(items, `${plural(group.length, "track")} selected`, anchor, returnTo, at);
}

$("#track-menu").addEventListener("keydown", (e) => {
  const items = [...$("#track-menu").querySelectorAll("button")];
  const at = items.indexOf(document.activeElement);
  if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); closeTrackMenu(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); items[(at + 1) % items.length].focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); items[(at - 1 + items.length) % items.length].focus(); }
});
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#track-menu")) closeTrackMenu();
});
$("#main").addEventListener("scroll", closeTrackMenu);

// ---------- up next view -------------------------------------------------

// The tracks that will play after the current one, from the playlist (not
// counting the queue), as far as `limit`.
function comingFromQueue(limit) {
  const out = [];
  const n = S.order.length;
  if (S.mode !== "track" || !n || S.pos < 0) return out;
  let pos = S.pos;
  for (let steps = 0; steps < n && out.length < limit; steps++) {
    pos += 1;
    if (pos >= n) {
      if (S.repeat !== "all") break;
      pos = 0;
    }
    if (pos === S.pos) break;  // round to the playing track again (repeat all)
    const t = S.queue[S.order[pos]];
    if (t.available) out.push(t);
  }
  return out;
}

function upNextView() {
  const t = S.mode === "track" ? current() : null;
  const line = (track) => el("span", {}, track.name, track.artist ? el("span", { class: "muted" }, ` · ${track.artist}`) : null);
  const queued = S.upNext.map((track, i) => el("li", {},
    line(track),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Move ${track.name} up`, disabled: i === 0 || null,
      onclick: () => moveQueued(i, -1),
    }, icon("up")),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Move ${track.name} down`, disabled: i === S.upNext.length - 1 || null,
      onclick: () => moveQueued(i, 1),
    }, icon("down")),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Remove ${track.name} from the queue`,
      onclick: () => { S.upNext.splice(i, 1); queueChanged(); announce(`Removed ${track.name}`); },
    }, "Remove")));
  const coming = comingFromQueue(30);
  const all = [...(t ? [t] : []), ...S.upNext, ...comingFromQueue(Infinity)];
  return el("div", { class: "settings upnext" },
    el("div", { class: "view-head" }, el("h1", {}, "Up next"), el("div", { class: "spacer" }),
      all.length ? el("button", {
        type: "button", class: "btn",
        title: "The playing track, your queue and the rest of the playlist, in play order",
        onclick: async () => {
          const pid = await newPlaylist(all.map((x) => x.id), S.queueLabel && S.queueLabel !== "your queue" ? `${S.queueLabel} (my order)` : "");
          if (pid) show({ type: "playlist", id: pid });
        },
      }, "Save as a playlist") : null),
    el("section", {},
      el("h2", {}, "Now playing"),
      t ? el("p", {}, line(t)) : el("p", { class: "help" }, S.mode === "radio" ? `${S.station.name} (radio)` : "Nothing yet.")),
    el("section", {},
      el("h2", {}, "Your queue"),
      el("p", { class: "help" }, "Tracks you chose with Play next or Add to queue play before the rest of the playlist. ",
        "Use the ⋯ button on a track, right-click it, or press N or Q on it."),
      queued.length ? el("ul", {}, queued) : el("p", { class: "help" }, "Empty."),
      queued.length ? el("button", {
        type: "button", class: "btn",
        onclick: () => { S.upNext = []; queueChanged(); announce("Queue cleared"); },
      }, "Clear queue") : null),
    coming.length ? el("section", {},
      el("h2", {}, `Then from ${S.queueLabel}`),
      el("ol", { class: "coming" }, coming.map((c) => el("li", {}, line(c))))) : null,
  );
}

function moveQueued(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= S.upNext.length) return;
  [S.upNext[i], S.upNext[j]] = [S.upNext[j], S.upNext[i]];
  queueChanged();
  announce(`${S.upNext[j].name} moved ${dir < 0 ? "up" : "down"}`);
}

// After the queue or the playing track changes: redraw what shows it,
// line up the next track and remember where we are.
function queueChanged() {
  $("#upnext-count").textContent = S.upNext.length || "";
  if (S.view.type === "upnext") {
    const active = document.activeElement;
    const inView = active && $("#view").contains(active);
    const label = inView && active.getAttribute("aria-label");
    renderView();
    if (inView) {
      let again = label && [...document.querySelectorAll("#view button")].find((b) => b.getAttribute("aria-label") === label);
      // "Move up" on what is now the first item is disabled: use the
      // next button along on the same item.
      if (again && again.disabled) again = [...again.parentElement.querySelectorAll("button")].find((b) => !b.disabled);
      if (again) again.focus();
      else focusHeading();
    }
  }
  preloadNext();
  saveSession();
}

// ---------- radio view --------------------------------------------------

// The station being edited, if any (kept across redraws).
let editingStation = null;

// Check that a stream URL plays, showing the answer in `out`.
async function testStream(url, out) {
  out.className = "test-result";
  out.replaceChildren("Testing…");
  out.hidden = false;
  try {
    const res = await api("/api/stations/test", { url }, { quiet: true });
    if (res.ok) {
      out.className = "test-result ok";
      out.replaceChildren(icon("check"), el("span", {}, `It works${res.name ? `: ${res.name}` : ""}${res.type ? ` (${res.type})` : ""}`));
    } else {
      out.className = "test-result bad";
      out.replaceChildren(icon("alert"), el("span", {}, res.message || "It didn't play"));
    }
    return res;
  } catch (err) {
    out.className = "test-result bad";
    out.replaceChildren(icon("alert"), el("span", {}, err.message));
    return null;
  }
}

// A station's logo: one kept by the player (saved stations, Recently
// played), else the finder's (fetched through the player).
function logoSrc(st) {
  if (st && st.logo && /^[0-9a-f]{16}\.\w+$/.test(st.logo)) return `/logo/${st.logo}`;
  if (st && st.logo_url) return `/api/directory/logo?u=${encodeURIComponent(st.logo_url)}`;
  return null;
}

// The logo, or the radio glyph when there's none (or it won't load).
function stationArt(st, cls = "glyph") {
  const glyph = el("div", { class: cls }, icon("radio"));
  const src = logoSrc(st);
  if (!src) return glyph;
  const img = el("img", { class: "logo", alt: "", loading: "lazy", src });
  glyph.hidden = true;
  img.addEventListener("error", () => { img.remove(); glyph.hidden = false; });
  return el("div", { class: "station-art" }, img, glyph);
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(new Error("That file couldn't be read"));
    r.readAsDataURL(file);
  });
}

function logoEditor(st, result, nameInput, urlInput) {
  const show = (ok, text) => {
    result.className = `test-result ${ok ? "ok" : "bad"}`;
    result.replaceChildren(icon(ok ? "check" : "alert"), el("span", {}, text));
    result.hidden = false;
  };
  const set = async (body, done) => {
    try {
      await loadState(await api("/api/stations/logo", { url: st.url, ...body }, { quiet: true }));
      // The form is drawn again: keep what was typed but not saved yet.
      renderKeepingEdits();
      refocus(`#view .card[data-url="${CSS.escape(st.url)}"] .logo-row button`);
      toast(done);
    } catch (err) { show(false, err.message); }
  };
  const file = el("input", {
    type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", hidden: true,
    onchange: async () => {
      const f = file.files && file.files[0];
      if (!f) return;
      if (f.size > 256 * 1024) { show(false, "That picture is too big (the limit is 256 KB)"); return; }
      try { await set({ image: await readFile(f) }, `New logo for ${st.name}`); } catch (err) { show(false, err.message); }
    },
  });
  return el("div", { class: "row logo-row" },
    stationArt(st),
    el("button", {
      type: "button", class: "btn small", title: "Ask the station's own Station Server, then Radio Browser",
      onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        show(true, "Looking…");
        await set({ find: true }, `Found a logo for ${st.name}`);
        btn.disabled = false;  // still here when nothing was found
      },
    }, ...iconLabel("search", "Find logo")),
    el("button", { type: "button", class: "btn small", onclick: () => file.click() }, "Choose picture…"),
    st.logo ? el("button", { type: "button", class: "btn small ghost", onclick: () => set({}, `Removed ${st.name}'s logo`) }, "No logo") : null,
    file);
}

// Your own tags on stations, like "jazz" or "late night": typed with
// commas between them, tidied the way the server keeps them.
function splitTags(text) {
  const out = [];
  for (const raw of text.split(",")) {
    const t = raw.split(/\s+/).filter(Boolean).join(" ").slice(0, 30).trim();
    if (t && !out.some((x) => x.toLowerCase() === t.toLowerCase())) out.push(t);
  }
  return out.slice(0, 12);
}

function stationTags() {
  const counts = new Map();
  for (const st of S.lib.stations || []) for (const t of st.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
  return counts;
}

let stationTag = store("station-tag") || "";
function setStationTag(tag) {
  stationTag = tag;
  store("station-tag", tag);
  renderKeepingEdits();
  refocus(`#view #station-tags [data-tag="${CSS.escape(tag)}"]`);
}

// Draw the Radio page again, keeping what's typed in a station being
// edited but not saved yet.
function renderKeepingEdits() {
  const sel = editingStation === null ? null : `#view .card[data-url="${CSS.escape(editingStation)}"] .edit input:not([type=file])`;
  const typed = sel ? [...document.querySelectorAll(sel)].map((x) => x.value) : [];
  renderView();
  if (!sel) return;
  const inputs = document.querySelectorAll(sel);
  if (inputs.length === typed.length) inputs.forEach((x, n) => { x.value = typed[n]; x.dispatchEvent(new Event("input")); });
}

async function putOnDesktop(st, btn) {
  btn.disabled = true;
  try {
    await api("/api/stations/shortcut", { url: st.url }, { quiet: true });
    toast(`${st.name} is on your desktop: double-click it to listen`);
  } catch (err) { toast(err.message, { error: true }); }
  btn.disabled = false;
}

function stationCard(st, i, count) {
  const isLive = () => S.mode === "radio" && S.station && S.station.url === st.url;
  const name = el("input", { value: st.name, "aria-label": "Station name", required: true });
  const url = el("input", { value: st.url, "aria-label": "Stream URL", required: true });
  const tags = el("input", { value: (st.tags || []).join(", "), "aria-label": "Tags", placeholder: "jazz, late night", autocomplete: "off" });
  const feed = el("input", { value: st.feed || "", "aria-label": "What's on feed", type: "url", autocomplete: "off",
    placeholder: "https://…/rebirth-classics.json" });
  const suggest = el("div", { class: "tag-row tag-suggest" });
  const syncSuggest = () => {
    const have = splitTags(tags.value).map((t) => t.toLowerCase());
    const others = [...stationTags().keys()].filter((t) => !have.includes(t.toLowerCase())).sort((a, b) => a.localeCompare(b));
    suggest.replaceChildren(...others.map((t) => el("button", {
      type: "button", class: "tag", "aria-label": `Add the tag ${t}`,
      onclick: () => { tags.value = [...splitTags(tags.value), t].join(", "); syncSuggest(); tags.focus(); },
    }, `+ ${t}`)));
    suggest.hidden = !others.length;
  };
  tags.addEventListener("input", syncSuggest);
  syncSuggest();
  const result = el("div", { class: "test-result", role: "status", hidden: true });
  const editButton = () => `#view .card[data-url="${CSS.escape(st.url)}"] .card-top button`;
  const close = () => { editingStation = null; renderView(); refocus(editButton()); };
  const save = async (e) => {
    e.preventDefault();
    const body = { url: st.url };
    if (name.value.trim() !== st.name) body.name = name.value;
    if (url.value.trim() !== st.url) body.new_url = url.value;
    const newTags = splitTags(tags.value);
    if (newTags.join("\n") !== (st.tags || []).join("\n")) body.tags = newTags;
    if (feed.value.trim() !== (st.feed || "")) body.feed = feed.value;
    if (!body.name && !body.new_url && !body.tags && body.feed === undefined) { close(); return; }
    try {
      await loadState(await api("/api/stations/update", body, { quiet: true }));
      editingStation = null;
      renderView();
      const moved = (S.lib.stations || []).find((x) => x.name === name.value.trim());
      refocus(moved ? `#view .card[data-url="${CSS.escape(moved.url)}"] .card-top button` : null);
      toast(`Saved ${name.value.trim()}`);
    } catch (err) {
      result.className = "test-result bad";
      result.replaceChildren(icon("alert"), el("span", {}, err.message));
      result.hidden = false;
    }
  };
  const move = async (to) => {
    try {
      await loadState(await api("/api/stations/move", { url: st.url, to }, { quiet: true }));
      renderView();
      announce(`${st.name} moved to number ${to + 1}`);
      const again = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] .move-${to < i ? "up" : "down"}`);
      const anyMove = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] .moves button:not([disabled])`);
      (again && !again.disabled ? again : anyMove)?.focus();
    } catch (err) { renderErrors(err.message); }
  };
  const editing = editingStation === st.url;
  const card = el("div", { class: "card", "data-url": st.url },
    el("div", { class: "view-part", hidden: editing },
      el("div", { class: "card-top" },
        stationArt(st),
        el("div", { class: "who" }, el("div", { class: "name" }, st.name),
          (st.tags || []).length ? el("div", { class: "tag-row" }, st.tags.map((t) => el("button", {
            type: "button", class: "tag", "data-tag": t, title: `Show only stations tagged ${t}`,
            onclick: () => setStationTag(t),
          }, t))) : null),
        el("button", {
          type: "button", class: "btn ghost icon-only", "aria-label": `Edit ${st.name}`, title: "Edit, test, move or remove",
          onclick: () => { editingStation = st.url; renderView(); const n = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] input`); if (n) n.focus(); },
        }, icon("edit"))),
      el("div", { class: "song", hidden: true }),
      st.feed ? el("div", { class: "whatson", "data-station": st.id, hidden: true }) : null,
      el("div", { class: "rec", hidden: true }, el("span", { class: "rec-text" }),
        el("button", { type: "button", class: "btn small rec-play", hidden: true }, ...iconLabel("play", "Play"))),
      el("div", { class: "row" },
        el("button", {
          type: "button", class: "btn primary listen",
          onclick: () => (isLive() && !audio.paused ? audio.pause() : playStation(st)),
        }, ...iconLabel("play", "Listen")),
        el("button", { type: "button", class: "btn rec-btn studio-only", onclick: () => toggleRecording(st) }, ...iconLabel("record", "Record")))),
    editing ? el("form", { class: "edit", onsubmit: save },
      el("label", {}, "Name", name),
      el("label", {}, "Stream URL", url),
      el("label", {}, "Tags", el("span", { class: "help" }, "Your own labels, with commas between them"), tags),
      suggest,
      el("label", {}, "What's on", el("span", { class: "help" },
        "Optional: the feed its Station Server publishes (Settings › Website), for on now, next and just played"), feed),
      logoEditor(st, result, name, url),
      result,
      el("div", { class: "row" },
        el("button", { class: "btn primary" }, "Save"),
        el("button", { type: "button", class: "btn", onclick: () => testStream(url.value, result) }, "Test"),
        el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel")),
      el("div", { class: "row moves" },
        el("button", { type: "button", class: "btn small move-up", disabled: i === 0 || null, "aria-label": `Move ${st.name} up`, onclick: () => move(i - 1) }, ...iconLabel("up", "Up")),
        el("button", { type: "button", class: "btn small move-down", disabled: i === count - 1 || null, "aria-label": `Move ${st.name} down`, onclick: () => move(i + 1) }, ...iconLabel("down", "Down")),
        S.lib.desktop && S.lib.desktop.supported ? el("button", {
          type: "button", class: "btn small desktop-icon", title: "Double-click the icon to start the player and listen",
          onclick: (e) => putOnDesktop(st, e.currentTarget),
        }, ...iconLabel("app", "Put on desktop")) : null,
        el("span", { class: "spacer" }),
        el("button", {
          type: "button", class: "btn small danger",
          onclick: () => {
            if (!confirm(`Remove ${st.name}?`)) return;
            editingStation = null;
            act("/api/stations/remove", { url: st.url }, `Removed ${st.name}`);
          },
        }, ...iconLabel("trash", "Remove")))) : null);
  card.addEventListener("keydown", (e) => { if (e.key === "Escape" && editingStation === st.url) { e.preventDefault(); close(); } });
  return card;
}

function radioView() {
  const msg = el("div", { class: "form-msg", role: "status" });
  const result = el("div", { class: "test-result", role: "status", hidden: true });
  const name = el("input", { placeholder: "Station name", "aria-label": "Station name" });
  const url = el("input", { placeholder: "https://… stream, .pls or .m3u link", "aria-label": "Stream URL", type: "url" });
  const add = async (e) => {
    e.preventDefault();
    msg.textContent = "";
    try {
      await loadState(await api("/api/stations", { name: name.value, url: url.value }));
      renderView();
      focusHeading();
      toast(`Added ${name.value.trim() || "the station"}`);
    } catch (err) { msg.textContent = err.message; }
  };
  const test = async () => {
    msg.textContent = "";
    const res = await testStream(url.value, result);
    if (res && res.ok && res.name && !name.value.trim()) name.value = res.name;
  };

  const stations = S.lib.stations || [];
  const counts = stationTags();
  if (stationTag && !counts.has(stationTag)) { stationTag = ""; store("station-tag", ""); }
  // Filtered cards are hidden, not left out, so Up and Down still move a
  // station past the ones you can't see.
  const cards = stations.map((st, i) => {
    const card = stationCard(st, i, stations.length);
    card.hidden = Boolean(stationTag) && !(st.tags || []).includes(stationTag) && editingStation !== st.url;
    return card;
  });
  const chip = (label, tag) => el("button", {
    type: "button", class: "chip", "data-tag": tag, "aria-pressed": String(stationTag === tag), onclick: () => setStationTag(tag),
  }, label);

  queueMicrotask(syncRadioCards);
  queueMicrotask(syncRecordings);
  if (stations.some((st) => st.feed)) queueMicrotask(refreshWhatsOn);
  const visible = stations.filter((st) => !stationTag || (st.tags || []).includes(stationTag));
  return el("div", {},
    el("div", { class: "view-head" }, el("h1", {}, "Radio"), el("div", { class: "spacer" }),
      stations.some((x) => !x.logo) ? el("button", {
        type: "button", class: "btn", id: "btn-find-logos", title: "Ask each station's own Station Server, then Radio Browser",
        onclick: findAllLogos,
      }, ...iconLabel("search", "Find logos")) : null),
    counts.size ? el("div", { class: "tag-filter", id: "station-tags", role: "group", "aria-label": "Show stations tagged" },
      chip("All", ""), ...[...counts.keys()].sort((a, b) => a.localeCompare(b)).map((t) => chip(`${t} (${counts.get(t)})`, t))) : null,
    visible.filter(onTuner).length > 1 ? tuner(visible.filter(onTuner)) : null,
    cards.length ? el("div", { class: "cards" }, cards) : el("div", { class: "empty-state" },
      el("div", { class: "glyph big" }, icon("radio")),
      el("h2", {}, "No stations yet"),
      el("p", {}, "Find stations in Discover, or add one with its stream link below. Most stations list one on their website, often as a .pls or .m3u file.")),
    el("div", { class: "settings" }, scheduleSection(), recentSection(), discoverLink(), el("section", { class: "add-station" },
      el("h2", {}, "Add a station by its link"),
      el("p", { class: "help" }, "Test checks the link plays, and fills in the name when the station sends one."),
      el("form", { class: "form-row", onsubmit: add }, name, url,
        el("button", { type: "button", class: "btn", onclick: test }, "Test"),
        el("button", { class: "btn primary" }, ...iconLabel("plus", "Add"))),
      result,
      msg),
    el("section", { class: "studio-only" },
      el("h2", {}, "Recordings"),
      el("p", { class: "help" },
        "Record records a station to a file, whether or not you're listening to it, exactly as the station sends it. ",
        "When the station sends song titles, they're saved in a text file beside the recording. ",
        `Recordings go in ${S.lib.recordings_folder || "your Music folder"}. A recording stops by itself after 12 hours.`),
      el("button", { type: "button", class: "btn", onclick: () => show({ type: "recordings" }) },
        ...iconLabel("record", "See your recordings")))),
  );
}

// ---------- Home and Discover ----------------------------------------------
// Home is where a new window opens: what's on now from Rebirth (picks.py),
// stations played lately, your stations, the globe and your music.

const HOME = { picks: null, fetched: 0, timer: null };
const PICK_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// Whether a pick shows now, by this computer's clock.
function pickNow(p, now = new Date()) {
  const date = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
  if (p.starts && date < p.starts) return false;
  if (p.ends && date > p.ends) return false;
  if (p.days && !p.days.includes(PICK_DAYS[(now.getDay() + 6) % 7])) return false;
  if (!p.from) return true;
  const t = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
  return p.from <= p.to ? t >= p.from && t < p.to : t >= p.from || t < p.to;  // overnight
}

async function loadPicks() {
  HOME.fetched = Date.now();
  try { HOME.picks = await api("/api/picks"); } catch { HOME.picks = HOME.picks || { title: "", picks: [] }; }
  syncHome();
}

function syncHome() {
  const old = $("#home-picks");
  if (old) old.replaceWith(picksSection());
}

function greeting(now = new Date()) {
  const h = now.getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const savedStation = (url) => (S.lib.stations || []).find((x) => x.url === url);

function picksSection() {
  const shown = (HOME.picks ? HOME.picks.picks : []).filter((p) => pickNow(p));
  if (!shown.length) return el("section", { id: "home-picks", hidden: true });
  return el("section", { id: "home-picks", class: "home-section" },
    el("h2", {}, HOME.picks.title || "Picks"),
    el("div", { class: "pick-row" }, shown.map((p) => {
      const mine = savedStation(p.station);
      const art = p.image
        ? el("img", { class: "pick-image", alt: "", src: `/api/picks/image?id=${encodeURIComponent(p.id)}` })
        : stationArt(mine || {}, "glyph big");
      if (p.image) art.addEventListener("error", () => art.replaceWith(stationArt(mine || {}, "glyph big")));
      return el("article", { class: "pick tile-live", "data-url": p.station },
        el("div", { class: "pick-art" }, art),
        el("div", { class: "pick-body" },
          el("h3", {}, p.title),
          p.text ? el("p", {}, p.text) : null,
          el("button", {
            type: "button", class: "btn primary listen", "aria-label": `${p.button}: ${p.title}`,
            onclick: () => playStation(mine || { name: p.name, url: p.station, preview: true }),
          }, ...iconLabel("play", p.button))));
    })));
}

// A square button with a station's logo that plays it.
function stationTile(st) {
  return el("button", {
    type: "button", class: "tile tile-live", "data-url": st.url, title: `Listen to ${st.name}`,
    onclick: () => {
      const live = S.mode === "radio" && S.station && S.station.url === st.url;
      if (live && !audio.paused) audio.pause(); else playStation(st);
    },
  }, el("div", { class: "tile-art" }, stationArt(st, "glyph big")), el("span", { class: "tile-name" }, st.name));
}

function homeView() {
  if (!HOME.picks || Date.now() - HOME.fetched > 10 * 60 * 1000) queueMicrotask(loadPicks);
  // Picks change with the time of day: look again every minute.
  clearInterval(HOME.timer);
  HOME.timer = setInterval(() => { if (S.view.type === "home") syncHome(); else clearInterval(HOME.timer); }, 60 * 1000);
  queueMicrotask(syncRadioCards);
  const recent = (S.lib.recent_stations || []).map((r) => savedStation(r.url) || { name: r.name, url: r.url, logo: r.logo, preview: true });
  const stations = S.lib.stations || [];
  const music = [...myPlaylists(), ...S.lib.playlists.filter((p) => p.kind !== "mine")].slice(0, 8);
  const section = (title, items, more) => items.length ? el("section", { class: "home-section" },
    el("div", { class: "section-head" }, el("h2", {}, title), el("span", { class: "spacer" }), more || null),
    el("div", { class: "tile-row" }, items)) : null;
  const seeAll = (label, view) => el("button", { type: "button", class: "btn small ghost", onclick: () => show(view) }, label);
  return el("div", { class: "home" },
    el("div", { class: "view-head" }, el("h1", {}, greeting())),
    picksSection(),
    section("Recently played", recent.slice(0, 8).map(stationTile)),
    section("Your stations", stations.map(stationTile), seeAll("All stations", { type: "radio" })),
    el("section", { class: "home-section" },
      el("div", { class: "section-head" }, el("h2", {}, "Discover"), el("span", { class: "spacer" }),
        seeAll("Open Discover", { type: "discover" })),
      discoverTiles()),
    music.length
      ? section("Your music", music.map((p) => el("button", {
        type: "button", class: "tile music-tile", title: p.name, onclick: () => show({ type: "playlist", id: p.id }),
      }, el("div", { class: "tile-art" }, el("div", { class: "glyph big" }, icon(p.kind === "folder" ? "folder" : "music"))),
        el("span", { class: "tile-name" }, p.name), el("span", { class: "tile-sub" }, `${p.count} tracks`))))
      : el("section", { class: "home-section" },
        el("h2", {}, "Your music"),
        el("p", { class: "help" }, "Play the music on this computer too: add your music folder, or playlists from station-studio or iTunes."),
        el("button", { type: "button", class: "btn", onclick: () => { show({ type: "welcome" }); focusHeading(); } },
          ...iconLabel("plus", "Add music"))));
}

// The ways into Discover: the globe with news and talk, or with music, or a search.
function discoverTiles() {
  const tile = (glyph, title, text, go) => el("button", { type: "button", class: "discover-tile", onclick: go },
    el("div", { class: "glyph big" }, icon(glyph)),
    el("span", { class: "discover-title" }, title),
    el("span", { class: "discover-text" }, text));
  return el("div", { class: "discover-tiles" },
    tile("mic", "News and talk around the world", "Spin the globe to hear the news where it happens, in every language.",
      () => openDiscover("news")),
    tile("music", "Music around the world", "Turn the globe to a country and hear its music, or pick a genre.",
      () => openDiscover("music")),
    tile("search", "Search for a station", "By name, genre or country, from thousands of stations.",
      () => openDiscover(null)));
}

async function openDiscover(kind) {
  if (kind) {
    G.kind = kind;
    store("globe-kind", kind);
    F.view = "globe";
    store("finder-view", "globe");
  } else {
    F.view = "list";
    store("finder-view", "list");
  }
  if (S.view.type === "discover") {
    const finder = $(".finder");
    if (finder) finder.replaceWith(finderSection());
  } else {
    await show({ type: "discover" });
  }
  if (kind) $("#globe")?.focus();
  else $("#finder-q")?.focus();
}

function discoverView() {
  return el("div", { class: "discover" },
    el("div", { class: "view-head" }, el("h1", {}, "Discover")),
    el("p", { class: "help lead" },
      "Radio from all over the world. Turn the globe to hear the news, talk or music where it's made, or search by name. ",
      "Listen first, then Add the ones you like to your stations."),
    discoverTiles(),
    el("div", { class: "settings" }, finderSection()));
}

// On the Radio page: the finder lives on Discover now.
function discoverLink() {
  return el("section", { class: "discover-link" },
    el("h2", {}, "Find more stations"),
    el("p", { class: "help" }, "News, talk and music from around the world on the globe, or search thousands of stations by name."),
    el("button", { type: "button", class: "btn", onclick: () => show({ type: "discover" }) }, ...iconLabel("globe", "Open Discover")));
}

// Switches at set times: the news at five, then back to the music.
function scheduleSection() {
  const switches = alarmsList().filter(isSwitch);
  const adding = C.editing === "new-switch";
  const playing = S.station ? S.station : null;
  const blank = { id: "", on: true, time: "17:00", label: "", days: [0, 1, 2, 3, 4], date: "", action: "switch",
    source: playing ? { kind: "station", id: playing.id, name: playing.name } : null };
  return el("section", { class: "alarms schedule" },
    el("div", { class: "section-head" },
      el("h2", {}, "Schedule"),
      el("span", { class: "spacer" }),
      el("button", {
        type: "button", class: "btn", id: "btn-add-switch", disabled: alarmsList().length >= MAX_ALARMS || adding || null,
        onclick: () => { C.editing = "new-switch"; renderView(); refocus("#alarm-time"); },
      }, ...iconLabel("plus", "Add a switch"))),
    el("p", { class: "help" },
      "Switch station at set times, once or every week: the news at five, then back to your station at ten past. ",
      "A switch only changes what's already playing; it doesn't start anything when Station Player is quiet."),
    adding ? el("div", { class: "alarm-card new" }, alarmEditor(blank, true)) : null,
    switches.length ? el("ul", { class: "alarm-list" }, [...switches].sort((x, y) => x.time.localeCompare(y.time)).map(alarmCard)) : null);
}

// Look up every saved station that has no logo yet, one at a time.
async function findAllLogos(e) {
  const btn = e.currentTarget;
  btn.disabled = true;
  const missing = (S.lib.stations || []).filter((x) => !x.logo);
  let found = 0;
  let state = null;
  for (const [i, st] of missing.entries()) {
    btn.replaceChildren(`Looking… ${i + 1} of ${missing.length}`);
    try {
      state = await api("/api/stations/logo", { url: st.url, find: true }, { quiet: true });
      found += 1;
    } catch { /* none for this one (or the directory is unreachable) */ }
  }
  if (state) await loadState(state);
  renderView();
  refocus("#btn-find-logos");
  toast(found ? `Found ${plural(found, "logo")}${found < missing.length ? `; none found for ${missing.length - found} (Edit a station to choose a picture)` : ""}`
    : "No logos found for these stations. Edit a station to choose a picture.");
}

// Stations listened to lately (saved or tried from the finder), newest first.
function recentSection() {
  const recent = S.lib.recent_stations || [];
  return el("section", { class: "recent", id: "recent", hidden: !recent.length || null },
    el("div", { class: "section-head" },
      el("h2", {}, "Recently played"),
      el("span", { class: "spacer" }),
      el("button", {
        type: "button", class: "btn small ghost", "aria-label": "Clear Recently played",
        onclick: () => act("/api/stations/recent/clear", {}, "Cleared Recently played"),
      }, "Clear")),
    el("ul", { class: "recent-list" }, recent.map((r) => {
      const mine = (S.lib.stations || []).find((x) => x.url === r.url);
      const st = mine || { name: r.name, url: r.url, logo: r.logo, preview: true };
      return el("li", { class: "recent-card find-card", "data-find": r.url },
        stationArt(st),
        el("div", { class: "info" },
          el("div", { class: "name" }, st.name),
          el("div", { class: "facts" }, mine ? "In your stations" : "Not saved"),
          el("div", { class: "song", hidden: true })),
        el("div", { class: "actions" },
          el("button", {
            type: "button", class: "btn small listen", "aria-label": `Listen to ${st.name}`,
            onclick: () => {
              const live = S.mode === "radio" && S.station && S.station.url === r.url;
              if (live && !audio.paused) audio.pause(); else playStation(st);
            },
          }, ...iconLabel("play", "Listen")),
          // Here too, so a station just tried from Find stations can be recorded straight away.
          el("button", {
            type: "button", class: "btn small rec-btn studio-only", "aria-label": `Record ${st.name}`,
            onclick: () => toggleRecording(mine || { id: r.id, url: r.url, name: r.name }),
          }, ...iconLabel("record", "Record")),
          mine ? null : el("button", {
            type: "button", class: "btn small primary add", "aria-label": `Add ${st.name} to your stations`,
            onclick: async (e) => {
              const btn = e.currentTarget;
              btn.disabled = true;
              try {
                await loadState(await api("/api/stations", { name: r.name, url: r.url, logo: r.logo || "" }, { quiet: true }));
                renderView();
                toast(`Added ${r.name} to your stations`);
                refocus(`#view .recent-card[data-find="${CSS.escape(r.url)}"] .listen`);
              } catch (err) { btn.disabled = false; toast(err.message, { error: true }); }
            },
          }, ...iconLabel("plus", "Add"))));
    })));
}

// Redraw Recently played in place (the rest of the page keeps what's typed).
function syncRecent() {
  const old = document.querySelector("#view #recent");
  if (!old) return;
  const focused = old.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
  const fresh = recentSection();
  old.replaceWith(fresh);
  syncRecordings();  // names the Record buttons for what's running
  if (focused) fresh.querySelector(`[aria-label="${CSS.escape(focused)}"]`)?.focus();
  syncRadioCards();
}

async function notePlayed(st) {
  S.notedUrl = st.url;
  try {
    const res = await api("/api/stations/played", { name: st.name, url: st.url, logo_url: st.logo_url || "" }, { quiet: true });
    // Only the list: the rest may be older than a change made meanwhile.
    S.lib.recent_stations = res.recent_stations;
    syncRecent();
  } catch { /* not one the player knows: nothing to remember */ }
}

// Reflect play state on the station cards without rebuilding the view,
// so text typed into "Add a station" survives play/pause/stall events.
// ---------- the tuner ----------------------------------------------------------
// An old radio's dial across the top of the Radio page: your stations
// sit along it, and dragging the needle tunes between them, with a hiss
// of static on the way. Left and Right (or a click) tune too.
const TUNE = { stations: [], drag: false, noise: null };
const TUNER_MARKS = [88, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108];

function tuner(stations) {
  TUNE.stations = stations;
  const n = stations.length;
  // Placed through .style: the page's security policy blocks style="".
  const place = (node, left, maxWidth) => {
    node.style.left = `${left}%`;
    if (maxWidth) node.style.maxWidth = `${maxWidth}%`;
    return node;
  };
  const scale = el("div", { class: "tuner-scale" },
    ...TUNER_MARKS.map((mhz, i) => place(el("span", { class: "tuner-mark" }, String(mhz)), (i / (TUNER_MARKS.length - 1)) * 100)));
  // Names take turns above and below the scale, so neighbours don't touch.
  const names = el("div", { class: "tuner-names" },
    ...stations.map((st, i) => place(el("button", {
      type: "button", class: i % 2 ? "tuner-name low" : "tuner-name", tabindex: -1, "data-url": st.url, title: st.name,
      onclick: () => tuneTo(i),
    }, st.name), ((i + 0.5) / n) * 100, Math.min(40, 190 / n))));
  const dial = el("div", {
    class: "tuner-dial", id: "tuner", role: "slider", tabindex: 0, "aria-label": "Tuner",
    "aria-valuemin": 1, "aria-valuemax": n,
    onkeydown: (e) => {
      const i = tunedIndex();
      const to = { ArrowLeft: i - 1, ArrowDown: i - 1, ArrowRight: i + 1, ArrowUp: i + 1, Home: 0, End: n - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      tuneTo(Math.max(0, Math.min(n - 1, i < 0 && to < 0 ? 0 : to)));
    },
    onpointerdown: (e) => {
      if (e.button !== 0 || e.target.closest(".tuner-name")) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      TUNE.drag = true;
      dial.classList.add("tuning");
      dragTo(e);
    },
    onpointermove: (e) => { if (TUNE.drag) dragTo(e); },
    onpointerup: (e) => endDrag(e, true),
    onpointercancel: (e) => endDrag(e, false),
  }, scale, el("div", { class: "tuner-needle", id: "tuner-needle" }), names);
  queueMicrotask(syncTuner);
  return el("div", { class: "tuner" }, dial);
}

const tunedIndex = () => TUNE.stations.findIndex((st) => S.mode === "radio" && S.station && st.url === S.station.url);

// Where on the dial (0 to 1) the pointer is, and the nearest station.
function dialSpot(e) {
  const box = $("#tuner").getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
  const n = TUNE.stations.length;
  const i = Math.max(0, Math.min(n - 1, Math.floor(x * n)));
  return { x, i, off: Math.abs(x * n - (i + 0.5)) * 2 };  // off: 0 on a station, 1 halfway to the next
}

function dragTo(e) {
  const { x, i, off } = dialSpot(e);
  $("#tuner-needle").style.left = `${x * 100}%`;
  for (const b of document.querySelectorAll(".tuner-name")) b.classList.toggle("near", b.dataset.url === TUNE.stations[i].url);
  staticHiss(off);
}

function endDrag(e, tune) {
  if (!TUNE.drag) return;
  TUNE.drag = false;
  $("#tuner")?.classList.remove("tuning");
  staticHiss(0);
  if (tune) tuneTo(dialSpot(e).i);
  else syncTuner();
}

function tuneTo(i) {
  const st = TUNE.stations[i];
  if (!st) return;
  if (!(S.mode === "radio" && S.station && S.station.url === st.url && !audio.paused)) playStation(st);
  syncTuner();
}

function syncTuner() {
  const dial = $("#tuner");
  if (!dial || TUNE.drag) return;
  const i = tunedIndex();
  const n = TUNE.stations.length;
  const needle = $("#tuner-needle");
  needle.hidden = i < 0;
  if (i >= 0) needle.style.left = `${((i + 0.5) / n) * 100}%`;
  dial.setAttribute("aria-valuenow", String(Math.max(1, i + 1)));
  dial.setAttribute("aria-valuetext", i >= 0 ? TUNE.stations[i].name : "Not tuned");
  for (const b of document.querySelectorAll(".tuner-name")) {
    b.classList.remove("near");
    b.classList.toggle("on", i >= 0 && b.dataset.url === TUNE.stations[i].url);
  }
}

// Static between stations: looped noise, band-limited like a radio's
// speaker, louder the further the needle is from a station.
function staticHiss(level) {
  if (level > 0 && !TUNE.noise) {
    waveListen();
    const ctx = WAVE.ctx;
    if (!ctx) return;
    const buf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = 1800;
    band.Q.value = 0.7;
    const gain = ctx.createGain();
    gain.gain.value = 0;
    src.connect(band).connect(gain).connect(ctx.destination);
    src.start();
    TUNE.noise = { src, gain };
  }
  if (!TUNE.noise) return;
  const { src, gain } = TUNE.noise;
  gain.gain.setTargetAtTime(Math.min(1, level) * 0.25 * playVolume(), src.context.currentTime, 0.03);
  if (level <= 0) {
    src.stop(src.context.currentTime + 0.2);
    TUNE.noise = null;
  }
}

// What's on stations with a feed from their Station Server (whatson.py):
// on now, next and just played, asked for every 30 seconds.
const WHATSON = {};

const whatText = (i) => (i.show ? i.show : [i.title, i.artist].filter(Boolean).join(" – "));

async function refreshWhatsOn() {
  if (document.hidden) return;
  const fed = (S.lib?.stations || []).filter((st) => st.feed);
  await Promise.all(fed.map(async (st) => {
    try { WHATSON[st.id] = await api(`/api/whatson?station=${st.id}`, undefined, { quiet: true }); } catch { delete WHATSON[st.id]; }
  }));
  for (const id of Object.keys(WHATSON)) if (!fed.some((st) => st.id === id)) delete WHATSON[id];
  syncRadioCards();
  if (S.mode === "radio") updateNow();
}
setInterval(refreshWhatsOn, 30_000);

function paintWhatsOn(box, live) {
  const w = WHATSON[box.dataset.station];
  const lines = [];
  if (w?.state === "live") lines.push(el("div", {}, el("b", {}, "Live now: "), w.now.live));
  else if (w?.state === "off") lines.push(el("div", { class: "off" }, "Off air"));
  else if (w?.state === "on") {
    if (!live) lines.push(el("div", {}, el("b", {}, "On now: "), whatText(w.now)));
    if (live) lines.push(el("div", { class: "off" }, "Coming up:"));
    for (const n of w.next.slice(0, live ? 3 : 1)) {
      lines.push(el("div", {}, el("b", {}, `${live ? "" : "Next "}${hhmm(new Date(n.at))} `), whatText(n)));
    }
    if (live && w.recent.length) lines.push(el("div", { class: "off" }, "Just played: ", w.recent.slice(0, 2).map(whatText).join(", ")));
  }
  box.replaceChildren(...lines);
  box.hidden = !lines.length;
}

function syncRadioCards() {
  syncTuner();
  if ($("#globe")) globeDraw();  // rings the station playing
  for (const card of document.querySelectorAll("#view .card[data-url]")) {
    const live = S.mode === "radio" && S.station && S.station.url === card.dataset.url;
    card.classList.toggle("live", Boolean(live));
    const listen = card.querySelector(".listen");
    const label = live && !audio.paused ? "Pause" : "Listen";
    if (listen.textContent !== label) listen.replaceChildren(...iconLabel(label === "Pause" ? "pause" : "play", label));
    const song = card.querySelector(".song");
    song.textContent = live && S.radioTitle ? `Now playing: ${S.radioTitle}` : "";
    song.hidden = !song.textContent;
    const whatson = card.querySelector(".whatson");
    if (whatson) paintWhatsOn(whatson, live);
  }
  for (const tile of document.querySelectorAll("#view .tile-live[data-url]")) {
    const live = S.mode === "radio" && S.station && S.station.url === tile.dataset.url;
    tile.classList.toggle("live", Boolean(live && !audio.paused));
  }
  for (const card of document.querySelectorAll("#view .find-card[data-find]")) {
    const live = S.mode === "radio" && S.station && S.station.url === card.dataset.find;
    card.classList.toggle("live", Boolean(live));
    const listen = card.querySelector(".listen");
    const label = live && !audio.paused ? "Pause" : "Listen";
    if (listen.textContent !== label) listen.replaceChildren(...iconLabel(label === "Pause" ? "pause" : "play", label));
    const song = card.querySelector(".song");
    song.textContent = live && S.radioTitle ? `Now playing: ${S.radioTitle}` : "";
    song.hidden = !song.textContent;
  }
}

// ---------- finding stations ------------------------------------------------
// Searches Radio Browser (radio-browser.info) through the player server.

// `source`: "" for Radio Browser, "somafm" for SomaFM's own channel list.
const F = { view: store("finder-view") === "globe" ? "globe" : "list",
  q: "", draft: null, tag: "", country: homeCountry(), order: "votes", source: "", results: null, loading: false,
  error: "", token: 0, asked: false, next: null, more: false, typing: null };
const FIND_COUNTRIES = ["GB", "IE", "US", "CA", "AU", "NZ", "DE", "FR", "NL", "BE", "ES", "PT", "IT", "CH", "AT", "SE",
  "NO", "DK", "FI", "PL", "CZ", "GR", "TR", "BR", "MX", "AR", "JM", "NG", "ZA", "KE", "IN", "JP", "KR"];

// Genres to browse (the directory's most used tags are mostly one
// country's, so these are picked by hand). "a / b" finds either.
const FIND_GENRES = ["techno / trance", "house / garage", "chart / pop", "asian / bhangra", "latin", "afrobeats",
  "jazz", "soul", "funk", "reggae", "hip hop", "drum and bass", "ambient",
  "chillout", "electronic", "rock", "indie", "metal", "folk", "blues", "country", "classical", "oldies", "80s",
  "90s", "news", "talk"];

// The country this computer is set to (en-GB: GB), searched by default.
function homeCountry() {
  const tag = (navigator.language || "").split(/[@.]/)[0];  // "en-US@posix" on some Linux setups
  const region = (tag.split(/[-_]/)[1] || "").toUpperCase();
  return /^[A-Z]{2}$/.test(region) ? region : "";
}

function countryName(code) {
  try { return new Intl.DisplayNames([], { type: "region" }).of(code) || code; } catch { return code; }
}

async function findStations(more = {}) {
  Object.assign(F, more);
  clearTimeout(F.typing);
  const token = ++F.token;
  F.loading = true;
  F.error = "";
  F.asked = true;
  F.next = null;
  F.more = false;  // a "Show more" for the last search no longer counts
  renderFinderResults();
  const params = new URLSearchParams({ q: F.q, tag: F.tag, country: F.country, order: F.order });
  try {
    const res = await api(F.source === "somafm" ? "/api/directory/somafm" : `/api/directory/search?${params}`,
      undefined, { quiet: true });
    if (token !== F.token) return;
    F.results = res.stations || [];
    F.next = res.next ?? null;
  } catch (err) {
    if (token !== F.token) return;
    F.results = null;
    F.error = err.message;
  }
  F.loading = false;
  renderFinderResults();
  const n = F.results ? F.results.length : 0;
  announce(F.error ? F.error : n ? `${plural(n, "station")} found` : "No stations found");
}

// The next page of the same search, added below what's shown.
async function moreStations() {
  if (F.next == null || F.more) return;
  const token = F.token;
  F.more = true;
  renderFinderResults();
  const params = new URLSearchParams({ q: F.q, tag: F.tag, country: F.country, order: F.order, offset: F.next });
  let added = 0;
  try {
    const res = await api(`/api/directory/search?${params}`, undefined, { quiet: true });
    if (token !== F.token) return;
    const have = new Set(F.results.map((x) => x.url));
    const fresh = (res.stations || []).filter((x) => !have.has(x.url));
    added = fresh.length;
    F.results = F.results.concat(fresh);
    F.next = res.next ?? null;
  } catch (err) {
    if (token !== F.token) return;
    toast(err.message, { error: true });
  } finally {
    if (token === F.token) F.more = false;
  }
  const first = F.results.length - added;
  renderFinderResults();
  announce(added ? `${plural(added, "more station")} shown` : "No more stations");
  // Keyboard users carry on from the first new result.
  if (added) document.querySelectorAll("#finder-results .find-card .listen")[first]?.focus();
}

function finderTagChips() {
  const chip = (label, pressed, onclick, cls = "chip") => el("button", { type: "button", class: cls, "aria-pressed": String(pressed), onclick }, label);
  return [
    chip("SomaFM", F.source === "somafm", () => {
      findStations({ source: F.source === "somafm" ? "" : "somafm", tag: "" });
      syncFinderChips();
    }, "chip somafm"),
    ...FIND_GENRES.map((g) => chip(g, F.source === "" && F.tag === g, () => {
      findStations({ source: "", tag: F.tag === g && F.source === "" ? "" : g });
      syncFinderChips();
    })),
  ];
}

function syncFinderChips() {
  for (const c of document.querySelectorAll("#finder-tags .chip")) {
    const on = c.classList.contains("somafm") ? F.source === "somafm" : F.source === "" && c.textContent === F.tag;
    c.setAttribute("aria-pressed", String(on));
  }
}

// Below these a stream tends to sound thin. AAC (and Opus) hold up at
// lower rates than MP3: BBC World Service's 48k AAC+ sounds fine.
function lowQuality(st) {
  const efficient = /aac|opus|ogg/i.test(st.codec || "");
  return st.bitrate > 0 && st.bitrate < (efficient ? 48 : 64);
}

function stationFacts(st) {
  // "United Kingdom", not the directory's "The United Kingdom Of Great
  // Britain And Northern Ireland"; left out when searching that country.
  const country = st.countrycode ? (st.countrycode === F.country ? "" : countryName(st.countrycode)) : st.country;
  const codec = /^unknown$/i.test(st.codec || "") ? "" : st.codec;  // the directory couldn't tell
  const text = [country, codec && st.bitrate ? `${codec} ${st.bitrate} kbps` : codec || (st.bitrate ? `${st.bitrate} kbps` : "")]
    .filter(Boolean).join(" · ");
  const low = lowQuality(st);
  return low ? [text, el("span", { class: "low-q", title: `${st.bitrate} kbps: may sound thin or muffled` }, "Low quality")] : [text];
}

function finderCard(st) {
  const saved = (S.lib.stations || []).some((x) => x.url === st.url);
  return el("li", { class: "find-card", "data-find": st.url },
    stationArt({ logo_url: st.logo }),
    el("div", { class: "info" },
      el("div", { class: "name" }, st.name),
      el("div", { class: "facts" }, ...stationFacts(st)),
      st.tags.length ? el("div", { class: "tag-row" }, st.tags.slice(0, 4).map((t) => el("button", {
        type: "button", class: "tag", title: `Find more ${t} stations`,
        onclick: () => {
          findStations({ source: "", tag: t, q: "", draft: "" });
          const box = $("#finder-q");
          if (box) box.value = "";
          syncFinderChips();
        },
      }, t))) : null,
      el("div", { class: "song", hidden: true })),
    el("div", { class: "actions" },
      el("button", {
        type: "button", class: "btn small listen", "aria-label": `Listen to ${st.name}`,
        onclick: () => {
          const live = S.mode === "radio" && S.station && S.station.url === st.url;
          if (live && !audio.paused) { audio.pause(); return; }
          const mine = (S.lib.stations || []).find((x) => x.url === st.url);
          playStation(mine || { name: st.name, url: st.url, logo_url: st.logo, preview: true });
          if (st.uuid) api("/api/directory/clicked", { uuid: st.uuid }, { quiet: true }).catch(() => {});
        },
      }, ...iconLabel("play", "Listen")),
      el("button", {
        type: "button", class: saved ? "btn small added" : "btn small primary add", disabled: saved || null,
        "aria-label": saved ? `${st.name} is in your stations` : `Add ${st.name} to your stations`,
        onclick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            const tags = F.source === "" && F.tag ? [F.tag] : [];
            await loadState(await api("/api/stations", { name: st.name, url: st.url, logo_url: st.logo || "", tags }, { quiet: true }));
            renderView();
            toast(`Added ${st.name} to your stations${tags.length ? `, tagged ${tags[0]}` : ""}`);
            refocus(`#finder-results .find-card[data-find="${CSS.escape(st.url)}"] .listen`);
          } catch (err) {
            btn.disabled = false;
            toast(err.message, { error: true });
          }
        },
      }, ...(saved ? iconLabel("check", "Added") : iconLabel("plus", "Add")))));
}

function renderFinderResults() {
  const box = $("#finder-results");
  if (!box) return;
  box.setAttribute("aria-busy", String(F.loading));
  if (F.loading) { box.replaceChildren(el("p", { class: "help finding" }, "Searching…")); return; }
  if (F.error) { box.replaceChildren(el("p", { class: "errors" }, F.error)); return; }
  if (!F.results) { box.replaceChildren(); return; }
  if (!F.results.length) {
    box.replaceChildren(el("p", { class: "help" }, "No stations found. Try fewer words, or another genre or country."));
    return;
  }
  const what = [F.q && `“${F.q}”`, F.tag].filter(Boolean).join(", ");
  const where = F.country ? ` in ${countryName(F.country)}` : "";
  const more = F.source !== "somafm" && F.next != null;
  box.replaceChildren(
    el("p", { class: "help" }, F.source === "somafm"
      ? "SomaFM: listener-supported radio from San Francisco, with no adverts"
      : what ? `${plural(F.results.length, "station")} for ${what}${where}` : `The most popular stations${where || " right now"}`),
    el("ul", { class: "find-list" }, F.results.map(finderCard)),
    more ? el("div", { class: "more-row" }, el("button", {
      type: "button", class: "btn", id: "btn-more", disabled: F.more || null, onclick: moreStations,
    }, F.more ? "Loading…" : "Show more")) : null);
  syncRadioCards();
}

function finderSection() {
  const q = el("input", {
    type: "search", id: "finder-q", value: F.draft ?? F.q, placeholder: "Station name, e.g. jazz fm", "aria-label": "Station name",
    oninput: (e) => {
      F.draft = e.target.value;  // kept if the page redraws
      // Search as you type, once typing pauses (and not on one letter).
      clearTimeout(F.typing);
      const typed = e.target.value.trim();
      if (typed === F.q || typed.length === 1) return;
      F.typing = setTimeout(() => {
        if ($("#finder-q") !== q) return;  // the page moved on
        findStations({ source: "", q: typed, country: country.value, order: order.value });
        syncFinderChips();
      }, 450);
    },
  });
  const here = homeCountry();
  const codes = here && !FIND_COUNTRIES.includes(here) ? [here, ...FIND_COUNTRIES] : FIND_COUNTRIES;
  const country = el("select", { id: "finder-country", "aria-label": "Country" },
    el("option", { value: "" }, "Any country"),
    ...codes.map((c) => el("option", { value: c }, countryName(c)))
      .sort((a, b) => a.textContent.localeCompare(b.textContent)));
  country.value = F.country;
  const order = el("select", { id: "finder-order", "aria-label": "Sort by" },
    el("option", { value: "votes" }, "Best liked"), el("option", { value: "popular" }, "Most played"), el("option", { value: "name" }, "A to Z"));
  order.value = F.order;
  const search = (e) => {
    if (e) e.preventDefault();
    finderView("list");
    findStations({ source: "", q: q.value.trim(), country: country.value, order: order.value });
    syncFinderChips();
  };
  country.addEventListener("change", () => search());
  order.addEventListener("change", () => search());
  queueMicrotask(() => {
    renderFinderResults();
    if (!F.asked) findStations();  // start with the most popular stations
  });
  return el("section", { class: "finder" },
    el("h2", {}, "Find stations"),
    el("p", { class: "help" }, "Search thousands of stations from around the world in the free Radio Browser directory (radio-browser.info), or browse SomaFM's ad-free channels. Listen first, then Add the ones you like."),
    el("form", { class: "form-row finder-form", role: "search", onsubmit: search },
      q, country, order, el("button", { class: "btn primary", id: "btn-find" }, ...iconLabel("search", "Search"))),
    el("div", { class: "finder-views", role: "group", "aria-label": "Show stations as" },
      ...[["list", "list", "List"], ["globe", "globe", "Globe"]].map(([view, ic, label]) => el("button", {
        type: "button", class: "btn small", "data-view": view, "aria-pressed": String(F.view === view),
        onclick: () => finderView(view, true),
      }, ...iconLabel(ic, label)))),
    el("div", { class: "finder-list", hidden: F.view !== "list" },
      el("div", { class: "tag-filter", id: "finder-tags", role: "group", "aria-label": "Genres" }, ...finderTagChips()),
      el("div", { id: "finder-results", "aria-live": "polite" })),
    F.view === "globe" ? globeSection() : null);
}

// The list of search results, or the globe.
function finderView(view, focus = false) {
  if (F.view === view) return;
  F.view = view;
  store("finder-view", view);
  const finder = $(".finder");
  if (!finder) return;
  for (const b of finder.querySelectorAll(".finder-views [data-view]")) b.setAttribute("aria-pressed", String(b.dataset.view === view));
  finder.querySelector(".finder-list").hidden = view !== "list";
  finder.querySelector(".globe-box")?.remove();
  if (view === "globe") {
    finder.append(globeSection());
    if (focus) $("#globe").focus();
  }
}

// ---------- world globe ----------------------------------------------------
// Find stations on a globe. The directory knows where many stations are;
// the countries are Natural Earth's small-scale map (public domain),
// bundled as world.json and drawn here, with no map service.

const G = { world: null, loading: null, stations: new Map(), countries: new Set(),
  kind: ["music", "news"].includes(store("globe-kind")) ? store("globe-kind") : "all", genre: store("globe-genre") || "",
  lon: 0, lat: 20, zoom: 1, spin: !matchMedia("(prefers-reduced-motion: reduce)").matches, picked: null, raf: 0, last: 0, pointers: new Map(), moved: 0,
  pinch: 0, settle: 0, error: "", busy: 0, token: 0 };
const GLOBE_MAX_ZOOM = 24;
const GLOBE_NEAR = 2.5;  // zoomed in this far, it fetches the country in the middle too
const RAD = Math.PI / 180;

async function loadWorld() {
  if (G.world) return G.world;
  G.loading = G.loading || fetch("/world.json").then((r) => {
    if (!r.ok) throw new Error("The map didn't load");
    return r.json();
  }).then((data) => {
    G.world = data.countries.map((c) => {
      // Each outline in degrees, with its box (for finding the country
      // under the middle) and a spot for its name.
      const rings = c.r.map((flat) => {
        const pts = new Float32Array(flat.length);
        let x0 = 180, x1 = -180, y0 = 90, y1 = -90;
        for (let i = 0; i < flat.length; i += 2) {
          const x = (pts[i] = flat[i] / 10), y = (pts[i + 1] = flat[i + 1] / 10);
          x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
        }
        return { pts, box: [x0, y0, x1, y1] };
      });
      const big = rings.reduce((a, b) => ((b.box[2] - b.box[0]) * (b.box[3] - b.box[1]) > (a.box[2] - a.box[0]) * (a.box[3] - a.box[1]) ? b : a));
      return { cc: c.cc, rings, label: [(big.box[0] + big.box[2]) / 2, (big.box[1] + big.box[3]) / 2],
        size: Math.max(big.box[2] - big.box[0], big.box[3] - big.box[1]) };
    });
    return G.world;
  });
  try {
    return await G.loading;
  } catch (err) {
    G.loading = null;
    throw err;
  }
}

function inRing(pts, x, y) {
  let inside = false;
  for (let i = 0, j = pts.length - 2; i < pts.length; j = i, i += 2) {
    const xi = pts[i], yi = pts[i + 1], xj = pts[j], yj = pts[j + 1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// The country at a place: from the map, or (for ones too small to be
// on it, like Malta) the nearest station's.
function countryAt(lon, lat) {
  for (const c of G.world || []) {
    if (c.cc && c.rings.some((r) => lon >= r.box[0] && lon <= r.box[2] && lat >= r.box[1] && lat <= r.box[3] && inRing(r.pts, lon, lat))) return c.cc;
  }
  let best = null, dist = 3;
  for (const st of G.stations.values()) {
    const d = Math.hypot(st.lon - lon, st.lat - lat);
    if (d < dist && st.countrycode) { best = st.countrycode; dist = d; }
  }
  return best;
}

async function globeStations(country = "") {
  const token = G.token;
  G.busy++;
  globeStatus();
  try {
    const params = new URLSearchParams({ country, kind: G.kind, genre: G.kind === "music" ? G.genre : "" });
    const res = await api(`/api/directory/map?${params}`, undefined, { quiet: true });
    if (token !== G.token) return;
    for (const st of res.stations || []) if (!G.stations.has(st.url)) G.stations.set(st.url, st);
    if (country) G.countries.add(country);
    G.error = "";
  } catch (err) {
    if (token === G.token) G.error = err.message;
  } finally {
    if (token === G.token) G.busy--;  // a switch to Music or News started counting again
  }
  if (token !== G.token) return;
  globeDraw();
  globeList();
}

// All stations, music only (any, or one genre), or news and talk.
function globeShow(kind, genre = G.genre) {
  G.kind = kind;
  G.genre = genre;
  store("globe-kind", kind);
  store("globe-genre", genre);
  G.token++;
  G.stations.clear();
  G.countries.clear();
  G.picked = null;
  G.busy = 0;
  for (const b of document.querySelectorAll(".globe-kinds [data-kind]")) b.setAttribute("aria-pressed", String(b.dataset.kind === kind));
  const pick = $("#globe-genre");
  if (pick) pick.hidden = kind !== "music";
  globeStations();
  globeSettled();
}

// Orthographic: the globe as seen from far away, centred on G.lon, G.lat.
function globeView(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.width, h = cv.height;
  const r = (Math.min(w, h) / 2 - 6 * dpr) * G.zoom;
  const l0 = G.lon * RAD, p0 = G.lat * RAD;
  const sp0 = Math.sin(p0), cp0 = Math.cos(p0);
  return {
    dpr, w, h, r, cx: w / 2, cy: h / 2,
    // [x, y, facing]: facing < 0 is round the back.
    at(lon, lat) {
      const l = lon * RAD - l0, p = lat * RAD;
      const cp = Math.cos(p), sp = Math.sin(p), cl = Math.cos(l);
      return [w / 2 + r * cp * Math.sin(l), h / 2 - r * (cp0 * sp - sp0 * cp * cl), sp0 * sp + cp0 * cp * cl];
    },
  };
}

function globeColours() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return { sea: v("--panel-2"), land: v("--panel-3"), edge: v("--border-strong"), dot: v("--accent"), live: v("--ok"),
    text: v("--muted"), ring: v("--text") };
}

function globeDraw() {
  const cv = $("#globe");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const v = globeView(cv);
  const c = globeColours();
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.save();
  g.beginPath();
  g.arc(v.cx, v.cy, v.r, 0, Math.PI * 2);
  g.fillStyle = c.sea;
  g.fill();
  g.clip();
  // Countries: points round the back are pulled onto the rim, so a
  // country going over the edge keeps its shape this side.
  g.fillStyle = c.land;
  g.strokeStyle = c.edge;
  g.lineWidth = dpr;
  g.lineJoin = "round";
  for (const country of G.world || []) {
    for (const ring of country.rings) {
      let seen = false;
      g.beginPath();
      for (let i = 0; i < ring.pts.length; i += 2) {
        let [x, y, f] = v.at(ring.pts[i], ring.pts[i + 1]);
        if (f < 0) {
          const dx = x - v.cx, dy = y - v.cy, d = Math.hypot(dx, dy) || 1;
          x = v.cx + (dx / d) * v.r;
          y = v.cy + (dy / d) * v.r;
        } else seen = true;
        i ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      if (!seen) continue;
      g.closePath();
      g.fill();
      g.stroke();
    }
  }
  // Names, once it's close enough to have room for them.
  if (G.zoom >= GLOBE_NEAR) {
    g.fillStyle = c.text;
    g.font = `${11 * dpr}px system-ui, sans-serif`;
    g.textAlign = "center";
    for (const country of G.world || []) {
      if (!country.cc || country.size * RAD * v.r < 60 * dpr) continue;
      const [x, y, f] = v.at(country.label[0], country.label[1]);
      if (f > 0.2 && x > 0 && x < w && y > 0 && y < h) g.fillText(countryName(country.cc), x, y);
    }
  }
  // Stations.
  const live = S.mode === "radio" && S.station ? S.station.url : "";
  const size = (2.2 + Math.min(G.zoom, 8) * 0.3) * dpr;
  g.fillStyle = c.dot;
  g.globalAlpha = 0.85;
  for (const st of G.stations.values()) {
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f < 0 || x < -9 || y < -9 || x > w + 9 || y > h + 9) continue;
    g.beginPath();
    g.arc(x, y, size, 0, Math.PI * 2);
    g.fill();
  }
  g.globalAlpha = 1;
  for (const [url, colour] of [[G.picked?.url, c.ring], [live, c.live]]) {
    const st = url && G.stations.get(url);
    if (!st) continue;
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f < 0) continue;
    g.strokeStyle = colour;
    g.lineWidth = 2 * dpr;
    g.beginPath();
    g.arc(x, y, size + 4 * dpr, 0, Math.PI * 2);
    g.stroke();
  }
  g.restore();
  g.strokeStyle = c.edge;
  g.lineWidth = dpr;
  g.beginPath();
  g.arc(v.cx, v.cy, v.r, 0, Math.PI * 2);
  g.stroke();
}

// Turning by itself until it's touched; drawn again only when it moves.
function globeTick(now) {
  G.raf = 0;
  const cv = $("#globe");
  if (!cv) return;
  if (G.spin && !calm.matches) {
    G.lon += Math.min(100, now - (G.last || now)) * 0.004;  // 4° a second
    G.last = now;
    G.raf = requestAnimationFrame(globeTick);
  }
  globeDraw();
}

function globeWake() {
  if (!G.raf) G.raf = requestAnimationFrame(globeTick);
}

window.addEventListener("resize", () => { if ($("#globe")) globeWake(); });

function globeStop() {
  G.spin = false;
}

function globeMove(dLon, dLat) {
  G.lon = ((G.lon + dLon + 540) % 360) - 180;
  G.lat = Math.max(-80, Math.min(80, G.lat + dLat));
  globeWake();
  globeSettled();
}

function globeZoom(by) {
  G.zoom = Math.max(1, Math.min(GLOBE_MAX_ZOOM, G.zoom * by));
  globeWake();
  globeSettled();
}

// Once it stops moving: list what's in view, and fetch the country in the
// middle if it's zoomed in.
function globeSettled() {
  clearTimeout(G.settle);
  G.settle = setTimeout(() => {
    globeList();
    if (G.zoom < GLOBE_NEAR || !G.world) return;
    const cc = countryAt(G.lon, G.lat);
    if (cc && !G.countries.has(cc)) {
      G.countries.add(cc);  // asked once, even if it fails
      globeStations(cc);
    }
  }, 300);
}

// Stations in view, nearest the middle first.
function globeInView(limit) {
  const cv = $("#globe");
  if (!cv || !cv.width) return [];
  const v = globeView(cv);
  const out = [];
  for (const st of G.stations.values()) {
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f >= 0 && x >= 0 && y >= 0 && x <= v.w && y <= v.h) out.push([Math.hypot(x - v.cx, y - v.cy), st]);
  }
  return out.sort((a, b) => a[0] - b[0]).slice(0, limit).map((x) => x[1]);
}

function globeStatus() {
  const line = $("#globe-status");
  if (!line) return;
  const n = G.stations.size;
  const what = G.kind === "news" ? "news and talk station"
    : G.kind === "music" ? `${G.genre || "music"} station` : "station";
  line.textContent = G.error || (G.busy && !n ? "Finding stations…"
    : `${plural(n, what)} on the globe${G.busy ? ", finding more…" : ""}`);
  line.classList.toggle("errors", Boolean(G.error));
}

function globeList() {
  globeStatus();
  const box = $("#globe-list");
  if (!box) return;
  const near = globeInView(8).filter((st) => st.url !== G.picked?.url);
  const shown = (G.picked ? [G.picked, ...near] : near).slice(0, 8);
  const keep = document.activeElement && box.contains(document.activeElement)
    ? document.activeElement.closest(".find-card")?.dataset.find : null;
  box.replaceChildren(...shown.map(finderCard));
  if (keep) box.querySelector(`.find-card[data-find="${CSS.escape(keep)}"] .listen`)?.focus();
  syncRadioCards();
}

// Tapping a dot plays it.
function globePick(st) {
  G.picked = st;
  const live = S.mode === "radio" && S.station && S.station.url === st.url && !audio.paused;
  if (!live) {
    const mine = (S.lib.stations || []).find((x) => x.url === st.url);
    playStation(mine || { name: st.name, url: st.url, logo_url: st.logo, preview: true });
    if (st.uuid) api("/api/directory/clicked", { uuid: st.uuid }, { quiet: true }).catch(() => {});
  }
  announce(`${st.name}${st.countrycode ? `, ${countryName(st.countrycode)}` : ""}`);
  globeDraw();
  globeList();
}

function globeHit(e) {
  const cv = e.currentTarget;
  const box = cv.getBoundingClientRect();
  const v = globeView(cv);
  const px = (e.clientX - box.left) * v.dpr, py = (e.clientY - box.top) * v.dpr;
  let best = null, dist = 16 * v.dpr;
  for (const st of G.stations.values()) {
    const [x, y, f] = v.at(st.lon, st.lat);
    const d = Math.hypot(x - px, y - py);
    if (f >= 0 && d < dist) { best = st; dist = d; }
  }
  return best;
}

function globeSection() {
  const cv = el("canvas", {
    id: "globe", class: "globe", tabindex: 0, role: "application", "aria-roledescription": "globe",
    "aria-label": "World globe of stations. Drag to turn it, scroll or pinch to zoom, and tap a dot to listen. With the keyboard: arrow keys turn it, plus and minus zoom, Enter plays the station in the middle.",
    onpointerdown: (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      cv.setPointerCapture(e.pointerId);
      G.pointers.set(e.pointerId, [e.clientX, e.clientY]);
      G.moved = 0;
      G.pinch = 0;
      globeStop();
    },
    onpointermove: (e) => {
      const was = G.pointers.get(e.pointerId);
      if (!was) {
        cv.classList.toggle("over-dot", Boolean(globeHit(e)));
        return;
      }
      G.pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (G.pointers.size === 2) {
        const [a, b] = [...G.pointers.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (G.pinch) globeZoom(d / G.pinch);
        G.pinch = d;
        G.moved = 99;
        return;
      }
      const dx = e.clientX - was[0], dy = e.clientY - was[1];
      G.moved += Math.abs(dx) + Math.abs(dy);
      const r = globeView(cv).r / (window.devicePixelRatio || 1);
      globeMove(-dx / r / RAD, dy / r / RAD);
    },
    onpointerup: (e) => {
      const tap = G.pointers.size === 1 && G.moved < 6;
      G.pointers.delete(e.pointerId);
      G.pinch = 0;
      if (!tap) return;
      const st = globeHit(e);
      if (st) globePick(st);
    },
    onpointercancel: (e) => { G.pointers.delete(e.pointerId); G.pinch = 0; },
    onkeydown: (e) => {
      const step = 12 / G.zoom;
      const turn = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
      if (turn) {
        e.preventDefault();
        globeStop();
        globeMove(...turn);
      } else if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        globeStop();
        globeZoom(1.5);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        globeZoom(1 / 1.5);
      } else if (e.key === "Enter") {
        const st = globeInView(1)[0];
        if (st) { e.preventDefault(); globePick(st); }
      }
    },
  });
  // Not passive: scrolling over the globe zooms it, not the page.
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    globeStop();
    globeZoom(Math.exp(-Math.max(-100, Math.min(100, e.deltaY)) * (e.ctrlKey ? 0.01 : 0.002)));
  }, { passive: false });
  queueMicrotask(async () => {
    try {
      await loadWorld();
    } catch (err) {
      G.error = err.message;
    }
    if (!G.stations.size && !G.busy) {
      // Start over home.
      const home = (G.world || []).find((c) => c.cc === homeCountry());
      if (home) { G.lon = home.label[0]; G.lat = Math.max(-60, Math.min(60, home.label[1])); }
      globeStations();
    }
    globeWake();
    globeList();
  });
  return el("div", { class: "globe-box" },
    el("div", { class: "globe-tools" },
      el("div", { class: "globe-kinds", role: "group", "aria-label": "Show" },
        ...[["all", "All"], ["music", "Music"], ["news", "News"]].map(([kind, label]) => el("button", {
          type: "button", class: "chip", "data-kind": kind, "aria-pressed": String(G.kind === kind),
          title: { all: "Every station", music: "Music stations, leaving out news and talk", news: "Only news and talk stations" }[kind],
          onclick: () => { if (G.kind !== kind) globeShow(kind); },
        }, label))),
      el("select", {
        id: "globe-genre", "aria-label": "Music genre", hidden: G.kind !== "music",
        onchange: (e) => globeShow("music", e.target.value),
      }, el("option", { value: "" }, "Any music"),
      ...FIND_GENRES.filter((g) => g !== "news" && g !== "talk").map((g) => el("option", { value: g, selected: g === G.genre }, g))),
      el("p", { class: "help", id: "globe-status", role: "status" }),
      el("button", { type: "button", class: "btn ghost icon-only", "aria-label": "Zoom out", onclick: () => { globeStop(); globeZoom(1 / 1.6); } }, icon("minus")),
      el("button", { type: "button", class: "btn ghost icon-only", "aria-label": "Zoom in", onclick: () => { globeStop(); globeZoom(1.6); } }, icon("plus"))),
    cv,
    el("ul", { class: "find-list", id: "globe-list", "aria-label": "Stations in view" }));
}

// ---------- recordings page -----------------------------------------------

// Finished recordings, with the label and tags you give them.
const R = { list: null, folder: "", tag: "", q: "", editing: null, token: 0 };

async function loadRecordings() {
  const token = ++R.token;
  try {
    const res = await api("/api/recordings/list");
    if (token !== R.token) return;
    R.list = res.recordings;
    R.folder = res.folder;
  } catch (err) {
    if (token !== R.token) return;
    R.list = R.list || [];
    renderErrors(err.message);
  }
  if (S.view.type !== "recordings") return;
  // Redrawn in place: whatever had focus keeps it, if it's still there.
  const active = document.activeElement;
  const key = active && $("#view").contains(active) ? (active.id || active.getAttribute("aria-label")) : null;
  renderView();
  if (key) refocus(`#view #${CSS.escape(key)}, #view [aria-label="${CSS.escape(key)}"]`);
}

const recTitle = (r) => r.label || `${r.station || "Radio"} · ${fmtWhen(r.started)}`;
function fmtWhen(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function recordingRow(r, i) {
  const editing = R.editing === r.file;
  const facts = [r.label ? r.station : null, r.label ? fmtWhen(r.started) : null,
    r.seconds ? fmtTime(r.seconds) : null, fmtBytes(r.bytes),
    r.songs.length ? plural(r.songs.length, "song") : null].filter(Boolean);
  const label = el("input", { value: r.label, id: `rec-label-${i}`, placeholder: recTitle({ ...r, label: "" }), maxlength: 200 });
  const tags = el("input", { value: r.tags.join(", "), id: `rec-tags-${i}`, placeholder: "e.g. breakfast show, interview" });
  const msg = el("div", { class: "form-msg", role: "status" });
  const close = () => { R.editing = null; renderView(); refocus(`#view [aria-label="Edit ${CSS.escape(recTitle(r))}"]`); };
  const save = async (e) => {
    e.preventDefault();
    const newTags = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      const res = await api("/api/recordings/label", { file: r.file, label: label.value, tags: newTags }, { quiet: true });
      R.list = res.recordings;
      R.editing = null;
      renderView();
      const now = R.list.find((x) => x.file === r.file);
      refocus(now ? `#view [aria-label="Edit ${CSS.escape(recTitle(now))}"]` : null);
      toast("Saved");
    } catch (err) { msg.textContent = err.message; }
  };
  return el("li", { class: "recording", "data-file": r.file },
    el("div", { class: "rec-main" },
      el("div", { class: "glyph" }, icon("radio")),
      el("div", { class: "info" },
        el("div", { class: "name" }, recTitle(r)),
        el("div", { class: "facts" }, facts.join(" · ")),
        r.tags.length ? el("div", { class: "tag-row" }, r.tags.map((t) => el("button", {
          type: "button", class: "tag", title: `Show recordings tagged ${t}`,
          onclick: () => { R.tag = t; renderView(); refocus(`#view .tag-filter [data-tag="${CSS.escape(t)}"]`); },
        }, t))) : null),
      el("div", { class: "actions" },
        el("button", {
          type: "button", class: "btn small primary", "aria-label": `Play ${recTitle(r)}`,
          onclick: () => playRecording(r.file, { stay: true }),
        }, ...iconLabel("play", "Play")),
        el("button", {
          type: "button", class: "btn small icon-only", "aria-label": `Add ${recTitle(r)} to a playlist`,
          title: "Add to playlist", "aria-haspopup": "menu",
          onclick: (e) => addRecordingToPlaylist(r, e.currentTarget),
        }, icon("plus")),
        el("button", {
          type: "button", class: "btn small ghost icon-only", "aria-label": `Edit ${recTitle(r)}`, title: "Label and tags",
          onclick: () => { R.editing = r.file; renderView(); refocus(`#rec-label-${i}`); },
        }, icon("edit")))),
    editing ? el("form", { class: "rec-edit", onsubmit: save,
      onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } } },
      el("label", { class: "field", for: `rec-label-${i}` }, "Label"), label,
      el("label", { class: "field", for: `rec-tags-${i}` }, "Tags, separated by commas"), tags,
      msg,
      el("div", { class: "row" },
        el("button", { class: "btn primary" }, "Save"),
        el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel"))) : null,
    r.songs.length ? el("details", { class: "songs" },
      el("summary", {}, `Songs (${r.songs.length})`),
      el("ol", {}, r.songs.map((s) => el("li", {}, s)))) : null);
}

function recordingsView() {
  if (R.list === null) {
    return el("div", {}, el("div", { class: "view-head" }, el("h1", {}, "Recordings")), el("div", { class: "empty" }, "Looking for your recordings…"));
  }
  const all = R.list;
  const tagCounts = new Map();
  for (const r of all) for (const t of r.tags) tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
  if (R.tag && !tagCounts.has(R.tag)) R.tag = "";
  const matching = () => {
    const q = R.q.trim().toLowerCase();
    return all.filter((r) => (!R.tag || r.tags.includes(R.tag)) &&
      (!q || [recTitle(r), r.station, ...r.tags, ...r.songs].some((v) => v && v.toLowerCase().includes(q))));
  };
  const shown = matching();
  const chip = (label, tag) => el("button", {
    type: "button", class: "chip", "data-tag": tag, "aria-pressed": String(R.tag === tag),
    onclick: () => { R.tag = tag; renderView(); refocus(`#view .tag-filter [data-tag="${CSS.escape(tag)}"]`); },
  }, label);
  const list = el("ul", { class: "rec-list" }, shown.map(recordingRow));
  const none = el("div", { class: "empty", hidden: shown.length > 0 }, "No recordings match.");
  // Typing in the search filters the list without redrawing the page.
  const filter = () => {
    const now = matching();
    list.replaceChildren(...now.map(recordingRow));
    none.hidden = now.length > 0;
  };
  return el("div", { class: "recordings" },
    el("div", { class: "view-head" },
      el("div", {}, el("h1", {}, "Recordings"),
        el("div", { class: "meta" }, all.length ? plural(all.length, "recording") : "")),
      el("div", { class: "spacer" }),
      all.length ? el("input", {
        type: "search", class: "search", id: "rec-search", placeholder: "Search labels, tags and songs", value: R.q,
        "aria-label": "Search recordings",
        oninput: (e) => { R.q = e.target.value; filter(); },
      }) : null,
      el("button", {
        type: "button", class: "btn",
        onclick: () => api("/api/recordings/open", {}, { quiet: true }).catch((err) => renderErrors(err.message)),
      }, ...iconLabel("folder", "Open folder"))),
    tagCounts.size ? el("div", { class: "tag-filter", role: "group", "aria-label": "Show recordings tagged" },
      chip("All", ""), ...[...tagCounts.keys()].sort((a, b) => a.localeCompare(b)).map((t) => chip(`${t} (${tagCounts.get(t)})`, t))) : null,
    all.length === 0 ? el("div", { class: "empty-state" },
      el("div", { class: "glyph big" }, icon("record")),
      el("h2", {}, "No recordings yet"),
      el("p", {}, "Press Record on any station on the Radio page. When it's finished it shows up here, ready to label, tag and play."),
      el("button", { type: "button", class: "btn primary", onclick: () => show({ type: "radio" }) }, ...iconLabel("radio", "Go to Radio")))
      : [list, none],
    activeRecordings().length ? el("p", { class: "help" }, "Recordings still going appear here once they're stopped.") : null);
}

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
  try { if (box.requestFullscreen) await box.requestFullscreen(); } catch { /* fine in a window */ }
  try { if (navigator.wakeLock) wakeLock = await navigator.wakeLock.request("screen"); } catch { wakeLock = null; }
}

// Resolves once full screen has closed (which moves focus), so focus can
// be put back afterwards: on the Night mode button, or wherever the caller says.
async function leaveNight({ restoreFocus = true } = {}) {
  const box = $("#night");
  if (box.hidden) return;
  box.hidden = true;
  box.replaceChildren();
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
  if (restoreFocus) refocus("#btn-night");
}
$("#night").addEventListener("click", () => leaveNight());
$("#night").addEventListener("keydown", (e) => { e.preventDefault(); e.stopPropagation(); leaveNight(); });
document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && !$("#night").hidden) leaveNight(); });


// ---------- ambient mode ----------------------------------------------------
// A calm scene filling the screen, with its own sound: a fish tank, waves
// on a shore, or birds at dawn. The sound is made here as it plays (no
// recordings to fetch), alone or quietly under the music, and the sleep
// timer fades it out with everything else.
const AMBIENT_SCENES = [["fish", "Fish tank"], ["waves", "Waves"], ["birds", "Birds"]];
const AMB = { scene: store("ambient-scene") || "waves", volume: Number(store("ambient-volume") ?? 0.6),
  ctx: null, out: null, sound: null, raf: 0, t0: 0, hideTimer: null, things: [], wake: null };

function openAmbient() {
  const box = $("#ambient");
  const canvas = el("canvas", { class: "ambient-canvas", "aria-hidden": "true" });
  const scenes = el("div", { class: "segmented", role: "group", "aria-label": "Scene" },
    ...AMBIENT_SCENES.map(([key, label]) => el("button", {
      type: "button", class: "seg", "data-scene": key, "aria-pressed": String(AMB.scene === key),
      onclick: () => setAmbientScene(key),
    }, label)));
  const vol = el("input", {
    type: "range", min: 0, max: 1, step: 0.01, value: AMB.volume, "aria-label": "Ambient volume",
    oninput: (e) => { AMB.volume = Number(e.target.value); store("ambient-volume", String(AMB.volume)); fillRange(e.target); ambientLevel(); },
  });
  fillRange(vol);
  box.replaceChildren(canvas,
    el("div", { class: "ambient-controls" },
      el("h2", { class: "sr-only" }, "Ambient"),
      scenes,
      el("label", { class: "ambient-volume" }, icon("volume"), vol),
      el("button", { type: "button", class: "btn", id: "btn-ambient-close", onclick: () => closeAmbient() }, "Close")));
  box.hidden = false;
  document.body.classList.add("ambient-on");
  setAmbientScene(AMB.scene);
  ambientNudge();
  refocus(`#ambient .seg[aria-pressed="true"]`);
  if (navigator.wakeLock) navigator.wakeLock.request("screen").then((w) => { AMB.wake = w; }).catch(() => {});
}

function closeAmbient() {
  const box = $("#ambient");
  if (box.hidden) return;
  stopAmbientSound();
  cancelAnimationFrame(AMB.raf);
  AMB.raf = 0;
  clearTimeout(AMB.hideTimer);
  box.hidden = true;
  box.replaceChildren();
  document.body.classList.remove("ambient-on");
  if (AMB.wake) { AMB.wake.release().catch(() => {}); AMB.wake = null; }
  refocus("#btn-ambient");
}

function setAmbientScene(key) {
  AMB.scene = key;
  store("ambient-scene", key);
  for (const b of document.querySelectorAll("#ambient .seg")) b.setAttribute("aria-pressed", String(b.dataset.scene === key));
  AMB.things = [];
  AMB.t0 = performance.now();
  AMB.last = 0;
  stopAmbientSound();
  startAmbientSound(key);
  cancelAnimationFrame(AMB.raf);
  AMB.raf = requestAnimationFrame(drawAmbient);
  announce(`Ambient: ${AMBIENT_SCENES.find(([k]) => k === key)[1]}`);
}

// The controls fade out after a few still seconds, and come back on a
// move of the mouse or a key.
function ambientNudge() {
  const box = $("#ambient");
  box.classList.remove("still");
  clearTimeout(AMB.hideTimer);
  AMB.hideTimer = setTimeout(() => box.classList.add("still"), 4000);
}
$("#ambient").addEventListener("pointermove", ambientNudge);
$("#ambient").addEventListener("keydown", (e) => {
  ambientNudge();
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeAmbient(); }
});
$("#btn-ambient").addEventListener("click", openAmbient);

// ---- the sound

function ambientLevel() {
  if (AMB.out) AMB.out.gain.setTargetAtTime(AMB.volume * volMul.sleep * 0.5, AMB.ctx.currentTime, 0.1);
}

function noiseBuffer(ctx, seconds, brown = false) {
  const buf = ctx.createBuffer(1, ctx.sampleRate * seconds, ctx.sampleRate);
  const d = buf.getChannelData(0);
  let last = 0;
  for (let i = 0; i < d.length; i++) {
    const white = Math.random() * 2 - 1;
    d[i] = brown ? (last = (last + 0.02 * white) / 1.02) * 3.5 : white;
  }
  return buf;
}

function loopNoise(ctx, brown) {
  const src = ctx.createBufferSource();
  src.buffer = noiseBuffer(ctx, 4, brown);
  src.loop = true;
  src.start();
  return src;
}

// A short tone that slides between pitches: a bird's chirp, a bubble.
function chirp(ctx, out, at, { from, to, length, level, type = "sine" }) {
  const osc = ctx.createOscillator();
  const env = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(from, at);
  osc.frequency.exponentialRampToValueAtTime(to, at + length);
  env.gain.setValueAtTime(0.0001, at);
  env.gain.exponentialRampToValueAtTime(level, at + Math.min(0.012, length / 4));
  env.gain.exponentialRampToValueAtTime(0.0001, at + length);
  osc.connect(env).connect(out);
  osc.start(at);
  osc.stop(at + length + 0.02);
}

function startAmbientSound(key) {
  if (!AMB.ctx) {
    try { AMB.ctx = new AudioContext(); } catch { return; }
  }
  const ctx = AMB.ctx;
  ctx.resume().catch(() => {});
  const out = ctx.createGain();
  out.gain.value = 0;
  out.connect(ctx.destination);
  AMB.out = out;
  const parts = [];
  let tick = null;
  if (key === "waves") {
    // Noise through a low filter, rising and falling like the sea.
    const src = loopNoise(ctx, false);
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    const swell = ctx.createGain();
    src.connect(lp).connect(swell).connect(out);
    parts.push(src);
    tick = () => {
      const s = ctx.currentTime;
      const wave = Math.max(0, Math.sin(s * 2 * Math.PI / 9) * 0.6 + Math.sin(s * 2 * Math.PI / 13.7) * 0.4);
      swell.gain.setTargetAtTime(0.08 + wave * 0.6, s, 0.25);
      lp.frequency.setTargetAtTime(380 + wave * 1400, s, 0.25);
    };
  } else if (key === "fish") {
    // The tank's pump hum, and bubbles: quick upward pops.
    const hum = loopNoise(ctx, true);
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 260;
    const g = ctx.createGain();
    g.gain.value = 0.5;
    hum.connect(lp).connect(g).connect(out);
    parts.push(hum);
    let next = ctx.currentTime;
    tick = () => {
      while (next < ctx.currentTime + 0.3) {
        const stream = Math.random() < 0.15;
        for (let i = 0; i < (stream ? 6 : 1); i++) {
          const f = 380 + Math.random() * 700;
          chirp(ctx, out, next + i * 0.07, { from: f, to: f * 2.4, length: 0.05 + Math.random() * 0.05, level: 0.12 + Math.random() * 0.15 });
        }
        next += 0.12 + Math.random() * 0.6;
      }
    };
  } else {
    // A breeze, and birds: short phrases of chirps, each bird its own pitch.
    const air = loopNoise(ctx, false);
    const hp = ctx.createBiquadFilter();
    hp.type = "bandpass";
    hp.frequency.value = 900;
    hp.Q.value = 0.4;
    const g = ctx.createGain();
    g.gain.value = 0.05;
    air.connect(hp).connect(g).connect(out);
    parts.push(air);
    const birds = [2600, 3400, 4300, 1900].map((pitch) => ({ pitch, next: ctx.currentTime + Math.random() * 3 }));
    tick = () => {
      for (const b of birds) {
        if (b.next > ctx.currentTime + 0.5) continue;
        let at = b.next;
        const notes = 2 + Math.floor(Math.random() * 6);
        for (let i = 0; i < notes; i++) {
          const up = Math.random() < 0.6;
          const f = b.pitch * (0.85 + Math.random() * 0.3);
          const length = 0.05 + Math.random() * 0.12;
          chirp(ctx, out, at, { from: up ? f * 0.7 : f * 1.3, to: up ? f * 1.25 : f * 0.8, length, level: 0.05 + Math.random() * 0.08 });
          at += length + 0.03 + Math.random() * 0.08;
        }
        b.next = at + 1.5 + Math.random() * 6;
      }
    };
  }
  tick();
  const timer = setInterval(tick, 100);
  AMB.sound = { parts, timer, out };
  ambientLevel();
}

function stopAmbientSound() {
  const sound = AMB.sound;
  if (!sound) return;
  AMB.sound = null;
  AMB.out = null;
  clearInterval(sound.timer);
  const ctx = AMB.ctx;
  sound.out.gain.setTargetAtTime(0, ctx.currentTime, 0.15);
  setTimeout(() => {
    for (const p of sound.parts) { try { p.stop(); } catch { /* already */ } }
    sound.out.disconnect();
  }, 900);
}

// ---- the picture

function drawAmbient(now) {
  AMB.raf = 0;
  const cv = document.querySelector("#ambient canvas");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  // Still frames for those who'd rather nothing moved.
  const t = calm.matches ? 0 : (now - AMB.t0) / 1000;
  ({ fish: drawFish, waves: drawSea, birds: drawDawn })[AMB.scene](g, w, h, t, dpr);
  if (!calm.matches && !document.hidden) AMB.raf = requestAnimationFrame(drawAmbient);
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !$("#ambient").hidden && !AMB.raf) AMB.raf = requestAnimationFrame(drawAmbient);
});

const rand = (a, b) => a + Math.random() * (b - a);

function drawSea(g, w, h, t) {
  const sky = g.createLinearGradient(0, 0, 0, h * 0.55);
  sky.addColorStop(0, "#1d2a52");
  sky.addColorStop(0.6, "#c4607a");
  sky.addColorStop(1, "#f6b06b");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  g.fillStyle = "rgba(255, 226, 170, .95)";
  g.beginPath();
  g.arc(w * 0.68, h * 0.5, Math.min(w, h) * 0.07, 0, Math.PI * 2);
  g.fill();
  const bands = [["#2a3f74", 0.5, 0.004, 0.25], ["#24366a", 0.58, 0.006, 0.4], ["#1c2c5c", 0.68, 0.008, 0.6], ["#16234d", 0.8, 0.011, 0.9]];
  for (const [color, top, amp, speed] of bands) {
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(0, h);
    for (let x = 0; x <= w; x += 8) {
      const y = h * top + Math.sin(x / w * 9 + t * speed) * h * amp * 4 + Math.sin(x / w * 23 - t * speed * 1.7) * h * amp;
      g.lineTo(x, y);
    }
    g.lineTo(w, h);
    g.fill();
  }
  // The sun's glitter on the water.
  g.fillStyle = "rgba(255, 220, 160, .3)";
  for (let i = 0; i < 60; i++) {
    const y = h * (0.52 + (i / 60) * 0.42);
    const spread = w * (0.015 + (i / 60) * 0.08);
    const x = w * 0.68 + Math.sin(i * 12.9 + t * 0.8) * spread;
    g.fillRect(x - spread * 0.4, y, spread * (0.3 + 0.5 * Math.abs(Math.sin(i * 7.3 + t))), 1.5);
  }
}

function drawFish(g, w, h, t, dpr) {
  const water = g.createLinearGradient(0, 0, 0, h);
  water.addColorStop(0, "#0f4f6e");
  water.addColorStop(1, "#06192b");
  g.fillStyle = water;
  g.fillRect(0, 0, w, h);
  // Light from above, swaying.
  g.fillStyle = "rgba(160, 230, 255, .05)";
  for (let i = 0; i < 5; i++) {
    const x = w * (0.1 + i * 0.2) + Math.sin(t * 0.3 + i) * w * 0.03;
    g.beginPath();
    g.moveTo(x - w * 0.02, 0);
    g.lineTo(x + w * 0.02, 0);
    g.lineTo(x + w * 0.09, h);
    g.lineTo(x - w * 0.03, h);
    g.fill();
  }
  // Weed on the bottom.
  g.strokeStyle = "#1f6b4a";
  g.lineWidth = 6 * dpr;
  g.lineCap = "round";
  for (let i = 0; i < 14; i++) {
    const x = w * (i + 0.5) / 14;
    g.beginPath();
    g.moveTo(x, h);
    g.quadraticCurveTo(x + Math.sin(t * 0.8 + i) * 30 * dpr, h * 0.86, x + Math.sin(t * 0.6 + i * 2) * 18 * dpr, h * (0.7 + (i % 3) * 0.05));
    g.stroke();
  }
  if (!AMB.things.length) {
    const colors = ["#ff8a3d", "#ffd23f", "#4fd1c5", "#f25f5c", "#a78bfa", "#ffb4a2"];
    for (let i = 0; i < 9; i++) {
      AMB.things.push({ kind: "fish", x: rand(0, w), y: rand(h * 0.15, h * 0.75), size: rand(14, 34) * dpr,
        speed: rand(20, 60) * dpr * (Math.random() < 0.5 ? -1 : 1), color: colors[i % colors.length], phase: rand(0, 6) });
    }
    for (let i = 0; i < 40; i++) AMB.things.push({ kind: "bubble", x: rand(0, w), y: rand(0, h), r: rand(1.5, 5) * dpr, speed: rand(30, 80) * dpr });
  }
  const dt = AMB.last ? Math.min(0.1, t - AMB.last) : 0;
  AMB.last = t;
  for (const f of AMB.things) {
    if (f.kind === "bubble") {
      f.y -= f.speed * dt;
      if (f.y < -10) { f.y = h + 10; f.x = rand(0, w); }
      g.strokeStyle = "rgba(200, 240, 255, .55)";
      g.lineWidth = 1.2 * dpr;
      g.beginPath();
      g.arc(f.x + Math.sin(t * 2 + f.r) * 4 * dpr, f.y, f.r, 0, Math.PI * 2);
      g.stroke();
      continue;
    }
    f.x += f.speed * dt;
    if (f.x > w + 60 * dpr) f.x = -60 * dpr;
    if (f.x < -60 * dpr) f.x = w + 60 * dpr;
    const y = f.y + Math.sin(t * 0.9 + f.phase) * 10 * dpr;
    const dir = Math.sign(f.speed);
    const s = f.size;
    const wag = Math.sin(t * 6 + f.phase) * 0.25;
    g.fillStyle = f.color;
    g.beginPath();
    g.ellipse(f.x, y, s, s * 0.55, 0, 0, Math.PI * 2);
    g.fill();
    g.beginPath();
    g.moveTo(f.x - dir * s * 0.8, y);
    g.lineTo(f.x - dir * s * 1.6, y - s * (0.5 + wag));
    g.lineTo(f.x - dir * s * 1.6, y + s * (0.5 - wag));
    g.fill();
    g.fillStyle = "#0b1a26";
    g.beginPath();
    g.arc(f.x + dir * s * 0.5, y - s * 0.12, s * 0.1, 0, Math.PI * 2);
    g.fill();
  }
}

function drawDawn(g, w, h, t, dpr) {
  const sky = g.createLinearGradient(0, 0, 0, h);
  sky.addColorStop(0, "#2b2d5c");
  sky.addColorStop(0.45, "#a45a7a");
  sky.addColorStop(0.75, "#f4a261");
  sky.addColorStop(1, "#f9d38a");
  g.fillStyle = sky;
  g.fillRect(0, 0, w, h);
  // The sun, rising a little over a few minutes.
  const rise = Math.min(1, t / 300);
  g.fillStyle = "rgba(255, 236, 190, .95)";
  g.beginPath();
  g.arc(w * 0.3, h * (0.78 - rise * 0.12), Math.min(w, h) * 0.08, 0, Math.PI * 2);
  g.fill();
  const hills = [["#5b3a5e", 0.72, 0.05, 2.3], ["#3d2a4a", 0.8, 0.06, 1.6], ["#251a33", 0.9, 0.04, 3.1]];
  for (const [color, top, amp, freq] of hills) {
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(0, h);
    for (let x = 0; x <= w; x += 10) g.lineTo(x, h * top + Math.sin(x / w * Math.PI * freq + top * 9) * h * amp);
    g.lineTo(w, h);
    g.fill();
  }
  if (!AMB.things.length) {
    for (let i = 0; i < 11; i++) {
      AMB.things.push({ x: rand(-w * 0.2, w), y: rand(h * 0.12, h * 0.55), size: rand(7, 15) * dpr, speed: rand(25, 55) * dpr, phase: rand(0, 6) });
    }
  }
  const dt = AMB.last ? Math.min(0.1, t - AMB.last) : 0;
  AMB.last = t;
  g.strokeStyle = "#1d1426";
  g.lineWidth = 2.2 * dpr;
  g.lineCap = "round";
  for (const b of AMB.things) {
    b.x += b.speed * dt;
    if (b.x > w + 40 * dpr) { b.x = -40 * dpr; b.y = rand(h * 0.12, h * 0.55); }
    const y = b.y + Math.sin(t * 0.7 + b.phase) * 8 * dpr;
    const flap = Math.sin(t * 7 + b.phase) * 0.6;
    g.beginPath();
    g.moveTo(b.x - b.size, y - b.size * flap);
    g.quadraticCurveTo(b.x - b.size * 0.4, y - b.size * 0.3, b.x, y);
    g.quadraticCurveTo(b.x + b.size * 0.4, y - b.size * 0.3, b.x + b.size, y - b.size * flap);
    g.stroke();
  }
}

// ---------- recording ---------------------------------------------------

const recordingOf = (url) => (S.lib.recordings || []).find((r) => r.url === url);
const isRunning = (r) => Boolean(r && !r.done && !r.stopping);
const activeRecordings = () => (S.lib.recordings || []).filter(isRunning);

function fmtBytes(n) {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`;
}

async function toggleRecording(st) {
  const on = !isRunning(recordingOf(st.url));
  recToken++;  // a poll in flight mustn't undo this
  try {
    await loadState(await api("/api/record", { station: st.id, on }, { quiet: true }));
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  const after = recordingOf(st.url);
  announce(on ? `Recording ${st.name}` : `Stopped recording ${st.name}${after && after.file ? `. Saved as ${baseName(after.file)}` : ""}`);
}

const baseName = (path) => path.split(/[\\/]/).pop();

// Show recordings on the station cards and in the sidebar, and keep
// checking on them while any is running. The ticking time is updated in
// place (not announced); starting, stopping and failing are announced.
let recTimer = null;
let recToken = 0;
const recWasRunning = new Set();
function syncRecordings() {
  const box = $("#rec-status");
  if (!box) return;  // the player has quit
  const active = activeRecordings();
  for (const r of S.lib.recordings || []) {
    if (r.done && recWasRunning.has(r.url) && r.error) announce(`Recording ${r.name} stopped: ${r.error}`);
    if ((r.done || r.stopping) && recWasRunning.has(r.url) && S.view.type === "recordings") setTimeout(loadRecordings, 1500);
  }
  recWasRunning.clear();
  for (const r of active) recWasRunning.add(r.url);
  const buttons = new Map([...box.children].map((b) => [b.dataset.url, b]));
  const same = active.length === buttons.size && active.every((r) => buttons.has(r.url));
  // Rebuilt only when the recordings change, so a focused button keeps focus.
  if (!same) {
    box.replaceChildren(...active.map((r) => el("button", {
      type: "button", "data-url": r.url, onclick: () => show({ type: "radio" }), title: "Go to Radio",
    }, el("span", { class: "rec-dot", "aria-hidden": "true" }), `Recording ${r.name}`,
    el("span", { "aria-hidden": "true" }, ""))));
  }
  for (const r of active) {
    box.querySelector(`button[data-url="${CSS.escape(r.url)}"]`).lastChild.textContent = ` · ${fmtTime(r.seconds)}`;
  }
  box.hidden = active.length === 0;
  for (const card of document.querySelectorAll("#view .recent-card[data-find]")) {
    const running = isRunning(recordingOf(card.dataset.find));
    card.classList.toggle("rec-on", running);
    const btn = card.querySelector(".rec-btn");
    const label = running ? "Stop recording" : "Record";
    if (btn.textContent !== label) btn.replaceChildren(...iconLabel(running ? "stop" : "record", label));
    btn.setAttribute("aria-label", `${label} ${card.querySelector(".name").textContent}`);
  }
  for (const card of document.querySelectorAll("#view .card[data-url]")) {
    const r = recordingOf(card.dataset.url);
    const running = isRunning(r);
    card.classList.toggle("recording", running);
    const btn = card.querySelector(".rec-btn");
    const label = running ? "Stop recording" : "Record";
    if (btn.textContent !== label) btn.replaceChildren(...iconLabel(running ? "stop" : "record", label));
    const line = card.querySelector(".rec");
    const text = line.querySelector(".rec-text");
    const play = line.querySelector(".rec-play");
    line.className = "rec";
    play.hidden = true;
    if (running) {
      text.textContent = `● Recording · ${fmtTime(r.seconds)} · ${fmtBytes(r.bytes)}`;
    } else if (r && r.stopping) {
      text.textContent = "Stopping…";
    } else if (r && r.error) {
      text.textContent = `Recording stopped: ${r.error}`;
      line.classList.add("error");
    } else if (r && r.file) {
      text.textContent = `Saved ${baseName(r.file)} (${fmtBytes(r.bytes)})`;
      line.classList.add("saved");
    } else {
      text.textContent = "";
    }
    // A recording that saved anything can be played here.
    if (r && r.done && r.file && r.bytes) {
      play.hidden = false;
      play.setAttribute("aria-label", `Play ${baseName(r.file)}`);
      play.onclick = () => playRecording(r.file);
    }
    line.hidden = !text.textContent;
  }
  clearTimeout(recTimer);
  if (active.length || (S.lib.recordings || []).some((r) => r.stopping)) recTimer = setTimeout(pollRecordings, 1000);
}

// Play a finished recording: its folder joins the music folders (once),
// so it shows under Folders like any other music.
async function playRecording(file, { stay = false } = {}) {
  let res;
  try {
    res = await api("/api/recordings/play", { file });
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  await loadState(res);
  if (stay) {  // play it without leaving the Recordings page
    let detail;
    try { detail = await api(`/api/playlists/${res.playlist}`); } catch (err) { renderErrors(err.message); return; }
    const i = detail.tracks.findIndex((t) => t.id === res.track);
    if (i >= 0) playPlaylist(detail, i);
    return;
  }
  await show({ type: "playlist", id: res.playlist });
  if (S.view.id !== res.playlist || !S.detail) return;
  const i = S.detail.tracks.findIndex((t) => t.id === res.track);
  if (i >= 0) playPlaylist(S.detail, i);
}

// A recording joins the library (as playRecording does) to be added.
async function addRecordingToPlaylist(r, button) {
  if (button.disabled) return;
  button.disabled = true;  // the first time, the library takes a moment to add the recordings
  let res;
  try {
    res = await api("/api/recordings/play", { file: r.file });
  } catch (err) {
    toast(err.message, { error: true });
    return;
  } finally {
    button.disabled = false;
  }
  await loadState(res);
  if (document.contains(button)) playlistChooser([res.track], recTitle(r), button, button);
}

async function pollRecordings() {
  const token = ++recToken;
  try {
    const data = await api("/api/recordings");
    if (token !== recToken) return;  // a newer answer (a button press) came first
    S.lib.recordings = data.recordings;
  } catch { /* try again next time */ }
  if (token === recToken) syncRecordings();
}

// ---------- adding music ----------------------------------------------------

const plural = (n, word) => `${(n || 0).toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
let pickerMissing = false;  // this computer can't show a chooser: type paths instead

// Ask the computer for a folder or file with its own chooser. Returns the
// path, or null when cancelled (or no chooser could be shown).
async function pickPath(kind) {
  try {
    const res = await api("/api/pick", { kind }, { quiet: true });
    return res.cancelled ? null : res.path || null;
  } catch (err) {
    if (err.status === 501 || (err.data && err.data.unsupported)) {
      pickerMissing = true;
      renderView();
      const typed = $("#view details.typed");
      if (typed) { typed.open = true; const input = typed.querySelector("input"); if (input) input.focus(); }
      toast("This computer can't show a chooser here, so type the path instead", { error: true });
    } else if (err.status === 409) {
      toast("A chooser is already open. It may be behind this window.", { error: true });
    } else {
      toast(err.message, { error: true });
    }
    return null;
  }
}

async function addSource(kind, path) {
  try {
    await loadState(await api("/api/sources", { kind, path }));
  } catch (err) {
    toast(err.message, { error: true });
    return false;
  }
  for (const s of suggestions || []) if (s.kind === kind && samePath(s.path, path)) s.added = true;
  renderView();
  const src = (S.lib.sources || []).find((s) => s.kind === kind && s.path === path) ||
    (S.lib.sources || []).find((s) => s.kind === kind && samePath(s.path, path));
  toast(src ? `Added ${src.name}: ${sourceStat(src)}` : `Added ${baseName(path)}`);
  return true;
}

const samePath = (a, b) => a.replace(/[\\/]+$/, "") === b.replace(/[\\/]+$/, "");

// After the page is redrawn, put focus back where it was (by selector).
function refocus(selector) {
  const target = selector && document.querySelector(selector);
  if (target) target.focus();
  else focusHeading();
}

async function pickAndAdd(kind, pick, from) {
  const path = await pickPath(pick);
  if (path) await addSource(kind, path);
  refocus(from);
}

function sourceStat(src) {
  if (!src.exists) return "Not found. It may have moved, or its drive isn't plugged in.";
  if (src.kind === "music") return plural(src.tracks, "track");
  return `${plural(src.playlists, "playlist")}, ${plural(src.tracks, "track")}`;
}

function sourceCard(src) {
  const folder = src.kind === "music";
  // A source that has gone can be pointed at its new place.
  const relocate = async () => {
    const path = await pickPath(folder || !/\.xml$/i.test(src.path) ? "folder" : "file");
    if (!path) return;
    if (!(await addSource(src.kind, path))) return;
    // Chose the same place (its drive is back): nothing to swap.
    const both = (S.lib.sources || []).filter((s) => s.kind === src.kind && (s.path === src.path || samePath(s.path, path)));
    if (both.length > 1 && !samePath(path, src.path)) {
      await act("/api/sources/remove", { kind: src.kind, path: src.path }, `Now using ${baseName(path)}`);
    }
  };
  return el("div", { class: src.exists ? "source" : "source gone" },
    el("div", { class: src.exists ? "glyph" : "glyph danger" }, icon(src.exists ? (folder ? "folder" : "list") : "alert")),
    el("div", { class: "info" },
      el("div", { class: "name" }, src.name || baseName(src.path)),
      el("div", { class: "path", title: src.path }, src.path),
      el("div", { class: "stat" }, sourceStat(src))),
    el("div", { class: "actions" },
      src.exists ? null : el("button", { type: "button", class: "btn small primary", onclick: relocate }, "Find it…"),
      el("button", {
        type: "button", class: "btn small ghost", "aria-label": `Remove ${src.name || src.path}`,
        title: "Stop using this in Station Player. The files stay where they are.",
        onclick: () => act("/api/sources/remove", { kind: src.kind, path: src.path }, `Removed ${src.name || baseName(src.path)}`),
      }, ...iconLabel("x", "Remove"))));
}

// The two ways in: a folder of music, or playlists exported from iTunes / station-studio.
function addTiles() {
  const tile = (glyph, title, sub, onclick, id, cls = "") => el("button", { type: "button", class: `tile ${cls}`, id, onclick },
    el("span", { class: "glyph" }, icon(glyph)),
    el("span", {}, el("span", { class: "t-title" }, title), el("span", { class: "t-sub" }, sub)));
  return el("div", {},
    el("div", { class: "add-tiles" },
      tile("folder", "Add a music folder", "A folder of songs, such as your Music folder. It shows under Folders.",
        () => pickAndAdd("music", "folder", "#add-folder"), "add-folder"),
      tile("list", "Add iTunes or station-studio playlists", "An exported library (.xml). Station Player picks up its playlists.",
        () => pickAndAdd("playlist", "file", "#add-playlists"), "add-playlists", "studio-only")),
    el("p", { class: "help studio-only" },
      "Station-studio saves a new export every week? ",
      el("button", { type: "button", class: "btn small", id: "add-exports", onclick: () => pickAndAdd("playlist", "folder", "#add-exports") }, "Choose its exports folder"),
      " and every new one appears by itself."),
    typedPaths());
}

// For computers without a chooser, or anyone who'd rather paste a path.
function typedPaths() {
  return el("details", { class: "typed", open: pickerMissing || null },
    el("summary", {}, "Type or paste a path instead"),
    el("div", { class: "checks" },
      pathForm("music", "Path to a folder of music"),
      el("div", { class: "studio-only" }, pathForm("playlist", "Path to an .xml file, or a folder of them"))));
}

function pathForm(kind, placeholder) {
  const input = el("input", { placeholder, "aria-label": placeholder });
  const msg = el("div", { class: "form-msg", role: "status" });
  const submit = async (e) => {
    e.preventDefault();
    msg.textContent = "";
    if (!input.value.trim()) { msg.textContent = "Type a path first"; return; }
    try {
      await loadState(await api("/api/sources", { kind, path: input.value }));
      renderView();
      focusHeading();
      toast(`Added ${baseName(input.value.trim())}`);
    } catch (err) { msg.textContent = err.message; }
  };
  return el("div", {}, el("form", { class: "form-row", onsubmit: submit }, input,
    el("button", { class: "btn" }, "Add")), msg);
}

// Music folders and libraries found on this computer, to add with a click.
let suggestions = null;
const justAdded = new Set();  // suggestions added on this visit stay listed, ticked
let suggestionsToken = 0;
let suggestionsVisit = -1;
function suggestionList(onAdded) {
  const box = el("div", { class: "suggestions" });
  const draw = () => {
    const list = (suggestions || []).filter((s) => !s.added || justAdded.has(s.path));
    box.replaceChildren(...list.map((s) => el("div", { class: s.added ? "suggestion added" : "suggestion", "data-path": s.path },
      el("div", { class: "glyph" }, icon(s.kind === "music" ? "folder" : "list")),
      el("div", { class: "info" },
        el("div", { class: "name" }, s.label || baseName(s.path)),
        s.detail ? el("div", { class: "detail" }, s.detail) : null,
        el("div", { class: "path", title: s.path }, s.path)),
      s.added ? el("span", { class: "added-mark", tabindex: "-1" }, icon("check"), "Added")
        : el("button", {
            type: "button", class: "btn small primary", "aria-label": `Add ${s.label || s.path}`,
            onclick: async () => {
              justAdded.add(s.path);
              if (await addSource(s.kind, s.path)) {
                if (onAdded) onAdded();
                refocus(`#view .suggestion[data-path="${CSS.escape(s.path)}"] .added-mark`);
              } else {
                justAdded.delete(s.path);
              }
            },
          }, ...iconLabel("plus", "Add")))));
    box.hidden = list.length === 0;
    const section = box.closest("[data-suggestions]");
    if (section) section.hidden = list.length === 0;
  };
  // Looked for once each time the page is opened, not on every redraw.
  if (suggestionsVisit === showToken && suggestions) {
    queueMicrotask(draw);
    return box;
  }
  suggestionsVisit = showToken;
  const token = ++suggestionsToken;
  api("/api/suggestions").then((res) => {
    if (token !== suggestionsToken) return;
    suggestions = res.suggestions || [];
    draw();
  }).catch(() => {});
  queueMicrotask(draw);
  return box;
}

// "Some tracks can't be found": point Station Player at where they are now.
function missingBanner(title) {
  return el("div", { class: "banner", role: "region", "aria-label": "Missing tracks" },
    el("div", { class: "glyph" }, icon("locate")),
    el("div", { class: "info" },
      el("strong", {}, title),
      el("span", { class: "sub" }, "If the music is on another drive, or came from another computer, show Station Player the folder it's in now.")),
    el("button", { type: "button", class: "btn primary", onclick: locateMissing }, "Find them…"));
}

async function locateMissing() {
  const folder = await pickPath("folder");
  if (!folder) return;
  let res;
  try {
    res = await api("/api/locate", { folder });
  } catch (err) {
    toast(err.message, { error: true });
    return;
  }
  await loadState(res);
  if (S.view.type === "playlist" && S.view.id) await refreshDetail(undefined, S.view.id);
  else renderView();
  refocus("#view .banner button");
  if (res.found) toast(`Found ${plural(res.found, "track")}${res.missing ? `. ${plural(res.missing, "track")} still missing.` : ""}`);
  else toast("None of the missing tracks are in that folder", { error: true });
}

// The first thing a new user sees, and where "Add music" leads.
function welcomeView() {
  const sources = S.lib.sources || [];
  const fresh = sources.length === 0;
  const finish = el("div", { class: "finish" });
  const drawFinish = () => {
    const first = S.lib.playlists[0];
    finish.replaceChildren((S.lib.sources || []).length && first ? el("button", {
      type: "button", class: "btn primary big", id: "btn-start-listening",
      onclick: () => show({ type: "playlist", id: first.id }),
    }, ...iconLabel("play", "Start listening")) : "");
  };
  drawFinish();
  return el("div", { class: "welcome" },
    el("div", { class: "hero" },
      el("img", { src: "/icon-192.png", alt: "" }),
      el("h1", {}, fresh ? "Welcome to Station Player" : "Add music"),
      el("p", {}, fresh
        ? "Let's find your music. Show Station Player where it lives and it does the rest: playlists, folders, artwork and all."
        : "Add another folder of music, or more playlists from iTunes or station\u2011studio.")),
    el("section", { "data-suggestions": "", hidden: true },
      el("h2", {}, "Found on this computer"),
      suggestionList(drawFinish)),
    el("section", {},
      el("h2", {}, fresh ? "Or choose it yourself" : "Choose it yourself"),
      addTiles()),
    finish,
    el("p", { class: "later" }, fresh ? "Just here for the radio? " : "Your music sources are listed under ",
      el("button", {
        type: "button", class: "btn small",
        onclick: () => show(fresh ? { type: "radio" } : { type: "settings", tab: "music" }),
      }, fresh ? "Go to Radio" : "Settings › Music"),
      fresh ? " You can add music any time with Add music in the sidebar." : "."));
}

// ---------- settings view -----------------------------------------------

function listSection({ title, help, items, onRemove, form }) {
  return el("section", {},
    el("h2", {}, title),
    el("p", { class: "help" }, help),
    items.length
      ? el("ul", {}, items.map((label, i) => el("li", {},
          el("span", {}, label),
          el("button", {
            type: "button", class: "btn small ghost", "aria-label": `Remove ${label}`,
            onclick: () => onRemove(i),
          }, ...iconLabel("x", "Remove")))))
      : null,
    form);
}

function musicPage() {
  const cfg = S.lib.config;
  const sources = S.lib.sources || [];
  const from = el("input", { placeholder: "From, e.g. /Users/rob/Music/iTunes/iTunes Media", "aria-label": "Path from" });
  const to = el("input", { placeholder: "To, e.g. D:/Music", "aria-label": "Path to" });
  const remapMsg = el("div", { class: "form-msg", role: "status" });
  const addRemap = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/remaps", { from: from.value, to: to.value }));
      renderView();
      toast("Path rewrite added");
    } catch (err) { remapMsg.textContent = err.message; }
  };
  const remaps = cfg.path_remaps || [];
  return el("div", {},
    S.lib.missing ? missingBanner(`${plural(S.lib.missing, "track")} in your playlists can't be found`) : null,
    el("section", {},
      el("h2", {}, "Your music"),
      el("p", { class: "help" }, "Where Station Player finds your songs and playlists. Removing one here leaves the files where they are."),
      sources.length ? el("div", { class: "sources" }, sources.map(sourceCard))
        : el("p", { class: "help" }, "Nothing yet. Add a folder or some playlists below."),
      addTiles()),
    el("section", { "data-suggestions": "", hidden: true },
      el("h2", {}, "Found on this computer"),
      suggestionList()),
    el("details", { class: "advanced", open: remaps.length ? true : null },
      el("summary", {}, "Advanced"),
      listSection({
        title: "Path rewrites",
        help: "Find them… above usually sets these up for you. When a playlist was made on another computer, the start of each track's path is rewritten so it points at your copy of the music.",
        items: remaps.map((r) => `${r.from}  →  ${r.to}`),
        onRemove: (i) => act("/api/remaps/remove", { from: remaps[i].from, to: remaps[i].to }, "Path rewrite removed"),
        form: el("div", {}, el("form", { class: "form-row", onsubmit: addRemap }, from, to,
          el("button", { class: "btn" }, "Add")), remapMsg),
      }),
      el("section", {},
        el("h2", {}, "Rescan"),
        el("p", { class: "help" }, "Station Player notices new music by itself. Rescan reads everything again now."),
        el("button", {
          type: "button", class: "btn", "data-busy-disable": true, disabled: pending > 0 || null,
          onclick: () => act("/api/rescan", {}, "Library rescanned"),
        }, ...iconLabel("refresh", "Rescan")))));
}

function alarmPage() {
  return el("div", {},
    el("section", {},
      el("h2", {}, "Alarms"),
      el("p", { class: "help" }, "Alarms now live on the Clock page, with a big clock and night mode for the bedside."),
      el("button", { type: "button", class: "btn", id: "btn-open-clock", onclick: () => show({ type: "clock" }) },
        ...iconLabel("clock", "Open the Clock page"))),
    el("section", {},
      el("h2", {}, "Sleep timer"),
      el("p", { class: "help" },
        "To fall asleep to music, choose a time from the sleep menu beside the volume, at the bottom right. ",
        "Music fades out over the last 20 seconds and stops. You can set it from the phone remote too.")));
}

const SETTINGS_TABS = [
  ["music", "Music", "music", musicPage],
  ["playback", "Playback", "headphones", () => playbackSection()],
  ["radio", "Radio", "radio", () => tunerSettings()],
  ["alarm", "Alarm & sleep", "clock", alarmPage],
  ["remote", "Phone remote", "phone", () => remoteSection()],
  ["car", "In the car", "car", () => carSection()],
  ["log", "Play log", "log", () => playLogSection()],
  ["app", "App", "app", () => appSection()],
];

function openSettingsTab(id) {
  S.view = { type: "settings", tab: id };
  store("view", JSON.stringify(S.view));
  renderNav();
  renderErrors();
  renderView();
  $("#main").scrollTop = 0;
  const tab = $(`#tab-${id}`);
  if (tab) tab.focus();
}

function settingsView() {
  const shown = SETTINGS_TABS.filter((t) => studio() || t[0] !== "log");
  const page = shown.find((t) => t[0] === S.view.tab) || shown[0];
  const ids = shown.map((t) => t[0]);
  const tabs = el("div", {
    class: "tabs", role: "tablist", "aria-label": "Settings pages",
    onkeydown: (e) => {
      const at = ids.indexOf(page[0]);
      let to = null;
      if (e.key === "ArrowRight") to = (at + 1) % ids.length;
      else if (e.key === "ArrowLeft") to = (at - 1 + ids.length) % ids.length;
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = ids.length - 1;
      if (to === null) return;
      e.preventDefault();
      openSettingsTab(ids[to]);
    },
  }, shown.map(([id, label, glyph]) => el("button", {
    type: "button", role: "tab", class: "tab", id: `tab-${id}`, "aria-controls": "settings-page",
    "aria-selected": String(id === page[0]), tabindex: id === page[0] ? "0" : "-1",
    onclick: () => openSettingsTab(id),
  }, ...iconLabel(glyph, label))));
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("h1", {}, "Settings")),
    tabs,
    el("div", { role: "tabpanel", id: "settings-page", "aria-labelledby": `tab-${page[0]}` }, page[3]()),
    el("p", { class: "help version" }, `Station Player ${S.lib.version || ""}`));
}

// A checkbox that redraws the view afterwards and keeps focus on itself.
function checkbox(key, label, checked, change) {
  return el("label", { class: "check" },
    el("input", {
      type: "checkbox", checked: checked || null, "data-key": key,
      onchange: async (e) => {
        await change(e.target.checked);
        renderView();
        const again = document.querySelector(`#view input[data-key="${key}"]`);
        if (again) again.focus();
      },
    }),
    el("span", {}, label));
}

// Shortcuts and start-at-login, worded for the computer it runs on.
function appSection() {
  const d = S.lib.desktop;
  const where = d && {
    windows: "Add Station Player to the Start menu and the desktop",
    mac: "Add Station Player to your Applications folder (find it in Launchpad and Spotlight)",
    linux: "Add Station Player to your apps menu and the desktop",
  }[d.platform];
  const names = { shortcut: "Shortcut", login: "Start at login" };
  // Redraw from what the server reports afterwards (unticking a failed change).
  const desktopChange = (key) => async (on) => {
    try {
      await loadState(await api("/api/desktop", { [key]: on }));
      toast(`${names[key]} turned ${on ? "on" : "off"}`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const updatesChange = async (on) => {
    try {
      await loadState(await api("/api/settings", { check_updates: on }));
      toast(`New version notices turned ${on ? "on" : "off"}`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const windowChange = async (on) => {
    try {
      await loadState(await api("/api/settings", { own_window: on }, { quiet: true }));
      toast(`Own window turned ${on ? "on" : "off"}. This applies the next time Station Player opens.`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const browser = S.lib.window_browser;
  // Installed as an app, the player gets its own name and icon in the
  // Dock / taskbar and Alt-Tab, instead of being one of the browser's windows.
  const install = installPrompt ? el("p", {},
    el("button", {
      type: "button", class: "btn", id: "btn-install",
      onclick: async () => {
        const prompt = installPrompt;
        installPrompt = null;  // each offer can be used once
        prompt.prompt();
        await prompt.userChoice.catch(() => null);
        if (S.view.type === "settings") renderView();
      },
    }, "Install Station Player as an app"),
    el("span", { class: "help" }, " so it has its own icon in the Dock (or taskbar) and in Alt-Tab.")) : null;
  return el("section", {},
    el("h2", {}, "App"),
    install,
    d && d.supported && !d.bundled ? el("p", { class: "help" },
      "Open Station Player like any other app, without a terminal. Turn these off before uninstalling.") : null,
    el("div", { class: "checks" },
      d && d.supported && !d.bundled ? checkbox("shortcut", where, d.shortcut, desktopChange("shortcut")) : null,
      d && d.supported ? checkbox("login", "Start Station Player in the background when I log in", d.login, desktopChange("login")) : null,
      S.lib.mac_app ? checkbox("window", "Open in Station Player's own window, not a browser tab",
        S.lib.config.own_window !== false, windowChange)
        : browser ? checkbox("window", `Open in a window of its own (using ${browser}), not a browser tab`,
          S.lib.config.own_window !== false, windowChange) : null,
      checkbox("updates", "Tell me when a new version is out (asks GitHub once a day)",
        S.lib.config.check_updates !== false, updatesChange)),
    updateSettings(),
    browser || S.lib.mac_app ? null : el("p", { class: "help" },
      "Station Player opens in a browser tab. With Google Chrome, Microsoft Edge or Brave installed it opens in a window of its own instead."),
    el("h2", { class: "spaced" }, "Studio tools"),
    el("p", { class: "help" },
      "For people who make a radio station: playlists exported from station-studio or iTunes, recording radio, ",
      "the Recordings page, and the play log for PRS and PPL. Off, Station Player is a simple radio and music player."),
    el("div", { class: "checks" },
      checkbox("studio", "Show the studio tools", studio(), async (on) => {
        try {
          await loadState(await api("/api/settings", { studio: on }, { quiet: true }));
          toast(on ? "Studio tools turned on" : "Studio tools turned off. Your recordings and playlists are kept.");
        } catch (err) {
          renderErrors(err.message);
        }
      })));
}

// Settings › App: this version, Check for updates, and Update to the new one.
function updateSettings() {
  const latest = S.lib.update;
  const u = S.lib.updating;
  const busy = u && u.stage !== "failed";
  let line;
  if (busy) line = updateStageText(u);
  else if (latest) line = `Station Player ${latest} is out. You have ${S.lib.version}.`;
  else if (U.checked) line = `You have the newest version, ${S.lib.version}.`;
  else line = `You have Station Player ${S.lib.version}.`;
  return el("div", { id: "update-settings", class: "update-settings" },
    el("p", { role: "status" }, line),
    u && u.stage === "failed" ? updateFailure(u) : null,
    el("div", { class: "row" },
      latest && !busy ? el("button", {
        type: "button", class: "btn primary", id: "btn-update", onclick: () => startUpdate(latest),
      }, `Update to ${latest}`) : null,
      busy ? null : el("button", {
        type: "button", class: "btn", id: "btn-check-update",
        onclick: async (e) => {
          e.currentTarget.disabled = true;
          try {
            await loadState(await api("/api/update/check", {}, { quiet: true }));
            U.checked = true;
            if (!S.lib.update) toast("Station Player is up to date");
          } catch (err) {
            toast(err.message, { error: true });
          }
          syncUpdateSettings();
          $("#btn-update, #btn-check-update")?.focus();
        },
      }, "Check for updates")),
    el("p", { class: "help" }, S.lib.bundled
      ? "Updating downloads the new version from Station Player's GitHub page, checks it's the genuine file, and restarts Station Player. Your stations, playlists and settings are kept."
      : "Updating runs pip to install the new version, then restarts Station Player. Your stations, playlists and settings are kept."));
}
const U = { checked: false };

function syncUpdateSettings() {
  const old = $("#update-settings");
  if (old) old.replaceWith(updateSettings());
}

// Which of your stations sit on the Radio page's tuner dial.
const onTuner = (st) => st.tuner !== false;
function tunerSettings() {
  const stations = S.lib.stations || [];
  const change = (st) => async (on) => {
    try {
      await loadState(await api("/api/stations/update", { url: st.url, tuner: on }, { quiet: true }));
      toast(on ? `${st.name} is on the tuner` : `${st.name} is off the tuner`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  return el("section", {},
    el("h2", {}, "On the tuner"),
    el("p", { class: "help" },
      "The dial across the top of the Radio page. Tick the stations you want on it; the rest still have their cards below it. ",
      "They sit in the order of your stations: change it with Up and Down when you edit a station. The tuner shows once two or more are ticked."),
    stations.length
      ? el("div", { class: "checks tuner-checks" }, stations.map((st, i) => checkbox(`tuner-${i}`, st.name, onTuner(st), change(st))))
      : el("p", { class: "help" }, "Add stations on the Radio page first."));
}

function playbackSection() {
  const label = (v) => (v ? `${v} s` : "Off");
  const out = el("output", { for: "crossfade" }, label(X.secs));
  const slider = el("input", {
    type: "range", id: "crossfade", min: 0, max: MAX_CROSSFADE, step: 1, value: X.secs,
    "aria-valuetext": label(X.secs),
    oninput: (e) => {
      setCrossfade(Number(e.target.value));
      out.textContent = label(X.secs);
      e.target.setAttribute("aria-valuetext", X.secs ? `${X.secs} seconds` : "Off");
    },
  });
  return el("section", {},
    el("h2", {}, "Playback"),
    el("p", { class: "help" },
      "Tracks play back to back with no gap, or, with crossfade, the next one fades in as the last one fades out. ",
      "Tracks from the same album and short ones (jingles, idents) are never crossfaded."),
    el("div", { class: "range-row" }, el("label", { for: "crossfade" }, "Crossfade"), slider, out),
    el("p", { class: "help" },
      "Volume levelling plays every track at about the same loudness, ",
      "using ReplayGain or Sound Check information where a file has it and measuring the rest the first time they play."),
    el("div", { class: "checks" },
      checkbox("level", "Even out the volume between tracks", L.on, async (on) => {
        setLevelling(on);
        toast(`Volume levelling turned ${on ? "on" : "off"}`);
      })));
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

async function saveSetting(body, message) {
  try {
    await loadState(await api("/api/settings", body, { quiet: true }));
    toast(message);
    return true;
  } catch (err) {
    renderErrors(err.message);
    return false;
  }
}

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The play log: what played and when, as a spreadsheet for music returns.
function playLogSection() {
  const now = new Date();
  // Last month, the usual reporting period.
  const from = el("input", { type: "date", id: "log-from", value: isoDate(new Date(now.getFullYear(), now.getMonth() - 1, 1)) });
  const to = el("input", { type: "date", id: "log-to", value: isoDate(new Date(now.getFullYear(), now.getMonth(), 0)) });
  const link = el("a", { class: "btn", id: "log-download", download: "" }, "Download play log (CSV)");
  const update = () => { link.href = `/api/playlog.csv?from=${from.value}&to=${to.value}`; };
  from.addEventListener("change", update);
  to.addEventListener("change", update);
  update();
  return el("section", {},
    el("h2", {}, "Play log"),
    el("p", { class: "help" },
      "A record of every track played, for PRS for Music and PPL returns: date and time, title, artist, album, ",
      "composer, label and ISRC (from the file's tags) and how long it played. Plays under 30 seconds and radio aren't included. ",
      "It stays on this computer."),
    el("div", { class: "checks" },
      checkbox("play_log", "Keep a log of the tracks I play", S.lib.config.play_log !== false,
        (on) => saveSetting({ play_log: on }, `Play log turned ${on ? "on" : "off"}`))),
    el("div", { class: "fields" },
      el("label", { class: "field" }, "From", from),
      el("label", { class: "field" }, "To", to),
      link));
}

// The phone remote: a link (and QR code) for a phone on the same Wi-Fi.
function carSection() {
  // The car page lives on the web, so it works on mobile data far from
  // this computer; the stations go to it inside the link (see car/car.js).
  const more = el("div", {});
  let link = null;
  const copy = el("button", {
    type: "button", class: "btn", id: "btn-car-copy", disabled: true,
    onclick: async () => {
      // Copied straight from the click (no fetch first), as Safari wants.
      try {
        await navigator.clipboard.writeText(link);
        toast("Link copied: send it to your phone by message or email");
      } catch { toast("Couldn't copy the link", { error: true }); }
    },
  }, "Copy the link");
  api("/api/car").then(({ url, http_only: httpOnly }) => {
    link = url;
    copy.disabled = false;
    if (httpOnly && httpOnly.length) {
      more.replaceChildren(el("p", { class: "help" },
        `${httpOnly.join(", ")} ${httpOnly.length > 1 ? "have" : "has"} only an http:// link, which phones may not play from the car page. `,
        "If the station has an https:// link, edit the station and use that."));
    }
  }).catch(() => {});
  return el("section", {},
    el("h2", {}, "In the car"),
    el("p", { class: "help" },
      "Take your radio stations in the car. Scan this code with your phone's camera: it opens a page with big buttons that plays your stations over the phone's mobile data, ",
      "so the computer doesn't need to be on. Then add it to the phone's home screen."),
    el("p", { class: "help" },
      "Connect the phone to the car by Bluetooth, CarPlay or Android Auto and the sound comes through the car's speakers. The steering-wheel buttons skip between stations."),
    el("div", { class: "remote-box car-box" },
      el("img", { src: `/api/car/qr.svg?${Date.now()}`, alt: "QR code for the car page with your stations" }),
      el("div", {},
        el("p", {}, "Can't scan it? Copy the link and send it to your phone."),
        copy,
        el("p", { class: "help" }, "Added or changed stations? Scan the new code to update the phone."))),
    more);
}

function remoteSection() {
  const r = S.lib.remote || {};
  const box = [];
  if (r.on && r.url) {
    box.push(el("div", { class: "remote-box" },
      el("img", { src: `/api/remote/qr.svg?${encodeURIComponent(r.url.slice(-8))}`, alt: "QR code for the phone remote link" }),
      el("div", {},
        el("p", {}, "Scan this with your phone's camera, or open:"),
        el("p", { class: "remote-link" }, r.url),
        el("button", {
          type: "button", class: "btn", id: "btn-new-link",
          onclick: async () => {
            try {
              await loadState(await api("/api/remote/new-link", {}, { quiet: true }));
              renderView();
              toast("New link made; the old one no longer works");
              const again = $("#btn-new-link");
              if (again) again.focus();
            } catch (err) { renderErrors(err.message); }
          },
        }, "Make a new link"),
        el("p", { class: "help" }, "The old link stops working."))));
  } else if (r.on && r.error) {
    box.push(el("p", { class: "errors" }, r.error));
  } else if (r.on) {
    box.push(el("p", { class: "help" }, "This computer doesn't seem to be on a network right now."));
  }
  return el("section", {},
    el("h2", {}, "Phone remote"),
    el("p", { class: "help" },
      "Play, pause, skip, change the volume and choose what plays from a phone on the same Wi-Fi. ",
      "The music still plays here. Anyone with the link can use it while they're on your network, so keep it to people you trust. ",
      "Your computer may ask whether to allow incoming connections: allow them."),
    el("div", { class: "checks" },
      checkbox("remote", "Turn on the phone remote", Boolean(r.on),
        (on) => saveSetting({ remote: on }, `Phone remote turned ${on ? "on" : "off"}`))),
    ...box);
}

// ---------- volume levelling ----------------------------------------------

// Each track deck runs through its own gain, then a master gain for the
// volume slider. The audio graph is only built once levelling is turned
// on, and only from a click or key press (browsers keep audio made
// without one silent).
const LEVEL_TARGET = -16;            // LUFS every track is brought to
const MAX_BOOST_DB = 9;
const MAX_CUT_DB = -15;
const HEADROOM = 0.89;                // about -1 dB below full scale
// Browsers decode a whole file into memory to measure it, so only songs
// of ordinary length are measured; longer files need tags to be levelled.
const MEASURE_MAX_MS = 12 * 60 * 1000;
const MEASURE_MAX_BYTES = 60e6;
const L = {
  on: store("level") === "1",
  ctx: null,
  master: null,
  analyser: null,
  gains: [null, null],
  known: new Map(),      // track id -> {lufs, peak}, or null while unknown
  pending: new Map(),    // track id -> promise of the lookup
  measureQueue: [],
  measuring: false,
};

function ensureGraph() {
  if (!L.on) return;
  if (!L.ctx) {
    try {
      L.ctx = new AudioContext();
    } catch {
      return;
    }
    L.ctx.addEventListener("statechange", connectGraph);
  }
  if (L.ctx.state !== "running") L.ctx.resume().catch(() => {});
  connectGraph();
}

// The decks are only routed through the audio graph once it is running:
// routed into a context the browser holds suspended, they'd be silent.
function connectGraph() {
  const ctx = L.ctx;
  if (L.master) {
    if (ctx.state === "suspended" && S.mode === "track" && !audio.paused) ctx.resume().catch(() => {});
    return;
  }
  if (ctx.state !== "running") return;
  try {
    const master = ctx.createGain();
    master.connect(ctx.destination);
    L.analyser = ctx.createAnalyser();  // for the sound wave
    L.analyser.fftSize = 2048;
    master.connect(L.analyser);
    decks.forEach((d, i) => {
      const gain = ctx.createGain();
      ctx.createMediaElementSource(d).connect(gain).connect(master);
      L.gains[i] = gain;
    });
    L.master = master;
  } catch {
    return;
  }
  applyVolume();
  decks.forEach((d, i) => setDeckGain(i, d.dataset.trackId, false));
}

// ---------- sound wave ---------------------------------------------------

// A line beside the song title that moves with the sound. It listens to
// the radio relay's player (in an audio context of its own) and, with
// volume levelling on, to the library's audio graph; anything else, it
// rests flat.
const WAVE = { ctx: null, radio: null, buf: new Float32Array(2048), raf: 0, scale: 1 };
const calm = matchMedia("(prefers-reduced-motion: reduce)");

// Route the relay's player through the analyser, once the context runs
// (routed into a context the browser holds suspended, it would be silent).
function waveListen() {
  if (!WAVE.ctx) {
    try {
      WAVE.ctx = new AudioContext();
    } catch {
      return;
    }
    WAVE.ctx.addEventListener("statechange", routeRadio);
  }
  if (WAVE.ctx.state !== "running") WAVE.ctx.resume().catch(() => {});
  routeRadio();
}

function routeRadio() {
  const ctx = WAVE.ctx;
  if (WAVE.radio || ctx.state !== "running") return;
  try {
    const an = ctx.createAnalyser();
    an.fftSize = 2048;
    ctx.createMediaElementSource(radioRelay).connect(an).connect(ctx.destination);
    WAVE.radio = an;
  } catch { /* plays as it is; the wave rests */ }
}

function waveSource() {
  if (audio.paused || S.mode === "idle") return null;
  if (S.mode === "radio") return audio === radioRelay ? WAVE.radio : null;
  return L.master ? L.analyser : null;
}

function drawWave() {
  WAVE.raf = 0;
  const cv = $("#wave");
  if (!cv) return;
  cv.hidden = S.mode === "idle";
  if (cv.hidden) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.strokeStyle = getComputedStyle(cv).color;
  g.lineWidth = 1.5 * dpr;
  g.lineJoin = "round";
  const an = calm.matches ? null : waveSource();
  g.globalAlpha = an ? 1 : 0.4;
  g.beginPath();
  if (an) {
    const buf = WAVE.buf;
    an.getFloatTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i]));
    // Quiet or loud, the line fills its space: the scale eases towards
    // the level, so it doesn't jump on every beat.
    const want = Math.min(12, 0.9 / Math.max(peak, 0.02));
    WAVE.scale += (want - WAVE.scale) * 0.08;
    for (let x = 0; x <= w; x += dpr) {
      const v = buf[Math.floor((x / w) * (buf.length - 1))] * WAVE.scale;
      const y = h / 2 - Math.max(-1, Math.min(1, v)) * (h / 2 - g.lineWidth);
      x ? g.lineTo(x, y) : g.moveTo(x, y);
    }
  } else {
    g.moveTo(0, h / 2);
    g.lineTo(w, h / 2);
  }
  g.stroke();
  if (an && !document.hidden) WAVE.raf = requestAnimationFrame(drawWave);
}

function waveWake() {
  if (!WAVE.raf) WAVE.raf = requestAnimationFrame(drawWave);
}
document.addEventListener("visibilitychange", waveWake);
calm.addEventListener("change", waveWake);

function setLevelling(on) {
  L.on = on;
  store("level", on ? "1" : "0");
  if (on) ensureGraph();
  decks.forEach((d, i) => setDeckGain(i, d.dataset.trackId, true));
  if (on && S.mode === "track") {
    lookupLoudness(current());
    preloadNext();
  }
}

// The gain that brings a track to LEVEL_TARGET without clipping it.
function levelGain(trackId) {
  const l = L.on && trackId ? L.known.get(trackId) : null;
  if (!l || typeof l.lufs !== "number") return 1;
  const db = Math.max(MAX_CUT_DB, Math.min(MAX_BOOST_DB, LEVEL_TARGET - l.lufs));
  const gain = 10 ** (db / 20);
  if (gain <= 1) return gain;
  // A quiet track is only raised as far as its loudest peak allows (with
  // a little headroom: peaks measured at a low sample rate read low).
  // Without a known peak it is raised by 1 dB at most.
  const ceiling = l.peak ? Math.max(1, HEADROOM / l.peak) : 10 ** (1 / 20);
  return Math.min(gain, ceiling);
}

// `smooth`: true eases into the new level, a number is the easing time.
function setDeckGain(i, trackId, smooth) {
  const node = L.gains[i];
  if (!node || !L.master) return;
  const value = levelGain(trackId) * fadeMul[i];
  const now = L.ctx.currentTime;
  const ease = smooth === true ? 0.4 : smooth || 0;
  node.gain.cancelScheduledValues(now);
  if (ease) node.gain.setTargetAtTime(value, now, ease);
  else node.gain.setValueAtTime(value, now);
}

// Loudness from the player's server (tags, or an earlier measurement);
// unknown tracks are measured in the background.
function lookupLoudness(track) {
  if (!L.on || !track || !track.available) return Promise.resolve();
  if (L.known.has(track.id)) return Promise.resolve();
  if (L.pending.has(track.id)) return L.pending.get(track.id);
  const job = (async () => {
    try {
      const found = await api(`/api/loudness/${track.id}`);
      if (typeof found.lufs === "number") {
        loudnessKnown(track.id, found);
      } else if (track.duration_ms && track.duration_ms <= MEASURE_MAX_MS) {
        L.measureQueue.push(track);
        runMeasurements();
      } else {
        L.known.set(track.id, null);  // can't be measured: plays as it is
      }
    } catch { /* levelling is best effort */ } finally {
      L.pending.delete(track.id);
    }
  })();
  L.pending.set(track.id, job);
  return job;
}

function loudnessKnown(id, l) {
  L.known.set(id, { lufs: l.lufs, peak: l.peak || null });
  decks.forEach((d, i) => { if (d.dataset.trackId === id) setDeckGain(i, id, true); });
}

async function runMeasurements() {
  if (L.measuring) return;
  L.measuring = true;
  try {
    while (L.measureQueue.length && L.on) {
      const track = L.measureQueue.shift();
      if (L.known.has(track.id)) continue;
      try {
        const result = await measureTrack(track);
        if (result) {
          loudnessKnown(track.id, result);
          api("/api/loudness", { id: track.id, lufs: result.lufs, peak: result.peak }, { quiet: true }).catch(() => {});
        } else {
          L.known.set(track.id, null);  // silent or too short: don't try again
        }
      } catch {
        L.known.set(track.id, null);  // undecodable: plays unlevelled
      }
    }
  } finally {
    L.measuring = false;
  }
}

async function measureTrack(track) {
  const res = await fetch(`/audio/${track.id}`);
  if (!res.ok) return null;
  if (Number(res.headers.get("Content-Length")) > MEASURE_MAX_BYTES) return null;
  const bytes = await res.arrayBuffer();
  // Decoded at a low sample rate: plenty for loudness, a fraction of the memory.
  const decoded = await new OfflineAudioContext(1, 1, 22050).decodeAudioData(bytes);
  return integratedLoudness(decoded);
}

// Biquad coefficients of the ITU-R BS.1770 "K" weighting at rate `fs`
// (as in libebur128): a high shelf, then a high-pass.
function kWeighting(fs) {
  let f0 = 1681.974450955533;
  const G = 3.999843853973347;
  let Q = 0.7071752369554196;
  let K = Math.tan(Math.PI * f0 / fs);
  const Vh = 10 ** (G / 20);
  const Vb = Vh ** 0.4996667741545416;
  let a0 = 1 + K / Q + K * K;
  const shelf = {
    b: [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0],
    a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0],
  };
  f0 = 38.13547087602444;
  Q = 0.5003270373238773;
  K = Math.tan(Math.PI * f0 / fs);
  a0 = 1 + K / Q + K * K;
  const highpass = { b: [1, -2, 1], a: [2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0] };
  return [shelf, highpass];
}

// Gated integrated loudness (LUFS) and sample peak of an AudioBuffer.
async function integratedLoudness(buffer) {
  const fs = buffer.sampleRate;
  const hop = Math.round(fs / 10);           // 100 ms
  const bins = Math.floor(buffer.length / hop);
  if (bins < 4) return null;
  const power = new Float64Array(bins);
  let peak = 0;
  const [f1, f2] = kWeighting(fs);
  const channels = Math.min(buffer.numberOfChannels, 2);
  for (let c = 0; c < channels; c++) {
    const x = buffer.getChannelData(c);
    let x1 = 0, x2 = 0, y1 = 0, y2 = 0, z1 = 0, z2 = 0;
    const end = bins * hop;
    for (let start = 0; start < end; start += hop * 600) {  // a minute at a time
      const stop = Math.min(end, start + hop * 600);
      for (let i = start; i < stop; i++) {
        const v = x[i];
        const a = v < 0 ? -v : v;
        if (a > peak) peak = a;
        const y = f1.b[0] * v + f1.b[1] * x1 + f1.b[2] * x2 - f1.a[0] * y1 - f1.a[1] * y2;
        x2 = x1; x1 = v;
        const z = f2.b[0] * y + f2.b[1] * y1 + f2.b[2] * y2 - f2.a[0] * z1 - f2.a[1] * z2;
        y2 = y1; y1 = y;
        z2 = z1; z1 = z;
        power[(i / hop) | 0] += z * z;
      }
      await new Promise((r) => setTimeout(r, 0));  // keep the page responsive
    }
  }
  // 400 ms blocks overlapping by 75%, gated at -70 LUFS, then at 10 LU
  // below the loudness of what passed.
  const blocks = [];
  for (let j = 0; j + 4 <= bins; j++) {
    blocks.push((power[j] + power[j + 1] + power[j + 2] + power[j + 3]) / (4 * hop));
  }
  const lufs = (z) => -0.691 + 10 * Math.log10(z);
  const mean = (list) => list.reduce((s, z) => s + z, 0) / list.length;
  const loud = blocks.filter((z) => z > 0 && lufs(z) > -70);
  if (!loud.length) return null;
  const gate = lufs(mean(loud)) - 10;
  const kept = loud.filter((z) => lufs(z) > gate);
  const value = lufs(mean(kept));
  if (!isFinite(value)) return null;
  return { lufs: Math.round(value * 100) / 100, peak: peak > 0 ? Math.round(peak * 1e6) / 1e6 : null };
}

// ---------- playback ----------------------------------------------------

function buildOrder(n, startIndex) {
  const order = [...Array(n).keys()];
  if (!S.shuffle) return { order, pos: startIndex ?? 0 };
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  if (startIndex === null || startIndex === undefined) return { order, pos: 0 };
  order.splice(order.indexOf(startIndex), 1);
  order.unshift(startIndex);
  return { order, pos: 0 };
}

const tagTracks = (tracks, source) => tracks.map((t, i) => ({ ...t, _index: i, _source: source }));

function playPlaylist(pl, startIndex) {
  S.queue = tagTracks(pl.tracks, pl.id);
  S.queueSource = pl.id;
  S.queueLabel = pl.search ? "Search" : pl.name;
  const { order, pos } = buildOrder(S.queue.length, startIndex);
  S.order = order;
  S.pos = pos - 1;
  S.extra = null;
  S.mode = "track";
  S.station = null;
  step(1, { userInitiated: true, skipQueue: true });
  saveQueue();
}

// "Play next" puts a track at the front of the queue, "Add to queue" at
// the back. With nothing from the library playing, it starts now.
function queueTrack(track, pl, where) {
  if (!track.available) return;
  const copy = { ...track, _index: pl.tracks.indexOf(track), _source: pl.id };
  // With nothing from the library playing, the chosen track starts now
  // (ahead of anything left in the queue).
  if (where === "next" || S.mode !== "track") S.upNext.unshift(copy);
  else S.upNext.push(copy);
  if (S.mode !== "track") {
    S.queue = [];
    S.order = [];
    S.pos = -1;
    S.queueLabel = "your queue";
    S.mode = "track";
    S.station = null;
    step(1, { userInitiated: true });
    saveQueue();
    announce(`Playing ${track.name}`);
    return;
  }
  announce(where === "next" ? `${track.name} plays next` : `${track.name} added to the queue`);
  queueChanged();
}

// Several tracks, in their order: "next" puts them all ahead of the
// rest of the queue; "end" after it.
function queueTracks(tracks, pl, where) {
  const ok = tracks.filter((t) => t.available);
  if (!ok.length) return;
  if (S.mode !== "track") {
    // Nothing from the library playing: the first starts now, the rest
    // straight after it (ahead of anything left in the queue).
    queueTrack(ok[0], pl, "next");
    S.upNext.unshift(...ok.slice(1).map((t) => ({ ...t, _index: pl.tracks.indexOf(t), _source: pl.id })));
    if (ok.length > 1) announce(`Playing ${ok[0].name}, ${plural(ok.length - 1, "more track")} next`);
    queueChanged();
    return;
  } else {
    const copies = ok.map((t) => ({ ...t, _index: pl.tracks.indexOf(t), _source: pl.id }));
    if (where === "next") S.upNext.unshift(...copies);
    else S.upNext.push(...copies);
  }
  announce(where === "next" ? `${plural(ok.length, "track")} play next` : `${plural(ok.length, "track")} added to the queue`);
  queueChanged();
}

// Where a move goes, without making it: {stop}, {same} (replay), or the
// new {pos} / {fromQueue} and the track that would play.
function nextMove(dir, { userInitiated = false, afterError = false, skipQueue = false } = {}) {
  if (S.repeat === "one" && !userInitiated && !afterError && current()) {
    return { same: true, track: current() };
  }
  if (dir > 0 && !skipQueue) {
    const queued = S.upNext.findIndex((t) => t.available);
    if (queued >= 0) return { fromQueue: queued, track: S.upNext[queued] };
  }
  if (dir < 0 && S.extra) {
    // Back from a queued track to the playlist track before it (or the
    // start of this one when it came first).
    return S.pos >= 0 ? { pos: S.pos, track: S.queue[S.order[S.pos]] } : { same: true, track: S.extra };
  }
  const n = S.order.length;
  let next = S.pos;
  for (let tries = 0; tries < n; tries++) {
    next += dir;
    if (next >= n || next < 0) {
      if (S.repeat !== "all" && !userInitiated) return { stop: true };
      next = (next + n) % n;
    }
    const t = S.queue[S.order[next]];
    if (t.available) return { pos: next, track: t };
  }
  return { stop: true };
}

function applyMove(m) {
  if (m.same) return;
  if (m.fromQueue !== undefined) {
    S.upNext.splice(0, m.fromQueue);  // drop queued tracks that went missing
    S.extra = S.upNext.shift();
  } else {
    S.extra = null;
    S.pos = m.pos;
  }
}

// Move through the queue, skipping tracks whose file is missing.
// `afterError` moves on from a track that failed to load, even when
// repeat-one is on (replaying a broken file would stall playback).
function step(dir, opts = {}) {
  if (S.mode !== "track") return;
  ensureGraph();
  const m = nextMove(dir, opts);
  if (m.stop) return stopAtEnd();
  if (m.same) {
    endFade();
    audio.currentTime = 0;
    audio.play().catch(() => {});
    return;
  }
  applyMove(m);
  loadTrack(current());
}

function stopAtEnd() {
  cancelHandoff();
  endFade();
  audio.pause();
  S.pos = -1;
  S.extra = null;
  S.mode = "idle";
  for (const d of decks) unload(d);
  updateNow();
  markPlaying();
  queueChanged();
}

function unload(d) {
  d.pause();
  if (d.hasAttribute("src")) {
    d.removeAttribute("src");
    d.load();
  }
  delete d.dataset.trackId;
}

function stopRadio() {
  for (const r of radios) if (r.hasAttribute("src")) unload(r);
}

// Start `track` on a deck: the other deck when it already holds the
// track (lined up by preloadNext), else this one.
function loadTrack(track, { startAt = 0, autoplay = true } = {}) {
  cancelHandoff();
  endFade();
  stopRadio();
  const pre = otherDeck();
  if (pre.dataset.trackId === track.id && !pre.error) deckIdx = 1 - deckIdx;
  audio = decks[deckIdx];
  for (const d of decks) if (d !== audio) unload(d);
  if (audio.dataset.trackId !== track.id || audio.error) {
    audio.src = `/audio/${track.id}`;
    audio.dataset.trackId = track.id;
  }
  S.resumeAt = startAt || null;
  if (startAt) {
    const deck = audio;
    const seekTo = () => {
      if (deck === audio && S.resumeAt === startAt && startAt < deck.duration - 1) deck.currentTime = startAt;
      S.resumeAt = null;
    };
    if (deck.readyState >= 1) seekTo();
    else deck.addEventListener("loadedmetadata", seekTo, { once: true });
  } else if (audio.currentTime) {
    audio.currentTime = 0;
  }
  setDeckGain(deckIdx, track.id, false);
  lookupLoudness(track);
  if (autoplay) audio.play().catch(() => {});
  updateNow();
  markPlaying();
  syncButtons();
  queueChanged();
}

// Line up the track that comes next on the idle deck, so it starts
// without a gap. Called whenever the queue or the playing track changes.
function preloadNext() {
  if (S.mode !== "track") return;
  const pre = otherDeck();
  if (!pre.paused && pre.dataset.trackId) return;  // still finishing the last track
  if (!current()) { unload(pre); return; }
  const m = nextMove(1);
  // Repeat-one restarts this deck when the track ends.
  if (m.stop || m.same) { unload(pre); return; }
  lookupLoudness(m.track);
  if (pre.dataset.trackId === m.track.id && !pre.error) return;
  pre.src = `/audio/${m.track.id}`;
  pre.dataset.trackId = m.track.id;
  setDeckGain(1 - deckIdx, m.track.id, false);
}

// Just before a track ends, start the lined-up one. A timer checks a few
// times a second; in the last quarter second it sets a precise one for
// the moment the track ends (less the time a player takes to start). If
// the next track isn't ready, the "ended" event moves on as before.
const START_LATENCY = 0.012;
let handoffTimer = null;

function nextIsReady() {
  if (SLEEP.track) return null;  // the sleep timer stops at the end of this one
  const pre = otherDeck();
  const m = nextMove(1);
  if (m.stop || pre.dataset.trackId !== m.track.id || pre.error || pre.readyState < 3) return null;
  return m;
}

function watchForEnd() {
  if (handoffTimer || X.timer || S.mode !== "track" || audio.paused || !audio.src) return;
  const left = audio.duration - audio.currentTime;
  if (!isFinite(left) || left > Math.max(X.secs, 0.25)) return;
  const m = nextIsReady();
  if (!m) return;
  const fade = crossfadeLength(m);
  if (fade) {
    if (left > fade) return;
    if (left > 0.5) { handoff(left); return; }
  }
  if (left > 0.25) return;
  const deck = audio;
  handoffTimer = setTimeout(() => {
    handoffTimer = null;
    // A seek, pause or track change since means it isn't time yet (the
    // watcher sets a new timer when it is).
    const stillEnding = deck.duration - deck.currentTime <= START_LATENCY + 0.03;
    if (deck === audio && !deck.paused && stillEnding) handoff();
  }, Math.max(0, (left - START_LATENCY) * 1000));
}
setInterval(watchForEnd, 50);

function cancelHandoff() {
  clearTimeout(handoffTimer);
  handoffTimer = null;
}

// Start the lined-up track: at once (gapless), or fading in over `fade`
// seconds while the last one fades out.
function handoff(fade = 0) {
  const m = nextIsReady();
  if (!m) return;
  const pre = otherDeck();
  applyMove(m);
  const old = audio;
  const oldIdx = deckIdx;
  deckIdx = 1 - deckIdx;
  audio = pre;
  if (audio.currentTime) audio.currentTime = 0;
  if (fade) {
    startFade(oldIdx, deckIdx, fade);
  } else {
    // The old deck plays out its last few milliseconds, then stops for
    // sure, even if its duration was misjudged.
    setTimeout(() => { if (old !== audio && !old.paused) { old.pause(); preloadNext(); } }, 250);
  }
  setDeckGain(deckIdx, m.track.id, false);
  audio.play().catch(() => {});
  updateNow();
  markPlaying();
  queueChanged();
}

// ---------- crossfade ----------------------------------------------------

// With crossfade on, the next track starts a few seconds before this one
// ends: one fades in as the other fades out. Tracks that run into each
// other on an album, short ones and repeat-one stay gapless, and so does
// skipping with Next.
const MAX_CROSSFADE = 12;
const X = {
  secs: Math.max(0, Math.min(MAX_CROSSFADE, Number(store("crossfade")) || 0)),
  timer: null, from: 0, to: 1, start: 0, length: 0,
};
const fadeMul = [1, 1];  // each deck's share of the volume during a fade

function crossfadeLength(m) {
  if (!X.secs || S.repeat === "one" || !m || m.same) return 0;
  const cur = current();
  const pre = otherDeck();
  if (!(audio.duration >= X.secs * 2) || !(pre.duration >= X.secs * 2)) return 0;
  if (cur && cur.album && cur.album === m.track.album && (cur.artist || "") === (m.track.artist || "")) return 0;
  return X.secs;
}

function startFade(from, to, secs) {
  endFade();
  Object.assign(X, { from, to, start: performance.now(), length: secs * 1000 });
  fadeMul[to] = 0;
  fadeMul[from] = 1;
  applyFade();
  X.timer = setInterval(stepFade, 50);
}

function stepFade() {
  const t = Math.min(1, (performance.now() - X.start) / X.length);
  // Equal-power curves keep the overall loudness steady through the fade.
  fadeMul[X.to] = Math.sin(t * Math.PI / 2);
  fadeMul[X.from] = Math.cos(t * Math.PI / 2);
  applyFade();
  if (t >= 1) endFade();
}

// Finish a fade at once: the old track stops, the new one plays at full
// volume. Pausing, seeking or changing track mid-fade does this.
function endFade() {
  if (X.timer === null) return;
  clearInterval(X.timer);
  X.timer = null;
  const old = decks[X.from];
  if (old !== audio && !old.paused) old.pause();
  fadeMul[0] = fadeMul[1] = 1;
  applyFade();
  preloadNext();
}

function applyFade() {
  const v = playVolume();
  decks.forEach((d, i) => {
    if (L.master) setDeckGain(i, d.dataset.trackId, 0.03);
    else d.volume = v * fadeMul[i];
  });
}

function setCrossfade(secs) {
  X.secs = Math.max(0, Math.min(MAX_CROSSFADE, secs));
  store("crossfade", String(X.secs));
}

// Radio plays through the player's relay, which reads the song titles
// stations send. If the relay can't handle a station, the browser plays
// the station's own URL instead (no song titles then).
function playStation(st, { via = "relay", autoplay = true } = {}) {
  if (SLEEP.track) setSleep(0);  // "after this track" doesn't apply to a station
  const sameStation = S.mode === "radio" && S.station && S.station.url === st.url;
  cancelHandoff();
  endFade();
  S.mode = "radio";
  S.station = st;
  S.pos = -1;
  S.extra = null;
  for (const d of decks) unload(d);
  // A finder result (not saved yet) relays too, for its song titles and
  // so HLS stations play at all.
  S.radioSrc = via !== "relay" ? st.url : st.id ? `/radio/${st.id}`
    : st.preview ? `/radio/preview?u=${encodeURIComponent(st.url)}` : st.url;
  const player = S.radioSrc.startsWith("/") ? radioRelay : radioDirect;
  if (player !== radioEl) unload(radioEl);
  radioEl = audio = player;
  if (player === radioRelay) waveListen();
  if (!sameStation || S.radioSrc === st.url) S.radioTitle = null;
  if (autoplay) {
    audio.src = S.radioSrc;
    audio.play().catch(() => {});
    if (!sameStation || S.notedUrl !== st.url) notePlayed(st);
  }
  updateNow();
  markPlaying();
  syncRadioCards();
  syncButtons();
  pollTitle();
  saveQueue();
  if (st.feed) refreshWhatsOn();
}

let titleTimer = null;
let titleToken = 0;

async function pollTitle() {
  clearTimeout(titleTimer);
  const token = ++titleToken;
  if (S.mode !== "radio" || !S.radioSrc.startsWith("/radio/") || audio.paused) {
    if (S.mode !== "radio" || !S.radioSrc.startsWith("/radio/")) setRadioTitle(null);
    return;
  }
  try {
    const data = await api(S.station.id ? `/api/nowplaying?station=${S.station.id}`
      : `/api/nowplaying?u=${encodeURIComponent(S.station.url)}`);
    if (token === titleToken && S.mode === "radio") setRadioTitle(data.title || null);
  } catch { /* keep the last title */ }
  // Paused radio isn't playing anything; polling restarts on "play".
  if (token === titleToken && !audio.paused) titleTimer = setTimeout(pollTitle, 5000);
}

function setRadioTitle(title) {
  if (title === S.radioTitle) return;
  S.radioTitle = title;
  if (S.mode === "radio") {
    updateNow();
    syncRadioCards();
    if (title && !audio.paused) announce(`Now playing: ${title}`);
  }
}

function togglePlay() {
  if (S.mode === "idle") {
    if ((S.view.type === "playlist" || S.view.type === "search") && S.detail) playPlaylist(S.detail, S.shuffle ? null : 0);
    return;
  }
  if (audio.paused) {
    // A paused live stream has fallen behind; reconnect to go live again.
    if (S.mode === "radio") {
      audio.src = S.radioSrc;
      if (S.notedUrl !== S.station.url) notePlayed(S.station);  // e.g. resumed after a restart
    }
    else ensureGraph();
    audio.play().catch(() => {});
  } else {
    pauseAll();
  }
}

// Pausing also stops a deck still playing out the end of the last track.
function pauseAll() {
  endFade();
  for (const d of [...decks, ...radios]) if (!d.paused) d.pause();
}

function previous() {
  if (S.mode !== "track") return;
  if (audio.currentTime > 3) { endFade(); audio.currentTime = 0; }
  else step(-1, { userInitiated: true });
}

function setArt(src) {
  const img = $("#now-art");
  if (!src) {
    img.hidden = true;
    img.removeAttribute("src");
    return;
  }
  if (img.getAttribute("src") !== src) {
    img.hidden = true;  // shown once it loads; most tracks may have none
    img.src = src;
  }
}

// In its own app window the system already shows "Station Player"
// before the title, so it isn't repeated there.
function appTitle(what) {
  const ownWindow = NATIVE || matchMedia("(display-mode: standalone), (display-mode: minimal-ui)").matches;
  return ownWindow ? what : `${what} · Station Player`;
}

function updateNow() {
  const title = $("#now-title");
  const sub = $("#now-sub");
  const t = current();
  if (S.mode === "radio") {
    title.replaceChildren(el("span", { class: "live-badge" }, "LIVE"), S.station.name);
    const w = S.station.id && WHATSON[S.station.id];
    const next = w?.state === "on" && w.next[0];
    const on = S.radioTitle || (w?.state === "on" ? whatText(w.now) : w?.state === "live" ? `Live: ${w.now.live}` : null);
    sub.textContent = [on || "Internet radio", next ? `Next ${hhmm(new Date(next.at))}: ${whatText(next)}` : null].filter(Boolean).join("  ·  ");
    document.title = appTitle(S.radioTitle || S.station.name);
  } else if (S.mode === "track" && t) {
    title.textContent = t.name;
    sub.textContent = [t.artist, S.extra ? "from your queue" : S.queueLabel].filter(Boolean).join(" · ");
    document.title = appTitle(t.name);
  } else {
    title.textContent = "Nothing playing";
    sub.textContent = "";
    document.title = "Station Player";
  }
  $("#seek").disabled = S.mode !== "track";
  if (S.mode !== "track") { $("#seek").value = 0; fillRange($("#seek")); }
  // Live radio has nowhere to seek to (kept in place so the bar doesn't jump).
  $(".bar .seek").classList.toggle("off", S.mode !== "track");
  const art = S.mode === "track" && t ? `/art/${t.id}` : S.mode === "radio" ? logoSrc(S.station) : null;
  $("#now-art").classList.toggle("logo-art", S.mode === "radio");
  setArt(art);
  if (!("mediaSession" in navigator)) return;
  if (S.mode === "idle") { navigator.mediaSession.metadata = null; return; }
  const artwork = art ? [{ src: new URL(art, location.href).href }] : [];
  navigator.mediaSession.metadata = new MediaMetadata(S.mode === "radio"
    ? { title: S.radioTitle || S.station.name, artist: S.radioTitle ? S.station.name : "Live radio", artwork }
    : { title: t.name, artist: t.artist || "", album: t.album || S.queueLabel || "", artwork });
}

function syncButtons() {
  const playing = !audio.paused;
  const btn = $("#btn-play");
  btn.replaceChildren(icon(playing ? "pause" : "play"));
  btn.title = playing ? "Pause" : "Play";
  btn.setAttribute("aria-label", btn.title);
  $("#btn-shuffle").setAttribute("aria-pressed", String(S.shuffle));
  const rep = $("#btn-repeat");
  rep.setAttribute("aria-pressed", String(S.repeat !== "off"));
  rep.replaceChildren(icon(S.repeat === "one" ? "repeat1" : "repeat"));
  rep.title = { off: "Repeat: off", all: "Repeat: all", one: "Repeat: this track" }[S.repeat];
  rep.setAttribute("aria-label", rep.title);
}

// ---------- remembering where you were -------------------------------------

// The queue is saved when it changes; the position every few seconds.
function saveQueue() {
  const radio = S.mode === "radio" && S.station;
  store("queue", JSON.stringify(radio
    ? { v: 1, mode: "radio", station: S.station.url }
    : {
        v: 1, mode: "track", ids: S.queue.map((t) => t.id), order: S.order, source: S.queueSource,
        label: S.queueLabel, upNext: S.upNext.map((t) => [t.id, t._source, t._index]),
      }));
}

let lastPosSave = 0;
function saveSession(force = true) {
  const now = Date.now();
  if (!force && now - lastPosSave < 5000) return;
  lastPosSave = now;
  const t = S.mode === "track" ? current() : null;
  store("position", JSON.stringify(t
    ? {
        pos: S.pos, extra: S.extra ? [S.extra.id, S.extra._source, S.extra._index] : null,
        time: Math.round((S.resumeAt ?? audio.currentTime ?? 0) * 10) / 10,
      }
    : { pos: -1, extra: null, time: 0 }));
  if (S.mode !== "track" || force) saveQueue();
}

// Put back the queue and track (paused, at the same point) or station
// that was playing when the player was last open.
async function restoreSession() {
  if (S.mode !== "idle") return;  // a desktop icon's station is playing already
  const q = readJSON("queue");
  const p = readJSON("position") || {};
  if (!q || q.v !== 1) return;
  if (q.mode === "radio") {
    const recent = (S.lib.recent_stations || []).find((s) => s.url === q.station);
    const st = S.lib.stations.find((s) => s.url === q.station) || (recent && { ...recent, preview: true });
    if (st) playStation(st, { autoplay: false });
    return;
  }
  if (!Array.isArray(q.ids) || !Array.isArray(q.order)) return;
  const queued = Array.isArray(q.upNext) ? q.upNext.filter(Array.isArray) : [];
  const extra = Array.isArray(p.extra) ? p.extra : null;
  const wanted = [...new Set([...q.ids, ...queued.map((u) => u[0]), ...(extra ? [extra[0]] : [])])];
  if (!wanted.length) return;
  let found;
  try { found = (await api("/api/tracks", { ids: wanted }, { quiet: true })).tracks; } catch { return; }
  if (S.mode !== "idle") return;  // the user started something meanwhile
  const byId = new Map(found.map((t) => [t.id, t]));
  // Tracks gone from the library since are dropped from the queue.
  const newIndex = [];
  const queue = [];
  q.ids.forEach((id, i) => {
    if (byId.has(id)) { newIndex[i] = queue.length; queue.push({ ...byId.get(id), _index: i, _source: q.source }); }
  });
  const order = q.order.filter((i) => newIndex[i] !== undefined).map((i) => newIndex[i]);
  S.queue = queue;
  S.order = order.length === queue.length ? order : [...queue.keys()];
  // The same place in the order; if that track has gone, the next one
  // still there, from its start.
  S.pos = -1;
  let time = Number(p.time) || 0;
  if (Number.isInteger(p.pos) && p.pos >= 0) {
    for (let k = p.pos; k < q.order.length && S.pos < 0; k++) {
      const ni = newIndex[q.order[k]];
      if (ni !== undefined) S.pos = S.order.indexOf(ni);
      if (S.pos < 0 || k > p.pos) time = 0;
    }
  }
  S.queueSource = q.source;
  S.queueLabel = typeof q.label === "string" ? q.label : "";
  S.upNext = queued.filter((u) => byId.has(u[0])).map(([id, source, index]) => ({ ...byId.get(id), _index: index, _source: source }));
  S.extra = extra && byId.has(extra[0]) ? { ...byId.get(extra[0]), _source: extra[1], _index: extra[2] } : null;
  const t = current();
  if (!t || !t.available) {
    S.extra = null;
    S.pos = -1;
    renderNav();
    return;
  }
  S.mode = "track";
  loadTrack(t, { startAt: S.extra ? Number(p.time) || 0 : time, autoplay: false });
}

// ---------- wiring ------------------------------------------------------

// Listen on every player, but only act on the one in use.
function onAudio(type, fn) {
  for (const a of [...decks, ...radios]) {
    a.addEventListener(type, (e) => { if (e.target === audio) fn(e); });
  }
}

for (const type of ["playing", "pause", "emptied"]) onAudio(type, waveWake);

let seeking = false;
onAudio("timeupdate", () => {
  if (S.mode !== "track") {
    $("#time-cur").textContent = S.mode === "radio" ? fmtTime(audio.currentTime) : "0:00";
    $("#time-dur").textContent = S.mode === "radio" ? "live" : "0:00";
    return;
  }
  $("#time-cur").textContent = fmtTime(audio.currentTime);
  $("#time-dur").textContent = fmtTime(audio.duration);
  if (!seeking && audio.duration) {
    $("#seek").value = Math.round((audio.currentTime / audio.duration) * 1000);
    fillRange($("#seek"));
    $("#seek").setAttribute("aria-valuetext", `${fmtTime(audio.currentTime)} of ${fmtTime(audio.duration)}`);
  }
  saveSession(false);
});
// A station that drops the connection is reconnected, a few times a
// minute at most, so a dead station doesn't loop forever.
function reconnectRadio() {
  const now = Date.now();
  S.reconnects = S.reconnects.filter((t) => now - t < 60000);
  if (S.reconnects.length >= 3) return false;
  S.reconnects.push(now);
  playStation(S.station, { via: S.radioSrc === S.station.url ? "direct" : "relay" });
  return true;
}

onAudio("ended", () => {
  if (S.mode !== "radio" && SLEEP.track) {
    // Sleep at the end of the track: line up the next one, paused.
    const m = nextMove(1);
    setSleep(0);
    announce("Sleep timer: stopped at the end of the track");
    if (m.stop) return stopAtEnd();
    if (!m.same) applyMove(m);
    return loadTrack(current(), { autoplay: false });
  }
  if (S.mode !== "radio") return step(1);
  if (!reconnectRadio()) $("#now-sub").textContent = "Lost the connection to this station";
});
// The deck that just handed over finishes in the background; once it
// has, it can line up the track after next.
for (const d of decks) {
  d.addEventListener("ended", () => { if (d !== audio) preloadNext(); });
  d.addEventListener("error", () => {
    if (d === audio || !d.dataset.trackId) return;
    // The last track failing as it fades out: just let it go.
    if (X.timer !== null && d === decks[X.from]) { endFade(); return; }
    // The lined-up track can't be played: skip it when its turn comes.
    for (const t of [...S.queue, ...S.upNext]) if (t.id === d.dataset.trackId) t.available = false;
    unload(d);
    preloadNext();
  });
}
onAudio("error", () => {
  if (S.mode === "track") {
    const t = current();
    if (t) t.available = false;
    step(1, { afterError: true });
  } else if (S.mode === "radio") {
    if (S.radioSrc !== S.station.url) {
      playStation(S.station, { via: "direct" });  // relay failed: try the station itself
      return;
    }
    audio.pause();
    $("#now-sub").textContent = "Couldn't connect to this stream";
  }
});
for (const ev of ["play", "pause", "playing"]) {
  onAudio(ev, () => {
    syncButtons();
    syncRadioCards();
  });
}
onAudio("pause", () => { cancelHandoff(); endFade(); saveSession(); });
onAudio("seeking", cancelHandoff);
onAudio("playing", () => { if (S.mode === "radio") pollTitle(); });
addEventListener("pagehide", () => saveSession());

$("#now-art").addEventListener("load", (e) => { e.target.hidden = false; });
$("#now-art").addEventListener("error", (e) => { e.target.hidden = true; });

async function quitApp() {
  const recording = activeRecordings().length > 0;
  if (!confirm(recording ? "Quit Station Player? Music and radio will stop, and so will recording."
    : "Quit Station Player? Music and radio will stop.")) return;
  saveSession();
  audio.pause();
  try {
    await api("/api/quit", {});
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  clearTimeout(titleTimer);
  clearTimeout(recTimer);
  titleToken++;  // a poll already in flight must not schedule another
  if (NATIVE) { tellApp("quit"); return; }  // the Mac app closes along with it
  const heading = el("h1", { tabindex: "-1" }, "Station Player has stopped");
  document.body.replaceChildren(el("main", { class: "stopped" },
    heading,
    el("p", {}, "You can close this tab. To start it again, open Station Player from its shortcut, or run station-player.")));
  document.title = "Station Player (stopped)";
  heading.focus();
}
$("#btn-quit").addEventListener("click", quitApp);

const seek = $("#seek");
seek.addEventListener("input", () => {
  seeking = true;
  if (audio.duration) $("#time-cur").textContent = fmtTime((seek.value / 1000) * audio.duration);
});
seek.addEventListener("change", () => {
  endFade();
  if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
  seeking = false;
});

// With levelling on, the decks run at full volume into the levelling
// gains and the slider sets the master gain.
const volume = $("#volume");
volume.value = store("volume") ?? 0.8;
fillRange(volume);
// The sleep timer fades the volume down at its end, the alarm up at its
// start; each has its own share, so one can't undo the other.
const volMul = { sleep: 1, alarm: 1 };
const playVolume = () => Number(volume.value) * volMul.sleep * volMul.alarm;
function applyVolume() {
  const v = playVolume();
  decks.forEach((d, i) => { d.volume = L.master ? 1 : v * fadeMul[i]; });
  for (const r of radios) r.volume = v;
  if (L.master) L.master.gain.value = v;
  ambientLevel();  // the sleep timer fades it too
}
applyVolume();
volume.addEventListener("input", () => {
  applyVolume();
  store("volume", volume.value);
});

$("#btn-play").addEventListener("click", togglePlay);
$("#btn-next").addEventListener("click", () => step(1, { userInitiated: true }));
$("#btn-prev").addEventListener("click", previous);
$("#btn-shuffle").addEventListener("click", () => {
  S.shuffle = !S.shuffle;
  store("shuffle", S.shuffle ? "1" : "0");
  if (S.mode === "track" && S.pos >= 0) {
    const idx = S.order[S.pos];
    const { order } = buildOrder(S.queue.length, idx);
    S.order = S.shuffle ? order : [...Array(S.queue.length).keys()];
    S.pos = S.order.indexOf(idx);
    saveQueue();
  }
  syncButtons();
  queueChanged();
});
$("#btn-repeat").addEventListener("click", () => {
  S.repeat = { off: "all", all: "one", one: "off" }[S.repeat];
  store("repeat", S.repeat);
  syncButtons();
  queueChanged();
});
for (const b of document.querySelectorAll(".nav-item[data-view]")) {
  b.addEventListener("click", () => show({ type: b.dataset.view }));
}
$("#btn-add-music").addEventListener("click", () => { show({ type: "welcome" }); focusHeading(); });
$("#btn-new-playlist").addEventListener("click", async () => {
  const pid = await newPlaylist([]);
  if (pid) { await show({ type: "playlist", id: pid }); focusHeading(); }
});

// Library search: results show as you type.
let searchTimer = null;
const libSearch = $("#lib-search");
libSearch.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = libSearch.value.trim();
  searchTimer = setTimeout(() => {
    if (q) show({ type: "search", q });
    else if (S.view.type === "search") show(readJSON("view") || { type: "settings" });
  }, 250);
});
libSearch.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    clearTimeout(searchTimer);
    const q = libSearch.value.trim();
    if (q) show({ type: "search", q }).then(() => {
      const row = $("#view tbody tr:not([hidden])");
      if (row) { row.tabIndex = 0; row.focus(); }
    });
  } else if (e.key === "Escape") {
    libSearch.value = "";
    libSearch.dispatchEvent(new Event("input"));
  }
});

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, button, select, #track-menu")) return;
  if (e.key === "/") { e.preventDefault(); libSearch.focus(); libSearch.select(); }
  else if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.code === "ArrowRight" && S.mode === "track") { endFade(); audio.currentTime += 10; }
  else if (e.code === "ArrowLeft" && S.mode === "track") { endFade(); audio.currentTime -= 10; }
});

if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", togglePlay);
  ms.setActionHandler("pause", pauseAll);
  ms.setActionHandler("nexttrack", () => step(1, { userInitiated: true }));
  ms.setActionHandler("previoustrack", previous);
}

// ---------- the Mac app's own window -----------------------------------------

// The downloaded Mac app shows the player in a window of its own
// (macwindow.py), which listens for a few words from the page and calls
// window.stationApp for its menus and Dock menu.
function tellApp(what) {
  if (NATIVE) window.webkit.messageHandlers.stationApp.postMessage(what);
}
window.stationApp = {
  // Before ⌘Q: keep the place to carry on from next time; the answer is
  // how many recordings Quit would stop.
  beforeQuit() {
    saveSession();
    flushMeter();
    return activeRecordings().length;
  },
  command(cmd) {
    if (launch.ready) runCommand({ cmd });
  },
  show(type) {
    if (launch.ready) show({ type });
  },
};

// Since 0.18 the Mac app has that window: a browser window left over from
// before (or opened by hand) offers it, once.
function offerMacWindow() {
  if (NATIVE || !S.lib || !S.lib.mac_app || S.lib.config.own_window === false || store("mac-window-offered")) return;
  store("mac-window-offered", "1");
  const box = $("#toasts");
  if (!box) return;
  const note = el("div", { class: "toast mac-window", role: "status" },
    el("span", {}, "Station Player now has a window of its own, with its own Dock icon. Open it, then close this browser window."),
    el("button", {
      type: "button", class: "btn small primary",
      onclick: async () => {
        note.remove();
        saveSession();
        audio.pause();  // carries on in the new window
        try { await api("/api/window", {}, { quiet: true }); } catch (err) { toast(err.message, { error: true }); }
      },
    }, "Open it"),
    el("button", { type: "button", class: "btn small", onclick: () => note.remove() }, "Not now"));
  box.append(note);
}

// ---------- installing as an app ------------------------------------------

// The browser offers to install the player as an app (see the App
// settings); it doesn't once it's installed.
let installPrompt = null;
addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  if (S.view.type === "settings") renderView();
});
// Opened from the Dock or taskbar while the player isn't running, the
// window shows a page that starts it (sw.js, offline.html). The Mac app
// starts the player itself.
if ("serviceWorker" in navigator && !NATIVE) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
addEventListener("appinstalled", () => {
  installPrompt = null;
  announce("Station Player is installed as an app");
  if (S.view.type === "settings") renderView();
});

// ---------- files opened with Station Player ----------------------------

// Files chosen with "Open With" in Finder / Explorer reach the server; the
// page checks for them and plays the first. Each request plays once, in
// whichever window gets to it first. The alarm going off, and commands
// from the phone remote, arrive the same way.
async function checkInbox({ fresh = 0 } = {}) {
  let box;
  try { box = await api("/api/inbox"); } catch { return; }
  for (const c of box.commands || []) {
    try { await runCommand(c); } catch { /* a playlist that's gone, say */ }
  }
  await alarmGoesOff(box.alarm, fresh);
  if (ALARM.seq !== null && String(box.ringing) !== String(ALARM.seq)) await alarmEndedElsewhere();
  const req = box.open;
  if (!req || String(req.seq) === store("opened-seq")) return;
  if (fresh && req.age > fresh) { store("opened-seq", String(req.seq)); return; }
  store("opened-seq", String(req.seq));
  try { await loadState(); } catch { return; }
  await show({ type: "playlist", id: req.playlist });
  if (S.view.id !== req.playlist || !S.detail) return;
  const i = S.detail.tracks.findIndex((t) => t.id === req.track);
  if (i >= 0) playPlaylist(S.detail, i);
}

async function playSource(kind, id) {
  if (kind === "station") {
    const st = (S.lib.stations || []).find((x) => x.id === id);
    if (st) playStation(st);
    return Boolean(st);
  }
  await show({ type: "playlist", id });
  if (S.view.id !== id || !S.detail || !S.detail.tracks.length) return false;
  playPlaylist(S.detail, S.shuffle ? null : 0);
  return true;
}

// ---------- the Dock / taskbar menu --------------------------------------
// The installed app's icon offers Play / Pause and Next (the manifest's
// shortcuts). They open /?do=…, which the window that's open receives
// (launch_handler: focus-existing), or a new window acts on once it's up.
const LAUNCH_COMMANDS = { toggle: "toggle", next: "next" };
const launch = { ready: false, todo: new URLSearchParams(location.search).get("do") };
if (launch.todo) history.replaceState(null, "", "/");  // a reload doesn't do it again
if ("launchQueue" in window) {
  window.launchQueue.setConsumer((params) => {
    if (!params.targetURL) return;
    const todo = new URL(params.targetURL).searchParams.get("do");
    if (!todo) return;
    if (launch.ready) runLaunch(todo); else launch.todo = todo;
  });
}
async function runLaunch(todo) {
  launch.todo = null;
  const cmd = LAUNCH_COMMANDS[todo];
  if (cmd) await runCommand({ cmd });
}

// ---------- the play log ------------------------------------------------------

// Tracks played are logged on the computer (for PRS / PPL returns): how
// long each was listened to is counted here, four times a second, and sent
// when the next one starts or the window closes.
const meter = { id: null, started: 0, secs: 0, last: 0, lastPos: 0 };
function flushMeter() {
  if (meter.id && meter.secs >= 1 && S.lib && S.lib.config.play_log !== false) {
    fetch("/api/played", {
      method: "POST", keepalive: true,
      headers: { "Content-Type": "application/json", "X-Station-Player": "1" },
      body: JSON.stringify({ id: meter.id, started: meter.started, seconds: Math.round(meter.secs) }),
    }).catch(() => {});
  }
  meter.id = null;
}
setInterval(() => {
  const now = Date.now();
  const t = S.mode === "track" ? current() : null;
  if (t && !audio.paused) {
    const again = meter.id === t.id && audio.currentTime < 3 && audio.currentTime + 2 < meter.lastPos;
    if (meter.id !== t.id || again) {
      flushMeter();
      Object.assign(meter, { id: t.id, started: now, secs: 0 });
    } else {
      meter.secs += Math.min(1, (now - meter.last) / 1000);
    }
    meter.lastPos = audio.currentTime;
  } else if (meter.id && (!t || t.id !== meter.id)) {
    flushMeter();
  }
  meter.last = now;
}, 250);
addEventListener("pagehide", () => {
  flushMeter();
  // Tell the player this window has gone, so the alarm or the phone remote
  // opens a new one rather than waiting for it.
  fetch("/api/page-closed", {
    method: "POST", keepalive: true,
    headers: { "Content-Type": "application/json", "X-Station-Player": "1" }, body: "{}",
  }).catch(() => {});
});

// ---------- sleep timer ---------------------------------------------------

// Stop after a number of minutes (fading out over the last 20 seconds),
// or at the end of the track playing.
const SLEEP = { at: 0, track: false, timer: null };
const SLEEP_FADE = 20;
function setSleep(minutes) {
  clearInterval(SLEEP.timer);
  Object.assign(SLEEP, { at: 0, track: false, timer: null });
  if (minutes === "track") SLEEP.track = S.mode !== "radio";  // a station has no end
  else if (Number(minutes) > 0) {
    SLEEP.at = Date.now() + Number(minutes) * 60000;
    SLEEP.timer = setInterval(sleepTick, 250);
  }
  if (volMul.sleep !== 1) { volMul.sleep = 1; applyVolume(); }
  if (SLEEP.track) preloadNext();
  syncSleep();
}
function sleepLeft() {
  return SLEEP.track ? "track" : SLEEP.at ? Math.max(0, Math.round((SLEEP.at - Date.now()) / 1000)) : 0;
}
function sleepTick() {
  const left = (SLEEP.at - Date.now()) / 1000;
  if (left <= 0) {
    pauseAll();
    stopAmbientSound();
    setSleep(0);
    announce("Sleep timer: playback stopped");
    return;
  }
  const mul = Math.min(1, left / SLEEP_FADE);
  if (mul !== volMul.sleep) { volMul.sleep = mul; applyVolume(); }
  syncSleep();
}
function syncSleep() {
  const sel = $("#sleep");
  const left = sleepLeft();
  const value = left === "track" ? "track" : left ? "on" : "";
  let on = sel.querySelector('option[value="on"]');
  if (left && left !== "track") {
    if (!on) { on = el("option", { value: "on" }); sel.prepend(on); }
    const m = Math.ceil(left / 60);
    on.textContent = `Sleep in ${m} min`;
  } else if (on) {
    on.remove();
  }
  sel.value = value;
  sel.classList.toggle("on", Boolean(left));
}
$("#sleep").addEventListener("change", (e) => {
  const v = e.target.value;
  if (v === "on") return;
  setSleep(v === "track" ? "track" : Number(v));
  announce(v === "track" ? "Sleep at the end of this track" : Number(v) ? `Sleep in ${v} minutes` : "Sleep timer off");
});

// ---------- alarm ------------------------------------------------------------

// The server sets the alarm off at its time (opening a window if none is
// open); the page plays it, fading in over a minute.
const ALARM = { timer: null, seq: null };  // seq: the alarm ringing in this window
const ALARM_FADE = 60;
async function alarmGoesOff(req, fresh) {
  if (!req || String(req.seq) === store("alarm-seq")) return;
  store("alarm-seq", String(req.seq));  // unique across restarts of the player
  if (req.age > (fresh ? 120 : 300)) return;  // long past: the computer was asleep
  try { await loadState(); } catch { return; }
  // Already stopped or snoozed (from the phone, or another window): stay quiet.
  const ringing = S.lib.alarm_state && S.lib.alarm_state.ringing;
  if (!ringing || String(ringing.seq) !== String(req.seq)) return;
  setSleep(0);
  clearInterval(ALARM.timer);
  volMul.alarm = 0.02;
  applyVolume();
  const played = await playSource(req.source.kind, req.source.id).catch(() => false);
  if (!played) {
    volMul.alarm = 1;
    applyVolume();
    renderErrors(`The alarm couldn't play ${req.source.name || "its playlist"}: it isn't in the library any more.`);
    api("/api/alarm/stop", {}, { quiet: true }).catch(() => {});  // so the phone doesn't offer Snooze for nothing
    return;
  }
  announce(`Alarm: playing ${req.source.name || ""}`);
  ALARM.seq = req.seq;
  await leaveNight({ restoreFocus: false });  // the alarm screen can't show over a full-screen clock
  showAlarmScreen(req);
  tellApp("show");  // the Mac app's window may be closed (hidden) while the music plays
  const start = Date.now();
  ALARM.timer = setInterval(() => {
    volMul.alarm = Math.min(1, 0.02 + (Date.now() - start) / 1000 / ALARM_FADE);
    applyVolume();
    if (volMul.alarm >= 1) { clearInterval(ALARM.timer); ALARM.timer = null; }
  }, 250);
}

// A schedule switch: only when something is playing (and no alarm is
// ringing), and not when it's playing already.
async function scheduleSwitch(c) {
  if (S.mode === "idle" || audio.paused || ALARM.seq != null) return;
  const same = c.kind === "station" ? S.mode === "radio" && S.station && S.station.id === c.id
    : S.mode === "track" && S.queueSource === c.id;
  if (same) return;
  if (await playSource(c.kind, c.id).catch(() => false)) {
    toast(`Schedule: ${c.label ? `${c.label}, ` : ""}switched to ${c.name || "the next station"}`);
  } else {
    toast(`The schedule couldn't switch to ${c.name || "its station"}: it isn't in your stations any more.`, { error: true });
  }
}

// ---------- phone remote ------------------------------------------------------

// The phone's commands, picked up by checkInbox.
async function runCommand(c) {
  if (c.cmd === "toggle") togglePlay();
  else if (c.cmd === "next") step(1, { userInitiated: true });
  else if (c.cmd === "prev") previous();
  else if (c.cmd === "volume") {
    volume.value = c.value;
    fillRange(volume);
    applyVolume();
    store("volume", volume.value);
  } else if (c.cmd === "playlist" || c.cmd === "station") await playSource(c.cmd, c.id);
  else if (c.cmd === "notice") toast(c.text, { error: true });
  else if (c.cmd === "sleep") setSleep(c.minutes);
  else if (c.cmd === "switch") await scheduleSwitch(c);
  // snooze and stop_alarm: the server has acted on them already, and
  // checkInbox ends the alarm in whichever window is ringing.
  syncButtons();
  reportStatus();
}

// While the remote is on, the page tells the server what's playing.
let lastReport = "";
function reportStatus() {
  if (!S.lib || !S.lib.config.remote) return;
  const t = S.mode === "track" ? current() : null;
  const status = {
    mode: S.mode, playing: !audio.paused, volume: Number(volume.value), sleep_left: sleepLeft(),
    title: S.mode === "radio" ? (S.radioTitle || "") : t ? t.name : "",
    artist: t ? (t.artist || "") : "", album: t ? (t.album || "") : "",
    station: S.mode === "radio" && S.station ? S.station.name : "",
    track: t ? t.id : "", queue: t ? (S.queueLabel || "") : "",
    position: S.mode === "track" ? Math.round(audio.currentTime || 0) : 0,
    duration: S.mode === "track" && isFinite(audio.duration) ? Math.round(audio.duration) : 0,
  };
  const text = JSON.stringify(status);
  if (text === lastReport) return;
  lastReport = text;
  api("/api/remote/status", status, { quiet: true }).catch(() => { lastReport = ""; });
}
setInterval(reportStatus, 1000);

// ---------- boot --------------------------------------------------------

(async function boot() {
  syncButtons();
  updateNow();
  try {
    await loadState();
    // A station's desktop icon started the player: play it now, without
    // waiting for the music library to be read.
    try {
      for (const c of (await api("/api/inbox?only=station")).commands || []) await runCommand(c);
      for (const c of (await api("/api/inbox?only=notice")).commands || []) await runCommand(c);
    } catch { /* checked again below */ }
    // The first scan runs in the background; wait for it before
    // choosing which playlist to show.
    if (S.lib.scanning) {
      $("#view").replaceChildren(el("div", { class: "empty" }, "Reading your music library…"));
      setBusy(1);
      const started = Date.now();
      try {
        while (S.lib.scanning && Date.now() - started < 10 * 60 * 1000) {
          await new Promise((r) => setTimeout(r, 700));
          await loadState();
        }
      } finally {
        setBusy(-1);
      }
      if (S.lib.scanning) {
        renderErrors("Reading your music library is taking a long time. Playlists appear in the sidebar as soon as it finishes.");
        const wait = setInterval(async () => {
          try { await loadState(); } catch { return; }
          if (!S.lib.scanning) clearInterval(wait);
        }, 3000);
      }
    }
  } catch (err) {
    $("#view").replaceChildren(el("div", { class: "errors" }, `Can't reach the player server: ${err.message}`));
    return;
  }
  if (!S.lib.scanning) await restoreSession();
  if (showToken === 0) {  // the user hasn't opened something while it scanned
    // A new window opens on Home; reloading stays where it was.
    let view = null;
    try { view = JSON.parse(sessionStorage.getItem("sp.view") || "null"); } catch { /* none */ }
    const known = view && view.type && !["search", "welcome"].includes(view.type)
      && (view.type !== "playlist" || S.lib.playlists.some((p) => p.id === view.id));
    // The very first time, with no music added yet: the welcome screen.
    const firstRun = !(S.lib.sources || []).length && !readJSON("view");
    await show(known ? view : firstRun ? { type: "welcome" } : { type: "home" });
  }
  // A file opened just now (which may be why this window opened) plays;
  // an old request doesn't replay when the player is reopened later.
  await checkInbox({ fresh: 30 });
  setInterval(() => { if ($("#view")) checkInbox(); }, 1000);
  launch.ready = true;
  if (launch.todo) await runLaunch(launch.todo);
  offerMacWindow();
})();
