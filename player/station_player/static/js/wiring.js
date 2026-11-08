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
      // The relay failed: try the station itself, but only playing if
      // the listener didn't pause while it was connecting.
      playStation(S.station, { via: "direct", autoplay: radioWanted });
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
onAudio("pause", () => { radioWanted = false; cancelHandoff(); endFade(); saveSession(); });
onAudio("play", () => { radioWanted = true; });
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

