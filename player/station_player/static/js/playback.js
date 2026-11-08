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

