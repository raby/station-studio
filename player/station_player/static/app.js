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
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
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
}

function renderErrors(extra) {
  const box = $("#errors");
  const all = extra ? [extra, ...S.lib.errors] : S.lib.errors;
  box.replaceChildren(...all.map((e) => el("div", {}, e)));
  box.hidden = all.length === 0;
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
    announce(done);
  } catch (err) {
    renderErrors(err.message);
  }
}

// After a view is rebuilt, put focus somewhere sensible, not <body>.
function focusHeading() {
  const h = $("#view h1");
  if (h) { h.tabIndex = -1; h.focus(); }
}

function navButton(label, count, view, active) {
  return el("li", {},
    el("button", {
      type: "button", class: "nav-item", "aria-current": active ? "true" : "false",
      onclick: () => show(view),
    }, el("span", {}, label), count === null ? null : el("span", { class: "count" }, count)));
}

function renderNav() {
  const isActive = (pl) => S.view.type === "playlist" && S.view.id === pl.id;
  const itunes = S.lib.playlists.filter((p) => p.kind === "itunes");
  const folders = S.lib.playlists.filter((p) => p.kind === "folder");
  $("#nav-itunes").replaceChildren(...(itunes.length
    ? itunes.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p)))
    : [el("li", { class: "nav-empty" }, "None yet")]));
  $("#nav-folders").replaceChildren(...(folders.length
    ? folders.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p)))
    : [el("li", { class: "nav-empty" }, "None yet")]));
  for (const b of document.querySelectorAll(".nav-item[data-view]")) {
    b.setAttribute("aria-current", S.view.type === b.dataset.view ? "true" : "false");
  }
  const count = $("#upnext-count");
  count.textContent = S.upNext.length || "";
}

let showToken = 0;

