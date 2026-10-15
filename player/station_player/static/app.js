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

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Station-Player": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

// ---------- state --------------------------------------------------------

const audio = new Audio();
audio.preload = "auto";

const S = {
  lib: { playlists: [], stations: [], config: {}, errors: [] },
  view: { type: "settings" },
  detail: null,          // playlist currently shown
  filter: "",
  queue: [],             // tracks (objects) in play order
  order: [],             // indexes into queue (shuffled or not)
  pos: -1,               // position in order
  queueSource: null,     // playlist id the queue came from
  mode: "idle",          // "idle" | "track" | "radio"
  station: null,
  shuffle: store("shuffle") === "1",
  repeat: store("repeat") || "off", // off | all | one
};

const current = () => (S.pos >= 0 ? S.queue[S.order[S.pos]] : null);

// ---------- library + navigation ----------------------------------------

async function loadState(newState) {
  S.lib = newState || (await api("/api/state"));
  renderNav();
  renderErrors();
}

function renderErrors(extra) {
  const box = $("#errors");
  const all = extra ? [extra, ...S.lib.errors] : S.lib.errors;
  box.replaceChildren(...all.map((e) => el("div", {}, e)));
  box.hidden = all.length === 0;
}

// Run a settings action from a button; failures show in the error box.
async function act(path, body) {
  try {
    await loadState(await api(path, body));
    renderView();
  } catch (err) {
    renderErrors(err.message);
  }
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
}

let showToken = 0;

async function show(view) {
  // Only the latest click may render: a slow response for an earlier
  // click must not replace the playlist the user has since opened.
  const token = ++showToken;
  S.view = view;
  store("view", JSON.stringify(view));
  if (view.type === "playlist") {
    let detail = null;
    try {
      detail = await api(`/api/playlists/${view.id}`);
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

function renderView() {
  const root = $("#view");
  if (S.view.type === "playlist" && S.detail) root.replaceChildren(playlistView(S.detail));
  else if (S.view.type === "radio") root.replaceChildren(radioView());
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
  const rows = pl.tracks.map((t, i) => el("tr", {
    "data-index": i,
    class: t.available ? null : "missing",
    title: t.available ? "Double-click to play" : "The audio file for this track wasn't found",
  },
    el("td", { class: "num" }, i + 1),
    el("td", { class: "title" }, t.name),
    el("td", { class: "artist" }, t.artist || ""),
    el("td", { class: "album" }, t.album || ""),
    el("td", { class: "dur" }, t.duration_ms ? fmtTime(t.duration_ms / 1000) : ""),
  ));
  const tbody = el("tbody", {
    ondblclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (tr) playPlaylist(pl, Number(tr.dataset.index));
    },
  }, rows);
  const empty = el("div", { class: "empty" }, pl.count ? "No tracks match." : "This playlist is empty.");
  const table = el("table", { class: "tracks" },
    el("thead", {}, el("tr", {},
      el("th", { class: "num" }, "#"), el("th", { class: "title" }, "Title"),
      el("th", { class: "artist" }, "Artist"), el("th", { class: "album" }, "Album"),
      el("th", { class: "dur" }, "Time"))),
    tbody);

  const applyFilter = () => {
    const q = S.filter.toLowerCase();
    let shown = 0;
    pl.tracks.forEach((t, i) => {
      const hit = matches(t, q);
      rows[i].hidden = !hit;
      if (hit) shown++;
    });
    table.hidden = shown === 0;
    empty.hidden = shown !== 0;
  };

  const playable = pl.tracks.filter((t) => t.available).length;
  const meta = `${pl.count} tracks` + (pl.missing ? ` · ${pl.missing} missing` : "");
  const search = el("input", {
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
    pl.missing === pl.count && pl.count
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
  if (S.mode !== "track" || !t || S.view.type !== "playlist" || S.queueSource !== S.view.id) return;
  const tr = document.querySelector(`#view tr[data-index="${t._index}"]`);
  if (tr) tr.classList.add("playing");
}

// ---------- radio view --------------------------------------------------

function radioView() {
  const msg = el("div", { class: "form-msg" });
  const name = el("input", { placeholder: "Station name", "aria-label": "Station name" });
  const url = el("input", { placeholder: "https://… stream, .pls or .m3u URL", "aria-label": "Stream URL" });
  const add = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/stations", { name: name.value, url: url.value }));
      renderView();
    } catch (err) { msg.textContent = err.message; }
  };

  const cards = S.lib.stations.map((st) => {
    const isLive = () => S.mode === "radio" && S.station && S.station.url === st.url;
    return el("div", { class: "card", "data-url": st.url },
      el("div", { class: "name" }, st.name),
      el("div", { class: "url" }, st.url),
      el("div", { class: "row" },
        el("button", {
          type: "button", class: "btn primary listen",
          onclick: () => (isLive() && !audio.paused ? audio.pause() : playStation(st)),
        }, "▶ Listen"),
        el("button", {
          type: "button", class: "btn",
          onclick: () => {
            if (confirm(`Remove ${st.name}?`)) act("/api/stations/remove", { url: st.url });
          },
        }, "Remove")));
  });

  queueMicrotask(syncRadioCards);
  return el("div", {},
    el("div", { class: "view-head" }, el("h1", {}, "Radio")),
    cards.length ? el("div", { class: "cards" }, cards) : el("div", { class: "empty" }, "No stations yet."),
    el("div", { class: "settings" }, el("section", { style: "margin-top:24px" },
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
          el("button", { type: "button", class: "btn small", onclick: () => onRemove(i) }, "Remove"))))
      : null,
    form);
}

function pathForm(kind, placeholder) {
  const input = el("input", { placeholder, "aria-label": placeholder });
  const msg = el("div", { class: "form-msg" });
  const submit = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/sources", { kind, path: input.value }));
      renderView();
    } catch (err) { msg.textContent = err.message; }
  };
  return el("div", {}, el("form", { class: "form-row", onsubmit: submit }, input,
    el("button", { class: "btn" }, "Add")), msg);
}

