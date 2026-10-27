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
  globe: ["c 12 12 10", "M2 12h20", "M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"],
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
  clock: ["c 12 12 9", "M12 7v5l3 2"],
  link: ["M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71", "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"],
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

// Pages of a view as tabs along the top, as in Station Player's settings.
// `tabs` is [[id, label, icon]]; the arrow keys move between them.
function tabBar(label, tabs, current, open) {
  const ids = tabs.map((t) => t[0]);
  const choose = (id) => {
    open(id);
    $(`#tab-${id}`)?.focus();  // the page was redrawn: stay on the tab
  };
  return el("div", {
    class: "tabs", role: "tablist", "aria-label": label,
    onkeydown: (e) => {
      const at = ids.indexOf(current);
      let to = null;
      if (e.key === "ArrowRight") to = (at + 1) % ids.length;
      else if (e.key === "ArrowLeft") to = (at - 1 + ids.length) % ids.length;
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = ids.length - 1;
      if (to === null) return;
      e.preventDefault();
      choose(ids[to]);
    },
  }, tabs.map(([id, words, glyph]) => el("button", {
    type: "button", role: "tab", class: "tab", id: `tab-${id}`, "aria-controls": "tab-page",
    "aria-selected": String(id === current), tabindex: id === current ? "0" : "-1",
    onclick: () => choose(id),
  }, ...iconLabel(glyph, words))));
}

const tabPage = (current, ...children) =>
  el("div", { role: "tabpanel", id: "tab-page", "aria-labelledby": `tab-${current}` }, ...children);

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
  const out = { kind: "shoutcast", host: "", port: "8000", sid: "1", mount: "/stream", user: "source", max: "20" };
  const sv = /^serve:\/\/[^:/?]*:(\d+)(?:\?(.*))?$/i.exec(url || "");
  if (sv) return { ...out, kind: "serve", port: sv[1], max: new URLSearchParams(sv[2] || "").get("max") || "20" };
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
  if (p.kind === "serve") return p.port ? `serve://:${p.port}?max=${p.max || 20}` : "";
  if (!p.host) return "";
  if (p.kind === "icecast") {
    const user = p.user && p.user !== "source" ? `${encodeURIComponent(p.user)}@` : "";
    const mount = p.mount.startsWith("/") ? p.mount : `/${p.mount}`;
    return `icecast://${user}${p.host}:${p.port}${mount}`;
  }
  return `shoutcast://${p.host}:${p.port}${p.sid && p.sid !== "1" ? `?sid=${p.sid}` : ""}`;
}

// "serve://:8000?max=20" as people say it.
function serverLabel(url) {
  const p = parseServer(url);
  return p.kind === "serve" && url ? `This computer, port ${p.port}` : url;
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
      }, el("span", {}, s.station.name),
      s.live ? el("span", { class: "nav-mark", title: `Live: ${s.live.presenter || "a presenter"}` }, icon("mic")) : null,
      S.listening === s.station.id ? el("span", { class: "nav-mark", title: "Listening" }, icon("headphones")) : null,
      el("span", { class: `dot ${s.state}`, "aria-label": STATE_WORDS[s.state] })),
    ));
  }
  // Listening to one station while looking at another (or at Settings):
  // the way to stop it is always here.
  const heard = S.listening && stationById(S.listening);
  if (heard) {
    list.append(el("li", {}, el("button", {
      type: "button", class: "nav-item nav-listening", title: `Stop listening to ${heard.station.name}`, onclick: () => listen(null),
    }, icon("stop"), el("span", {}, "Stop listening"))));
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
      el("div", { class: "meta" }, (rehearsing ? `Test stream: ${serverLabel(st.test_server)}` : serverLabel(st.server)) || "No server set"),
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
  if (s.on_fallback) parts.push(el("div", { class: "errors" }, "None of the playlist's tracks can be read, so the station is playing the fallback. Check the music folders in Edit › Station; the playlist is tried again after the fallback."));
  else if (s.stuck) parts.push(el("div", { class: "errors" }, "None of the playlist's tracks can be read, so the station is sending silence. Check the music folders in Edit › Station, or set a fallback there."));

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
      s.serving ? `Streaming from ${s.server}`
        : s.connected ? `Connected to ${s.server}` : `Reconnecting to ${s.server}…`));
    if (s.changed) facts.push(el("span", { class: "warn" }, "New server settings apply when it next goes on air: ",
      el("a", { href: "#", class: "inline-link", onclick: async (e) => {
        e.preventDefault();
        if (confirm("Switch over now? The station goes off air for a moment and comes back on with the new server settings.")) {
          try { setState(await api(`/api/stations/${st.id}/restart`, {})); } catch (err) { toast(err.message); }
        }
      } }, "switch over now")));
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
  const here = parseServer(st.server).kind === "serve";
  if (!confirm(here
    ? `Go live from this computer, port ${parseServer(st.server).port}?\n\nIf the SHOUTcast DNAS or Icecast runs on this computer on that port, stop it first. Listeners reconnect to Station Server instead.`
    : `Go live on ${st.server}?\n\nStop Winamp's SHOUTcast plugin first (and iTunes): the server takes one source at a time. Listeners hear a short gap while the station switches over.`)) return;
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
    presenters: (st?.presenters || []).map((p) => ({ id: p.id, name: p.name, password: "", has_password: !!p.has_password })),
    record_live: st ? st.record_live !== false : true,
    live_level: st ? st.live_level !== false : true,
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
    presenters: v.presenters.map((p) => ({ id: p.id, name: p.name, password: p.password })),
    record_live: v.record_live,
    live_level: v.live_level,
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

