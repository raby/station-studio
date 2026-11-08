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
  putBackPlayer();  // nor in the mini player
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

