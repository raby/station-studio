// ---------- library + navigation ----------------------------------------

// Studio tools (station-studio's playlists, recording, the play log) are
// for people making a station; without them it's the plain player.
const studio = () => S.lib?.config?.studio !== false;
const STUDIO_HELP = ["record", "log"];

async function loadState(newState) {
  S.lib = newState || (await api("/api/state"));
  document.body.classList.toggle("public", !studio());
  renderNav();
  renderErrors();
  renderUpdate();
  syncRecordings();
}

function renderErrors(extra) {
  const box = $("#errors");
  // A folder or library that has gone is shown (with a way to find it)
  // on the Music settings page, so here it's one line pointing there.
  const gone = (S.lib.sources || []).filter((s) => !s.exists);
  const errors = S.lib.errors.filter((e) => !gone.some((s) => e.includes(s.path)));
  const onMusicPage = S.view.type === "settings" && (S.view.tab || "music") === "music";
  const goneLine = gone.length && !onMusicPage ? el("div", {},
    gone.length === 1 ? `The ${gone[0].kind === "music" ? "music folder" : "playlist library"} “${gone[0].name || gone[0].path}” can't be found. ` : `${gone.length} of your music sources can't be found. `,
    el("button", {
      type: "button", class: "btn small",
      onclick: () => show({ type: "settings", tab: "music" }),
    }, "Show me")) : null;
  const all = extra ? [extra, ...errors] : errors;
  box.replaceChildren(...all.map((e) => el("div", {}, e)), goneLine || "");
  box.hidden = all.length === 0 && !goneLine;
}

// "A new version is out", until dismissed for that version; while
// updating, how far it's got.
function renderUpdate() {
  const box = $("#update");
  const latest = S.lib.update;
  const u = S.lib.updating;
  if (u && u.stage !== "failed") {
    box.replaceChildren(el("div", {}, updateStageText(u)));
    box.hidden = false;
    return;
  }
  if (!latest || store("dismissed-update") === latest) {
    box.hidden = true;
    return;
  }
  box.replaceChildren(...[  // (replaceChildren would show a null as "null")
    el("div", {}, `Station Player ${latest} is out.`),
    u && u.stage === "failed" && u.version === latest ? updateFailure(u) : null,
    el("div", { class: "row" },
      el("button", { type: "button", class: "btn small primary", onclick: () => startUpdate(latest) }, "Update now"),
      el("button", {
        type: "button", class: "btn small",
        onclick: () => { store("dismissed-update", latest); renderUpdate(); },
      }, "Not now")),
  ].filter(Boolean));
  box.hidden = false;
}

function updateStageText(u) {
  if (u.stage === "downloading") return `Downloading Station Player ${u.version}… ${u.percent || 0}%`;
  if (u.stage === "installing") return `Installing Station Player ${u.version}…`;
  return `Restarting Station Player… ${u.version} opens here by itself.`;
}

function updateFailure(u) {
  return el("div", { class: "update-failed", role: "alert" }, u.error, " ",
    el("a", { href: u.page, target: "_blank", rel: "noopener" }, "Download it yourself"), ".");
}

// Update now: the player downloads and installs the new version, quits,
// and the new one starts; this page waits for it and reloads into it.
async function startUpdate(version) {
  if (activeRecordings().length && !confirm("Updating stops your recordings (what's recorded so far is saved). Update now?")) return;
  const from = S.lib.version;
  try {
    await loadState(await api("/api/update", { version }, { quiet: true }));
  } catch (err) {
    toast(err.message, { error: true });
    return;
  }
  announce(`Updating Station Player to ${version}`);
  if (S.view.type === "settings") renderView();
  const until = Date.now() + 10 * 60 * 1000;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 1000));
    let state = null;
    try {
      const res = await fetch("/api/state");
      if (res.ok) state = await res.json();
    } catch { /* restarting */ }
    if (!state) {
      S.lib.updating = { ...(S.lib.updating || { version }), stage: "restarting" };
      renderUpdate();
      continue;
    }
    if (state.version !== from) {
      location.reload();
      return;
    }
    S.lib.updating = state.updating;
    renderUpdate();
    if (S.view.type === "settings") syncUpdateSettings();
    if (!state.updating || state.updating.stage === "failed") return;
  }
  S.lib.updating = { version, stage: "failed", page: `https://github.com/raby/station-studio/releases/tag/player-v${version}`,
    error: "Station Player didn't come back after updating. Open it again from Applications or the Start menu." };
  renderUpdate();
}

// Run a settings action from a button; failures show in the error box.
async function act(path, body, done = "Done") {
  try {
    await loadState(await api(path, body));
    renderView();
    focusHeading();
    toast(done);
  } catch (err) {
    renderErrors(err.message);
  }
}

