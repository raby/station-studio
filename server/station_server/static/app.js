"use strict";
// Station Server's page. Plain JavaScript, no build step, like station-player's:
// it polls /api/state and redraws the view; every change is a POST that
// carries the X-Station-Server header (see web.py).

const $ = (sel) => document.querySelector(sel);
// Inside the downloaded Mac app's own window (macwindow.py), not a browser.
const NATIVE = Boolean(window.webkit?.messageHandlers?.stationApp) || /StationServerApp\//.test(navigator.userAgent);
function tellApp(message) {
  try { window.webkit.messageHandlers.stationApp.postMessage(message); return true; } catch { return false; }
}
const S = {
  state: null,
  view: { type: "station", id: null },
  editing: null,       // the station form's values while it's open
  stateAt: 0,          // when `state` arrived (performance.now()), for the progress bar
  installPrompt: null,
  missingOpen: false,  // the list of tracks that can't be found, kept open across redraws
  updating: null,      // how far an update has got, while this page follows it
  listening: null,     // the station whose broadcast this page is playing (Listen)
  alertsForm: null,    // Settings › Alerts while it's being changed
  checks: {},          // each station's last Ready check: { busy, at, checks }
};
// Seen from a phone or another computer (Settings › Phone access): only
// watching, listening and on/off air.
const remoteView = () => !!S.state?.remote_view;

// ---------- small helpers ---------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

// The same line icons as station-player (24x24, stroked).
const ICONS = {
  plus: ["M12 5v14", "M5 12h14"],
  radio: ["c 12 12 2", "M16.24 7.76a6 6 0 0 1 0 8.48", "M7.76 16.24a6 6 0 0 1 0-8.48", "M19.07 4.93a10 10 0 0 1 0 14.14", "M4.93 19.07a10 10 0 0 1 0-14.14"],
  tower: ["M12 10l-4 11", "M12 10l4 11", "M9.6 16.5h4.8", "*c 12 8 1.6", "M8.5 4.5a5 5 0 0 0 0 7", "M15.5 4.5a5 5 0 0 1 0 7"],
  settings: ["M4 21v-7", "M4 10V3", "M12 21v-9", "M12 8V3", "M20 21v-5", "M20 12V3", "M1.5 14h5", "M9.5 8h5", "M17.5 16h5"],
  power: ["M18.36 6.64a9 9 0 1 1-12.73 0", "M12 2v10"],
  log: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M8 13h8", "M8 17h8"],
  help: ["c 12 12 9", "M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 3.9", "*c 12 17 1.1"],
  play: ["*M7 4.8v14.4a.8.8 0 0 0 1.2.7l11.4-7.2a.8.8 0 0 0 0-1.4L8.2 4.1A.8.8 0 0 0 7 4.8z"],
  stop: ["*r 6 6 12 12 2"],
  next: ["*M5 5.5v13a.7.7 0 0 0 1.1.6l9.4-6.5a.7.7 0 0 0 0-1.2L6.1 4.9A.7.7 0 0 0 5 5.5z", "M19 5v14"],
  edit: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6"],
  folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z", "M14 2v6h6", "M9 15h6"],
  check: ["M20 6L9 17l-5-5"],
  refresh: ["M21 12a9 9 0 1 1-2.64-6.36L21 8", "M21 3v5h-5"],
  download: ["M12 3v12", "M7 10l5 5 5-5", "M5 21h14"],
  app: ["r 3 4 18 16 2.5", "M3 9h18"],
  copy: ["r 9 9 12 12 2", "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"],
  alert: ["M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
  headphones: ["M3 18v-6a9 9 0 0 1 18 0v6", "M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3z", "M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"],
  bell: ["M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9", "M13.73 21a2 2 0 0 1-3.46 0"],
  phone: ["r 6 2 12 20 2.5", "M11 18h2"],
  mic: ["r 9 2 6 12 3", "M19 10v1a7 7 0 0 1-14 0v-1", "M12 18v4", "M8 22h8"],
};
const SVG = "http://www.w3.org/2000/svg";
function icon(name) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i");
  svg.setAttribute("aria-hidden", "true");
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
const iconLabel = (name, label) => [icon(name), el("span", {}, label)];
const btn = (label, iconName, onclick, cls = "btn") =>
  el("button", { type: "button", class: cls, onclick }, iconName ? iconLabel(iconName, label) : label);

function clock(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const h = Math.floor(secs / 3600), m = Math.floor(secs / 60) % 60, s = secs % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
const hhmm = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dayTime = (iso) => new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

let toastTimer = null;
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Station-Server": "1" },
    body: JSON.stringify(body),
  };
  const resp = await fetch(path, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `${resp.status} ${resp.statusText}`);
  return data;
}

function setState(state) {
  S.state = state;
  S.stateAt = performance.now();
}

// ---------- server addresses ------------------------------------------------
// Stored as one URL (see senders.py); the form shows its parts.

function parseServer(url) {
  const out = { kind: "shoutcast", host: "", port: "8000", sid: "1", mount: "/stream", user: "source" };
  const m = /^(shoutcast|icecast):\/\/(?:([^@/]*)@)?([^:/?]+)(?::(\d+))?([^?]*)(?:\?(.*))?$/i.exec(url || "");
  if (!m) return out;
  out.kind = m[1].toLowerCase();
  out.host = m[3];
  out.port = m[4] || "8000";
  if (out.kind === "icecast") {
    out.mount = m[5] || "/stream";
    out.user = decodeURIComponent((m[2] || "source").split(":")[0]) || "source";
  } else {
    out.sid = new URLSearchParams(m[6] || "").get("sid") || "1";
  }
  return out;
}

function buildServer(p) {
  if (!p.host) return "";
  if (p.kind === "icecast") {
    const user = p.user && p.user !== "source" ? `${encodeURIComponent(p.user)}@` : "";
    const mount = p.mount.startsWith("/") ? p.mount : `/${p.mount}`;
    return `icecast://${user}${p.host}:${p.port}${mount}`;
  }
  return `shoutcast://${p.host}:${p.port}${p.sid && p.sid !== "1" ? `?sid=${p.sid}` : ""}`;
}

// ---------- sidebar -----------------------------------------------------------

function stationById(id) {
  return (S.state?.stations || []).find((s) => s.station.id === id);
}

function renderNav() {
  const list = $("#nav-stations");
  list.replaceChildren();
  const stations = S.state?.stations || [];
  if (!stations.length) list.append(el("li", { class: "nav-empty" }, "No stations yet"));
  for (const s of stations) {
    const current = S.view.type === "station" && S.view.id === s.station.id || S.view.type === "edit" && S.view.id === s.station.id;
    list.append(el("li", {},
      el("button", {
        type: "button", class: "nav-item", "aria-current": current ? "true" : null,
        title: STATE_WORDS[s.state],
        onclick: () => go({ type: "station", id: s.station.id }),
      }, el("span", {}, s.station.name), el("span", { class: `dot ${s.state}`, "aria-label": STATE_WORDS[s.state] })),
    ));
  }
  for (const b of document.querySelectorAll(".nav-item[data-view]")) {
    b.setAttribute("aria-current", b.dataset.view === S.view.type ? "true" : "false");
  }
  $("#version").textContent = S.state ? `version ${S.state.version}` : "";
  document.body.classList.toggle("remote", remoteView());
  renderUpdate();
}

// ---------- new versions --------------------------------------------------------
// As in Station Player: a note at the foot of the sidebar until dismissed
// for that version, and while updating, how far it's got.

function remember(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`station-server:${key}`);
    localStorage.setItem(`station-server:${key}`, value);
  } catch { /* private window: forget it */ }
  return null;
}

