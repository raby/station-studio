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