function settingsView() {
  const cfg = S.lib.config;
  const removeSource = (kind, list) => (i) => act("/api/sources/remove", { kind, path: list[i] });

  const from = el("input", { placeholder: "From, e.g. /Users/rob/Music/iTunes/iTunes Media", "aria-label": "Path from" });
  const to = el("input", { placeholder: "To, e.g. D:/Music", "aria-label": "Path to" });
  const remapMsg = el("div", { class: "form-msg" });
  const addRemap = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/remaps", { from: from.value, to: to.value }));
      renderView();
    } catch (err) { remapMsg.textContent = err.message; }
  };

  return el("div", { class: "settings" },
    el("div", { class: "view-head" },
      el("h1", {}, "Sources & settings"),
      el("div", { class: "spacer" }),
      el("button", {
        type: "button", class: "btn",
        onclick: () => act("/api/rescan", {}),
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
        act("/api/remaps/remove", { from: r.from, to: r.to });
      },
      form: el("div", {}, el("form", { class: "form-row", onsubmit: addRemap }, from, to,
        el("button", { class: "btn" }, "Add")), remapMsg),
    }),
  );
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

function playPlaylist(pl, startIndex) {
  S.queue = pl.tracks.map((t, i) => ({ ...t, _index: i, _playlist: pl.name }));
  S.queueSource = pl.id;
  const { order, pos } = buildOrder(S.queue.length, startIndex);
  S.order = order;
  S.pos = pos - 1;
  S.mode = "track";
  S.station = null;
  step(1, { userInitiated: true });
}

// Move through the queue, skipping tracks whose file is missing.
// `afterError` moves on from a track that failed to load, even when
// repeat-one is on (replaying a broken file would stall playback).
function step(dir, { userInitiated = false, afterError = false } = {}) {
  if (S.mode !== "track" || !S.queue.length) return;
  if (S.repeat === "one" && !userInitiated && !afterError && S.pos >= 0) {
    audio.currentTime = 0;
    audio.play().catch(() => {});
    return;
  }
  const n = S.order.length;
  for (let tries = 0; tries < n; tries++) {
    let next = S.pos + dir;
    if (next >= n || next < 0) {
      if (S.repeat !== "all" && !userInitiated) return stopAtEnd();
      next = (next + n) % n;
    }
    S.pos = next;
    if (current().available) return loadTrack(current());
  }
  stopAtEnd();
}

function stopAtEnd() {
  audio.pause();
  S.pos = -1;
  S.mode = "idle";
  updateNow();
  markPlaying();
}

function loadTrack(track) {
  audio.src = `/audio/${track.id}`;
  audio.play().catch(() => {});
  updateNow();
  markPlaying();
}

function playStation(st) {
  S.mode = "radio";
  S.station = st;
  S.pos = -1;
  audio.src = st.url;
  audio.play().catch(() => {});
  updateNow();
  markPlaying();
  syncRadioCards();
}

function togglePlay() {
  if (S.mode === "idle") {
    if (S.view.type === "playlist" && S.detail) playPlaylist(S.detail, S.shuffle ? null : 0);
    return;
  }
  if (audio.paused) {
    // A paused live stream has fallen behind; reconnect to go live again.
    if (S.mode === "radio") audio.src = S.station.url;
    audio.play().catch(() => {});
  } else {
    audio.pause();
  }
}