function renderUpdate() {
  const box = $("#update");
  const latest = S.state?.update;
  const u = S.state?.updating || S.updating;
  if (remoteView()) { box.hidden = true; return; }
  if (u && u.stage !== "failed") {
    box.replaceChildren(el("div", {}, updateStageText(u)));
    box.hidden = false;
    return;
  }
  if (!latest || remember("dismissed-update") === latest) {
    // A browser window or tab, once: the Mac app has a window of its own now.
    if (S.state?.mac_app && !NATIVE && S.state.open_window && !remember("mac-window-offered")) {
      const done = () => { remember("mac-window-offered", "1"); renderUpdate(); };
      box.replaceChildren(
        el("div", {}, "Station Server now has a window of its own."),
        el("div", { class: "row" },
          el("button", {
            type: "button", class: "btn small primary",
            onclick: async () => { done(); try { await api("/api/window", {}); } catch (e) { toast(e.message); } },
          }, "Open it"),
          el("button", { type: "button", class: "btn small", onclick: done }, "Not now")));
      box.hidden = false;
      return;
    }
    box.hidden = true;
    return;
  }
  box.replaceChildren(...[
    el("div", {}, `Station Server ${latest} is out.`),
    u && u.stage === "failed" && u.version === latest ? updateFailure(u) : null,
    el("div", { class: "row" },
      S.state.can_update
        ? el("button", { type: "button", class: "btn small primary", onclick: () => startUpdate(latest) }, "Update now")
        : el("a", { class: "btn small primary", href: S.state.release_page, target: "_blank", rel: "noopener" }, "Download it"),
      el("button", {
        type: "button", class: "btn small",
        onclick: () => { remember("dismissed-update", latest); renderUpdate(); },
      }, "Not now")),
  ].filter(Boolean));  // replaceChildren, unlike el(), would show a null
  box.hidden = false;
}

function updateStageText(u) {
  if (u.stage === "downloading") return `Downloading Station Server ${u.version}… ${u.percent || 0}%`;
  if (u.stage === "installing") return `Installing Station Server ${u.version}…`;
  return `Restarting Station Server… ${u.version} opens here by itself.`;
}

function updateFailure(u) {
  return el("div", { class: "update-failed", role: "alert" }, u.error, " ",
    el("a", { href: u.page, target: "_blank", rel: "noopener" }, "Download it yourself"), ".");
}

// Update now: Station Server downloads and installs the new version, quits,
// and the new one starts (putting the stations that were on air back on
// air); this page waits for it and reloads into it.
async function startUpdate(version) {
  const live = (S.state?.stations || []).filter((s) => s.state === "on").length;
  if (live && !confirm(`Update now? ${live === 1 ? "The station goes" : `The ${live} stations go`} off air for a moment while Station Server restarts, then back on air.`)) return;
  const from = S.state.version;
  try {
    setState(await api("/api/update", { version }));
  } catch (err) {
    toast(err.message);
    return;
  }
  S.updating = S.state.updating;
  renderUpdate();
  syncUpdateSettings();
  const until = Date.now() + 10 * 60 * 1000;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 1000));
    let state = null;
    try {
      const res = await fetch("/api/state", { cache: "no-store" });
      if (res.ok) state = await res.json();
    } catch { /* restarting */ }
    if (!state) {
      S.updating = { ...(S.updating || { version }), stage: "restarting" };
      renderUpdate();
      continue;
    }
    if (state.version !== from) {
      location.reload();
      return;
    }
    setState(state);
    S.updating = state.updating;
    renderUpdate();
    syncUpdateSettings();
    if (!state.updating || state.updating.stage === "failed") return;
  }
  S.updating = { version, stage: "failed", page: `https://github.com/raby/station-studio/releases/tag/server-v${version}`,
    error: "Station Server didn't come back after updating. Open it again from Applications or the Start menu." };
  renderUpdate();
}

const U = { checked: false };

function updateSettings() {
  const latest = S.state?.update;
  const u = S.state?.updating || S.updating;
  const busy = u && u.stage !== "failed";
  const version = S.state?.version || "";
  let line;
  if (busy) line = updateStageText(u);
  else if (latest) line = `Station Server ${latest} is out. You have ${version}.`;
  else if (U.checked) line = `You have the newest version, ${version}.`;
  else line = `You have Station Server ${version}.`;
  const notices = el("input", { type: "checkbox", id: "check-updates" });
  notices.checked = !!S.state?.check_updates;
  notices.addEventListener("change", async () => {
    try { setState(await api("/api/settings", { check_updates: notices.checked })); toast("Saved"); }
    catch (e) { toast(e.message); notices.checked = !notices.checked; }
  });
  return el("div", { id: "update-settings", class: "update-settings" },
    el("p", { role: "status" }, line),
    u && u.stage === "failed" ? updateFailure(u) : null,
    el("div", { class: "row" },
      latest && !busy && S.state.can_update ? el("button", {
        type: "button", class: "btn primary", id: "btn-update", onclick: () => startUpdate(latest),
      }, `Update to ${latest}`) : null,
      latest && !busy && !S.state.can_update
        ? el("a", { class: "btn primary", href: S.state.release_page, target: "_blank", rel: "noopener" }, `Download ${latest}`) : null,
      busy ? null : el("button", {
        type: "button", class: "btn", id: "btn-check-update",
        onclick: async (e) => {
          e.currentTarget.disabled = true;
          try {
            setState(await api("/api/update/check", {}));
            U.checked = true;
            if (!S.state.update) toast("Station Server is up to date");
          } catch (err) {
            toast(err.message);
          }
          syncUpdateSettings();
          renderUpdate();
          $("#btn-update, #btn-check-update")?.focus();
        },
      }, "Check for updates")),
    el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, notices,
      "Tell me when a new version is out (asks GitHub once a day)"),
    el("p", { class: "help" }, S.state?.can_update
      ? "Updating downloads the new version from Station Server's GitHub page, checks it's the genuine file, and restarts Station Server. Stations that were on air go back on air; your settings and play logs are kept."
      : "This copy of Station Server was installed with pip, so update it the same way. The downloaded Mac and Windows apps update themselves."));
}

function syncUpdateSettings() {
  const old = $("#update-settings");
  if (old) old.replaceWith(updateSettings());
}

const STATE_WORDS = { off: "Off air", starting: "Going on air…", on: "On air", waiting: "Trying again…", stopping: "Going off air…", error: "Problem" };

function go(view) {
  S.view = view;
  S.editing = null;
  render();
  $("#main").focus({ preventScroll: true });
}

// ---------- views ---------------------------------------------------------------

function render() {
  renderNav();
  const errors = [];
  if (S.state?.ffmpeg_error) errors.push(`ffmpeg: ${S.state.ffmpeg_error}`);
  for (const w of S.state?.warnings || []) errors.push(w);
  $("#errors").hidden = !errors.length;
  $("#errors").replaceChildren(...errors.map((e) => el("div", {}, e)));
  const view = $("#view");
  const t = S.view.type;
  if (t === "new" || t === "edit") return renderForm(view);  // the form keeps its own state
  view.dataset.form = "";
  if (t === "log") return view.replaceChildren(renderLog());
  if (t === "settings" && !remoteView()) return view.replaceChildren(renderSettings());
  if (t === "help") return view.replaceChildren(renderHelp());
  if (!S.state) return;
  let st = S.view.id && stationById(S.view.id);
  if (!st && S.state.stations.length) {
    st = S.state.stations[0];
    S.view = { type: "station", id: st.station.id };
    renderNav();
  }
  view.replaceChildren(st ? renderStation(st) : renderWelcome());
}

function renderWelcome() {
  return el("div", { class: "empty-state" },
    el("div", { class: "glyph big" }, icon("tower")),
    el("h2", {}, "Put a station on air"),
    el("p", {}, "Station Server plays your station-studio playlists straight to a SHOUTcast or Icecast server, with song titles. No cable, no iTunes, no Winamp."),
    btn("Add a station", "plus", () => go({ type: "new" }), "btn primary big"),
  );
}