const STATION_TABS = [
  ["station", "Station", "tower"],
  ["server", "Server", "radio"],
  ["playback", "Playback", "headphones"],
  ["shows", "Shows", "clock"],
  ["live", "Live shows", "mic"],
];

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
  const formMsg = el("div", { class: "form-msg", id: "form-msg", role: "status" });
  const fallbackMsg = el("div", { class: "form-msg", role: "status" });
  const jinglesMsg = el("div", { class: "form-msg", role: "status" });

  async function pick(kind, into = (path) => { v.playlist = path; }, msg = playlistMsg) {
    try {
      const r = await api("/api/pick", { kind });
      if (r.path) { into(r.path); redraw(); }
    } catch (e) { msg.className = "form-msg"; msg.textContent = e.message; }
  }
  const showsMsg = el("div", { class: "form-msg", role: "status" });
  function presenterRow(p, i) {
    const field = (label, input, cls) => el("label", { class: cls || null }, label, input);
    const name = el("input", { type: "text", value: p.name || "", placeholder: "DJ Rob", autocomplete: "off" });
    name.addEventListener("input", () => { p.name = name.value; });
    const password = el("input", { type: "password", value: p.password || "", placeholder: p.has_password ? "unchanged" : "", autocomplete: "new-password" });
    password.addEventListener("input", () => { p.password = password.value; });
    return el("div", { class: "grid" }, field("Presenter", name), field("Their password", password),
      el("div", { class: "actions", style: "align-self:end" },
        btn("Remove", "trash", () => { v.presenters.splice(i, 1); redraw(); }, "btn small ghost")));
  }
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
      msg.textContent = (test ? buildTestServer(v) : buildServer(v)).startsWith("serve:")
        ? (r.on_air ? `Streaming from ${r.server} now.` : `Port ${test ? v.t_port || v.port : v.port} is free on this computer: listeners can connect once it's on air.`)
        : r.on_air
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
      go({ type: "station", id });
      if (runner && runner.changed) {
        if (confirm("Switch over now? The station goes off air for a moment and comes back on with the new server settings.")) {
          setState(await api(`/api/stations/${id}/restart`, {}));
          toast("Switching over…");
        } else toast("Saved. The new server settings apply the next time it goes on air.");
      } else toast(runner && runner.state === "on" ? "Saved. Shows and presenters apply straight away; other changes the next time it goes on air." : "Saved");
    } catch (e) {
      // Show the tab the problem is on: a show's or presenter's name, or what it's about.
      const named = (list) => list.some((x) => x.name && e.message.includes(x.name.trim()));
      const on = named(v.presenters) ? "live" : named(v.shows) ? "shows"
        : [[/presenter|live/i, "live"], [/show/i, "shows"], [/server|stream|bitrate|password/i, "server"],
          [/crossfade|jingle|week/i, "playback"], [/playlist|fallback|folder|name/i, "station"]]
          .find(([re]) => re.test(e.message))?.[1];
      if (on && on !== tab) {
        openTab(on);
        $("#form-msg").textContent = e.message;
      } else formMsg.textContent = e.message;
    }
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
  const isServe = v.kind === "serve";
  const tab = STATION_TABS.some((t) => t[0] === S.view.tab) ? S.view.tab : "station";
  function openTab(id) {
    S.view.tab = id;
    redraw();
  }
  const pages = {
    station: () => [
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
    ],
    server: () => [
      el("fieldset", {}, el("legend", {}, "Server"),
        el("div", { class: "grid" },
          select("kind", "Type", [["serve", "This computer"], ["shoutcast", "SHOUTcast"], ["icecast", "Icecast"]], true),
          isServe ? null : text("host", "Address", { placeholder: "81.150.147.206" }),
          text("port", "Port", { narrow: true, placeholder: "8000" }),
          isServe ? text("max", "Most listeners", { narrow: true, placeholder: "20" })
            : isIce ? text("mount", "Mount", { narrow: true, placeholder: "/classics" }) : text("sid", "Stream", { narrow: true, placeholder: "1" })),
        el("div", { class: "grid" },
          isIce ? text("user", "User", { narrow: true }) : null,
          isServe ? null : text("password", "Source password", { type: "password", placeholder: v.has_password ? "unchanged" : "" }),
          select("bitrate", "MP3 bitrate", [["64", "64 kbps"], ["96", "96 kbps"], ["128", "128 kbps"], ["192", "192 kbps"], ["256", "256 kbps"], ["320", "320 kbps"]], true)),
        el("p", { class: "help" }, isServe
          ? `Station Server is the server: listeners connect straight to this computer on this port, as they would to a SHOUTcast DNAS, so a DNAS's old links keep working on the same port. From outside this network, forward the port on the router to this computer. Each listener takes about ${v.bitrate || 128} kbps of the internet connection's upload: Help › Streaming from this computer has a guide to how many it can take.`
          : isIce
            ? "Icecast 2.4 or later. The mount is the stream's name on the server, like /classics."
            : "SHOUTcast 1.9 or 2.x. Use the listener port, the one in the station's link; Station Server connects to the port above it, as Winamp does. Stream is 1 unless the server runs several."),
        el("div", { class: "actions" }, btn(isServe ? "Check the port" : "Test connection", "refresh", () => checkServer(false), "btn small")),
        serverMsg,
        el("h3", {}, "Test stream (optional)"),
        el("p", { class: "help" }, isServe
          ? "A spare port to rehearse on before going live, like 8100: listen with the Listen button, or a player on this network."
          : isIce
            ? "A spare mount to rehearse on before going live, like /test. Leave the address blank for the same server."
            : "A spare stream to rehearse on before going live: another port of the same server, or stream 2 of a SHOUTcast 2 server. Leave the address blank for the same server."),
        el("div", { class: "grid" },
          isServe ? null : text("t_host", "Address", { placeholder: v.host || "same as live" }),
          text("t_port", "Port", { narrow: true, placeholder: isServe ? "8100" : v.port || "8000" }),
          isServe ? null : isIce ? text("t_mount", "Mount", { narrow: true, placeholder: "/test" }) : text("t_sid", "Stream", { narrow: true, placeholder: "1" }),
          isServe ? null : text("test_password", "Password", { type: "password", placeholder: v.has_test_password ? "unchanged" : "same as live" })),
        el("div", { class: "actions" }, btn(isServe ? "Check the test port" : "Test the test stream", "refresh", () => checkServer(true), "btn small")),
        testMsg,
      ),
    ],
    playback: () => [
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
    ],
    shows: () => [
      el("fieldset", {}, el("legend", {}, "Shows"),
        el("p", { class: "help" }, "Shows go out at set times: a recording, a playlist, or a folder of recordings played in name order. When a show ends, the music comes back where the week's clock says, so the running order's times stay right."),
        v.shows.map(showRow),
        el("div", { class: "actions" }, btn("Add a show", "plus", () => {
          v.shows.push({ name: "", time: "20:00", days: [5], source: "", on_time: true });
          redraw();
        }, "btn small")),
        showsMsg,
      ),
    ],
    live: () => [
      el("fieldset", {}, el("legend", {}, "Live shows"),
        el("div", { class: "grid" }, check("live", "Let presenters go live from their own encoder (BUTT, Mixxx, Winamp)")),
        el("div", { class: "grid" },
          text("live_port", "Port", { narrow: true, placeholder: "8010" }),
          text("live_password", "Shared password (optional)", { type: "password", placeholder: v.has_live_password ? "unchanged" : "" })),
        el("p", { class: "help" }, "Give each presenter their own password: their name is shown and logged when they're live, and one can be removed without telling the others a new password. A shared password still works for anyone you haven't added."),
        v.presenters.map(presenterRow),
        el("div", { class: "actions" }, btn("Add a presenter", "plus", () => {
          v.presenters.push({ id: "", name: "", password: "", has_password: false });
          redraw();
        }, "btn small")),
        el("div", { class: "grid" }, check("live_level", "Even out presenters' volume, to match the music")),
        el("div", { class: "grid" }, check("record_live", "Keep a recording of each live show (about 60 MB an hour at 128 kbps)")),
        el("p", { class: "help" }, "While the station is on air, a presenter's encoder connects to this computer as if it were the station's server, and they're live: the song fades out under them, and when they disconnect the music comes back where the week's clock says. In BUTT or Mixxx choose Icecast, with this computer's address, the port, mount /live, user source and their password; or SHOUTcast, with the address, port and password. From outside this network, forward the port and the one above it on the router to this computer."),
      ),
    ],
  };
  view.replaceChildren(el("div", {},
    el("div", { class: "view-head" }, el("div", {},
      el("div", { class: "kicker" }, editing ? "Edit station" : "New station"),
      el("h1", {}, editing ? s.station.name : "Add a station"))),
    el("form", { class: "form", onsubmit: (e) => { e.preventDefault(); save(); } },
      tabBar("Station settings", STATION_TABS, tab, openTab),
      tabPage(tab, pages[tab]()),
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

const SETTINGS_TABS = [
  ["startup", "Start-up", "power"],
  ["alerts", "Alerts", "bell"],
  ["phone", "Phone access", "phone"],
  ["studio", "Station Studio", "link"],
  ["website", "Website", "globe"],
  ["internet", "Internet", "tower"],
  ["updates", "Updates", "refresh"],
  ["app", "App", "app"],
];

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
  const pages = {
    startup: () => [
      el("h2", {}, "Start when this computer starts"),
      el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, login, "Start Station Server when I log in"),
      el("p", { class: "help" }, "Turn this on for the station's computer: after a restart, a power cut or Windows updates, Station Server starts in the background and the stations that were on air go back on air, with no window until you open one. On a Mac it is also started again if it ever stops unexpectedly. For a computer nobody logs in to, set Windows or macOS to log in automatically."),
    ],
    alerts: () => [alertsSettings()],
    phone: () => [phoneSettings()],
    studio: () => [studioSettings()],
    website: () => [websiteSettings()],
    internet: () => [internetSettings()],
    updates: () => [updateSettings()],
    app: () => [
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
    ],
  };
  const tab = SETTINGS_TABS.some((t) => t[0] === S.view.tab) ? S.view.tab : "startup";
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("div", {}, el("h1", {}, "Settings"))),
    tabBar("Settings pages", SETTINGS_TABS, tab, (id) => { S.view.tab = id; render(); }),
    tabPage(tab, el("section", {}, pages[tab]())),
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

// Settings › Station Studio: Station Studio sends each week here, with a
// link code (studiolink.py).
function studioSettings() {
  const l = S.state?.studio || {};
  const msg = el("div", { class: "form-msg", role: "status" });
  const on = el("input", { type: "checkbox" });
  on.checked = !!l.enabled;
  const port = el("input", { type: "text", value: String(l.port || 8772), autocomplete: "off" });
  const redraw = () => { const old = $("#studio-settings"); if (old) old.replaceWith(studioSettings()); };
  async function save(body, done = "Saved") {
    try {
      setState(await api("/api/settings", body));
      toast(done);
      redraw();
    } catch (e) { msg.textContent = e.message; if ("studio_link" in body) on.checked = !on.checked; }
  }
  on.addEventListener("change", () => save({ studio_link: on.checked, studio_port: port.value }));
  const newCode = () => {
    if (confirm("Make a new link code? Station Studio can't send weeks until you enter the new code there.")) {
      save({ studio_new_code: true }, "New link code made");
    }
  };
  const last = l.last;
  return el("div", { id: "studio-settings", class: "form" },
    el("p", { class: "help" }, "Station Studio can send each week's playlist straight to this computer, with no exporting or copying by hand, and show what's on air. Station Server keeps each week in the station's folder of weeks, where it waits for the start of its week, as an exported week does. Station Studio can be on this computer or another one on the network."),
    el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, on, "Let Station Studio send weeks to this computer"),
    el("div", { class: "grid" }, el("label", { class: "narrow" }, "Port", port)),
    el("div", { class: "actions" }, btn("Save port", "check", () => save({ studio_port: port.value }), "btn small")),
    msg,
    l.error ? el("div", { class: "errors" }, l.error) : null,
    l.running ? el("fieldset", {},
      el("legend", {}, "In Station Studio"),
      el("p", {}, "Open Settings › Station Server and enter this computer's address, ",
        l.addresses.length ? l.addresses.flatMap((a, i) => [i ? " or " : "", el("b", {}, a)]) : el("b", {}, `127.0.0.1:${l.port}`),
        l.addresses.length ? [" (with Station Studio on this computer, ", el("b", {}, `127.0.0.1:${l.port}`), ")"] : null,
        ", and the link code ", el("b", { class: "code" }, l.code),
        ". Then, on each station's page in Station Studio, choose the station here it sends to."),
      el("div", { class: "actions" }, btn("Make a new link code", "refresh", newCode, "btn small")),
      last ? el("p", { class: "help" }, `Last week received ${dayTime(last.at)}: week ${parseInt(last.week, 10)} for ${last.station}, ${last.tracks} tracks`,
        last.missing_count ? el("span", { class: "warn" }, ` (${last.missing_count} can't be found on this computer)`) : "") : null,
    ) : null,
    el("p", { class: "help" }, "The first time, Windows or macOS may ask whether Station Server may accept connections: allow it, on private networks. Only Station Studio with the code can send. For Station Studio outside this network, use a VPN such as Tailscale on both."),
  );
}

