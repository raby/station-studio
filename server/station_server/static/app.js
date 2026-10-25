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
};

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
  alert: ["M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z", "M12 9v4", "M12 17h.01"],
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

const STATE_WORDS = { off: "Off air", starting: "Going on air…", on: "On air", stopping: "Going off air…", error: "Problem" };

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
  if (t === "settings") return view.replaceChildren(renderSettings());
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
  const busy = s.state === "starting" || s.state === "stopping";
  const head = el("div", { class: "view-head" },
    el("div", {},
      el("div", { class: `status-pill ${s.state}` }, el("span", { class: "dot" }), STATE_WORDS[s.state]),
      el("h1", {}, st.name),
      el("div", { class: "meta" }, st.server || "No server set"),
    ),
    el("div", { class: "spacer" }),
    on || busy
      ? btn("Take off air", "stop", () => setOnAir(st.id, false), "btn")
      : btn("Go on air", "play", () => setOnAir(st.id, true), "btn primary"),
    on ? btn("Skip", "next", () => post(`/api/stations/${st.id}/skip`, "Skipped")) : null,
    btn("Edit", "edit", () => go({ type: "edit", id: st.id }), "btn ghost"),
  );
  const parts = [head];
  if (s.error) parts.push(el("div", { class: "errors" }, s.error));
  if (s.stuck) parts.push(el("div", { class: "errors" }, "None of the playlist's tracks can be read, so the station is sending silence. Check the music folders in Edit."));

  if (on && s.now) {
    const n = s.now;
    parts.push(el("div", { class: "panel now" },
      el("div", { class: "glyph big" }, icon("radio")),
      el("div", { class: "title" }, n.title),
      el("div", { class: "artist" }, [n.artist, n.album].filter(Boolean).join(" · ") || " "),
      el("div", { class: "meter" },
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
    if (s.now && s.now.gain_db) facts.push(el("span", {}, "Levelled ", el("b", {}, `${s.now.gain_db > 0 ? "+" : ""}${s.now.gain_db} dB`)));
  }
  if (s.playlist) {
    facts.push(el("span", {}, "Playlist ", el("b", {}, s.playlist.name)));
    facts.push(el("span", {}, `${s.playlist.tracks} tracks · ${s.playlist.hours} hours`));
    if (s.playlist.next) facts.push(el("span", { class: "ok" }, "Next: ", el("b", {}, s.playlist.next.name), " after this track"));
  }
  facts.push(el("span", {}, st.start_mode === "clock"
    ? `Week starts ${DAYS[st.week_start.day]} ${st.week_start.time}` : "Starts from track 1"));
  facts.push(el("span", {}, `MP3 ${st.bitrate} kbps`));
  parts.push(el("div", { class: "facts" }, facts));

  if (s.playlist && s.playlist.missing_count) {
    parts.push(el("details", { class: "missing", open: S.missingOpen, ontoggle: (e) => { S.missingOpen = e.target.open; } },
      el("summary", {}, s.playlist.missing_count === 1 ? "1 track can't be found and is left out" : `${s.playlist.missing_count} tracks can't be found and are left out`),
      el("ul", {}, s.playlist.missing.map((m) => el("li", {}, `${m.artist ? m.artist + " – " : ""}${m.title}`,
        el("span", { class: "help" }, `  ${m.location}`)))),
    ));
  }

  if (on && s.order.length) {
    parts.push(el("h2", { class: "section" }, "Running order"));
    parts.push(el("table", { class: "tracks" },
      el("thead", {}, el("tr", {}, el("th", { class: "time" }, "Time"), el("th", { class: "title" }, "Title"),
        el("th", {}, "Artist"), el("th", { class: "dur" }, "Length"))),
      el("tbody", {}, s.order.map((r, i) => el("tr", { class: i === 0 ? "playing" : null },
        el("td", { class: "time" }, hhmm(r.at)),
        el("td", { class: "title" }, r.title),
        el("td", { class: "artist" }, r.artist || ""),
        el("td", { class: "dur" }, clock(r.seconds)),
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

async function setOnAir(id, on) {
  try {
    setState(await api(`/api/stations/${id}/on`, { on }));
    render();
  } catch (e) { toast(e.message); }
}

async function post(path, message) {
  try {
    setState(await api(path, {}));
    if (message) toast(message);
    render();
  } catch (e) { toast(e.message); }
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
    bitrate: String(st?.bitrate || 128),
    level: st ? !!st.level : true,
    start_mode: st?.start_mode || "clock",
    day: String(st?.week_start?.day ?? 0),
    time: st?.week_start?.time || "00:00",
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
    bitrate: Number(v.bitrate),
    level: v.level,
    start_mode: v.start_mode,
    week_start: { day: Number(v.day), time: v.time },
  };
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
  const formMsg = el("div", { class: "form-msg", role: "status" });

  async function pick(kind) {
    try {
      const r = await api("/api/pick", { kind });
      if (r.path) { v.playlist = r.path; redraw(); }
    } catch (e) { playlistMsg.className = "form-msg"; playlistMsg.textContent = e.message; }
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
  async function checkServer() {
    serverMsg.className = "form-msg ok";
    serverMsg.textContent = "Connecting…";
    try {
      const r = await api("/api/check/server", { ...formPayload(v), id: s?.station.id });
      serverMsg.textContent = r.on_air
        ? `This station is on air to ${r.server} now, so the connection works.`
        : `Connected to ${r.server}: the server accepted the password.`;
    } catch (e) { serverMsg.className = "form-msg"; serverMsg.textContent = e.message; }
  }
  async function save() {
    try {
      const payload = formPayload(v);
      const r = await api(editing ? `/api/stations/${s.station.id}` : "/api/stations", payload);
      setState(r);
      const id = editing ? s.station.id : r.id;
      const runner = stationById(id);
      toast(runner && runner.state === "on" ? "Saved. Changes take effect the next time it goes on air." : "Saved");
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
        el("p", { class: "help" }, "Choose station-studio's exports folder for this station and the newest week is played, switching to the next week's as soon as it's exported."),
        playlistMsg,
        el("div", { class: "grid" },
          el("label", {}, "Music folders (one per line), searched for tracks that have moved",
            bind("music_folders", el("textarea", { rows: 2 }, v.music_folders))),
          el("label", {}, "Path rewrites (one per line: from = to)",
            bind("remaps", el("textarea", { rows: 2, placeholder: "/Users/rob/Music = D:/Music" }, v.remaps)))),
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
        el("div", { class: "actions" }, btn("Test connection", "refresh", checkServer, "btn small")),
        serverMsg,
      ),
      el("fieldset", {}, el("legend", {}, "Playback"),
        el("div", { class: "grid" }, check("level", "Even out the volume (uses ReplayGain / Sound Check in the files)")),
        el("div", { class: "radio-row" },
          radio("start_mode", "clock", "Follow the week's clock"),
          radio("start_mode", "top", "Always start from track 1")),
        el("div", { class: "grid" },
          select("day", "The week starts on", DAYS.map((d, i) => [String(i), d]), true),
          text("time", "at", { type: "time", narrow: true })),
        el("p", { class: "help" }, "Following the clock, the first track plays at the start of the week and the rest follow by their lengths, so after a restart the station picks up where it should be."),
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
      el("h2", {}, "Two stations on one computer"),
      p("Add a station for each. They run side by side, each with its own playlist, server, clock and play log, and go on and off air separately: for example Rebirth Classics to port 8000 and Rebirth Radio to port 8002 of the same server."),
      el("h2", {}, "Running unattended"),
      p("Turn on Settings › Start Station Server when I log in. After a restart, a power cut or Windows updates, it starts in the background and the stations that were on air go back on air. Closing this window leaves them on air; Quit takes them off."),
      el("h2", {}, "The week's clock"),
      p("Following the clock, the first track plays at the start of the week (Monday 00:00 unless you change it) and the rest follow by their lengths. A restart picks up where the station should be. A new week's export is picked up within a minute and starts after the playing track."),
      el("h2", {}, "Its own app, and new versions"),
      p("On a Mac, the downloaded Station Server has a window of its own, like Apple Music: closing the window leaves the stations on air, clicking its Dock icon brings it back, and its menus have Go, Settings (⌘,) and Quit (⌘Q). Right-click its Dock icon for Add a station and Play log."),
      p("Settings › Install as app gives Station Server its own icon in the Dock or taskbar, with Add a station and Play log on its menu. Opened from there when Station Server isn't running, the window starts it."),
      p("When a new version is out, a note at the foot of the sidebar offers Update now. Station Server downloads it from its GitHub page, checks it's the genuine file and restarts; stations that were on air go back on air. Settings › Updates has Check for updates, and can stop it asking GitHub."),
      el("h2", {}, "If something goes wrong"),
      p("If the server goes away, the station keeps playing and reconnects every few seconds. A track whose file can't be read is skipped; if none can be, the station sends silence and the page says so. Tracks the playlist names but this computer can't find are listed under the station: add the folder they're in as a music folder."),
      p("ffmpeg does the decoding and encoding. The downloaded app has its own; otherwise install it (macOS: brew install ffmpeg; Windows: winget install ffmpeg)."),
      p("Station Server keeps a log, station-server.log, with its settings and play logs in the .station-server folder in your home folder."),
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