function renderStation(s) {
  const st = s.station;
  const on = s.state === "on";
  const busy = s.state === "starting" || s.state === "stopping" || s.state === "waiting";
  const rehearsing = s.rehearsal && (on || busy);
  const head = el("div", { class: "view-head" },
    el("div", {},
      el("div", { class: `status-pill ${s.state}${rehearsing ? " rehearsal" : ""}` }, el("span", { class: "dot" }),
        rehearsing ? (on ? "Rehearsal" : `Rehearsal: ${STATE_WORDS[s.state]}`) : STATE_WORDS[s.state]),
      el("h1", {}, st.name),
      el("div", { class: "meta" }, (rehearsing ? `Test stream: ${st.test_server}` : st.server) || "No server set"),
    ),
    el("div", { class: "spacer" }),
    rehearsing ? btn("Go live", "play", () => goLive(st), "btn primary") : null,
    on || busy
      ? btn(rehearsing ? "Stop rehearsal" : "Take off air", "stop", () => setOnAir(st.id, false), "btn")
      : btn("Go on air", "play", () => setOnAir(st.id, true), "btn primary"),
    !on && !busy && st.test_server ? btn("Rehearse", "radio", () => setOnAir(st.id, true, true), "btn") : null,
    on && s.live ? btn("End live show", "stop", () => endLive(st.id, s.live), "btn") : null,
    on && !s.live ? btn("Skip", "next", () => post(`/api/stations/${st.id}/skip`, "Skipped")) : null,
    on ? btn(S.listening === st.id ? "Stop listening" : "Listen", S.listening === st.id ? "stop" : "headphones",
      () => listen(S.listening === st.id ? null : st.id), S.listening === st.id ? "btn primary" : "btn") : null,
    remoteView() ? null : btn("Ready check", "check", () => readyCheck(st.id), "btn ghost"),
    remoteView() ? null : btn("Edit", "edit", () => go({ type: "edit", id: st.id }), "btn ghost"),
  );
  const parts = [head];
  const rc = S.checks[st.id];
  if (rc) parts.push(renderChecks(st.id, rc));
  if (s.state === "waiting") {
    parts.push(el("div", { class: "errors" }, `Can't go on air: ${s.error}. `,
      s.retry_in != null ? `Trying again in ${s.retry_in} s.` : "Trying again…",
      el("span", { class: "help" }, " It keeps trying until it's on air or you take it off air.")));
  } else if (s.error) parts.push(el("div", { class: "errors" }, s.error));
  if (s.on_fallback) parts.push(el("div", { class: "errors" }, "None of the playlist's tracks can be read, so the station is playing the fallback. Check the music folders in Edit; the playlist is tried again after the fallback."));
  else if (s.stuck) parts.push(el("div", { class: "errors" }, "None of the playlist's tracks can be read, so the station is sending silence. Check the music folders in Edit, or set a fallback there."));

  if (on && s.live_input && s.live_input.error) parts.push(el("div", { class: "errors" }, s.live_input.error));
  if (on && s.live) {
    const l = s.live;
    parts.push(el("div", { class: "panel now live" },
      el("div", { class: "glyph big" }, icon("mic")),
      el("div", { class: "show-kicker" }, `Live${l.presenter ? `: ${l.presenter}` : ""}`),
      el("div", { class: "title" }, l.title || "A presenter is live"),
      el("div", { class: "artist" }, `On air since ${hhmm(l.since)}, from ${l.address}. The music comes back where the week's clock says when they finish.`),
      l.recording ? el("div", { class: "artist" }, `Recording to ${l.recording}`) : null,
    ));
  } else if (on && s.now) {
    const n = s.now;
    parts.push(el("div", { class: "panel now" },
      el("div", { class: "glyph big" }, icon("radio")),
      n.show ? el("div", { class: "show-kicker" }, `Show: ${n.show}`) : null,
      n.jingle ? el("div", { class: "show-kicker" }, "Jingle") : null,
      el("div", { class: "title" }, n.jingle || n.title),
      el("div", { class: "artist" }, n.jingle ? `After ${n.title}` : [n.artist, n.album].filter(Boolean).join(" · ") || " "),
      n.jingle ? null : el("div", { class: "meter" },
        el("span", { id: "now-elapsed" }, clock(n.elapsed)),
        el("div", { class: "bar" }, el("div", { id: "now-bar", style: `width:${Math.min(100, 100 * n.elapsed / (n.seconds || 1))}%` })),
        el("span", {}, clock(n.seconds)),
      ),
    ));
  }

  const facts = [];
  if (on) {
    facts.push(el("span", { class: s.connected ? "ok" : "warn" },
      s.connected ? `Connected to ${s.server}` : `Reconnecting to ${s.server}…`));
    if (s.audience) facts.push(el("span", { title: `Most at once: ${s.audience.peak}` },
      el("b", {}, `${s.audience.listeners}`), s.audience.max ? ` of ${s.audience.max}` : "",
      s.audience.listeners === 1 ? " listener" : " listeners"));
    if (s.now && s.now.gain_db && !s.live) facts.push(el("span", {}, "Levelled ", el("b", {}, `${s.now.gain_db > 0 ? "+" : ""}${s.now.gain_db} dB`),
      s.now.levelled_by === "measured" ? " (measured)" : ""));
    if (s.measuring) facts.push(el("span", {}, `Measuring tracks: ${s.measuring} to go`));
  }
  if (s.playlist) {
    facts.push(el("span", {}, "Playlist ", el("b", {}, s.playlist.name)));
    facts.push(el("span", {}, `${s.playlist.tracks} tracks · ${s.playlist.hours} hours`));
    if (s.playlist.next) facts.push(el("span", { class: "ok" }, "Next: ", el("b", {}, s.playlist.next.name), " after this track"));
  }
  if (s.next_week && !(s.playlist && s.playlist.next)) {
    facts.push(el("span", {}, "Next week ", el("b", {}, s.next_week.name), ` from ${dayTime(s.next_week.at)}`));
  }
  facts.push(el("span", {}, st.start_mode === "clock"
    ? `Week starts ${DAYS[st.week_start.day]} ${st.week_start.time}` : "Starts from track 1"));
  facts.push(el("span", {}, `MP3 ${st.bitrate} kbps`));
  if (st.crossfade) facts.push(el("span", {}, `Crossfade ${st.crossfade} s`));
  if (st.jingles) facts.push(el("span", {}, `A jingle every ${st.jingle_every} songs`));
  if (on && s.live_input && s.live_input.listening && !s.live) facts.push(el("span", {}, "Presenters can go live on port ", el("b", {}, `${s.live_input.port}`)));
  parts.push(el("div", { class: "facts" }, facts));
  if (s.shows && s.shows.length) {
    parts.push(el("div", { class: "facts" }, el("span", {}, "Shows"),
      s.shows.map((sh) => el("span", { class: "show-chip" }, el("b", {}, sh.label), ` ${sh.name}`,
        sh.seconds ? ` (${clock(sh.seconds)})` : el("span", { class: "warn" }, " (can't be found)")))));
  }

  if (s.playlist && s.playlist.missing_count) {
    parts.push(el("details", { class: "missing", open: S.missingOpen, ontoggle: (e) => { S.missingOpen = e.target.open; } },
      el("summary", {}, s.playlist.missing_count === 1 ? "1 track can't be found and is left out" : `${s.playlist.missing_count} tracks can't be found and are left out`),
      el("ul", {}, s.playlist.missing.map((m) => el("li", {}, `${m.artist ? m.artist + " – " : ""}${m.title}`,
        el("span", { class: "help" }, `  ${m.location}`)))),
    ));
  }

  if (on && s.order.length && !s.live) {
    parts.push(el("h2", { class: "section" }, "Running order"));
    parts.push(el("table", { class: "tracks" },
      el("thead", {}, el("tr", {}, el("th", { class: "time" }, "Time"), el("th", { class: "title" }, "Title"),
        el("th", {}, "Artist"), el("th", { class: "dur" }, "Length"))),
      el("tbody", {}, s.order.map((r, i) => el("tr", { class: [i === 0 ? "playing" : "", r.show ? "show" : ""].join(" ").trim() || null },
        el("td", { class: "time" }, hhmm(r.at)),
        el("td", { class: "title" }, r.show || r.title, r.from ? el("span", { class: "help" }, ` from ${clock(r.from)}`) : null),
        el("td", { class: "artist" }, r.show ? "Show" : (r.artist || "")),
        el("td", { class: "dur", title: r.cut ? "Cut short for the show" : null }, clock(r.seconds), r.cut ? " ✂" : ""),
      ))),
    ));
  } else if (!on && !busy && !s.error) {
    parts.push(el("p", { class: "help" }, st.playlist && st.server
      ? "Off air. Go on air to start the playlist; the running order shows here."
      : "Set the station's playlist and server in Edit, then go on air."));
  }
  return el("div", {}, parts);
}

// Between polls the progress bar moves on by itself.
function tick() {
  if (S.view.type !== "station" || !S.state) return;
  const s = stationById(S.view.id);
  if (!s || !s.now) return;
  const elapsed = s.now.elapsed + (performance.now() - S.stateAt) / 1000;
  const e = $("#now-elapsed"), b = $("#now-bar");
  if (e) e.textContent = clock(Math.min(elapsed, s.now.seconds));
  if (b) b.style.width = `${Math.min(100, 100 * elapsed / (s.now.seconds || 1))}%`;
}

async function endLive(id, live) {
  if (!confirm(`Take ${live.presenter || "the presenter"} off air? The music comes back, and their encoder can't reconnect for two minutes.`)) return;
  await post(`/api/stations/${id}/endlive`, "Live show ended");
}

async function setOnAir(id, on, rehearse = false) {
  try {
    setState(await api(`/api/stations/${id}/on`, { on, rehearse }));
    render();
  } catch (e) { toast(e.message); }
}

