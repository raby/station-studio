// ---------- the mini player ----------------------------------------------------

// The player bar comes out into a small window of its own that stays on
// top of other windows, to be moved and sized anywhere, and goes back in.
// In Chrome and Edge it moves into a picture-in-picture window; in the Mac
// app the window itself shrinks to the bar and floats (macwindow.py), and
// grows back again.
const MINI = { on: false, win: null, spot: null, opening: false };
const canMini = () => NATIVE || "documentPictureInPicture" in window;

async function popOutPlayer() {
  if (MINI.on) return putBackPlayer();
  if (MINI.opening) return;  // a second click while the window is coming
  if (NATIVE) {
    MINI.on = true;
    document.body.classList.add("mini");
    tellApp("mini");
  } else {
    let pip;
    MINI.opening = true;
    try {
      // Small every time, not whatever size Chrome last had it at.
      pip = await window.documentPictureInPicture.requestWindow({ ...MINI_SIZE, preferInitialWindowPlacement: true });
    } catch {
      toast("The player can't come out of this window", { error: true });
      return;
    } finally {
      MINI.opening = false;
    }
    const bar = document.querySelector(".bar");
    const doc = pip.document;
    doc.title = "Station Player";
    doc.head.append(el("link", { rel: "stylesheet", href: new URL("/style.css", location.href).href }));
    doc.body.className = "mini-window";
    MINI.spot = document.createComment("the player bar, while it's out");
    bar.before(MINI.spot);
    doc.body.append(bar);
    // Space plays and pauses there too.
    doc.addEventListener("keydown", (e) => {
      if (e.code === "Space" && !e.target.closest("input, button, select")) { e.preventDefault(); togglePlay(); }
    });
    // Closing the little window puts the bar back.
    pip.addEventListener("pagehide", () => putBackPlayer());
    pip.addEventListener("resize", fitMini);
    MINI.win = pip;
    miniDoc = doc;
    MINI.on = true;
  }
  fitMini();
  syncMiniButton();
  announce("Mini player");
}

function putBackPlayer({ fromApp = false } = {}) {
  if (!MINI.on) return;
  MINI.on = false;
  if (NATIVE) {
    document.body.classList.remove("mini");
    $(".bar").style.zoom = "";
    if (!fromApp) tellApp("full");
  } else {
    const bar = miniDoc && miniDoc.querySelector(".bar");
    if (bar) bar.style.zoom = "";
    if (bar && MINI.spot) MINI.spot.replaceWith(bar);
    miniDoc = null;
    const pip = MINI.win;
    MINI.win = MINI.spot = null;
    if (pip && !pip.closed) pip.close();
  }
  syncMiniButton();
  refocus("#btn-mini");
}

// Made bigger, the bar grows with its window, so a big mini player has
// big buttons (laid out for 380 by 110, and up to twice that).
const MINI_SIZE = { width: 380, height: 110 };
const MINI_BASE = [MINI_SIZE.width, MINI_SIZE.height];
function fitMini() {
  if (!MINI.on) return;
  const view = MINI.win || window;
  const bar = $(".bar");
  const scale = Math.min(view.innerWidth / MINI_BASE[0], view.innerHeight / MINI_BASE[1]);
  bar.style.zoom = String(Math.max(1, Math.min(2, scale)).toFixed(3));
}
window.addEventListener("resize", () => { if (NATIVE) fitMini(); });

function syncMiniButton() {
  const btn = $("#btn-mini");
  if (!btn) return;
  btn.hidden = !canMini();
  const label = MINI.on ? "Put the player back" : "Pop out the player";
  btn.title = label;
  btn.setAttribute("aria-label", label);
  btn.replaceChildren(icon(MINI.on ? "popin" : "popout"));
}
$("#btn-mini").addEventListener("click", () => popOutPlayer());
syncMiniButton();

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
  // View › Mini Player, and the window putting itself back (as when the
  // page reloads).
  mini(on) {
    const want = on === undefined ? !MINI.on : on;
    if (want && !MINI.on) popOutPlayer();
    else if (!want) putBackPlayer({ fromApp: on === false });
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