async function show(view) {
  // Only the latest click may render: a slow response for an earlier
  // click must not replace the playlist the user has since opened.
  const token = ++showToken;
  S.view = view;
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
  else if (S.view.type === "upnext") root.replaceChildren(upNextView());
  else root.replaceChildren(settingsView());
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
  const rows = pl.tracks.map((t, i) => el("tr", {
    "data-index": i,
    "data-id": t.id,
    tabindex: "-1",
    class: t.available ? null : "missing",
    title: t.available ? "Double-click or press Enter to play" : "The audio file for this track wasn't found",
  },
    el("td", { class: "num" }, i + 1),
    el("td", { class: "title" }, t.name),
    el("td", { class: "artist" }, t.artist || ""),
    el("td", { class: "album" }, t.album || ""),
    el("td", { class: "dur" }, t.duration_ms ? fmtTime(t.duration_ms / 1000) : ""),
    el("td", { class: "more" }, t.available
      ? el("button", { type: "button", class: "row-more", tabindex: "-1", "aria-label": `More for ${t.name}`, title: "Play next or add to queue" }, "⋯")
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
      if (!tr || !trackOf(tr).available) return;
      e.preventDefault();
      focusRow(tr);
      openTrackMenu(trackOf(tr), pl, tr, tr, { x: e.clientX, y: e.clientY });
    },
    onkeydown: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
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
        if (trackOf(tr).available) openTrackMenu(trackOf(tr), pl, tr, tr);
        return;
      } else return;
      e.preventDefault();
      if (next) focusRow(next);
    },
  }, rows);
  const empty = el("div", { class: "empty" },
    pl.search ? "Nothing in your library matches." : pl.count ? "No tracks match." : "This playlist is empty.");
  const table = el("table", { class: "tracks" },
    el("caption", { class: "sr-only" },
      `${pl.name}. Use the arrow keys to move between tracks and Enter to play. ` +
      "N plays the track next, Q adds it to the queue."),
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

  const view = el("div", {},
    el("div", { class: "view-head" },
      el("div", {}, el("h1", {}, pl.name), el("div", { class: "meta" }, meta)),
      el("div", { class: "spacer" }),
      search,
      el("button", {
        type: "button", class: "btn primary", disabled: playable ? null : true,
        onclick: () => playPlaylist(pl, S.shuffle ? null : 0),
      }, "▶ Play"),
    ),
    !pl.search && pl.missing === pl.count && pl.count
      ? el("p", { class: "errors" },
          "None of these tracks were found on disk. Add the folder your music lives in under Sources & settings, ",
          "or add a path rewrite if the playlist was made on another computer.")
      : null,
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

// ---------- "play next" menu ------------------------------------------------

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
  const item = (label, where) => el("button", {
    type: "button", role: "menuitem", tabindex: "-1",
    onclick: () => { closeTrackMenu(); queueTrack(track, pl, where); },
  }, label);
  menu.replaceChildren(item("Play next", "next"), item("Add to queue", "end"));
  menu.setAttribute("aria-label", track.name);
  menu.hidden = false;
  const box = anchor.getBoundingClientRect();
  const x = at ? at.x : box.right - menu.offsetWidth;
  const y = at ? at.y : box.bottom;
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  menu.querySelector("button").focus();
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
    }, "↑"),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Move ${track.name} down`, disabled: i === S.upNext.length - 1 || null,
      onclick: () => moveQueued(i, 1),
    }, "↓"),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Remove ${track.name} from the queue`,
      onclick: () => { S.upNext.splice(i, 1); queueChanged(); announce(`Removed ${track.name}`); },
    }, "Remove")));
  const coming = comingFromQueue(30);
  return el("div", { class: "settings upnext" },
    el("div", { class: "view-head" }, el("h1", {}, "Up next")),
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

function radioView() {
  const msg = el("div", { class: "form-msg", role: "status" });
  const name = el("input", { placeholder: "Station name", "aria-label": "Station name" });
  const url = el("input", { placeholder: "https://… stream, .pls or .m3u URL", "aria-label": "Stream URL" });
  const add = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/stations", { name: name.value, url: url.value }));
      renderView();
      focusHeading();
      announce("Station added");
    } catch (err) { msg.textContent = err.message; }
  };

  const cards = S.lib.stations.map((st) => {
    const isLive = () => S.mode === "radio" && S.station && S.station.url === st.url;
    return el("div", { class: "card", "data-url": st.url },
      el("div", { class: "name" }, st.name),
      el("div", { class: "song", hidden: true }),
      el("div", { class: "url" }, st.url),
      el("div", { class: "row" },
        el("button", {
          type: "button", class: "btn primary listen",
          onclick: () => (isLive() && !audio.paused ? audio.pause() : playStation(st)),
        }, "▶ Listen"),
        el("button", {
          type: "button", class: "btn",
          onclick: () => {
            if (confirm(`Remove ${st.name}?`)) act("/api/stations/remove", { url: st.url }, `Removed ${st.name}`);
          },
        }, "Remove")));
  });

  queueMicrotask(syncRadioCards);
  return el("div", {},
    el("div", { class: "view-head" }, el("h1", {}, "Radio")),
    cards.length ? el("div", { class: "cards" }, cards) : el("div", { class: "empty" }, "No stations yet."),
    el("div", { class: "settings" }, el("section", { class: "add-station" },
      el("h2", {}, "Add a station"),
      el("form", { class: "form-row", onsubmit: add }, name, url, el("button", { class: "btn" }, "Add")),
      msg)),
  );
}

// Reflect play state on the station cards without rebuilding the view,
// so text typed into "Add a station" survives play/pause/stall events.
function syncRadioCards() {
  for (const card of document.querySelectorAll("#view .card[data-url]")) {
    const live = S.mode === "radio" && S.station && S.station.url === card.dataset.url;
    card.classList.toggle("live", Boolean(live));
    card.querySelector(".listen").textContent = live && !audio.paused ? "❚❚ Pause" : "▶ Listen";
    const song = card.querySelector(".song");
    song.textContent = live && S.radioTitle ? `Now playing: ${S.radioTitle}` : "";
    song.hidden = !song.textContent;
  }
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
            type: "button", class: "btn small", "aria-label": `Remove ${label}`,
            onclick: () => onRemove(i),
          }, "Remove"))))
      : null,
    form);
}