async function goLive(st) {
  if (!confirm(`Go live on ${st.server}?\n\nStop Winamp's SHOUTcast plugin first (and iTunes): the server takes one source at a time. Listeners hear a short gap while the station switches over.`)) return;
  setOnAir(st.id, true, false);
}

// Ready check: everything the station needs to run by itself, in one go.
async function readyCheck(id) {
  S.checks[id] = { busy: true, checks: S.checks[id]?.checks || [] };
  render();
  try {
    const r = await api(`/api/stations/${id}/check`, {});
    S.checks[id] = { busy: false, at: new Date(), checks: r.checks };
  } catch (e) {
    S.checks[id] = { busy: false, at: new Date(), checks: [{ name: "Ready check", status: "fail", detail: e.message }] };
  }
  render();
}

function renderChecks(id, rc) {
  const marks = { ok: "✓", warn: "!", fail: "✕" };
  const fails = rc.checks.filter((c) => c.status === "fail").length;
  const warns = rc.checks.filter((c) => c.status === "warn").length;
  const summary = rc.busy ? "Checking…" : fails ? `${fails} thing${fails > 1 ? "s" : ""} to fix before going live`
    : warns ? "Ready to go live, with notes" : "Ready to go live";
  return el("div", { class: "panel checks" },
    el("div", { class: "checks-head" }, el("b", {}, summary),
      rc.at && !rc.busy ? el("span", { class: "help" }, ` · checked ${rc.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`) : null,
      el("div", { class: "spacer" }),
      btn("Close", null, () => { delete S.checks[id]; render(); }, "btn small ghost")),
    el("ul", {}, rc.checks.map((c) => el("li", { class: c.status },
      el("span", { class: "mark", "aria-label": c.status }, marks[c.status]), el("b", {}, c.name), " ", c.detail))));
}

async function post(path, message) {
  try {
    setState(await api(path, {}));
    if (message) toast(message);
    render();
  } catch (e) { toast(e.message); }
}

// Listen: the broadcast as it goes to the server (about two seconds ahead
// of what listeners hear). One <audio> outside the view, which redraws.
function listen(id) {
  let audio = $("#listen-audio");
  if (!audio) {
    audio = el("audio", { id: "listen-audio", preload: "none" });
    audio.addEventListener("error", () => {
      if (!S.listening) return;
      S.listening = null;
      toast("Listening stopped: the station went off air or Station Server can't be reached");
      render();
    });
    document.body.append(audio);
  }
  S.listening = id;
  if (id) {
    audio.src = `/api/stations/${id}/listen.mp3?at=${Date.now()}`;
    audio.play().catch((e) => { S.listening = null; toast(`Can't play it here: ${e.message}`); render(); });
  } else {
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
  }
  render();
}

// A station listened to that goes off air stops being listened to.
function checkListening() {
  if (!S.listening) return;
  const s = stationById(S.listening);
  if (!s || s.state !== "on") listen(null);
}

// ---------- the station form ------------------------------------------------------

function formValues(st) {
  const srv = parseServer(st?.server);
  return {
    name: st?.name || "",
    genre: st?.genre || "",
    url: st?.url || "",
    public: !!st?.public,
    playlist: st?.playlist || "",
    music_folders: (st?.music_folders || []).join("\n"),
    remaps: (st?.remaps || []).map((r) => `${r.from} = ${r.to}`).join("\n"),
    ...srv,
    password: "",
    has_password: !!st?.has_password,
    ...testStreamValues(st?.test_server),
    test_password: "",
    has_test_password: !!st?.has_test_password,
    bitrate: String(st?.bitrate || 128),
    level: st ? !!st.level : true,
    start_mode: st?.start_mode || "clock",
    day: String(st?.week_start?.day ?? 0),
    time: st?.week_start?.time || "00:00",
    shows: (st?.shows || []).map((sh) => ({ ...sh, days: [...sh.days] })),
    fallback: st?.fallback || "",
    crossfade: String(st?.crossfade || 0),
    jingles: st?.jingles || "",
    jingle_every: String(st?.jingle_every || 4),
    live: !!st?.live,
    live_port: String(st?.live_port || 8010),
    live_password: "",
    has_live_password: !!st?.has_live_password,
    record_live: st ? st.record_live !== false : true,
  };
}

function formPayload(v) {
  return {
    name: v.name, genre: v.genre, url: v.url, public: v.public,
    playlist: v.playlist,
    music_folders: v.music_folders.split("\n").map((x) => x.trim()).filter(Boolean),
    remaps: v.remaps.split("\n").map((line) => {
      const i = line.lastIndexOf("=");
      return i > 0 ? { from: line.slice(0, i).trim(), to: line.slice(i + 1).trim() } : null;
    }).filter((r) => r && r.from && r.to),
    server: buildServer(v),
    password: v.password,
    test_server: buildTestServer(v),
    test_password: v.test_password,
    bitrate: Number(v.bitrate),
    level: v.level,
    start_mode: v.start_mode,
    week_start: { day: Number(v.day), time: v.time },
    shows: v.shows,
    fallback: v.fallback,
    crossfade: Number(v.crossfade),
    jingles: v.jingles,
    jingle_every: Number(v.jingle_every),
    live: v.live,
    live_port: Number(v.live_port),
    live_password: v.live_password,
    record_live: v.record_live,
  };
}

// The test stream: the same kind of server as the live one, usually the
// same address with a spare port or stream number.
function testStreamValues(url) {
  if (!url) return { t_host: "", t_port: "", t_sid: "", t_mount: "" };
  const t = parseServer(url);
  return { t_host: t.host, t_port: t.port, t_sid: t.sid === "1" ? "" : t.sid, t_mount: t.kind === "icecast" ? t.mount : "" };
}

function buildTestServer(v) {
  if (!v.t_host && !v.t_port && !v.t_sid && !v.t_mount) return "";
  return buildServer({ ...v, host: v.t_host || v.host, port: v.t_port || v.port, sid: v.t_sid || "1", mount: v.t_mount || v.mount });
}

