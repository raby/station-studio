"use strict";

// ---------- small helpers ----------------------------------------------

const $ = (sel) => document.querySelector(sel);

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
  alert: ["M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
  edit: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"],
  up: ["M12 19V5", "M5 12l7-7 7 7"],
  down: ["M12 5v14", "M19 12l-7 7-7-7"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  clock: ["c 12 12 9", "M12 7v5l3 2"],
  phone: ["r 6.5 2 11 20 2.5", "M11 18h2"],
  download: ["M12 3v12", "M7 10l5 5 5-5", "M5 21h14"],
  app: ["r 3 4 18 16 2.5", "M3 9h18"],
  headphones: ["M3 18v-6a9 9 0 0 1 18 0v6", "M21 19a2 2 0 0 1-2 2h-1v-6h3z", "M3 19a2 2 0 0 0 2 2h1v-6H3z"],
  log: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M8 13h8", "M8 17h8"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M9 15h6"],
  sparkle: ["M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z", "M19 17l.6 1.4L21 19l-1.4.6L19 21l-.6-1.4L17 19l1.4-.6z"],
  refresh: ["M21 12a9 9 0 1 1-2.64-6.36L21 8", "M21 3v5h-5"],
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
// has its own player, never routed through volume levelling (a station
// played directly from another site would be silenced by it).
const decks = [new Audio(), new Audio()];
for (const d of decks) d.preload = "auto";
const radioEl = new Audio();
radioEl.preload = "none";
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

async function loadState(newState) {
  S.lib = newState || (await api("/api/state"));
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

// "A new version is out", until dismissed for that version.
function renderUpdate() {
  const box = $("#update");
  const latest = S.lib.update;
  if (!latest || store("dismissed-update") === latest) {
    box.hidden = true;
    return;
  }
  box.replaceChildren(
    el("div", {}, `Station Player ${latest} is out.`),
    el("div", {}, "To update, run ", el("code", {}, "pipx upgrade station-player"), " and restart it."),
    el("button", {
      type: "button", class: "btn small",
      onclick: () => { store("dismissed-update", latest); renderUpdate(); },
    }, "Dismiss"));
  box.hidden = false;
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
  if ((S.view.type === "playlist" || S.view.type === "search") && S.detail) root.replaceChildren(playlistView(S.detail));
  else if (S.view.type === "radio") root.replaceChildren(radioView());
  else if (S.view.type === "recordings") root.replaceChildren(recordingsView());
  else if (S.view.type === "upnext") root.replaceChildren(upNextView());
  else if (S.view.type === "welcome") root.replaceChildren(welcomeView());
  else if (S.view.type === "clock") root.replaceChildren(clockView());
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
  const tbody = el("tbody", {
    ondblclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (tr && !e.target.closest(".row-more")) playPlaylist(pl, Number(tr.dataset.index));
    },
    onclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      focusRow(tr);
      if (e.target.closest(".row-more")) openTrackMenu(trackOf(tr), pl, e.target.closest(".row-more"), tr);
    },
    oncontextmenu: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr || !(trackOf(tr).available || mine)) return;
      e.preventDefault();
      focusRow(tr);
      openTrackMenu(trackOf(tr), pl, tr, tr, { x: e.clientX, y: e.clientY });
    },
    onkeydown: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      // In my playlists: Alt+arrows move a track, Delete removes it.
      if (mine && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const i = Number(tr.dataset.index);
        moveInPlaylist(pl, i, i + (e.key === "ArrowUp" ? -1 : 1));
        return;
      }
      if (mine && (e.key === "Delete" || e.key === "Backspace")) {
        e.preventDefault();
        removeFromPlaylist(pl, Number(tr.dataset.index));
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
        queueTrack(trackOf(tr), pl, "next");
        return;
      } else if ((e.key === "q" || e.key === "Q") && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        queueTrack(trackOf(tr), pl, "end");
        return;
      } else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
        e.preventDefault();
        if (trackOf(tr).available || mine) openTrackMenu(trackOf(tr), pl, tr, tr);
        return;
      } else return;
      e.preventDefault();
      if (next) focusRow(next);
    },
  }, rows);
  if (mine) dragToReorder(tbody, pl);
  const empty = el("div", { class: "empty" },
    pl.search ? "Nothing in your library matches." : pl.count ? "No tracks match."
      : mine ? "This playlist is empty. Add tracks with the ⋯ button on any track, or by right-clicking it." : "This playlist is empty.");
  const table = el("table", { class: "tracks" },
    el("caption", { class: "sr-only" },
      `${pl.name}. Use the arrow keys to move between tracks and Enter to play. ` +
      "N plays the track next, Q adds it to the queue." +
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
      if (hit) { shown++; first = first || rows[i]; }
    });
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
    announce(`Added ${what} to ${target ? target.name : "the playlist"}`);
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

