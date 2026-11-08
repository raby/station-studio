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

// ---------- phone remote ------------------------------------------------------

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