function renderForm(view) {
  const editing = S.view.type === "edit";
  const s = editing ? stationById(S.view.id) : null;
  if (editing && !s) return go({ type: "station", id: null });
  if (!S.editing) S.editing = formValues(s?.station);
  const v = S.editing;
  if (view.dataset.form === `${S.view.type}:${S.view.id || ""}`) return;  // already showing: keep what's typed
  view.dataset.form = `${S.view.type}:${S.view.id || ""}`;

  const bind = (key, input) => {
    input.addEventListener("input", () => {
      v[key] = input.type === "checkbox" ? input.checked : input.value;
      if (key === "kind") redraw();
    });
    return input;
  };
  const text = (key, label, attrs = {}) => el("label", { class: attrs.narrow ? "narrow" : null }, label,
    bind(key, el("input", { type: attrs.type || "text", value: v[key], placeholder: attrs.placeholder || null, autocomplete: "off" })));
  const check = (key, label) => {
    const input = bind(key, el("input", { type: "checkbox" }));
    input.checked = !!v[key];
    return el("label", { class: "check" }, input, label);
  };
  const select = (key, label, options, narrow) => {
    const s = bind(key, el("select", {}, options.map(([val, words]) => el("option", { value: val }, words))));
    s.value = v[key];
    return el("label", { class: narrow ? "narrow" : null }, label, s);
  };
  const radio = (key, val, label) => {
    const input = el("input", { type: "radio", name: key, value: val });
    input.checked = v[key] === val;
    input.addEventListener("change", () => { v[key] = val; });
    return el("label", {}, input, label);
  };
  const playlistMsg = el("div", { class: "form-msg", role: "status" });
  const serverMsg = el("div", { class: "form-msg", role: "status" });
  const testMsg = el("div", { class: "form-msg", role: "status" });
  const formMsg = el("div", { class: "form-msg", role: "status" });
  const fallbackMsg = el("div", { class: "form-msg", role: "status" });
  const jinglesMsg = el("div", { class: "form-msg", role: "status" });

  async function pick(kind, into = (path) => { v.playlist = path; }, msg = playlistMsg) {
    try {
      const r = await api("/api/pick", { kind });
      if (r.path) { into(r.path); redraw(); }
    } catch (e) { msg.className = "form-msg"; msg.textContent = e.message; }
  }
  const showsMsg = el("div", { class: "form-msg", role: "status" });
  function showRow(sh, i) {
    const field = (label, input, cls) => el("label", { class: cls || null }, label, input);
    const name = el("input", { type: "text", value: sh.name || "", placeholder: "Saturday Night Mix", autocomplete: "off" });
    name.addEventListener("input", () => { sh.name = name.value; });
    const time = el("input", { type: "time", value: sh.time || "20:00" });
    time.addEventListener("input", () => { sh.time = time.value; });
    const source = el("input", { type: "text", value: sh.source || "", placeholder: "~/Music/Shows/Saturday Night Mix.mp3", autocomplete: "off" });
    source.addEventListener("input", () => { sh.source = source.value; });
    const when = el("select", {}, el("option", { value: "1" }, "Exactly on time (cuts the song playing)"),
      el("option", { value: "0" }, "After the song playing ends"));
    when.value = sh.on_time === false ? "0" : "1";
    when.addEventListener("change", () => { sh.on_time = when.value === "1"; });
    const days = DAYS.map((d, n) => {
      const box = el("input", { type: "checkbox" });
      box.checked = sh.days.includes(n);
      box.addEventListener("change", () => {
        sh.days = box.checked ? [...new Set([...sh.days, n])].sort() : sh.days.filter((x) => x !== n);
      });
      return el("label", { class: "check day" }, box, d.slice(0, 3));
    });
    return el("div", { class: "show-edit" },
      el("div", { class: "grid" }, field("Show", name), field("Time", time, "narrow"), field("Starts", when)),
      el("div", { class: "days" }, days),
      el("div", { class: "grid" }, field("Recording, playlist or folder of recordings", source)),
      el("div", { class: "actions" },
        btn("Choose file…", "file", () => pick("file", (p) => { sh.source = p; }, showsMsg), "btn small"),
        btn("Choose folder…", "folder", () => pick("folder", (p) => { sh.source = p; }, showsMsg), "btn small"),
        el("div", { class: "spacer" }),
        btn("Remove", "trash", () => { v.shows.splice(i, 1); redraw(); }, "btn small ghost")),
    );
  }
  async function checkPlaylist() {
    playlistMsg.className = "form-msg ok";
    playlistMsg.textContent = "Reading the playlist…";
    try {
      const r = await api("/api/check/playlist", formPayload(v));
      playlistMsg.textContent = `${r.name}: ${r.tracks} tracks, ${r.hours} hours` +
        (r.missing_count ? `, ${r.missing_count} not found` : ", all found") + ` (${r.file})`;
      if (r.missing_count) playlistMsg.className = "form-msg";
    } catch (e) { playlistMsg.className = "form-msg"; playlistMsg.textContent = e.message; }
  }
  async function checkServer(test) {
    const msg = test ? testMsg : serverMsg;
    msg.className = "form-msg ok";
    msg.textContent = "Connecting…";
    try {
      const r = await api("/api/check/server", { ...formPayload(v), id: s?.station.id, test });
      msg.textContent = r.on_air
        ? `This station is on air to ${r.server} now, so the connection works.`
        : `Connected to ${r.server}: the server accepted the password.`;
    } catch (e) { msg.className = "form-msg"; msg.textContent = e.message; }
  }
  async function save() {
    try {
      const payload = formPayload(v);
      const r = await api(editing ? `/api/stations/${s.station.id}` : "/api/stations", payload);
      setState(r);
      const id = editing ? s.station.id : r.id;
      const runner = stationById(id);
      toast(runner && runner.state === "on" ? "Saved. Shows apply straight away; other changes the next time it goes on air." : "Saved");
      go({ type: "station", id });
    } catch (e) { formMsg.textContent = e.message; }
  }
  async function remove() {
    if (!confirm(`Delete ${s.station.name}? It goes off air, and its settings are removed (its play log is kept).`)) return;
    try {
      setState(await api(`/api/stations/${s.station.id}/delete`, {}));
      go({ type: "station", id: null });
    } catch (e) { formMsg.textContent = e.message; }
  }

  function redraw() {
    view.dataset.form = "";
    renderForm(view);
  }

  const isIce = v.kind === "icecast";
  view.replaceChildren(el("div", {},
    el("div", { class: "view-head" }, el("div", {},
      el("div", { class: "kicker" }, editing ? "Edit station" : "New station"),
      el("h1", {}, editing ? s.station.name : "Add a station"))),
    el("form", { class: "form", onsubmit: (e) => { e.preventDefault(); save(); } },
      el("fieldset", {}, el("legend", {}, "Station"),
        el("div", { class: "grid" }, text("name", "Name", { placeholder: "Rebirth Classics" }), text("genre", "Genre", { placeholder: "Vintage Oldies / Club Classics" })),
        el("div", { class: "grid" }, text("url", "Website", { placeholder: "https://www.rebirthclassics.uk" })),
        el("div", { class: "grid" }, check("public", "List the station in the server's directory")),
      ),
      el("fieldset", {}, el("legend", {}, "Playlist"),
        el("div", { class: "grid" }, text("playlist", "Playlist or folder of weekly exports",
          { placeholder: "~/.station-studio/exports/rebirth-classics" })),
        el("div", { class: "actions", style: "margin-top:8px" },
          btn("Choose folder…", "folder", () => pick("folder"), "btn small"),
          btn("Choose file…", "file", () => pick("file"), "btn small"),
          btn("Check playlist", "check", checkPlaylist, "btn small")),
        el("p", { class: "help" }, "Choose station-studio's exports folder for this station and each week is played in turn: next week's export waits for the start of its week."),
        playlistMsg,
        el("div", { class: "grid" },
          el("label", {}, "Music folders (one per line), searched for tracks that have moved",
            bind("music_folders", el("textarea", { rows: 2 }, v.music_folders))),
          el("label", {}, "Path rewrites (one per line: from = to)",
            bind("remaps", el("textarea", { rows: 2, placeholder: "/Users/rob/Music = D:/Music" }, v.remaps)))),
      ),
      el("fieldset", {}, el("legend", {}, "Fallback"),
        el("div", { class: "grid" }, text("fallback", "Play this if the playlist can't be read",
          { placeholder: "A folder of jingles or songs, or a playlist" })),
        el("div", { class: "actions", style: "margin-top:8px" },
          btn("Choose folder…", "folder", () => pick("folder", (p) => { v.fallback = p; }, fallbackMsg), "btn small"),
          btn("Choose file…", "file", () => pick("file", (p) => { v.fallback = p; }, fallbackMsg), "btn small")),
        el("p", { class: "help" }, "If none of the playlist's music can be read (its drive is missing, say), this plays instead of silence, then the playlist is tried again. Keep it on this computer's own disk."),
        fallbackMsg,
      ),
      el("fieldset", {}, el("legend", {}, "Server"),
        el("div", { class: "grid" },
          select("kind", "Type", [["shoutcast", "SHOUTcast"], ["icecast", "Icecast"]], true),
          text("host", "Address", { placeholder: "81.150.147.206" }),
          text("port", "Port", { narrow: true, placeholder: "8000" }),
          isIce ? text("mount", "Mount", { narrow: true, placeholder: "/classics" }) : text("sid", "Stream", { narrow: true, placeholder: "1" })),
        el("div", { class: "grid" },
          isIce ? text("user", "User", { narrow: true }) : null,
          text("password", "Source password", { type: "password", placeholder: v.has_password ? "unchanged" : "" }),
          select("bitrate", "MP3 bitrate", [["64", "64 kbps"], ["96", "96 kbps"], ["128", "128 kbps"], ["192", "192 kbps"], ["256", "256 kbps"], ["320", "320 kbps"]], true)),
        el("p", { class: "help" }, isIce
          ? "Icecast 2.4 or later. The mount is the stream's name on the server, like /classics."
          : "SHOUTcast 1.9 or 2.x. Use the listener port, the one in the station's link; Station Server connects to the port above it, as Winamp does. Stream is 1 unless the server runs several."),
        el("div", { class: "actions" }, btn("Test connection", "refresh", () => checkServer(false), "btn small")),
        serverMsg,
        el("h3", {}, "Test stream (optional)"),
        el("p", { class: "help" }, isIce
          ? "A spare mount to rehearse on before going live, like /test. Leave the address blank for the same server."
          : "A spare stream to rehearse on before going live: another port of the same server, or stream 2 of a SHOUTcast 2 server. Leave the address blank for the same server."),
        el("div", { class: "grid" },
          text("t_host", "Address", { placeholder: v.host || "same as live" }),
          text("t_port", "Port", { narrow: true, placeholder: v.port || "8000" }),
          isIce ? text("t_mount", "Mount", { narrow: true, placeholder: "/test" }) : text("t_sid", "Stream", { narrow: true, placeholder: "1" }),
          text("test_password", "Password", { type: "password", placeholder: v.has_test_password ? "unchanged" : "same as live" })),
        el("div", { class: "actions" }, btn("Test the test stream", "refresh", () => checkServer(true), "btn small")),
        testMsg,
      ),
      el("fieldset", {}, el("legend", {}, "Playback"),
        el("div", { class: "grid" }, check("level", "Even out the volume (ReplayGain / Sound Check in the files, or measured)")),
        el("p", { class: "help" }, "Station Server measures each track once, in the background: how loud it is, for tracks without volume tags, and where its sound starts and ends, so the silence at either end is left out and songs follow on without dead air."),
        el("div", { class: "grid" }, select("crossfade", "Crossfade between songs",
          [["0", "Off: one straight after the other"], ...[1, 2, 3, 4, 5, 6, 8, 10].map((n) => [String(n), `${n} seconds`])])),
        el("p", { class: "help" }, "With a crossfade, the end of each song fades out under the start of the next. A track that fades out by itself sounds best with 2 to 4 seconds."),
        el("div", { class: "radio-row" },
          radio("start_mode", "clock", "Follow the week's clock"),
          radio("start_mode", "top", "Always start from track 1")),
        el("div", { class: "grid" },
          select("day", "The week starts on", DAYS.map((d, i) => [String(i), d]), true),
          text("time", "at", { type: "time", narrow: true })),
        el("p", { class: "help" }, "Following the clock, the first track plays at the start of the week and the rest follow by their lengths, so after a restart the station picks up where it should be."),
      ),
      el("fieldset", {}, el("legend", {}, "Jingles"),
        el("div", { class: "grid" },
          text("jingles", "Folder of jingles", { placeholder: "~/Music/Jingles (blank for none)" }),
          select("jingle_every", "Play one after every", Array.from({ length: 20 }, (_, i) => [String(i + 1), i ? `${i + 1} songs` : "song"]), true)),
        el("div", { class: "actions", style: "margin-top:8px" },
          btn("Choose folder…", "folder", () => pick("folder", (p) => { v.jingles = p; }, jinglesMsg), "btn small")),
        el("p", { class: "help" }, "Station idents and jingles, played between songs in a shuffled order, each once before any plays again. Not during shows or the fallback, and not in the play log. The week's clock allows for them, and for the crossfade."),
        jinglesMsg,
      ),
      el("fieldset", {}, el("legend", {}, "Live shows"),
        el("div", { class: "grid" }, check("live", "Let presenters go live from their own encoder (BUTT, Mixxx, Winamp)")),
        el("div", { class: "grid" },
          text("live_port", "Port", { narrow: true, placeholder: "8010" }),
          text("live_password", "Presenters' password", { type: "password", placeholder: v.has_live_password ? "unchanged" : "" })),
        el("div", { class: "grid" }, check("record_live", "Keep a recording of each live show (about 60 MB an hour at 128 kbps)")),
        el("p", { class: "help" }, "While the station is on air, a presenter's encoder connects to this computer as if it were the station's server, and they're live: the song fades out under them, and when they disconnect the music comes back where the week's clock says. In BUTT or Mixxx choose Icecast, with this computer's address, the port, mount /live, user source and the password; or SHOUTcast, with the address, port and password. From outside this network, forward the port and the one above it on the router to this computer."),
      ),
      el("fieldset", {}, el("legend", {}, "Shows"),
        el("p", { class: "help" }, "Shows go out at set times: a recording, a playlist, or a folder of recordings played in name order. When a show ends, the music comes back where the week's clock says, so the running order's times stay right."),
        v.shows.map(showRow),
        el("div", { class: "actions" }, btn("Add a show", "plus", () => {
          v.shows.push({ name: "", time: "20:00", days: [5], source: "", on_time: true });
          redraw();
        }, "btn small")),
        showsMsg,
      ),
      formMsg,
      el("div", { class: "actions" },
        el("button", { type: "submit", class: "btn primary" }, "Save"),
        btn("Cancel", null, () => go(editing ? { type: "station", id: s.station.id } : { type: "station", id: null }), "btn ghost"),
        el("div", { class: "spacer" }),
        editing ? btn("Delete station", "trash", remove, "btn danger") : null),
    ),
  ));
}

