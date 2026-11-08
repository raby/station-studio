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