function previous() {
  if (S.mode !== "track") return;
  if (audio.currentTime > 3) audio.currentTime = 0;
  else step(-1, { userInitiated: true });
}

function updateNow() {
  const title = $("#now-title");
  const sub = $("#now-sub");
  const t = current();
  if (S.mode === "radio") {
    title.replaceChildren(el("span", { class: "live-badge" }, "LIVE"), S.station.name);
    sub.textContent = "Internet radio";
    document.title = `${S.station.name} · Station Player`;
  } else if (S.mode === "track" && t) {
    title.textContent = t.name;
    sub.textContent = [t.artist, t._playlist].filter(Boolean).join(" · ");
    document.title = `${t.name} · Station Player`;
  } else {
    title.textContent = "Nothing playing";
    sub.textContent = "";
    document.title = "Station Player";
  }
  $("#seek").disabled = S.mode !== "track";
  if (S.mode !== "track") $("#seek").value = 0;
  if (!("mediaSession" in navigator)) return;
  if (S.mode === "idle") { navigator.mediaSession.metadata = null; return; }
  navigator.mediaSession.metadata = new MediaMetadata(S.mode === "radio"
    ? { title: S.station.name, artist: "Live radio" }
    : { title: t.name, artist: t.artist || "", album: t.album || t._playlist || "" });
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
  rep.title = `Repeat: ${S.repeat}`;
}

// ---------- wiring ------------------------------------------------------

let seeking = false;
audio.addEventListener("timeupdate", () => {
  if (S.mode !== "track") {
    $("#time-cur").textContent = S.mode === "radio" ? fmtTime(audio.currentTime) : "0:00";
    $("#time-dur").textContent = S.mode === "radio" ? "live" : "0:00";
    return;
  }
  $("#time-cur").textContent = fmtTime(audio.currentTime);
  $("#time-dur").textContent = fmtTime(audio.duration);
  if (!seeking && audio.duration) $("#seek").value = Math.round((audio.currentTime / audio.duration) * 1000);
});
audio.addEventListener("ended", () => step(1));
audio.addEventListener("error", () => {
  if (S.mode === "track") {
    const t = current();
    if (t) t.available = false;
    step(1, { afterError: true });
  } else if (S.mode === "radio") {
    audio.pause();
    $("#now-sub").textContent = "Couldn't connect to this stream";
  }
});
for (const ev of ["play", "pause", "playing"]) {
  audio.addEventListener(ev, () => {
    syncButtons();
    syncRadioCards();
  });
}

const seek = $("#seek");
seek.addEventListener("input", () => {
  seeking = true;
  if (audio.duration) $("#time-cur").textContent = fmtTime((seek.value / 1000) * audio.duration);
});
seek.addEventListener("change", () => {
  if (audio.duration) audio.currentTime = (seek.value / 1000) * audio.duration;
  seeking = false;
});

const volume = $("#volume");
volume.value = store("volume") ?? 0.8;
audio.volume = Number(volume.value);
volume.addEventListener("input", () => {
  audio.volume = Number(volume.value);
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
  }
  syncButtons();
});
$("#btn-repeat").addEventListener("click", () => {
  S.repeat = { off: "all", all: "one", one: "off" }[S.repeat];
  store("repeat", S.repeat);
  syncButtons();
});
for (const b of document.querySelectorAll(".nav-item[data-view]")) {
  b.addEventListener("click", () => show({ type: b.dataset.view }));
}

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, button, select")) return;
  if (e.code === "Space") { e.preventDefault(); togglePlay(); }
  else if (e.code === "ArrowRight" && S.mode === "track") audio.currentTime += 10;
  else if (e.code === "ArrowLeft" && S.mode === "track") audio.currentTime -= 10;
});

if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", togglePlay);
  ms.setActionHandler("pause", () => audio.pause());
  ms.setActionHandler("nexttrack", () => step(1, { userInitiated: true }));
  ms.setActionHandler("previoustrack", previous);
}

// ---------- boot --------------------------------------------------------

(async function boot() {
  syncButtons();
  updateNow();
  try {
    await loadState();
  } catch (err) {
    $("#view").replaceChildren(el("div", { class: "errors" }, `Can't reach the player server: ${err.message}`));
    return;
  }
  let view = null;
  try { view = JSON.parse(store("view") || "null"); } catch { view = null; }
  const known = view && (view.type !== "playlist" || S.lib.playlists.some((p) => p.id === view.id));
  if (known) return show(view);
  const first = S.lib.playlists[0];
  show(first ? { type: "playlist", id: first.id } : { type: "settings" });
})();