// ---------- play log, settings, help -----------------------------------------------

function renderLog() {
  const stations = S.state?.stations || [];
  const today = new Date();
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const last = new Date(today.getFullYear(), today.getMonth(), 0);
  const which = el("select", {}, stations.map((s) => el("option", { value: s.station.id }, s.station.name)));
  const from = el("input", { type: "date", value: iso(first) });
  const to = el("input", { type: "date", value: iso(last) });
  const download = () => {
    if (!which.value) return;
    location.href = `/api/stations/${which.value}/playlog.csv?from=${from.value}&to=${to.value}`;
  };
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("div", {}, el("h1", {}, "Play log"))),
    el("section", {},
      el("p", { class: "help" }, "Every track each station plays is logged. Download a station's plays between two dates as a spreadsheet (date, time, title, artist, album, composer, label, ISRC, length, how long it played), ready for a PRS for Music or PPL return. Plays under 30 seconds aren't counted."),
      stations.length ? el("div", { class: "form-row" }, which, from, to, btn("Download CSV", "download", download, "btn primary"))
        : el("p", { class: "help" }, "No stations yet.")),
  );
}

function renderSettings() {
  const own = el("input", { type: "checkbox" });
  own.checked = !!S.state?.open_window;
  own.addEventListener("change", async () => {
    try { setState(await api("/api/settings", { open_window: own.checked })); toast("Saved"); } catch (e) { toast(e.message); }
  });
  const login = el("input", { type: "checkbox" });
  login.checked = !!S.state?.login;
  login.addEventListener("change", async () => {
    try { setState(await api("/api/settings", { login: login.checked })); toast("Saved"); } catch (e) { toast(e.message); login.checked = !login.checked; }
  });
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("div", {}, el("h1", {}, "Settings"))),
    el("section", {},
      el("h2", {}, "Start when this computer starts"),
      el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, login, "Start Station Server when I log in"),
      el("p", { class: "help" }, "Turn this on for the station's computer: after a restart, a power cut or Windows updates, Station Server starts in the background and the stations that were on air go back on air, with no window until you open one. On a Mac it is also started again if it ever stops unexpectedly. For a computer nobody logs in to, set Windows or macOS to log in automatically."),
      el("h2", {}, "Alerts"),
      alertsSettings(),
      el("h2", {}, "Phone access"),
      phoneSettings(),
      el("h2", {}, "Updates"),
      updateSettings(),
      el("h2", {}, "App"),
      el("p", { class: "help" }, "Station Server runs in the background; this window is how you see it. Closing the window leaves the stations on air. Quit Station Server takes them off air."),
      el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, own,
        S.state?.mac_app ? "Open in Station Server's own window, not a browser tab" : "Open in a window of its own (with Chrome, Edge or Brave)"),
      // The Mac app has its own Dock icon already.
      S.state?.mac_app ? null : [
        el("h3", {}, "Its own icon in the Dock or taskbar"),
        el("p", { class: "help" }, "Install Station Server as an app and it gets its own name and icon in the Dock, the taskbar and Alt-Tab, and opens that way from then on."),
        S.installPrompt
          ? btn("Install as app", "app", async () => { S.installPrompt.prompt(); await S.installPrompt.userChoice; S.installPrompt = null; render(); }, "btn primary")
          : el("p", { class: "help" }, "Already installed, or your browser offers it from its menu (Install Station Server…)."),
      ],
      el("h3", {}, "ffmpeg"),
      el("p", { class: "help" }, S.state?.ffmpeg ? `Using ${S.state.ffmpeg}` : (S.state?.ffmpeg_error || "")),
    ),
  );
}