function pathForm(kind, placeholder) {
  const input = el("input", { placeholder, "aria-label": placeholder });
  const msg = el("div", { class: "form-msg", role: "status" });
  const submit = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/sources", { kind, path: input.value }));
      renderView();
      focusHeading();
      announce("Added");
    } catch (err) { msg.textContent = err.message; }
  };
  return el("div", {}, el("form", { class: "form-row", onsubmit: submit }, input,
    el("button", { class: "btn" }, "Add")), msg);
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
      announce(`${names[key]} turned ${on ? "on" : "off"}`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const updatesChange = async (on) => {
    try {
      await loadState(await api("/api/settings", { check_updates: on }));
      announce(`New version notices turned ${on ? "on" : "off"}`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  return el("section", {},
    el("h2", {}, "App"),
    d && d.supported ? el("p", { class: "help" },
      "Open Station Player like any other app, without a terminal. Turn these off before uninstalling.") : null,
    el("div", { class: "checks" },
      d && d.supported ? checkbox("shortcut", where, d.shortcut, desktopChange("shortcut")) : null,
      d && d.supported ? checkbox("login", "Start Station Player in the background when I log in", d.login, desktopChange("login")) : null,
      checkbox("updates", "Tell me when a new version is out (asks pypi.org once a day)",
        S.lib.config.check_updates !== false, updatesChange)));
}

function playbackSection() {
  return el("section", {},
    el("h2", {}, "Playback"),
    el("p", { class: "help" },
      "Tracks always play back to back with no gap. Volume levelling plays every track at about the same loudness, ",
      "using ReplayGain or Sound Check information where a file has it and measuring the rest the first time they play."),
    el("div", { class: "checks" },
      checkbox("level", "Even out the volume between tracks", L.on, async (on) => {
        setLevelling(on);
        announce(`Volume levelling turned ${on ? "on" : "off"}`);
      })));
}

function settingsView() {
  const cfg = S.lib.config;
  const removeSource = (kind, list) => (i) => act("/api/sources/remove", { kind, path: list[i] }, "Removed");

  const from = el("input", { placeholder: "From, e.g. /Users/rob/Music/iTunes/iTunes Media", "aria-label": "Path from" });
  const to = el("input", { placeholder: "To, e.g. D:/Music", "aria-label": "Path to" });
  const remapMsg = el("div", { class: "form-msg", role: "status" });
  const addRemap = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/remaps", { from: from.value, to: to.value }));
      renderView();
      focusHeading();
      announce("Path rewrite added");
    } catch (err) { remapMsg.textContent = err.message; }
  };

  return el("div", { class: "settings" },
    el("div", { class: "view-head" },
      el("h1", {}, "Sources & settings"),
      el("div", { class: "spacer" }),
      el("button", {
        type: "button", class: "btn", "data-busy-disable": true, disabled: pending > 0 || null,
        onclick: () => act("/api/rescan", {}, "Library rescanned"),
      }, "Rescan")),
    listSection({
      title: "Playlists (iTunes XML)",
      help: "Station-studio exports, or a library exported from Music / iTunes (File › Library › Export Library). A folder is scanned for .xml files, so pointing at station-studio's exports folder picks up every new week.",
      items: cfg.playlist_sources || [],
      onRemove: removeSource("playlist", cfg.playlist_sources || []),
      form: pathForm("playlist", "Path to an .xml file or a folder of them"),
    }),
    listSection({
      title: "Music folders",
      help: "Folders of audio files. Each one appears under Folders, and their files are used to find playlist tracks that have moved.",
      items: cfg.music_folders || [],
      onRemove: removeSource("music", cfg.music_folders || []),
      form: pathForm("music", "Path to a folder of music"),
    }),
    listSection({
      title: "Path rewrites",
      help: "When a playlist was made on another computer, rewrite the start of each track's path so it points at your copy of the music.",
      items: (cfg.path_remaps || []).map((r) => `${r.from}  →  ${r.to}`),
      onRemove: (i) => {
        const r = cfg.path_remaps[i];
        act("/api/remaps/remove", { from: r.from, to: r.to }, "Path rewrite removed");
      },
      form: el("div", {}, el("form", { class: "form-row", onsubmit: addRemap }, from, to,
        el("button", { class: "btn" }, "Add")), remapMsg),
    }),
    playbackSection(),
    appSection(),
    el("p", { class: "help version" }, `Station Player ${S.lib.version || ""}`),
  );
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

function setDeckGain(i, trackId, smooth) {
  const node = L.gains[i];
  if (!node || !L.master) return;
  const value = levelGain(trackId);
  const now = L.ctx.currentTime;
  node.gain.cancelScheduledValues(now);
  if (smooth) node.gain.setTargetAtTime(value, now, 0.4);
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
    audio.currentTime = 0;
    audio.play().catch(() => {});
    return;
  }
  applyMove(m);
  loadTrack(current());
}

function stopAtEnd() {
  cancelHandoff();
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
  const pre = otherDeck();
  const m = nextMove(1);
  if (m.stop || pre.dataset.trackId !== m.track.id || pre.error || pre.readyState < 3) return null;
  return m;
}

function watchForEnd() {
  if (handoffTimer || S.mode !== "track" || audio.paused || !audio.src) return;
  const left = audio.duration - audio.currentTime;
  if (!isFinite(left) || left > 0.25 || !nextIsReady()) return;
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

function handoff() {
  const m = nextIsReady();
  if (!m) return;
  const pre = otherDeck();
  applyMove(m);
  const old = audio;
  deckIdx = 1 - deckIdx;
  audio = pre;
  if (audio.currentTime) audio.currentTime = 0;
  // The old deck plays out its last few milliseconds, then stops for
  // sure, even if its duration was misjudged.
  setTimeout(() => { if (old !== audio && !old.paused) { old.pause(); preloadNext(); } }, 250);
  setDeckGain(deckIdx, m.track.id, false);
  audio.play().catch(() => {});
  updateNow();
  markPlaying();
  queueChanged();
}

// Radio plays through the player's relay, which reads the song titles
// stations send. If the relay can't handle a station, the browser plays
// the station's own URL instead (no song titles then).
function playStation(st, { via = "relay", autoplay = true } = {}) {
  const sameStation = S.mode === "radio" && S.station && S.station.url === st.url;
  cancelHandoff();
  S.mode = "radio";
  S.station = st;
  S.pos = -1;
  S.extra = null;
  for (const d of decks) unload(d);
  audio = radioEl;
  S.radioSrc = via === "relay" && st.id ? `/radio/${st.id}` : st.url;
  if (!sameStation || S.radioSrc === st.url) S.radioTitle = null;
  if (autoplay) {
    audio.src = S.radioSrc;
    audio.play().catch(() => {});
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
    const data = await api(`/api/nowplaying?station=${S.station.id}`);
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
    if (S.mode === "radio") audio.src = S.radioSrc;
    else ensureGraph();
    audio.play().catch(() => {});
  } else {
    pauseAll();
  }
}

// Pausing also stops a deck still playing out the end of the last track.
function pauseAll() {
  for (const d of [...decks, radioEl]) if (!d.paused) d.pause();
}

function previous() {
  if (S.mode !== "track") return;
  if (audio.currentTime > 3) audio.currentTime = 0;
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

function updateNow() {
  const title = $("#now-title");
  const sub = $("#now-sub");
  const t = current();
  if (S.mode === "radio") {
    title.replaceChildren(el("span", { class: "live-badge" }, "LIVE"), S.station.name);
    sub.textContent = S.radioTitle || "Internet radio";
    document.title = `${S.radioTitle || S.station.name} · Station Player`;
  } else if (S.mode === "track" && t) {
    title.textContent = t.name;
    sub.textContent = [t.artist, S.extra ? "from your queue" : S.queueLabel].filter(Boolean).join(" · ");
    document.title = `${t.name} · Station Player`;
  } else {
    title.textContent = "Nothing playing";
    sub.textContent = "";
    document.title = "Station Player";
  }
  $("#seek").disabled = S.mode !== "track";
  if (S.mode !== "track") $("#seek").value = 0;
  const art = S.mode === "track" && t ? `/art/${t.id}` : null;
  setArt(art);
  if (!("mediaSession" in navigator)) return;
  if (S.mode === "idle") { navigator.mediaSession.metadata = null; return; }
  const artwork = art ? [{ src: new URL(art, location.href).href }] : [];
  navigator.mediaSession.metadata = new MediaMetadata(S.mode === "radio"
    ? { title: S.radioTitle || S.station.name, artist: S.radioTitle ? S.station.name : "Live radio" }
    : { title: t.name, artist: t.artist || "", album: t.album || S.queueLabel || "", artwork });
}

function syncButtons() {
  const playing = !audio.paused;
  const btn = $("#btn-play");
  btn.textContent = playing ? "❚❚" : "▶";
  btn.title = playing ? "Pause" : "Play";
  btn.setAttribute("aria-label", btn.title);
  $("#btn-shuffle").setAttribute("aria-pressed", String(S.shuffle));
  const rep = $("#btn-repeat");
  rep.setAttribute("aria-pressed", String(S.repeat !== "off"));
  rep.textContent = S.repeat === "one" ? "↻¹" : "↻";
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
    const st = S.lib.stations.find((s) => s.url === q.station);
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
  if (S.mode !== "radio") return step(1);
  if (!reconnectRadio()) $("#now-sub").textContent = "Lost the connection to this station";
});
// The deck that just handed over finishes in the background; once it
// has, it can line up the track after next.
for (const d of decks) {
  d.addEventListener("ended", () => { if (d !== audio) preloadNext(); });
  d.addEventListener("error", () => {
    if (d === audio || !d.dataset.trackId) return;
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
onAudio("pause", () => { cancelHandoff(); saveSession(); });
onAudio("seeking", cancelHandoff);
onAudio("playing", () => { if (S.mode === "radio") pollTitle(); });
addEventListener("pagehide", () => saveSession());

$("#now-art").addEventListener("load", (e) => { e.target.hidden = false; });
$("#now-art").addEventListener("error", (e) => { e.target.hidden = true; });

async function quitApp() {
  if (!confirm("Quit Station Player? Music and radio will stop.")) return;
  saveSession();
  audio.pause();
  try {
    await api("/api/quit", {});
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  clearTimeout(titleTimer);
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
  if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
  seeking = false;
});

// With levelling on, the decks run at full volume into the levelling
// gains and the slider sets the master gain.
const volume = $("#volume");
volume.value = store("volume") ?? 0.8;
function applyVolume() {
  const v = Number(volume.value);
  for (const d of decks) d.volume = L.master ? 1 : v;
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
  else if (e.code === "ArrowRight" && S.mode === "track") audio.currentTime += 10;
  else if (e.code === "ArrowLeft" && S.mode === "track") audio.currentTime -= 10;
});

if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", togglePlay);
  ms.setActionHandler("pause", pauseAll);
  ms.setActionHandler("nexttrack", () => step(1, { userInitiated: true }));
  ms.setActionHandler("previoustrack", previous);
}

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
  if (showToken > 0) return;  // the user already opened something while it scanned
  const view = readJSON("view");
  const known = view && view.type !== "search" && (view.type !== "playlist" || S.lib.playlists.some((p) => p.id === view.id));
  if (known) return show(view);
  const first = S.lib.playlists[0];
  show(first ? { type: "playlist", id: first.id } : { type: "settings" });
})();
