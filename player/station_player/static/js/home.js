// ---------- Home and Discover ----------------------------------------------
// Home is where a new window opens: what's on now from Rebirth (picks.py),
// stations played lately, your stations, the globe and your music.

const HOME = { picks: null, fetched: 0, timer: null };
const PICK_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// Whether a pick shows now, by this computer's clock.
function pickNow(p, now = new Date()) {
  const date = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
  if (p.starts && date < p.starts) return false;
  if (p.ends && date > p.ends) return false;
  if (p.days && !p.days.includes(PICK_DAYS[(now.getDay() + 6) % 7])) return false;
  if (!p.from) return true;
  const t = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
  return p.from <= p.to ? t >= p.from && t < p.to : t >= p.from || t < p.to;  // overnight
}

async function loadPicks() {
  HOME.fetched = Date.now();
  try { HOME.picks = await api("/api/picks"); } catch { HOME.picks = HOME.picks || { title: "", picks: [] }; }
  syncHome();
}

function syncHome() {
  const old = $("#home-picks");
  if (!old) return;
  old.replaceWith(picksSection());
  // The new buttons say Play; the one whose station is playing says Pause.
  syncRadioCards();
}

function greeting(now = new Date()) {
  const h = now.getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

const savedStation = (url) => (S.lib.stations || []).find((x) => x.url === url);

function picksSection() {
  const shown = (HOME.picks ? HOME.picks.picks : []).filter((p) => pickNow(p));
  if (!shown.length) return el("section", { id: "home-picks", hidden: true });
  return el("section", { id: "home-picks", class: "home-section" },
    el("h2", {}, HOME.picks.title || "Picks"),
    el("div", { class: "pick-row" }, shown.map((p) => {
      const mine = savedStation(p.station);
      const art = p.image
        ? el("img", { class: "pick-image", alt: "", src: `/api/picks/image?id=${encodeURIComponent(p.id)}` })
        : stationArt(mine || {}, "glyph big");
      if (p.image) art.addEventListener("error", () => art.replaceWith(stationArt(mine || {}, "glyph big")));
      return el("article", { class: "pick tile-live", "data-url": p.station },
        el("div", { class: "pick-art" }, art),
        el("div", { class: "pick-body" },
          el("h3", {}, p.title),
          p.text ? el("p", {}, p.text) : null,
          el("button", {
            type: "button", class: "btn primary listen", "aria-label": `${p.button}: ${p.title}`, "data-label": p.button, "data-title": p.title,
            onclick: () => {
              if (stationLive(p.station)) audio.pause();
              else playStation(mine || { name: p.name, url: p.station, preview: true });
            },
          }, ...iconLabel("play", p.button))));
    })));
}

// Whether the station at `url` is the one playing now (not paused).
const stationLive = (url) => S.mode === "radio" && S.station && S.station.url === url && !audio.paused;

// A square button with a station's logo that plays it, or pauses it
// while it's playing.
function stationTile(st) {
  return el("button", {
    type: "button", class: "tile tile-live", "data-url": st.url, "data-name": st.name, "aria-label": `Listen to ${st.name}`,
    onclick: () => { if (stationLive(st.url)) audio.pause(); else playStation(st); },
  }, el("div", { class: "tile-art" }, stationArt(st, "glyph big"), el("span", { class: "tile-play", "aria-hidden": "true" }, icon("play"))),
  el("span", { class: "tile-name" }, st.name));
}

function homeView() {
  if (!HOME.picks || Date.now() - HOME.fetched > 10 * 60 * 1000) queueMicrotask(loadPicks);
  // Picks change with the time of day: look again every minute.
  clearInterval(HOME.timer);
  HOME.timer = setInterval(() => { if (S.view.type === "home") syncHome(); else clearInterval(HOME.timer); }, 60 * 1000);
  queueMicrotask(syncRadioCards);
  const recent = (S.lib.recent_stations || []).map((r) => savedStation(r.url) || { name: r.name, url: r.url, logo: r.logo, preview: true });
  const stations = S.lib.stations || [];
  const music = [...myPlaylists(), ...S.lib.playlists.filter((p) => p.kind !== "mine")].slice(0, 8);
  const section = (title, items, more) => items.length ? el("section", { class: "home-section" },
    el("div", { class: "section-head" }, el("h2", {}, title), el("span", { class: "spacer" }), more || null),
    el("div", { class: "tile-row" }, items)) : null;
  const seeAll = (label, view) => el("button", { type: "button", class: "btn small ghost", onclick: () => show(view) }, label);
  return el("div", { class: "home" },
    el("div", { class: "view-head" }, el("h1", {}, greeting())),
    picksSection(),
    section("Recently played", recent.slice(0, 8).map(stationTile)),
    section("Your stations", stations.map(stationTile), seeAll("All stations", { type: "radio" })),
    el("section", { class: "home-section" },
      el("div", { class: "section-head" }, el("h2", {}, "Discover"), el("span", { class: "spacer" }),
        seeAll("Open Discover", { type: "discover" })),
      discoverTiles()),
    music.length
      ? section("Your music", music.map((p) => el("button", {
        type: "button", class: "tile music-tile", title: p.name, onclick: () => show({ type: "playlist", id: p.id }),
      }, el("div", { class: "tile-art" }, el("div", { class: "glyph big" }, icon(p.kind === "folder" ? "folder" : "music"))),
        el("span", { class: "tile-name" }, p.name), el("span", { class: "tile-sub" }, `${p.count} tracks`))))
      : el("section", { class: "home-section" },
        el("h2", {}, "Your music"),
        el("p", { class: "help" }, "Play the music on this computer too: add your music folder, or playlists from station-studio or iTunes."),
        el("button", { type: "button", class: "btn", onclick: () => { show({ type: "welcome" }); focusHeading(); } },
          ...iconLabel("plus", "Add music"))));
}

// The ways into Discover: the globe with news and talk, music or sport, or a search.
function discoverTiles() {
  const tile = (glyph, title, text, go) => el("button", { type: "button", class: "discover-tile", onclick: go },
    el("div", { class: "glyph big" }, icon(glyph)),
    el("span", { class: "discover-title" }, title),
    el("span", { class: "discover-text" }, text));
  return el("div", { class: "discover-tiles" },
    tile("mic", "News and talk around the world", "Spin the globe to hear the news where it happens, in every language.",
      () => openDiscover("news")),
    tile("music", "Music around the world", "Turn the globe to a country and hear its music, or pick a genre.",
      () => openDiscover("music")),
    tile("sport", "Sport around the world", "Live commentary and sports talk: football, cricket, racing and more.",
      () => openDiscover("sport")),
    tile("search", "Search for a station", "By name, genre or country, from thousands of stations.",
      () => openDiscover(null)));
}

async function openDiscover(kind) {
  if (kind) {
    G.kind = kind;
    store("globe-kind", kind);
    F.view = "globe";
  } else {
    F.view = "list";
  }
  if (S.view.type === "discover") {
    const finder = $(".finder");
    if (finder) finder.replaceWith(finderSection());
  } else {
    await show({ type: "discover", list: !kind });
  }
  if (kind) $("#globe")?.focus();
  else $("#finder-q")?.focus();
}

function discoverView() {
  return el("div", { class: "discover" },
    el("div", { class: "view-head" }, el("h1", {}, "Discover")),
    el("p", { class: "help lead" },
      "Radio from all over the world. Turn the globe to hear the news, talk, music or sport where it's made, or search by name. ",
      "Listen first, then Add the ones you like to your stations."),
    discoverTiles(),
    el("div", { class: "settings" }, finderSection()));
}

// On the Radio page: the finder lives on Discover now.
function discoverLink() {
  return el("section", { class: "discover-link" },
    el("h2", {}, "Find more stations"),
    el("p", { class: "help" }, "News, talk and music from around the world on the globe, or search thousands of stations by name."),
    el("button", { type: "button", class: "btn", onclick: () => show({ type: "discover" }) }, ...iconLabel("globe", "Open Discover")));
}

// Switches at set times: the news at five, then back to the music.
function scheduleSection() {
  const switches = alarmsList().filter(isSwitch);
  const adding = C.editing === "new-switch";
  const playing = S.station ? S.station : null;
  const blank = { id: "", on: true, time: "17:00", label: "", days: [0, 1, 2, 3, 4], date: "", action: "switch",
    source: playing ? { kind: "station", id: playing.id, name: playing.name } : null };
  return el("section", { class: "alarms schedule" },
    el("div", { class: "section-head" },
      el("h2", {}, "Schedule"),
      el("span", { class: "spacer" }),
      el("button", {
        type: "button", class: "btn", id: "btn-add-switch", disabled: alarmsList().length >= MAX_ALARMS || adding || null,
        onclick: () => { C.editing = "new-switch"; renderView(); refocus("#alarm-time"); },
      }, ...iconLabel("plus", "Add a switch"))),
    el("p", { class: "help" },
      "Switch station at set times, once or every week: the news at five, then back to your station at ten past. ",
      "A switch only changes what's already playing; it doesn't start anything when Station Player is quiet."),
    adding ? el("div", { class: "alarm-card new" }, alarmEditor(blank, true)) : null,
    switches.length ? el("ul", { class: "alarm-list" }, [...switches].sort((x, y) => x.time.localeCompare(y.time)).map(alarmCard)) : null);
}

// Look up every saved station that has no logo yet, one at a time.
async function findAllLogos(e) {
  const btn = e.currentTarget;
  btn.disabled = true;
  const missing = (S.lib.stations || []).filter((x) => !x.logo);
  let found = 0;
  let state = null;
  for (const [i, st] of missing.entries()) {
    btn.replaceChildren(`Looking… ${i + 1} of ${missing.length}`);
    try {
      state = await api("/api/stations/logo", { url: st.url, find: true }, { quiet: true });
      found += 1;
    } catch { /* none for this one (or the directory is unreachable) */ }
  }
  if (state) await loadState(state);
  renderView();
  refocus("#btn-find-logos");
  toast(found ? `Found ${plural(found, "logo")}${found < missing.length ? `; none found for ${missing.length - found} (Edit a station to choose a picture)` : ""}`
    : "No logos found for these stations. Edit a station to choose a picture.");
}

// Stations listened to lately (saved or tried from the finder), newest first.
function recentSection() {
  const recent = S.lib.recent_stations || [];
  return el("section", { class: "recent", id: "recent", hidden: !recent.length || null },
    el("div", { class: "section-head" },
      el("h2", {}, "Recently played"),
      el("span", { class: "spacer" }),
      el("button", {
        type: "button", class: "btn small ghost", "aria-label": "Clear Recently played",
        onclick: () => act("/api/stations/recent/clear", {}, "Cleared Recently played"),
      }, "Clear")),
    el("ul", { class: "recent-list" }, recent.map((r) => {
      const mine = (S.lib.stations || []).find((x) => x.url === r.url);
      const st = mine || { name: r.name, url: r.url, logo: r.logo, preview: true };
      return el("li", { class: "recent-card find-card", "data-find": r.url },
        stationArt(st),
        el("div", { class: "info" },
          el("div", { class: "name" }, st.name),
          el("div", { class: "facts" }, mine ? "In your stations" : "Not saved"),
          el("div", { class: "song", hidden: true })),
        el("div", { class: "actions" },
          el("button", {
            type: "button", class: "btn small listen", "aria-label": `Listen to ${st.name}`,
            onclick: () => {
              const live = S.mode === "radio" && S.station && S.station.url === r.url;
              if (live && !audio.paused) audio.pause(); else playStation(st);
            },
          }, ...iconLabel("play", "Listen")),
          // Here too, so a station just tried from Find stations can be recorded straight away.
          el("button", {
            type: "button", class: "btn small rec-btn studio-only", "aria-label": `Record ${st.name}`,
            onclick: () => toggleRecording(mine || { id: r.id, url: r.url, name: r.name }),
          }, ...iconLabel("record", "Record")),
          mine ? null : el("button", {
            type: "button", class: "btn small primary add", "aria-label": `Add ${st.name} to your stations`,
            onclick: async (e) => {
              const btn = e.currentTarget;
              btn.disabled = true;
              try {
                await loadState(await api("/api/stations", { name: r.name, url: r.url, logo: r.logo || "" }, { quiet: true }));
                renderView();
                toast(`Added ${r.name} to your stations`);
                refocus(`#view .recent-card[data-find="${CSS.escape(r.url)}"] .listen`);
              } catch (err) { btn.disabled = false; toast(err.message, { error: true }); }
            },
          }, ...iconLabel("plus", "Add"))));
    })));
}

// Redraw Recently played in place (the rest of the page keeps what's typed).
function syncRecent() {
  const old = document.querySelector("#view #recent");
  if (!old) return;
  const focused = old.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
  const fresh = recentSection();
  old.replaceWith(fresh);
  syncRecordings();  // names the Record buttons for what's running
  if (focused) fresh.querySelector(`[aria-label="${CSS.escape(focused)}"]`)?.focus();
  syncRadioCards();
}

async function notePlayed(st) {
  S.notedUrl = st.url;
  try {
    const res = await api("/api/stations/played", { name: st.name, url: st.url, logo_url: st.logo_url || "" }, { quiet: true });
    // Only the list: the rest may be older than a change made meanwhile.
    S.lib.recent_stations = res.recent_stations;
    syncRecent();
  } catch { /* not one the player knows: nothing to remember */ }
}

// Reflect play state on the station cards without rebuilding the view,
// so text typed into "Add a station" survives play/pause/stall events.