// After a view is rebuilt, put focus somewhere sensible, not <body>.
function focusHeading() {
  const h = $("#view h1");
  if (h) { h.tabIndex = -1; h.focus(); }
}

function navButton(label, count, view, active, iconName = "list") {
  return el("li", {},
    el("button", {
      type: "button", class: "nav-item", "aria-current": active ? "true" : "false",
      onclick: () => show(view),
    }, icon(iconName), el("span", {}, label), count === null ? null : el("span", { class: "count" }, count)));
}

function renderNav() {
  const isActive = (pl) => S.view.type === "playlist" && S.view.id === pl.id;
  const itunes = S.lib.playlists.filter((p) => p.kind === "itunes");
  const folders = S.lib.playlists.filter((p) => p.kind === "folder");
  const mine = myPlaylists();
  $("#nav-mine").replaceChildren(...(mine.length
    ? mine.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p), "music"))
    : [el("li", { class: "nav-empty" }, "Make one with +")]));
  $("#nav-itunes").replaceChildren(...(itunes.length
    ? itunes.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p)))
    : [el("li", { class: "nav-empty" }, "None yet")]));
  $("#nav-folders").replaceChildren(...(folders.length
    ? folders.map((p) => navButton(p.name, p.count, { type: "playlist", id: p.id }, isActive(p), "folder"))
    : [el("li", { class: "nav-empty" }, "None yet")]));
  for (const b of document.querySelectorAll(".nav-item[data-view]")) {
    b.setAttribute("aria-current", S.view.type === b.dataset.view ? "true" : "false");
  }
  $("#btn-add-music").setAttribute("aria-current", S.view.type === "welcome" ? "true" : "false");
  const count = $("#upnext-count");
  count.textContent = S.upNext.length || "";
  const next = S.lib && S.lib.next_alarm;
  $("#nav-next-alarm").textContent = next ? hhmm(new Date(next.at)) : "";
  $("#nav-next-alarm").title = next ? `Next alarm ${hhmm(new Date(next.at))}` : "";
}

let showToken = 0;

async function show(view) {
  // Only the latest click may render: a slow response for an earlier
  // click must not replace the playlist the user has since opened.
  const token = ++showToken;
  if (view.type === "discover" && S.view.type !== "discover" && !view.list) F.view = "globe";
  S.view = view;
  editingStation = null;
  renderErrors();
  if (view.type !== "search") store("view", JSON.stringify(view));
  if (view.type !== "search") try { sessionStorage.setItem("sp.view", JSON.stringify(view)); } catch { /* private window */ }
  if (view.type === "playlist" || view.type === "search") {
    let detail = null;
    try {
      detail = view.type === "playlist"
        ? await api(`/api/playlists/${view.id}`)
        : searchResults(view.q, await api(`/api/search?q=${encodeURIComponent(view.q)}`));
    } catch {
      detail = null;
    }
    if (token !== showToken) return;
    if (detail) {
      S.detail = detail;
    } else {
      S.view = { type: "settings" };
      S.detail = null;
    }
    S.filter = "";
  }
  if (view.type === "recordings") loadRecordings();
  renderNav();
  renderView();
  $("#main").scrollTop = 0;
}

function searchResults(q, found) {
  return {
    id: `search:${q}`, name: `Search: ${q}`, search: true, tracks: found.tracks,
    count: found.tracks.length, total: found.total,
    missing: found.tracks.filter((t) => !t.available).length,
  };
}

function renderView() {
  const root = $("#view");
  if (!studio() && (S.view.type === "recordings" || (S.view.type === "settings" && S.view.tab === "log"))) {
    S.view = { type: "radio" };  // studio tools were turned off
    renderNav();
  }
  if ((S.view.type === "playlist" || S.view.type === "search") && S.detail) root.replaceChildren(playlistView(S.detail));
  else if (S.view.type === "home") root.replaceChildren(homeView());
  else if (S.view.type === "discover") root.replaceChildren(discoverView());
  else if (S.view.type === "radio") root.replaceChildren(radioView());
  else if (S.view.type === "recordings") root.replaceChildren(recordingsView());
  else if (S.view.type === "upnext") root.replaceChildren(upNextView());
  else if (S.view.type === "welcome") root.replaceChildren(welcomeView());
  else if (S.view.type === "clock") root.replaceChildren(clockView());
  else if (S.view.type === "help") root.replaceChildren(helpView());
  else root.replaceChildren(settingsView());
  for (const r of root.querySelectorAll('input[type="range"]')) fillRange(r);
}