// Settings › Alerts: who is told when a station is off air (alerts.py).
function alertsSettings() {
  const a = S.state?.alerts || {};
  if (!S.alertsForm) S.alertsForm = { ...a, smtp_password: "", after_minutes: String(a.after_minutes ?? 3), smtp_port: String(a.smtp_port ?? 587) };
  const v = S.alertsForm;
  const msg = el("div", { class: "form-msg", role: "status" });
  const field = (key, label, attrs = {}) => {
    const input = el("input", { type: attrs.type || "text", value: v[key] ?? "", placeholder: attrs.placeholder || null, autocomplete: "off" });
    input.addEventListener("input", () => { v[key] = input.value; });
    return el("label", { class: attrs.narrow ? "narrow" : null }, label, input);
  };
  const security = el("select", {}, [["starttls", "STARTTLS (usually port 587)"], ["ssl", "SSL/TLS (usually port 465)"], ["none", "None"]]
    .map(([val, words]) => el("option", { value: val }, words)));
  security.value = v.smtp_security || "starttls";
  security.addEventListener("change", () => { v.smtp_security = security.value; });
  const backOn = el("input", { type: "checkbox" });
  backOn.checked = v.back_on !== false;
  backOn.addEventListener("change", () => { v.back_on = backOn.checked; });
  const redraw = () => { const old = $("#alerts-settings"); if (old) old.replaceWith(alertsSettings()); };
  async function save(quiet) {
    try {
      setState(await api("/api/settings", { alerts: { ...v, after_minutes: Number(v.after_minutes), smtp_port: Number(v.smtp_port) } }));
      S.alertsForm = null;
      if (!quiet) { toast("Saved"); redraw(); }
      return true;
    } catch (e) { msg.textContent = e.message; return false; }
  }
  async function test() {
    if (!(await save(true))) return;
    msg.className = "form-msg ok";
    msg.textContent = "Sending…";
    try {
      const r = await api("/api/alerts/test", {});
      setState(r);
      msg.textContent = `Sent to ${r.sent.join(" and ")}. If it doesn't arrive in a minute, check the settings (and the spam folder).`;
    } catch (e) { msg.className = "form-msg"; msg.textContent = e.message; }
  }
  const topic = () => { v.ntfy = `https://ntfy.sh/station-${Math.random().toString(36).slice(2, 8)}${Math.random().toString(36).slice(2, 8)}`; redraw(); };
  const last = a.last;
  return el("div", { id: "alerts-settings", class: "form" },
    el("p", { class: "help" }, "If a station you've put on air goes off air, can't reach its server, or is playing silence or its fallback, and that lasts a few minutes (Station Server keeps trying to put it right first), you're told: on your phone, by email, or both. You're told again when it's back."),
    el("fieldset", {}, el("legend", {}, "Phone notification"),
      el("div", { class: "grid" }, field("ntfy", "ntfy topic", { placeholder: "https://ntfy.sh/your-own-topic-name" })),
      el("div", { class: "actions" }, btn("Make up a topic name", "refresh", topic, "btn small")),
      el("p", { class: "help" }, "Install the free ntfy app (iPhone or Android), tap +, and subscribe to the same topic name. Anyone who knows the name can read the alerts, so use one that's hard to guess.")),
    el("fieldset", {}, el("legend", {}, "Email"),
      el("div", { class: "grid" }, field("email_to", "Send to", { placeholder: "you@example.com" }), field("email_from", "From (optional)", { placeholder: "station@example.com" })),
      el("div", { class: "grid" }, field("smtp_host", "Mail server", { placeholder: "smtp.gmail.com" }), field("smtp_port", "Port", { narrow: true }),
        el("label", {}, "Security", security)),
      el("div", { class: "grid" }, field("smtp_user", "User name", { placeholder: "usually your email address" }),
        field("smtp_password", "Password", { type: "password", placeholder: a.has_smtp_password ? "unchanged" : "" })),
      el("p", { class: "help" }, "Your email provider's outgoing (SMTP) server. For Gmail or Outlook, make an app password in your account's security settings and use that, not your usual password.")),
    el("div", { class: "grid" }, field("after_minutes", "Alert after (minutes)", { narrow: true }),
      el("label", { class: "check" }, backOn, "Tell me when it's back on air too")),
    msg,
    el("div", { class: "actions" }, btn("Save", "check", () => save(false), "btn primary"), btn("Send a test", "bell", test, "btn")),
    last ? el("p", { class: "help" }, `Last alert ${dayTime(last.at)}: ${last.title}`,
      last.errors && last.errors.length ? el("span", { class: "warn" }, ` (not sent: ${last.errors.join("; ")})`) : "") : null,
    el("p", { class: "help" }, "Station Server can't tell anyone if the computer itself is off or has lost its internet connection. A free outside check of the stream (an uptime monitor) covers that."),
  );
}

// Settings › Phone access: a second address for phones and other computers
// on the same network, with a password (web.py RemoteAccess).
function phoneSettings() {
  const r = S.state?.remote || {};
  const msg = el("div", { class: "form-msg", role: "status" });
  const on = el("input", { type: "checkbox" });
  on.checked = !!r.enabled;
  const password = el("input", { type: "password", autocomplete: "new-password", placeholder: r.has_password ? "unchanged" : "at least 8 characters" });
  const port = el("input", { type: "text", value: String(r.port || 8771), autocomplete: "off" });
  const redraw = () => { const old = $("#phone-settings"); if (old) old.replaceWith(phoneSettings()); };
  async function save(body) {
    try {
      setState(await api("/api/settings", body));
      toast("Saved");
      redraw();
    } catch (e) { msg.textContent = e.message; if ("remote" in body) on.checked = !on.checked; }
  }
  on.addEventListener("change", () => save({ remote: on.checked, ...(password.value ? { remote_password: password.value } : {}), remote_port: port.value }));
  return el("div", { id: "phone-settings", class: "form" },
    el("p", { class: "help" }, "See the stations from your phone or another computer on the same network (Wi-Fi), listen, and put them on or off air. Changing settings stays on this computer."),
    el("div", { class: "grid" },
      el("label", {}, "Password", password),
      el("label", { class: "narrow" }, "Port", port)),
    el("div", { class: "actions" }, btn("Save password and port", "check", () => save({ remote_password: password.value, remote_port: port.value }), "btn small")),
    el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, on, "Let phones and other computers on this network in"),
    msg,
    r.error ? el("div", { class: "errors" }, r.error) : null,
    r.running && r.addresses.length ? el("p", {}, "On your phone, open ", r.addresses.flatMap((a, i) => [i ? " or " : "", el("b", {}, a)]),
      " and enter the password (any user name). Then use Add to Home Screen to keep it.") : null,
    el("p", { class: "help" }, "The first time, Windows or macOS may ask whether Station Server may accept connections: allow it, on private networks. It isn't reachable from the internet; for that, use a VPN such as Tailscale on both."),
  );
}

async function copyDiagnostics() {
  try {
    const res = await fetch("/api/diagnostics", { cache: "no-store" });
    const text = await res.text();
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied. Paste it into your message.");
    } catch {
      S.diagnostics = text;  // no clipboard here: show it to copy by hand
      render();
    }
  } catch {
    toast("Couldn't reach Station Server");
  }
}

