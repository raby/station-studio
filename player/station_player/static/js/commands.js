// ---------- commands sent to the page ------------------------------------------

// The server keeps an inbox for the page, checked every second (see boot):
// files chosen with "Open With" in Finder / Explorer, the phone remote's
// commands, a station's desktop icon, notices, schedule switches and the
// alarm going off. Each is acted on once, in whichever window gets to it
// first; an opened file plays its playlist from that song.
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

// What the inbox asks for. The Dock / taskbar menu (runLaunch) and the
// Mac app's Controls and Dock menus (window.stationApp.command) use it too.
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