// Settings › Website: what's playing, published to a GitHub Pages site for
// the station's website and Station Player (feed.py).
// Settings › Internet: streaming from this computer (netcheck.py, upnp.py).
function internetSettings() {
  const n = S.state?.internet || {};
  const check = n.check;
  const here = n.stations || [];
  const router = n.router;
  const dd = n.duckdns || {};
  // Each part's messages show beside its buttons.
  const checkMsg = el("div", { class: "form-msg", role: "status" });
  const routerMsg = el("div", { class: "form-msg", role: "status" });
  const ddMsg = el("div", { class: "form-msg", role: "status" });
  const redraw = () => { const old = $("#internet-settings"); if (old) old.replaceWith(internetSettings()); };
  async function post(path, body, done, busyText, msg = checkMsg) {
    msg.className = "form-msg ok";
    msg.textContent = busyText || "";
    try {
      setState(await api(path, body));
      if (done) toast(done);
      redraw();
    } catch (e) { msg.className = "form-msg"; msg.textContent = e.message; }
  }
  const ddName = el("input", { type: "text", value: dd.name || "", placeholder: "rebirth", autocomplete: "off", spellcheck: "false" });
  const ddToken = el("input", { type: "password", value: "", autocomplete: "off",
    placeholder: dd.has_token ? "Kept (type a new one to change it)" : "the token from duckdns.org" });
  const outside = dd.host || check?.address;
  const portState = (port) => {
    const to = router?.ports?.[String(port)];
    if (!router || !router.found) return null;
    return to ? el("span", { class: "ok" }, to === router.here ? "Forwarded to this computer" : `Forwarded to ${to}, not this computer`)
      : el("span", { class: "warn" }, "Not forwarded");
  };
  return el("div", { id: "internet-settings", class: "form" },
    el("p", { class: "help" }, "For stations streaming from this computer (Edit › Server › This computer): how many listeners this internet connection can take, and how listeners outside this network reach it."),
    here.length ? null : el("p", { class: "help" }, "No station streams from this computer yet: choose This computer in a station's Edit › Server."),
    el("fieldset", {}, el("legend", {}, "How many listeners"),
      el("p", { class: "help" }, "Checking sends test data to Cloudflare's free speed test for about 10 seconds, to measure how fast this connection uploads. Each listener takes the station's bitrate from that; some is left for everything else."),
      el("div", { class: "actions" }, btn(n.checking ? "Checking…" : "Check my internet", "refresh",
        () => post("/api/internet/check", {}, "Checked", "Checking: about 10 seconds…"), "btn small")),
      checkMsg,
      check ? el("p", {}, "Upload ", el("b", {}, `${check.upload_mbps} Mbps`),
        check.address ? [" · seen from outside as ", el("b", {}, check.address)] : " · its address outside couldn't be found",
        el("span", { class: "help" }, ` · checked ${dayTime(check.at)}`)) : null,
      here.length ? el("table", { class: "help-table" },
        el("tr", {}, el("th", {}, "Station"), el("th", {}, "Port"), el("th", {}, "Allowed"), el("th", {}, "Suggested"), el("th", {}, "")),
        here.map((s) => el("tr", {},
          el("td", {}, s.name), el("td", {}, String(s.port)), el("td", {}, String(s.max)),
          el("td", {}, s.suggested === undefined ? "–" : String(s.suggested)),
          el("td", {}, s.suggested !== undefined && s.suggested > 0 && s.suggested !== s.max
            ? btn(`Use ${s.suggested}`, "check", () => post(`/api/stations/${s.id}/listeners`, { max: s.suggested }, `${s.name}: up to ${s.suggested} listeners`), "btn small")
            : null)))) : null,
      here.length > 1 ? el("p", { class: "help" }, "The stations share the upload, in proportion to their bitrates.") : null),
    here.length ? el("fieldset", {}, el("legend", {}, "The router"),
      el("p", { class: "help" }, "Listeners outside this network reach this computer through the router, once it forwards each station's port here. Most routers let Station Server do that itself (UPnP)."),
      el("div", { class: "actions" }, btn("Look at the router", "refresh", () => post("/api/internet/router", { action: "look" }, null, "Asking the router…", routerMsg), "btn small")),
      routerMsg,
      router && !router.found ? el("div", { class: "errors" }, router.error) : null,
      router && router.found ? el("p", {}, "Router's address: ", el("b", {}, router.address || "unknown"), " · this computer: ", el("b", {}, router.here || "unknown")) : null,
      router && router.found && router.address && check?.address && router.address !== check.address
        ? el("div", { class: "errors" }, `The router's address (${router.address}) isn't the one the internet sees (${check.address}): the internet provider shares one address between several customers, so a forwarded port won't reach here from outside. Ask the provider for a public IP address (often free, or a small charge), or send to a hosted server instead (Help › Streaming).`)
        : null,
      router && router.found ? here.map((s) => el("div", { class: "form-row" },
        el("b", {}, `${s.name}, port ${s.port}:`), portState(s.port),
        router.ports?.[String(s.port)]
          ? btn("Close the port", "stop", () => post("/api/internet/router", { action: "close", port: s.port }, `Port ${s.port} closed`, "", routerMsg), "btn small")
          : btn(`Open port ${s.port}`, "check", () => post("/api/internet/router", { action: "open", port: s.port }, `Port ${s.port} forwarded here`, "", routerMsg), "btn small"))) : null,
      el("p", { class: "help" }, "If the router won't, open its settings (often http://192.168.1.1 or http://192.168.0.1) and forward TCP ", here.map((s) => s.port).join(" and "), " to this computer. Give this computer a fixed address there first (a DHCP reservation), so the forward keeps pointing at it.")) : null,
    here.length ? el("fieldset", {}, el("legend", {}, "An address that stays the same"),
      el("p", { class: "help" }, "A home connection's address can change now and then, which breaks listeners' links. DuckDNS (free) gives it a name, like rebirth.duckdns.org, and Station Server keeps the name pointing at it."),
      el("ol", { class: "help steps" },
        el("li", {}, "Sign in at duckdns.org (with Google, GitHub or similar) and add a name."),
        el("li", {}, "Enter the name and the token shown at the top of that page here, and Save.")),
      el("div", { class: "grid" }, el("label", {}, "DuckDNS name", ddName), el("label", {}, "Token", ddToken)),
      el("div", { class: "actions" },
        btn("Save", "check", () => post("/api/settings", { duckdns_name: ddName.value, duckdns_token: ddToken.value }, "Saved", "", ddMsg), "btn small"),
        dd.name && dd.has_token ? btn("Update now", "refresh", () => post("/api/internet/duckdns", {}, null, "", ddMsg), "btn small") : null),
      ddMsg,
      dd.last ? el("p", { class: dd.last.ok ? "help" : "errors" }, dd.last.ok ? `${dd.host} updated ${dayTime(new Date(dd.last.at * 1000).toISOString())}` : dd.last.error) : null) : null,
    here.length && outside ? el("fieldset", {}, el("legend", {}, "Try it from a phone"),
      el("p", { class: "help" }, "Turn Wi-Fi off on the phone, so it uses mobile data like a listener would, then scan a code or open the address."),
      el("div", { class: "qr-row" }, here.map((s) => {
        const link = `http://${outside}:${s.port}/`;
        return el("div", { class: "qr" },
          el("img", { src: `/api/qr.svg?text=${encodeURIComponent(link)}`, alt: `QR code for ${link}`, width: 140, height: 140 }),
          el("b", {}, s.name), el("code", { class: "code" }, link));
      }))) : null,
  );
}