function renderHelp() {
  const p = (...t) => el("p", { class: "help" }, ...t);
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("div", {}, el("h1", {}, "Help"))),
    el("section", {},
      el("h2", {}, "What it does"),
      p("Station Server plays a station-studio playlist in real time and sends it straight to your SHOUTcast or Icecast server with each song's title. It replaces iTunes playing out of the headphone jack, the loopback cable, and Winamp with its SHOUTcast plugin. Nothing else playing on this computer reaches the broadcast, and one computer can run several stations."),
      el("h2", {}, "Setting up a station"),
      p("Add a station, choose station-studio's exports folder for it (the newest week is played), enter the server's address, port and source password (the one Winamp used), and press Test connection. Then Save and Go on air."),
      p("A SHOUTcast server takes one source at a time: stop Winamp's SHOUTcast plugin before going on air, or the server refuses Station Server."),
      el("h2", {}, "Switching over from Winamp"),
      el("ol", { class: "help steps" },
        el("li", {}, "On the station's computer, install Station Server, copy the music onto it, and turn on Settings › Start Station Server when I log in."),
        el("li", {}, "Add the station: its exports folder, the music folders, and the live server's address, port and source password (the ones in Winamp's SHOUTcast plugin). Under Test stream, enter a spare stream: another port of the server, or stream 2 of a SHOUTcast 2 server."),
        el("li", {}, "Set up Settings › Alerts, and a fallback folder in Edit."),
        el("li", {}, "Press Ready check on the station's page and fix anything marked ✕."),
        el("li", {}, "Press Rehearse. The station plays to the test stream while Winamp carries on as normal. Press Listen, or open the test stream in a player, and check the songs, their titles and the levels. Nothing goes in the play log."),
        el("li", {}, "When it sounds right: stop Winamp's SHOUTcast plugin (and close iTunes), then press Go live. Listeners hear a short gap."),
        el("li", {}, "Keep Winamp and iTunes installed, closed, for a week or two. To go back: Take off air here, then start Winamp's plugin again.")),
      p("If going on air says something else is already sending to the server, Winamp's plugin (or another copy of Station Server) is still connected: a server takes one source at a time."),
      el("h2", {}, "Two stations on one computer"),
      p("Add a station for each. They run side by side, each with its own playlist, server, clock and play log, and go on and off air separately: for example Rebirth Classics to port 8000 and Rebirth Radio to port 8002 of the same server."),
      el("h2", {}, "Running unattended"),
      p("Turn on Settings › Start Station Server when I log in. After a restart, a power cut or Windows updates, it starts in the background and the stations that were on air go back on air. Closing this window leaves them on air; Quit takes them off."),
      p("While a station is on air the computer doesn't go to sleep by itself (the screen can still turn off). If the server can't be reached, or the music's drive isn't there yet, the station shows Trying again… and keeps trying until it's on air; it does the same if the broadcast ever fails."),
      p("For a computer nobody is at, after a power cut: set it to switch itself back on when the power returns (a setting in the BIOS, often called Restore on AC power loss), and let Windows sign in by itself, since Station Server starts when you sign in. Also check Windows Update's active hours, so restarts happen when it suits you."),
      el("h2", {}, "The week's clock"),
      p("Following the clock, the first track plays at the start of the week (Monday 00:00 unless you change it) and the rest follow by their lengths. A restart picks up where the station should be."),
      p("Next week's export can be made any time: it waits, and goes on at the start of its week (the station's page says when). A week exported late, after its week has begun, goes on within a minute, after the playing track. Exporting the week on air again picks up the changes the same way."),
      el("h2", {}, "Levels and gaps"),
      p("With Even out the volume on, each track is played at about the same loudness: from its ReplayGain or Sound Check tags, or, for a track without them, from measuring it. Station Server measures every track once, in the background and gently (the coming tracks first), and remembers the results. Measuring also finds the silence at the start and end of each track, which is then left out, so songs follow on without dead air. Until a track has been measured it plays as it is."),
      el("h2", {}, "Crossfades and jingles"),
      p("Edit › Playback › Crossfade fades the end of each song out under the start of the next, over 1 to 10 seconds (Off plays them one straight after the other). Edit › Jingles plays one of a folder of jingles or station idents after every few songs, in a shuffled order, each once before any plays again. The song before fades out under the jingle; the next song starts as the jingle ends. Jingles aren't played during shows or the fallback and don't go in the play log; while one plays, the server shows the station's name as the title. Skip fades the song out over at most 2 seconds, however long the crossfade."),
      p("The week's clock allows for both, so the running order's times and a restart still land in the right place."),
      el("h2", {}, "Live shows"),
      p("Turn on Edit › Live shows and set a port (8010 unless you choose another) and a password for presenters. While the station is on air, a presenter's encoder connects to this computer as if it were the station's server: the song playing fades out under them and they're live, with the song titles their encoder sends. When they disconnect, the music fades back in where the week's clock says. A show due while they're live is left out."),
      p("In BUTT: Settings › Main › Server › Add. Type Icecast, Address this computer's address, Port 8010, Password the presenters' password, Mountpoint live, User source. (Or Type SHOUTcast, with the address, port and password.) In Mixxx: Options › Live Broadcasting, Type Icecast 2, the same details, Login source. Winamp's SHOUTcast plugin works too, with the address, port and password."),
      p("Presenters at home connect over the internet: on the router at the station, forward the port and the one above it (8010 and 8011) to this computer, as for the SHOUTcast server, and give presenters the station's internet address. Allow Station Server through the firewall when Windows or macOS asks. The Ready check shows the address to use on this network."),
      p("End live show takes a presenter off air; their encoder will try to reconnect, so they're kept off for two minutes. If a presenter's connection drops, the music comes back after 10 seconds. What they play goes in the play log by the titles their encoder sends (or as one Live show entry if it sends none), and each show is recorded, exactly as their encoder sent it, in Station Server's recordings folder (the Ready check says where); turn that off in Edit › Live shows."),
      el("h2", {}, "Keeping an eye on it"),
      p("On air, a station's page shows how many are listening (as the server counts them: listeners to a relay aren't included), and Listen plays the broadcast exactly as it goes to the server."),
      p("Settings › Alerts tells you, on your phone or by email, when a station has been off air for a few minutes, and again when it's back. Settings › Phone access lets a phone or another computer on the same network see the stations, listen, and put them on or off air, with a password."),
      el("h2", {}, "Shows at set times"),
      p("In a station's Edit page, Shows pins a show to times in the week, like Saturday 20:00. A show is a recording, a playlist, or a folder of recordings played in name order. It starts exactly on time (cutting the song playing) or after that song. When it ends, the music comes back where the week's clock says, so the running order's times stay right. After a restart part-way through a show, the station joins the show where it should be."),
      el("h2", {}, "Its own app, and new versions"),
      p("On a Mac, the downloaded Station Server has a window of its own, like Apple Music: closing the window leaves the stations on air, clicking its Dock icon brings it back, and its menus have Go, Settings (⌘,) and Quit (⌘Q). Right-click its Dock icon for Add a station and Play log."),
      p("Settings › Install as app gives Station Server its own icon in the Dock or taskbar, with Add a station and Play log on its menu. Opened from there when Station Server isn't running, the window starts it."),
      p("When a new version is out, a note at the foot of the sidebar offers Update now. Station Server downloads it from its GitHub page, checks it's the genuine file and restarts; stations that were on air go back on air. Settings › Updates has Check for updates, and can stop it asking GitHub."),
      el("h2", {}, "If something goes wrong"),
      p("If the server goes away, the station keeps playing and reconnects every few seconds. A track whose file can't be read is skipped. If none can be (the music's drive is missing, say), the station plays its fallback, a folder of jingles or songs set in Edit, and then tries the playlist again; with no fallback it sends silence. The page says which. Tracks the playlist names but this computer can't find are listed under the station: add the folder they're in as a music folder."),
      p("ffmpeg does the decoding and encoding. The downloaded app has its own; otherwise install it (macOS: brew install ffmpeg; Windows: winget install ffmpeg)."),
      p("Station Server keeps a log, station-server.log, with its settings and play logs in the .station-server folder in your home folder."),
      p("Asking for help? Copy diagnostics puts the version, each station's state and the end of the log on the clipboard, with passwords taken out, ready to paste into a message."),
      el("div", { class: "actions" }, btn("Copy diagnostics", "copy", copyDiagnostics, "btn")),
      S.diagnostics ? el("textarea", { class: "diagnostics", readonly: true, rows: 12 }, S.diagnostics) : null,
    ),
  );
}

// ---------- start-up --------------------------------------------------------------

for (const span of document.querySelectorAll("[data-icon]")) span.replaceWith(icon(span.dataset.icon));
for (const b of document.querySelectorAll(".nav-item[data-view]")) {
  b.addEventListener("click", () => go({ type: b.dataset.view, id: null }));
}
$("#btn-quit").addEventListener("click", async () => {
  const live = (S.state?.stations || []).filter((s) => s.state === "on").length;
  if (live && !confirm(`Quit Station Server? ${live === 1 ? "The station goes" : `All ${live} stations go`} off air.`)) return;
  try { await api("/api/quit", {}); } catch { /* it's going */ }
  if (NATIVE && tellApp("quit")) return;  // the Mac app's own window quits with it
  document.body.replaceChildren(el("div", { class: "goodbye" }, el("div", {},
    el("h1", {}, "Station Server has stopped"),
    el("p", {}, "Stations that were on air go back on air the next time it starts."))));
  clearInterval(pollTimer);
});
if ("serviceWorker" in navigator && !NATIVE) {
  // Opened from the Dock while Station Server isn't running, the window
  // shows a page that starts it (sw.js, offline.html).
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
// What the Mac app's menus call (macwindow.py `tell`).
window.stationApp = {
  go(type) { go({ type, id: null }); },
};
addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  S.installPrompt = e;
  if (S.view.type === "settings") render();
});

async function poll() {
  try {
    setState(await api("/api/state"));
    checkListening();
    if (S.view.type !== "new" && S.view.type !== "edit" && S.view.type !== "log" && S.view.type !== "settings") render();
    else renderNav();
  } catch { /* the server is restarting, or gone; try again */ }
}
let pollTimer = setInterval(poll, 2000);
setInterval(tick, 250);
// The Dock menu (manifest.webmanifest) opens /?view=new or /?view=log.
const startView = new URLSearchParams(location.search).get("view");
if (["new", "log", "settings", "help"].includes(startView)) {
  S.view = { type: startView, id: null };
  history.replaceState(null, "", "/");
}
poll().then(render);