function openTrackMenu(track, pl, anchor, returnTo, at) {
  const menu = $("#track-menu");
  menuReturn = returnTo;
  const index = pl.tracks.indexOf(track);
  const item = (label, fn) => el("button", {
    type: "button", role: "menuitem", tabindex: "-1",
    onclick: () => { closeTrackMenu(); fn(); },
  }, label);
  const place = () => {
    const box = anchor.getBoundingClientRect();
    const x = at ? at.x : box.right - menu.offsetWidth;
    const y = at ? at.y : box.bottom;
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
    menu.querySelector("button").focus();
  };
  // "Add to playlist…" swaps the menu for a list of my playlists.
  const choosePlaylist = () => {
    menu.replaceChildren(
      el("div", { class: "menu-label", "aria-hidden": "true" }, "Add to playlist"),
      ...myPlaylists().filter((p) => p.id !== pl.id).map((p) =>
        item(p.name, () => addToPlaylist(p.id, [track.id], track.name))),
      item("New playlist…", () => newPlaylist([track.id])));
    place();
  };
  const items = [];
  if (track.available) {
    items.push(item("Play next", () => queueTrack(track, pl, "next")),
      item("Add to queue", () => queueTrack(track, pl, "end")),
      el("button", { type: "button", role: "menuitem", tabindex: "-1", "aria-haspopup": "menu", onclick: choosePlaylist },
        "Add to playlist…"));
  }
  if (isMine(pl)) {
    if (items.length) items.push(el("hr", { role: "separator" }));
    if (index > 0) items.push(item("Move up", () => moveInPlaylist(pl, index, index - 1)));
    if (index < pl.tracks.length - 1) items.push(item("Move down", () => moveInPlaylist(pl, index, index + 1)));
    items.push(item("Remove from this playlist", () => removeFromPlaylist(pl, index)));
  }
  if (!items.length) return;
  menu.replaceChildren(...items);
  menu.setAttribute("aria-label", track.name);
  menu.hidden = false;
  place();
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
      const typed = [nameInput.value, urlInput.value];
      renderView();
      const inputs = document.querySelectorAll(`#view .card[data-url="${CSS.escape(st.url)}"] .edit input:not([type=file])`);
      if (inputs.length >= 2) [inputs[0].value, inputs[1].value] = typed;
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
      type: "button", class: "btn small", title: "Look this station up in Radio Browser",
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

function stationCard(st, i, count) {
  const isLive = () => S.mode === "radio" && S.station && S.station.url === st.url;
  const name = el("input", { value: st.name, "aria-label": "Station name", required: true });
  const url = el("input", { value: st.url, "aria-label": "Stream URL", required: true });
  const result = el("div", { class: "test-result", role: "status", hidden: true });
  const editButton = () => `#view .card[data-url="${CSS.escape(st.url)}"] .card-top button`;
  const close = () => { editingStation = null; renderView(); refocus(editButton()); };
  const save = async (e) => {
    e.preventDefault();
    const body = { url: st.url };
    if (name.value.trim() !== st.name) body.name = name.value;
    if (url.value.trim() !== st.url) body.new_url = url.value;
    if (!body.name && !body.new_url) { close(); return; }
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
        el("div", { class: "name" }, st.name),
        el("button", {
          type: "button", class: "btn ghost icon-only", "aria-label": `Edit ${st.name}`, title: "Edit, test, move or remove",
          onclick: () => { editingStation = st.url; renderView(); const n = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] input`); if (n) n.focus(); },
        }, icon("edit"))),
      el("div", { class: "song", hidden: true }),
      el("div", { class: "rec", hidden: true }, el("span", { class: "rec-text" }),
        el("button", { type: "button", class: "btn small rec-play", hidden: true }, ...iconLabel("play", "Play"))),
      el("div", { class: "row" },
        el("button", {
          type: "button", class: "btn primary listen",
          onclick: () => (isLive() && !audio.paused ? audio.pause() : playStation(st)),
        }, ...iconLabel("play", "Listen")),
        el("button", { type: "button", class: "btn rec-btn", onclick: () => toggleRecording(st) }, ...iconLabel("record", "Record")))),
    editing ? el("form", { class: "edit", onsubmit: save },
      el("label", {}, "Name", name),
      el("label", {}, "Stream URL", url),
      logoEditor(st, result, name, url),
      result,
      el("div", { class: "row" },
        el("button", { class: "btn primary" }, "Save"),
        el("button", { type: "button", class: "btn", onclick: () => testStream(url.value, result) }, "Test"),
        el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel")),
      el("div", { class: "row moves" },
        el("button", { type: "button", class: "btn small move-up", disabled: i === 0 || null, "aria-label": `Move ${st.name} up`, onclick: () => move(i - 1) }, ...iconLabel("up", "Up")),
        el("button", { type: "button", class: "btn small move-down", disabled: i === count - 1 || null, "aria-label": `Move ${st.name} down`, onclick: () => move(i + 1) }, ...iconLabel("down", "Down")),
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
  const cards = stations.map((st, i) => stationCard(st, i, stations.length));

  queueMicrotask(syncRadioCards);
  queueMicrotask(syncRecordings);
  return el("div", {},
    el("div", { class: "view-head" }, el("h1", {}, "Radio")),
    cards.length ? el("div", { class: "cards" }, cards) : el("div", { class: "empty-state" },
      el("div", { class: "glyph big" }, icon("radio")),
      el("h2", {}, "No stations yet"),
      el("p", {}, "Find stations below, or add one with its stream link. Most stations list one on their website, often as a .pls or .m3u file.")),
    el("div", { class: "settings" }, recentSection(), finderSection(), el("section", { class: "add-station" },
      el("h2", {}, "Add a station by its link"),
      el("p", { class: "help" }, "Test checks the link plays, and fills in the name when the station sends one."),
      el("form", { class: "form-row", onsubmit: add }, name, url,
        el("button", { type: "button", class: "btn", onclick: test }, "Test"),
        el("button", { class: "btn primary" }, ...iconLabel("plus", "Add"))),
      result,
      msg),
    el("section", {},
      el("h2", {}, "Recordings"),
      el("p", { class: "help" },
        "Record records a station to a file, whether or not you're listening to it, exactly as the station sends it. ",
        "When the station sends song titles, they're saved in a text file beside the recording. ",
        `Recordings go in ${S.lib.recordings_folder || "your Music folder"}. A recording stops by itself after 12 hours.`),
      el("button", { type: "button", class: "btn", onclick: () => show({ type: "recordings" }) },
        ...iconLabel("record", "See your recordings")))),
  );
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
function syncRadioCards() {
  for (const card of document.querySelectorAll("#view .card[data-url]")) {
    const live = S.mode === "radio" && S.station && S.station.url === card.dataset.url;
    card.classList.toggle("live", Boolean(live));
    const listen = card.querySelector(".listen");
    const label = live && !audio.paused ? "Pause" : "Listen";
    if (listen.textContent !== label) listen.replaceChildren(...iconLabel(label === "Pause" ? "pause" : "play", label));
    const song = card.querySelector(".song");
    song.textContent = live && S.radioTitle ? `Now playing: ${S.radioTitle}` : "";
    song.hidden = !song.textContent;
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
const F = { q: "", draft: null, tag: "", country: homeCountry(), order: "votes", source: "", results: null, loading: false,
  error: "", token: 0, asked: false, next: null, more: false, typing: null };
const FIND_COUNTRIES = ["GB", "IE", "US", "CA", "AU", "NZ", "DE", "FR", "NL", "BE", "ES", "PT", "IT", "CH", "AT", "SE",
  "NO", "DK", "FI", "PL", "CZ", "GR", "TR", "BR", "MX", "AR", "JM", "NG", "ZA", "KE", "IN", "JP", "KR"];

// Genres to browse (the directory's most used tags are mostly one
// country's, so these are picked by hand).
const FIND_GENRES = ["jazz", "soul", "funk", "reggae", "hip hop", "house", "techno", "drum and bass", "ambient",
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

const LOW_KBPS = 64;

function stationFacts(st) {
  // "United Kingdom", not the directory's "The United Kingdom Of Great
  // Britain And Northern Ireland"; left out when searching that country.
  const country = st.countrycode ? (st.countrycode === F.country ? "" : countryName(st.countrycode)) : st.country;
  const text = [country, st.codec && st.bitrate ? `${st.codec} ${st.bitrate} kbps` : st.codec || (st.bitrate ? `${st.bitrate} kbps` : "")]
    .filter(Boolean).join(" · ");
  const low = st.bitrate > 0 && st.bitrate < LOW_KBPS;
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
            await loadState(await api("/api/stations", { name: st.name, url: st.url, logo_url: st.logo || "" }, { quiet: true }));
            renderView();
            toast(`Added ${st.name} to your stations`);
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
    el("div", { class: "tag-filter", id: "finder-tags", role: "group", "aria-label": "Genres" }, ...finderTagChips()),
    el("div", { id: "finder-results", "aria-live": "polite" }));
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

const MAX_ALARMS = 10;
const SNOOZE_MINUTES = 9;
const C = { editing: null, face: store("clock-face") || "digital", timer: null, draft: null };

const alarmsList = () => (S.lib.config.alarms || []);
const pad2 = (n) => String(n).padStart(2, "0");
const hhmm = (d) => d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// "Weekdays", "Every day", "Sat, Sun"…
function daysText(days) {
  const set = [...days].sort().join("");
  if (set === "0123456") return "Every day";
  if (set === "01234") return "Weekdays";
  if (set === "56") return "Weekends";
  return days.map((d) => DAY_NAMES[d]).join(", ");
}

function nextAlarmText() {
  const n = S.lib.next_alarm;
  const snooze = S.lib.alarm_state && S.lib.alarm_state.snoozed_until;
  if (snooze) return `Snoozed until ${hhmm(new Date(snooze * 1000))}`;
  if (!n) return alarmsList().some((a) => a.on) ? "" : "No alarms on";
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
  const time = el("input", { type: "time", id: "alarm-time", value: a.time, required: true });
  const label = el("input", { id: "alarm-label", value: a.label || "", placeholder: "Label (optional), e.g. Work", maxlength: 60 });
  const days = DAY_NAMES.map((name, i) => el("input", { type: "checkbox", value: i, checked: a.days.includes(i) || null }));
  const source = el("select", { id: "alarm-source" },
    el("optgroup", { label: "Playlists" }, ...S.lib.playlists.map((p) => el("option", { value: `playlist:${p.id}` }, p.name))),
    el("optgroup", { label: "Radio" }, ...(S.lib.stations || []).map((st) => el("option", { value: `station:${st.id}` }, st.name))));
  if (a.source) source.value = `${a.source.kind}:${a.source.id}`;
  const msg = el("div", { class: "form-msg", role: "status" });
  const close = () => { C.editing = null; renderView(); refocus(isNew ? "#btn-add-alarm" : `#view [data-alarm="${a.id}"] .alarm-edit`); };
  const save = async (e) => {
    e.preventDefault();
    const chosen = days.filter((d) => d.checked).map((d) => Number(d.value));
    if (!chosen.length) { msg.textContent = "Choose at least one day"; return; }
    if (!source.value) { msg.textContent = "Choose something to play"; return; }
    const [kind, ...rest] = source.value.split(":");
    const name = source.selectedOptions[0] ? source.selectedOptions[0].textContent : "";
    const updated = { ...a, on: true, time: time.value, label: label.value.trim(), days: chosen, source: { kind, id: rest.join(":"), name } };
    const list = isNew ? [...alarmsList(), updated] : alarmsList().map((x) => (x.id === a.id ? updated : x));
    if (await saveAlarms(list, `Alarm set for ${time.value}, ${daysText(chosen).toLowerCase()}`)) {
      C.editing = null;
      renderView();
      const saved = alarmsList()[isNew ? alarmsList().length - 1 : alarmsList().findIndex((x) => x.id === a.id)];
      refocus(saved ? `#view [data-alarm="${saved.id}"] .alarm-edit` : "#btn-add-alarm");
    }
  };
  const remove = async () => {
    if (!confirm(`Delete the ${a.time} alarm${a.label ? ` (${a.label})` : ""}?`)) return;
    if (await saveAlarms(alarmsList().filter((x) => x.id !== a.id), "Alarm deleted")) {
      C.editing = null;
      renderView();
      refocus("#btn-add-alarm");
    }
  };
  return el("form", { class: "alarm-form", onsubmit: save,
    onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } } },
    el("div", { class: "fields" },
      el("label", { class: "field" }, "Time", time),
      el("label", { class: "field grow" }, "Label", label),
      el("label", { class: "field grow" }, "Play", source)),
    el("fieldset", { class: "days" }, el("legend", {}, "Days"), ...days.map((d, i) => el("label", {}, d, DAY_NAMES[i]))),
    msg,
    el("div", { class: "row" },
      el("button", { class: "btn primary" }, isNew ? "Set alarm" : "Save alarm"),
      el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel"),
      el("span", { class: "spacer" }),
      isNew ? null : el("button", { type: "button", class: "btn small danger", onclick: remove }, ...iconLabel("trash", "Delete"))));
}

function alarmCard(a) {
  const editing = C.editing === a.id;
  const toggle = el("input", {
    type: "checkbox", checked: a.on || null, "data-key": `alarm-${a.id}`,
    "aria-label": `Alarm at ${a.time}${a.label ? `, ${a.label}` : ""}`,
    onchange: async (e) => {
      const on = e.target.checked;
      const list = alarmsList().map((x) => (x.id === a.id ? { ...x, on } : x));
      await saveAlarms(list, on ? `Alarm on for ${a.time}` : `Alarm at ${a.time} off`);
      renderView();
      refocus(`#view input[data-key="alarm-${a.id}"]`);
    },
  });
  return el("li", { class: a.on ? "alarm-card" : "alarm-card off", "data-alarm": a.id },
    el("div", { class: "alarm-row" },
      el("div", { class: "alarm-time" }, a.time),
      el("div", { class: "info" },
        el("div", { class: "name" }, a.label || "Alarm"),
        el("div", { class: "facts" }, `${daysText(a.days)} · ${a.source ? a.source.name : ""}`)),
      el("button", {
        type: "button", class: "btn ghost icon-only alarm-edit", "aria-label": `Edit the ${a.time} alarm`,
        onclick: () => { C.editing = a.id; renderView(); refocus("#alarm-time"); },
      }, icon("edit")),
      el("label", { class: "check" }, toggle)),
    editing ? alarmEditor(a, false) : null);
}

function clockView() {
  const alarms = alarmsList();
  const adding = C.editing === "new";
  const faceButton = (face, label) => el("button", {
    type: "button", class: "seg", "aria-pressed": String(C.face === face),
    onclick: () => { C.face = face; store("clock-face", face); renderView(); refocus(`#view .seg[aria-pressed="true"]`); },
  }, label);
  const blank = { id: "", on: true, time: "07:00", label: "", days: [0, 1, 2, 3, 4], source: null };
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
          type: "button", class: "btn primary", id: "btn-add-alarm", disabled: alarms.length >= MAX_ALARMS || adding || null,
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

function showAlarmScreen(req) {
  const box = $("#alarm-screen");
  alarmScreenReturn = document.activeElement;
  box.replaceChildren(
    el("div", { class: "alarm-inner" },
      clockFace("huge"),
      el("h2", { id: "alarm-screen-title" }, req.label || (req.snoozed ? "Alarm (snoozed)" : "Good morning")),
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
  const tile = (glyph, title, sub, onclick, id) => el("button", { type: "button", class: "tile", id, onclick },
    el("span", { class: "glyph" }, icon(glyph)),
    el("span", {}, el("span", { class: "t-title" }, title), el("span", { class: "t-sub" }, sub)));
  return el("div", {},
    el("div", { class: "add-tiles" },
      tile("folder", "Add a music folder", "A folder of songs, such as your Music folder. It shows under Folders.",
        () => pickAndAdd("music", "folder", "#add-folder"), "add-folder"),
      tile("list", "Add iTunes or station-studio playlists", "An exported library (.xml). Station Player picks up its playlists.",
        () => pickAndAdd("playlist", "file", "#add-playlists"), "add-playlists")),
    el("p", { class: "help" },
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
      pathForm("playlist", "Path to an .xml file, or a folder of them")));
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
  ["alarm", "Alarm & sleep", "clock", alarmPage],
  ["remote", "Phone remote", "phone", () => remoteSection()],
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
  const page = SETTINGS_TABS.find((t) => t[0] === S.view.tab) || SETTINGS_TABS[0];
  const ids = SETTINGS_TABS.map((t) => t[0]);
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
  }, SETTINGS_TABS.map(([id, label, glyph]) => el("button", {
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
    d && d.supported ? el("p", { class: "help" },
      "Open Station Player like any other app, without a terminal. Turn these off before uninstalling.") : null,
    el("div", { class: "checks" },
      d && d.supported ? checkbox("shortcut", where, d.shortcut, desktopChange("shortcut")) : null,
      d && d.supported ? checkbox("login", "Start Station Player in the background when I log in", d.login, desktopChange("login")) : null,
      browser ? checkbox("window", `Open in a window of its own (using ${browser}), not a browser tab`,
        S.lib.config.own_window !== false, windowChange) : null,
      checkbox("updates", "Tell me when a new version is out (asks pypi.org once a day)",
        S.lib.config.check_updates !== false, updatesChange)),
    browser ? null : el("p", { class: "help" },
      "Station Player opens in a browser tab. With Google Chrome, Microsoft Edge or Brave installed it opens in a window of its own instead."));
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
  if (radioEl.hasAttribute("src")) unload(radioEl);
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
  audio = radioEl;
  // A finder result (not saved yet) relays too, for its song titles and
  // so HLS stations play at all.
  S.radioSrc = via !== "relay" ? st.url : st.id ? `/radio/${st.id}`
    : st.preview ? `/radio/preview?u=${encodeURIComponent(st.url)}` : st.url;
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
  for (const d of [...decks, radioEl]) if (!d.paused) d.pause();
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
  const ownWindow = matchMedia("(display-mode: standalone), (display-mode: minimal-ui)").matches;
  return ownWindow ? what : `${what} · Station Player`;
}

function updateNow() {
  const title = $("#now-title");
  const sub = $("#now-sub");
  const t = current();
  if (S.mode === "radio") {
    title.replaceChildren(el("span", { class: "live-badge" }, "LIVE"), S.station.name);
    sub.textContent = S.radioTitle || "Internet radio";
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
  // Live radio has nowhere to seek to.
  $(".bar .seek").hidden = S.mode === "radio";
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
  for (const a of [...decks, radioEl]) {
    a.addEventListener(type, (e) => { if (e.target === audio) fn(e); });
  }
}

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
  radioEl.volume = v;
  if (L.master) L.master.gain.value = v;
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
// window shows a page that starts it (sw.js, offline.html).
if ("serviceWorker" in navigator) {
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
  const start = Date.now();
  ALARM.timer = setInterval(() => {
    volMul.alarm = Math.min(1, 0.02 + (Date.now() - start) / 1000 / ALARM_FADE);
    applyVolume();
    if (volMul.alarm >= 1) { clearInterval(ALARM.timer); ALARM.timer = null; }
  }, 250);
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
  else if (c.cmd === "sleep") setSleep(c.minutes);
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
    const view = readJSON("view");
    const known = view && !["search", "welcome"].includes(view.type) && (view.type !== "playlist" || S.lib.playlists.some((p) => p.id === view.id));
    const first = S.lib.playlists[0];
    const newcomer = !(S.lib.sources || []).length;
    await show(known && !(newcomer && view.type === "settings") ? view
      : first ? { type: "playlist", id: first.id } : newcomer ? { type: "welcome" } : { type: "settings" });
  }
  // A file opened just now (which may be why this window opened) plays;
  // an old request doesn't replay when the player is reopened later.
  await checkInbox({ fresh: 30 });
  setInterval(() => { if ($("#view")) checkInbox(); }, 1000);
})();