function websiteSettings() {
  const f = S.state?.feed || {};
  const msg = el("div", { class: "form-msg", role: "status" });
  const on = el("input", { type: "checkbox" });
  on.checked = !!f.enabled;
  const repo = el("input", { type: "text", value: f.repo || "", placeholder: "raby/rebirth-now", autocomplete: "off", spellcheck: "false" });
  const token = el("input", { type: "password", value: "", autocomplete: "off",
    placeholder: f.has_token ? "Kept (type a new one to change it)" : "github_pat_…" });
  const redraw = () => { const old = $("#website-settings"); if (old) old.replaceWith(websiteSettings()); };
  async function post(path, body, done) {
    try {
      setState(await api(path, body));
      toast(done);
      redraw();
    } catch (e) { msg.textContent = e.message; if ("feed" in body) on.checked = !on.checked; }
  }
  const fields = () => ({ feed_repo: repo.value, feed_token: token.value });
  on.addEventListener("change", () => post("/api/settings", { ...fields(), feed: on.checked },
    on.checked ? "Publishing to the website" : "Stopped publishing"));
  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast(text); }
  };
  const last = f.last;
  return el("div", { id: "website-settings", class: "form" },
    el("p", { class: "help" }, "Shows what each station is playing, what's coming up and what's just played, on the station's website and in Station Player. Station Server publishes it to a small website of its own on GitHub Pages (free), which your website shows in an embedded box. It's published about once an hour, and straight away when something changes (a live show, a skip, going off air), and works out the song on now from the times."),
    el("fieldset", {},
      el("legend", {}, "Setting it up"),
      el("ol", { class: "help steps" },
        el("li", {}, "On GitHub, make a new public repository, such as rebirth-now, ticking Add a README."),
        el("li", {}, "In its Settings › Pages, choose Deploy from a branch, main, / (root), and Save."),
        el("li", {}, "Make a fine-grained token (GitHub › Settings › Developer settings › Fine-grained tokens) for that repository only, with Contents: Read and write. Give it a long expiry, or none."),
        el("li", {}, "Enter the repository and the token below, and turn publishing on."))),
    el("div", { class: "grid" },
      el("label", {}, "GitHub repository", repo),
      el("label", {}, "Token", token)),
    el("label", { class: "check", style: "display:flex;gap:8px;align-items:center" }, on, "Publish what's playing to the website"),
    el("div", { class: "actions" },
      btn("Save", "check", () => post("/api/settings", fields(), "Saved"), "btn small"),
      f.enabled ? btn("Publish now", "refresh", () => post("/api/feed/publish", {}, "Publishing"), "btn small") : null),
    msg,
    f.error ? el("div", { class: "errors" }, f.error) : null,
    f.enabled && f.url ? el("fieldset", {},
      el("legend", {}, "On your website"),
      el("p", {}, "The page with every station: ", el("a", { href: f.url, target: "_blank", rel: "noopener" }, f.url),
        last ? el("span", { class: "help" }, ` · last published ${dayTime(last.at)}`) : null),
      el("p", { class: "help" }, "On Wix: Add › Embed code › Embed a site, and give it a station's widget address below. Make the box about 340 wide and 520 tall. Add &theme=dark for a dark box, or &next=3&recent=3 for shorter lists."),
      (f.stations || []).flatMap((s) => {
        const url = `${f.url}widget.html?station=${s.slug}`;
        const feed = `${f.url}${s.slug}.json`;
        return [
          el("div", { class: "form-row" }, el("b", {}, s.name), el("code", { class: "code" }, url),
            btn("Copy", "copy", () => copy(url), "btn small")),
          el("div", { class: "form-row help" }, "For Station Player's What's on box:",
            el("code", { class: "code" }, feed), btn("Copy", "copy", () => copy(feed), "btn small")),
        ];
      })) : null,
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

const HELP_TABS = [
  ["start", "Getting started", "play"],
  ["onair", "On air", "radio"],
  ["live", "Live shows", "mic"],
  ["stream", "Streaming", "tower"],
  ["unattended", "Unattended", "power"],
  ["new", "What's new", "bell"],
  ["trouble", "Problems", "alert"],
];

// Help diagrams: our own fixed SVG, coloured from the theme (.dg-* in style.css).
const DG = {
  box: (x, y, w, h, title, sub, cls = "") => `<g class="dg-node ${cls}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>`
    + `<text x="${x + w / 2}" y="${y + (sub ? h / 2 - 3 : h / 2 + 5)}" class="dg-title">${title}</text>`
    + (sub ? `<text x="${x + w / 2}" y="${y + h / 2 + 15}" class="dg-sub">${sub}</text>` : "") + "</g>",
  arrow: (x1, y1, x2, y2, label, dy = -8, cls = "") => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" class="dg-line ${cls}" marker-end="url(#dg-arrow)"/>`
    + (label ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 + dy}" class="dg-label">${label}</text>` : ""),
};
function diagram(height, body, caption) {
  const fig = el("figure", { class: "diagram" });
  fig.innerHTML = `<svg viewBox="0 0 720 ${height}" role="img" aria-label="${caption}"><defs><marker id="dg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="dg-head"/></marker></defs>${body}</svg>`;
  fig.append(el("figcaption", {}, caption));
  return fig;
}
const DIAGRAMS = {
  journey: () => diagram(250,
    DG.box(10, 30, 120, 56, "Music", "this week's playlist")
    + DG.arrow(130, 58, 180, 58)
    + DG.box(180, 22, 160, 72, "Station Server", "this computer, port 8000", "dg-accent")
    + DG.arrow(340, 58, 420, 58, "port 8000", -10)
    + DG.box(420, 30, 120, 56, "Router", "81.150.147.206")
    + DG.arrow(540, 58, 580, 58)
    + DG.box(580, 30, 130, 56, "Internet", "listeners anywhere")
    + DG.arrow(260, 94, 260, 168)
    + `<text x="268" y="135" class="dg-label dg-left">same Wi-Fi: straight here</text>`
    + DG.box(170, 168, 180, 56, "Listeners at home", "192.168.1.20:8000")
    + DG.box(510, 168, 200, 56, "Phones, speakers, website", "rebirth.duckdns.org:8000")
    + DG.arrow(645, 86, 645, 168),
    "Station Server is the server: it plays the music and sends it to every listener itself. Listeners outside come in through the router."),
  forward: () => diagram(200,
    DG.box(10, 20, 170, 60, "A listener asks for", "rebirth.duckdns.org:8000")
    + DG.arrow(180, 50, 260, 50, "DuckDNS")
    + DG.box(260, 20, 170, 60, "81.150.147.206:8000", "the router's address")
    + DG.arrow(430, 50, 520, 50, "port forward")
    + DG.box(520, 20, 190, 60, "192.168.1.20:8000", "this computer", "dg-accent")
    + `<text x="345" y="120" class="dg-sub">The router only knows where to send it because port 8000 is forwarded to this computer:</text>`
    + `<text x="345" y="142" class="dg-sub">Open port in Settings › Internet does that (UPnP), or set it in the router's own settings.</text>`
    + `<text x="345" y="170" class="dg-sub">DuckDNS just turns the name into the router's address, and follows it when it changes.</text>`,
    "From a name to this computer: DuckDNS finds the router, the port forward finds this computer."),
  upload: () => {
    const w = 700, share = 0.7;
    let body = `<rect x="10" y="30" width="${w}" height="44" rx="8" class="dg-track"/>`
      + `<rect x="10" y="30" width="${w * share}" height="44" rx="8" class="dg-fill"/>`;
    for (let i = 1; i < 14; i++) body += `<line x1="${10 + i * w * share / 14}" y1="34" x2="${10 + i * w * share / 14}" y2="70" class="dg-tick"/>`;
    body += `<text x="${10 + w * share / 2}" y="57" class="dg-title dg-on-fill">Listeners: 128 kbps each</text>`
      + `<text x="${10 + w * share + w * (1 - share) / 2}" y="57" class="dg-title">Rest of the house</text>`
      + `<text x="10" y="20" class="dg-sub dg-left">10 Mbps upload</text>`
      + `<text x="${10 + w * share / 2}" y="100" class="dg-sub">70%: 7,000 kbps ÷ 128 kbps = about 54 listeners</text>`
      + `<text x="${10 + w * share + w * (1 - share) / 2}" y="100" class="dg-sub">30% kept free</text>`;
    return diagram(120, body, "Every listener takes the stream's bitrate from the upload, all the time they're listening.");
  },
  choose: () => diagram(220,
    `<text x="10" y="22" class="dg-sub dg-left">This computer (free; listeners limited by the upload)</text>`
    + DG.box(10, 34, 170, 52, "Station Server", "", "dg-accent")
    + DG.arrow(180, 60, 300, 60, "one stream each")
    + DG.box(300, 34, 130, 52, "Router")
    + DG.arrow(430, 60, 560, 60, "54 listeners")
    + DG.box(560, 34, 150, 52, "Listeners")
    + `<text x="10" y="134" class="dg-sub dg-left">A hosted SHOUTcast or Icecast server (a monthly fee; hundreds or thousands of listeners)</text>`
    + DG.box(10, 146, 170, 52, "Station Server", "", "dg-accent")
    + DG.arrow(180, 172, 300, 172, "one stream")
    + DG.box(300, 146, 130, 52, "Hosted server")
    + DG.arrow(430, 172, 560, 172, "any number")
    + DG.box(560, 146, 150, 52, "Listeners"),
    "Both work from the same Station Server: Edit › Server › Type switches between them."),
};

function renderHelp() {
  const p = (...t) => el("p", { class: "help" }, ...t);
  const h2 = (t) => el("h2", { class: "spaced" }, t);
  const list = (...items) => el("ul", { class: "help steps" }, items.map((i) => el("li", {}, i)));
  const pages = {
    start: () => [
      el("h2", {}, "What it does"),
      p("Station Server plays a station-studio playlist in real time and sends it straight to your SHOUTcast or Icecast server with each song's title. It replaces iTunes playing out of the headphone jack, the loopback cable, and Winamp with its SHOUTcast plugin. Nothing else playing on this computer reaches the broadcast, and one computer can run several stations."),
      h2("Setting up a station"),
      p("Add a station. Its settings are in tabs:"),
      list(
        "Station: its name, station-studio's exports folder for it (each week is played in turn; leave it blank if Station Studio sends the weeks), the music folders, and a fallback to play if the music can't be read.",
        "Server: the server's address, port and source password (the ones in Winamp's SHOUTcast plugin), and a spare test stream to rehearse on. Test connection checks them. Or choose This computer, and Station Server is the server itself (Help › Streaming).",
        "Playback: evening out the volume, the crossfade, when the week starts, and jingles.",
        "Shows: recordings or playlists that go out at set times.",
        "Live shows: presenters going live from their own encoder."),
      p("Then Save and Go on air. A SHOUTcast server takes one source at a time: stop Winamp's SHOUTcast plugin before going on air, or the server refuses Station Server."),
      h2("Switching over from Winamp"),
      el("ol", { class: "help steps" },
        el("li", {}, "On the station's computer, install Station Server, copy the music onto it, and turn on Settings › Start-up › Start Station Server when I log in."),
        el("li", {}, "Add the station: its exports folder and music folders, and in Edit › Server the live server's address, port and source password. Under Test stream, enter a spare stream: another port of the server, or stream 2 of a SHOUTcast 2 server."),
        el("li", {}, "Set up Settings › Alerts, and a fallback folder in Edit › Station."),
        el("li", {}, "Press Ready check on the station's page and fix anything marked ✕."),
        el("li", {}, "Press Rehearse. The station plays to the test stream while Winamp carries on as normal. Press Listen, or open the test stream in a player, and check the songs, their titles and the levels. Nothing goes in the play log."),
        el("li", {}, "When it sounds right: stop Winamp's SHOUTcast plugin (and close iTunes), then press Go live. Listeners hear a short gap."),
        el("li", {}, "Keep Winamp and iTunes installed, closed, for a week or two. To go back: Take off air here, then start Winamp's plugin again.")),
      p("If going on air says something else is already sending to the server, Winamp's plugin (or another copy of Station Server) is still connected: a server takes one source at a time."),
      h2("Weeks straight from Station Studio"),
      p("Instead of exporting each week to a shared folder, Station Studio can send it here over the network. Turn on Settings › Station Studio: it shows this computer's address and a link code. In Station Studio, enter them in Settings › Station Server, then on each station's page choose the station here it sends to."),
      list(
        "Send to Station Server on a week's page sends it; or tick Also send each week when I export it to disk, on the station's page.",
        "Each week is checked and kept in the station's folder of weeks (Station Server makes one if the station has none), where it waits for its week like an exported week. Sending the week on air again replaces it within a minute, after the playing song.",
        "Station Studio is told how many of the week's songs this computer has, and lists any it can't find.",
        "The station's page in Station Studio shows whether it's on air here, the song playing, the weeks it has, and warns when it has nothing after this week."),
      h2("Two stations on one computer"),
      p("Add a station for each. They run side by side, each with its own playlist, server, clock and play log, and go on and off air separately: for example Rebirth Classics to port 8000 and Rebirth Radio to port 8002 of the same server, or each to a different server altogether. The sidebar shows each one's state, a microphone while a presenter is live, and headphones on the one you're listening to: Listen plays one station at a time, and Stop listening at the foot of the station list stops it from any page. If both take live shows, each needs its own live shows port, like 8010 and 8020."),
    ],
    onair: () => [
      el("h2", {}, "The station's page"),
      list(
        "The pill at the top says whether it's on air, rehearsing, trying again or off. Below it: the song playing (or the show, or the presenter), how many are listening, how much it was levelled by, the playlist and next week's.",
        "The running order shows what's coming and when, with shows in their places.",
        "Skip fades the song out over at most 2 seconds and plays the next. Listen plays the broadcast exactly as it goes to the server.",
        "Ready check runs through everything the station needs (ffmpeg, the playlist, the server, the test stream, the fallback, jingles, live shows, start at login, alerts) and says what to fix.",
        "Rehearse plays the station to the test stream, so you can listen before going live; Go live switches it to the real server. A rehearsal doesn't go in the play log."),
      p("The listener count is as the server counts them: listeners to a relay (such as a hosted SHOUTcast account relaying the station) aren't included."),
      h2("The week's clock"),
      p("Following the clock, the first track plays at the start of the week (Monday 00:00 unless you change it in Edit › Playback) and the rest follow by their lengths. A restart picks up where the station should be."),
      p("Next week's export can be made any time: it waits, and goes on at the start of its week (the station's page says when). A week exported late, after its week has begun, goes on within a minute, after the playing track. Exporting the week on air again picks up the changes the same way."),
      h2("Levels and gaps"),
      p("With Even out the volume on, each track is played at about the same loudness: from its ReplayGain or Sound Check tags, or, for a track without them, from measuring it. Station Server measures every track once, in the background and gently (the coming tracks first), and remembers the results. Measuring also finds the silence at the start and end of each track, which is then left out, so songs follow on without dead air. Until a track has been measured it plays as it is."),
      h2("Crossfades and jingles"),
      p("Edit › Playback › Crossfade fades the end of each song out under the start of the next, over 1 to 10 seconds (Off plays them one straight after the other). A track that fades out by itself sounds best with 2 to 4 seconds."),
      p("Edit › Playback › Jingles plays one of a folder of jingles or station idents after every few songs, in a shuffled order, each once before any plays again. The song before fades out under the jingle; the next song starts as the jingle ends. Jingles aren't played during shows, live shows or the fallback and don't go in the play log; while one plays, the server shows the station's name as the title."),
      p("The week's clock allows for both, so the running order's times and a restart still land in the right place."),
      h2("Shows at set times"),
      p("Edit › Shows pins a show to times in the week, like Saturday 20:00. A show is a recording, a playlist, or a folder of recordings played in name order. It starts exactly on time (cutting the song playing) or after that song. When it ends, the music comes back where the week's clock says, so the running order's times stay right. After a restart part-way through a show, the station joins the show where it should be. Shows apply as soon as you save, even on air."),
      h2("On your website"),
      p("Settings › Website shows each station's song on now, what's coming up (with times) and what's just played on your website, in a box you embed (on Wix: Add › Embed code › Embed a site). Station Server publishes it to a small free GitHub Pages site of its own: Settings › Website has the steps, and the address to give your website for each station. While a presenter is live, the box shows their name; off air, it says so. Station Player shows it on the station's card too: paste the station's feed address (Settings › Website, under its widget address) into the station's What's on box."),
      p("It's published about once an hour, and within a couple of minutes of anything changing what's on (a skip, a restart, a show, a live show, going off or on air). The box works out the song on now from the times, so it stays right in between. A rehearsal isn't shown. The page with every station is at the site's own address."),
      h2("The play log"),
      p("Every track each station plays is logged, live shows included. Play log downloads a station's plays between two dates as a spreadsheet, ready for a PRS for Music or PPL return. Plays under 30 seconds aren't counted."),
    ],
    live: () => [
      el("h2", {}, "Setting up"),
      p("Turn on Edit › Live shows and set a port (8010 unless you choose another). Add each presenter with their own password: their name shows on the station's page when they're live, and goes in the play log and the recording's name, whatever their encoder calls itself. One can be removed without giving the others a new password. A shared password works too, for anyone you haven't added. Presenters you add or change can go live straight away, even while the station is on air."),
      p("While the station is on air, a presenter's encoder connects to this computer as if it were the station's server. The song playing fades out under them and they're live, with the song titles their encoder sends. When they disconnect, the music fades back in where the week's clock says. A show due while they're live is left out. One presenter at a time: a second is turned away."),
      h2("The presenter's encoder"),
      list(
        "BUTT: Settings › Main › Server › Add. Type Icecast, Address this computer's address, Port 8010, Password their password, Mountpoint live, User source. (Or Type SHOUTcast, with the address, port and password.)",
        "Mixxx: Options › Live Broadcasting. Type Icecast 2, the same details, Login source.",
        "Winamp's SHOUTcast plugin: the address, port and password."),
      p("Presenters at home connect over the internet: on the router at the station, forward the port and the one above it (8010 and 8011) to this computer, as for the SHOUTcast server, and give presenters the station's internet address. Allow Station Server through the firewall when Windows or macOS asks. The Ready check shows the address to use on this network."),
      h2("Their volume"),
      p("With Even out presenters' volume on (Edit › Live shows), a presenter who's too quiet or too loud is brought towards the music's loudness as they go, lifting a quiet one by up to about 18 dB. It adds 2 to 3 seconds' delay, so the music keeps playing until their sound is ready and the hand-over has no gap."),
      h2("While they're live"),
      p("The station's page shows who's live, the title they've sent, since when and where from. End live show takes them off air; their encoder will try to reconnect, so they're kept off for two minutes. It works from Phone access too. If a presenter's connection drops, the music comes back after 10 seconds. After 5 wrong passwords in 5 minutes, that address is kept out for a few minutes."),
      p("What they play goes in the play log by the titles their encoder sends (or as one Live show entry if it sends none). Each show is recorded, exactly as their encoder sent it, in Station Server's recordings folder (the Ready check says where; about 60 MB an hour at 128 kbps); turn that off in Edit › Live shows."),
    ],
    unattended: () => [
      el("h2", {}, "Running unattended"),
      p("Turn on Settings › Start-up › Start Station Server when I log in. After a restart, a power cut or Windows updates, it starts in the background and the stations that were on air go back on air. Closing this window leaves them on air; Quit takes them off."),
      p("While a station is on air the computer doesn't go to sleep by itself (the screen can still turn off). If the server can't be reached, or the music's drive isn't there yet, the station shows Trying again… and keeps trying until it's on air; it does the same if the broadcast ever fails."),
      p("For a computer nobody is at, after a power cut: set it to switch itself back on when the power returns (a setting in the BIOS, often called Restore on AC power loss), and let Windows sign in by itself, since Station Server starts when you sign in. Also check Windows Update's active hours, so restarts happen when it suits you."),
      h2("Alerts"),
      p("Settings › Alerts tells you, on your phone (with the free ntfy app) or by email, when a station has been off air, can't reach its server, or is playing silence or its fallback for a few minutes, and again when it's back. Send a test checks it reaches you. Station Server can't tell anyone if the computer itself is off or offline: a free outside uptime check of the stream covers that."),
      h2("Phone access"),
      p("Settings › Phone access lets a phone or another computer on the same network see the stations, listen, put them on or off air and end a live show, with a password. Changing settings stays on this computer."),
      h2("Its own app, and new versions"),
      p("On a Mac, the downloaded Station Server has a window of its own, like Apple Music: closing the window leaves the stations on air, clicking its Dock icon brings it back, and its menus have Go, Settings (⌘,) and Quit (⌘Q). Right-click its Dock icon for Add a station and Play log."),
      p("Settings › App › Install as app gives Station Server its own icon in the Dock or taskbar, with Add a station and Play log on its menu. Opened from there when Station Server isn't running, the window starts it."),
      p("When a new version is out, a note at the foot of the sidebar offers Update now. Station Server downloads it from its GitHub page, checks it's the genuine file and restarts; stations that were on air go back on air. Settings › Updates has Check for updates, and can stop it asking GitHub."),
    ],
    stream: () => [
      el("h2", {}, "Streaming from this computer"),
      p("Station Server can be the station's server itself: listeners connect straight to this computer, with no SHOUTcast DNAS, Icecast or hosting needed, and nothing to pay."),
      DIAGRAMS.journey(),
      h2("Setting it up, step by step"),
      el("ol", { class: "help steps" }, [
        "Edit › Server: choose This computer as the type, a port (8000 keeps a DNAS's old links working) and the most listeners to allow. Check the port says whether it's free.",
        "Go on air (or Rehearse first). Listen, or open http://this-computer's-address:8000/ on a phone on the same Wi-Fi: it plays. The Ready check shows the address.",
        "Settings › Internet › Check my internet: it measures the upload and suggests how many listeners to allow. Use sets it.",
        "Settings › Internet › Look at the router, then Open port. If the router won't, forward the port by hand (below).",
        "Optional: a DuckDNS name, so listeners have an address that doesn't change.",
        "Scan the code on Settings › Internet with a phone on mobile data (Wi-Fi off). If it plays, the world can hear it.",
        "Give listeners the address, and put it in the website's player and station directories.",
      ].map((t) => el("li", {}, t))),
      h2("Ports and the router"),
      p("At home, every device shares the router's one internet address. When a listener outside connects to that address, the router needs to know which computer to send them to: that's a port forward."),
      DIAGRAMS.forward(),
      list(
        "Most routers let Station Server set it up (UPnP): Settings › Internet › Open port. Close the port takes it away again.",
        "By hand: open the router's settings (often http://192.168.1.1 or http://192.168.0.1; the address is on a sticker on the router), find Port forwarding (sometimes Virtual servers or NAT), and forward TCP 8000 (the station's port) to this computer's address.",
        "First give this computer a fixed address on the network (DHCP reservation, in the same settings), so the forward keeps pointing at it after a restart.",
        "Two stations streaming from here use two ports, like 8000 and 8002: forward both."),
      h2("A name that stays the same"),
      p("A home connection's address can change now and then (after the router restarts, say), which would break listeners' links. DuckDNS is free: sign in at duckdns.org, add a name like rebirth, and enter the name and token in Settings › Internet. Station Server tells DuckDNS this connection's address every few minutes, so http://rebirth.duckdns.org:8000/ always reaches the station."),
      h2("How many listeners"),
      p("Each listener takes the stream's bitrate from the internet connection's upload, for as long as they listen, and the rest of the house needs some too. Download speed doesn't matter here: it's the upload."),
      DIAGRAMS.upload(),
      el("table", { class: "help-table" },
        el("tr", {}, el("th", {}, "Upload"), el("th", {}, "64 kbps"), el("th", {}, "128 kbps"), el("th", {}, "192 kbps")),
        ...[5, 10, 20, 50].map((mbps) => el("tr", {}, el("td", {}, `${mbps} Mbps`),
          ...[64, 128, 192].map((kbps) => el("td", {}, String(Math.floor(mbps * 1000 * 0.7 / kbps))))))),
      p("Settings › Internet measures it for you. Set Most listeners a little under that; when it's full, the next listener is turned away rather than everyone stuttering. A lower bitrate (Edit › Server) fits more listeners."),
      h2("This computer or a hosted server?"),
      DIAGRAMS.choose(),
      p("Streaming from this computer is free and needs nothing else, and suits a station whose listeners fit in the upload. For more, send to a hosted SHOUTcast or Icecast server: Station Server sends it one stream and the host copies it to everyone. Switching is one setting, and old links keep working if the host uses the same address and port."),
      h2("When it doesn't work"),
      list(
        "Plays on the Wi-Fi but not on mobile data: the port isn't forwarded, or is forwarded to another computer. Look at the router in Settings › Internet.",
        "Mac, \"macOS isn't letting Station Server reach the router\": quit Station Server and open it again from Applications, and allow it if macOS asks. If it's listed in System Settings › Privacy & Security › Local Network, turn it on.",
        "Windows: when it asks whether Station Server may use the network, allow Private and Public. Otherwise Windows Security › Firewall › Allow an app through the firewall.",
        "\"No router on this network offers UPnP\": turn on UPnP in the router's settings, or forward the port by hand.",
        "The router's address isn't the one the internet sees: the internet provider shares one address between customers (CGNAT), so nothing outside can reach in. Ask the provider for a public IP address, or use a hosted server.",
        "\"Port 8000 is in use\": another program (an old DNAS, say) has it. Close that, or choose another port.",
        "Listeners turned away: the station is full. Settings › Internet suggests how many the upload can take."),
    ],
    new: () => [
      el("h2", {}, "0.16.4"),
      list("On a Mac, Station Server always starts through the app (opened, at login and after an update), which stays on quietly in the background, so macOS lets it reach the router and servers on this network. Start at login set up by an older version is updated by itself."),
      h2("0.16.3"),
      list("Look at the router searches out of this computer's own network even with a VPN on, and also asks the router directly, so more routers are found."),
      h2("0.16.2"),
      list("Help › Streaming is rewritten, with diagrams: how listeners reach the station, ports and the router, DuckDNS, how many listeners, this computer or a hosted server, and what to do when it doesn't work.",
        "On a Mac, Look at the router says when macOS needs Station Server allowed in System Settings › Privacy & Security › Local Network, and the app now asks for it.",
        "Settings › Internet warns when the internet provider shares one address between customers, so a forwarded port can't work."),
      h2("0.16.1"),
      list("Check my internet finds this connection's outside address a second way, if Cloudflare's speed test page doesn't give it."),
      h2("0.16: Settings › Internet"),
      list(
        "Check my internet measures the upload and suggests how many listeners each station streaming from this computer can take, with Use to set it.",
        "Opens each station's port on the router (UPnP), keeps a free DuckDNS name pointing at this connection, and shows a code to scan from a phone on mobile data.",
        "The Ready check says when a station allows more listeners than the upload suits.",
        "Changing a station's server while it's on air offers to switch over straight away, and the status line shows where it's really streaming until then."),
      h2("0.15: streaming from this computer"),
      list(
        "Station Server can be the station's server: choose This computer in Edit › Server, and listeners connect straight to it. No DNAS, Icecast or hosting needed (Help › Streaming)."),
      h2("0.14: on your website"),
      list(
        "Settings › Website publishes each station's now playing, coming up and just played to a GitHub Pages site, with a box to embed on your website."),
      h2("0.13: Station Studio link"),
      list(
        "Station Studio sends each week straight here, with a link code: no shared folder.",
        "Station Studio's station page shows what's on air here, the weeks it has, and warns when next week is missing."),
      h2("0.12.1: tabs"),
      list("A station's settings, Settings and Help are in tabs, like Station Player's."),
      h2("0.12: live shows"),
      list(
        "Presenters go live from BUTT, Mixxx or Winamp, connecting to this computer: the song fades out under them and the music comes back when they leave.",
        "Each presenter has their own password, which names them on the page, in the play log and on the recording.",
        "Presenters' volume is evened out to match the music.",
        "Each live show is recorded; End live show takes a presenter off air."),
      h2("0.11: crossfades and jingles"),
      list(
        "A crossfade between songs, of 1 to 10 seconds.",
        "Jingles or idents after every few songs, shuffled.",
        "Skip fades out quickly, whatever the crossfade."),
      h2("0.10: the switch-over kit"),
      list(
        "Ready check: everything a station needs, with what to fix.",
        "Rehearse on a test stream while Winamp carries on, then Go live.",
        "It notices Winamp (or anything else) still sending to the server."),
      h2("0.9: keeping an eye on it"),
      list(
        "The listener count, and Listen to hear the broadcast itself.",
        "Alerts to your phone or email when a station is off air.",
        "Phone access from a phone on the same network."),
    ],
    trouble: () => [
      el("h2", {}, "If something goes wrong"),
      p("If the server goes away, the station keeps playing and reconnects every few seconds. A track whose file can't be read is skipped. If none can be (the music's drive is missing, say), the station plays its fallback, a folder of jingles or songs set in Edit › Station, and then tries the playlist again; with no fallback it sends silence. The page says which. Tracks the playlist names but this computer can't find are listed under the station: add the folder they're in as a music folder."),
      p("Go live says something else is sending to the server? Winamp's plugin is probably still connected: stop it, then press Go live again. A presenter can't connect? Check the Ready check's Live shows row, the router's port forwarding and their password; after 5 wrong passwords in 5 minutes their address is kept out for a few minutes."),
      p("Station Studio can't reach Station Server? Check Settings › Station Studio is on, that Station Studio has the address it shows (with the port) and the current link code, and that the firewall lets Station Server accept connections on private networks. After 5 wrong codes, that address is kept out for a few minutes."),
      p("The website not changing? Settings › Website says if GitHub turned the token down (it may have expired: make a new one) or couldn't be reached. The site itself takes a minute or two to change after each publish. If the address says Not found, turn on GitHub Pages in the repository's Settings › Pages (Deploy from a branch, main)."),
      p("ffmpeg does the decoding and encoding. The downloaded app has its own; otherwise install it (macOS: brew install ffmpeg; Windows: winget install ffmpeg)."),
      p("Station Server keeps a log, station-server.log, with its settings, play logs and recordings in the .station-server folder in your home folder."),
      p("Asking for help? Copy diagnostics puts the version, each station's state and the end of the log on the clipboard, with passwords taken out, ready to paste into a message."),
      el("div", { class: "actions" }, btn("Copy diagnostics", "copy", copyDiagnostics, "btn")),
      S.diagnostics ? el("textarea", { class: "diagnostics", readonly: true, rows: 12 }, S.diagnostics) : null,
    ],
  };
  const tab = HELP_TABS.some((t) => t[0] === S.view.tab) ? S.view.tab : "start";
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("div", {}, el("h1", {}, "Help"))),
    tabBar("Help pages", HELP_TABS, tab, (id) => { S.view.tab = id; render(); }),
    tabPage(tab, el("section", {}, pages[tab]())),
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
  go(type, tab) { go({ type, id: null, tab }); },
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
