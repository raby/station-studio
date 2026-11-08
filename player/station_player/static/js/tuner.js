// ---------- the tuner ----------------------------------------------------------
// An old radio's dial across the top of the Radio page: your stations
// sit along it, and dragging the needle tunes between them, with a hiss
// of static on the way. Left and Right (or a click) tune too.
const TUNE = { stations: [], drag: false, noise: null, fit: null };
const TUNER_MARKS = [88, 90, 92, 94, 96, 98, 100, 102, 104, 106, 108];

function tuner(stations) {
  TUNE.stations = stations;
  const n = stations.length;
  // Placed through .style: the page's security policy blocks style="".
  const place = (node, left, maxWidth) => {
    node.style.left = `${left}%`;
    if (maxWidth) node.style.maxWidth = `${maxWidth}%`;
    return node;
  };
  const scale = el("div", { class: "tuner-scale" },
    ...TUNER_MARKS.map((mhz, i) => place(el("span", { class: "tuner-mark" }, String(mhz)), (i / (TUNER_MARKS.length - 1)) * 100)));
  // Names, each with its logo, take turns above and below the scale, so
  // neighbours don't touch, with a short line to their spot on the dial.
  // When names would still touch (syncTunerFit), only the logos show.
  const spot = (i) => ((i + 0.5) / n) * 100;
  const names = el("div", { class: "tuner-names" },
    ...stations.map((st, i) => place(el("span", { class: i % 2 ? "tuner-line low" : "tuner-line", "data-url": st.url }), spot(i))),
    ...stations.map((st, i) => place(el("button", {
      type: "button", class: i % 2 ? "tuner-name low" : "tuner-name", tabindex: -1, "data-url": st.url, title: st.name, "aria-label": st.name,
      onclick: () => tuneTo(i),
    }, stationArt(st, "glyph"), el("span", { class: "tuner-label" }, st.name)), spot(i), Math.min(48, 200 / n))));
  const dial = el("div", {
    class: "tuner-dial", id: "tuner", role: "slider", tabindex: 0, "aria-label": "Tuner",
    "aria-valuemin": 1, "aria-valuemax": n,
    onkeydown: (e) => {
      const i = tunedIndex();
      const to = { ArrowLeft: i - 1, ArrowDown: i - 1, ArrowRight: i + 1, ArrowUp: i + 1, Home: 0, End: n - 1 }[e.key];
      if (to === undefined) return;
      e.preventDefault();
      tuneTo(Math.max(0, Math.min(n - 1, i < 0 && to < 0 ? 0 : to)));
    },
    onpointerdown: (e) => {
      if (e.button !== 0 || e.target.closest(".tuner-name")) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      TUNE.drag = true;
      dial.classList.add("tuning");
      dragTo(e);
    },
    onpointermove: (e) => { if (TUNE.drag) dragTo(e); },
    onpointerup: (e) => endDrag(e, true),
    onpointercancel: (e) => endDrag(e, false),
  }, scale, names, el("div", { class: "tuner-needle", id: "tuner-needle" }));
  queueMicrotask(() => { syncTuner(); syncTunerFit(); });
  TUNE.fit?.disconnect();
  if (window.ResizeObserver) {
    TUNE.fit = new ResizeObserver(() => syncTunerFit());
    TUNE.fit.observe(dial);
  }
  return el("div", { class: "tuner" }, dial);
}

// Logos only, when neighbours' names on the same row would overlap.
function syncTunerFit() {
  const dial = $("#tuner");
  if (!dial) return;
  dial.classList.remove("logos-only");
  // Names at the ends are moved in, so none hangs off the dial.
  const box = dial.getBoundingClientRect();
  for (const b of dial.querySelectorAll(".tuner-name")) {
    b.style.marginLeft = "0px";
    const r = b.getBoundingClientRect();
    const shift = r.left < box.left + 4 ? box.left + 4 - r.left : r.right > box.right - 4 ? box.right - 4 - r.right : 0;
    b.style.marginLeft = `${shift}px`;
  }
  const rows = [[], []];
  dial.querySelectorAll(".tuner-name").forEach((b, i) => rows[i % 2].push(b.getBoundingClientRect()));
  const crowded = rows.some((row) => row.some((r, i) => i && r.left < row[i - 1].right + 4))
    || [...dial.querySelectorAll(".tuner-label")].some((l) => l.scrollWidth > l.clientWidth + 1);
  dial.classList.toggle("logos-only", crowded);
}

const tunedIndex = () => TUNE.stations.findIndex((st) => S.mode === "radio" && S.station && st.url === S.station.url);

// Where on the dial (0 to 1) the pointer is, and the nearest station.
function dialSpot(e) {
  const box = $("#tuner").getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
  const n = TUNE.stations.length;
  const i = Math.max(0, Math.min(n - 1, Math.floor(x * n)));
  return { x, i, off: Math.abs(x * n - (i + 0.5)) * 2 };  // off: 0 on a station, 1 halfway to the next
}

function dragTo(e) {
  const { x, i, off } = dialSpot(e);
  $("#tuner-needle").style.left = `${x * 100}%`;
  for (const b of document.querySelectorAll("#tuner [data-url]")) b.classList.toggle("near", b.dataset.url === TUNE.stations[i].url);
  staticHiss(off);
}

function endDrag(e, tune) {
  if (!TUNE.drag) return;
  TUNE.drag = false;
  $("#tuner")?.classList.remove("tuning");
  staticHiss(0);
  if (tune) tuneTo(dialSpot(e).i);
  else syncTuner();
}

function tuneTo(i) {
  const st = TUNE.stations[i];
  if (!st) return;
  if (!(S.mode === "radio" && S.station && S.station.url === st.url && !audio.paused)) playStation(st);
  syncTuner();
}

function syncTuner() {
  const dial = $("#tuner");
  if (!dial || TUNE.drag) return;
  const i = tunedIndex();
  const n = TUNE.stations.length;
  const needle = $("#tuner-needle");
  needle.hidden = i < 0;
  if (i >= 0) needle.style.left = `${((i + 0.5) / n) * 100}%`;
  dial.setAttribute("aria-valuenow", String(Math.max(1, i + 1)));
  dial.setAttribute("aria-valuetext", i >= 0 ? TUNE.stations[i].name : "Not tuned");
  for (const b of document.querySelectorAll("#tuner [data-url]")) {
    b.classList.remove("near");
    b.classList.toggle("on", i >= 0 && b.dataset.url === TUNE.stations[i].url);
  }
}

// Static between stations: looped noise, band-limited like a radio's
// speaker, louder the further the needle is from a station, over the
// low hum of an old valve set (mains hum's harmonics, which small
// speakers can play) that's there all the way across.
function staticHiss(level) {
  if (level > 0 && !TUNE.noise) {
    waveListen();
    const ctx = WAVE.ctx;
    if (!ctx) return;
    const buf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.loop = true;
    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = 1800;
    band.Q.value = 0.7;
    const gain = ctx.createGain();
    gain.gain.value = 0;
    src.connect(band).connect(gain).connect(ctx.destination);
    src.start();
    const humGain = ctx.createGain();
    humGain.gain.value = 0;
    humGain.connect(ctx.destination);
    const hum = [[100, 0.6], [150, 0.3], [200, 0.15]].map(([hz, level]) => {
      const osc = ctx.createOscillator();
      osc.type = "triangle";
      osc.frequency.value = hz;
      const g = ctx.createGain();
      g.gain.value = level;
      osc.connect(g).connect(humGain);
      osc.start();
      return osc;
    });
    TUNE.noise = { src, gain, hum, humGain };
  }
  if (!TUNE.noise) return;
  const { src, gain, hum, humGain } = TUNE.noise;
  const now = src.context.currentTime, vol = playVolume(), off = Math.min(1, level);
  gain.gain.setTargetAtTime(off * 0.5 * vol, now, 0.03);
  humGain.gain.setTargetAtTime(level > 0 ? (0.12 + 0.18 * off) * vol : 0, now, 0.03);
  if (level <= 0) {
    for (const node of [src, ...hum]) node.stop(now + 0.2);
    TUNE.noise = null;
  }
}

// What's on stations with a feed from their Station Server (whatson.py):
// on now, next and just played, asked for every 30 seconds.
const WHATSON = {};

const whatText = (i) => (i.show ? i.show : [i.title, i.artist].filter(Boolean).join(" – "));

async function refreshWhatsOn() {
  if (document.hidden) return;
  const fed = (S.lib?.stations || []).filter((st) => st.feed);
  await Promise.all(fed.map(async (st) => {
    try { WHATSON[st.id] = await api(`/api/whatson?station=${st.id}`, undefined, { quiet: true }); } catch { delete WHATSON[st.id]; }
  }));
  for (const id of Object.keys(WHATSON)) if (!fed.some((st) => st.id === id)) delete WHATSON[id];
  syncRadioCards();
  if (S.mode === "radio") updateNow();
}
setInterval(refreshWhatsOn, 30_000);

function paintWhatsOn(box, live) {
  const w = WHATSON[box.dataset.station];
  const lines = [];
  if (w?.state === "live") lines.push(el("div", {}, el("b", {}, "Live now: "), w.now.live));
  else if (w?.state === "off") lines.push(el("div", { class: "off" }, "Off air"));
  else if (w?.state === "on") {
    if (!live) lines.push(el("div", {}, el("b", {}, "On now: "), whatText(w.now)));
    if (live) lines.push(el("div", { class: "off" }, "Coming up:"));
    for (const n of w.next.slice(0, live ? 3 : 1)) {
      lines.push(el("div", {}, el("b", {}, `${live ? "" : "Next "}${hhmm(new Date(n.at))} `), whatText(n)));
    }
    if (live && w.recent.length) lines.push(el("div", { class: "off" }, "Just played: ", w.recent.slice(0, 2).map(whatText).join(", ")));
  }
  box.replaceChildren(...lines);
  box.hidden = !lines.length;
}

// Reflect play state on the station cards without rebuilding the view,
// so text typed into "Add a station" survives play/pause/stall events.
function syncRadioCards() {
  syncTuner();
  if ($("#globe")) globeDraw();  // rings the station playing
  for (const card of document.querySelectorAll("#view .card[data-url]")) {
    const live = S.mode === "radio" && S.station && S.station.url === card.dataset.url;
    card.classList.toggle("live", Boolean(live));
    const listen = card.querySelector(".listen");
    const label = live && !audio.paused ? "Pause" : "Listen";
    if (listen.textContent !== label) listen.replaceChildren(...iconLabel(label === "Pause" ? "pause" : "play", label));
    const song = card.querySelector(".song");
    song.textContent = live && S.radioTitle ? `Now playing: ${S.radioTitle}` : "";
    song.hidden = !song.textContent;
    const whatson = card.querySelector(".whatson");
    if (whatson) paintWhatsOn(whatson, live);
  }
  for (const tile of document.querySelectorAll("#view .tile-live[data-url]")) {
    const live = Boolean(stationLive(tile.dataset.url));
    tile.classList.toggle("live", live);
    const badge = tile.querySelector(".tile-play");
    if (badge && badge.dataset.live !== String(live)) {
      badge.dataset.live = String(live);
      badge.replaceChildren(icon(live ? "pause" : "play"));
      tile.setAttribute("aria-label", `${live ? "Pause" : "Listen to"} ${tile.dataset.name}`);
    }
    const listen = tile.querySelector(".listen[data-label]");  // a pick's button
    if (listen) {
      const label = live ? "Pause" : listen.dataset.label;
      if (listen.textContent !== label) {
        listen.replaceChildren(...iconLabel(live ? "pause" : "play", label));
        listen.setAttribute("aria-label", `${label}: ${listen.dataset.title}`);
      }
    }
  }
  for (const card of document.querySelectorAll("#view .find-card[data-find]")) {
    const live = S.mode === "radio" && S.station && S.station.url === card.dataset.find;
    card.classList.toggle("live", Boolean(live));
    const listen = card.querySelector(".listen");
    const label = live && !audio.paused ? "Pause" : "Listen";
    if (listen.textContent !== label) listen.replaceChildren(...iconLabel(label === "Pause" ? "pause" : "play", label));
    const song = card.querySelector(".song");
    song.textContent = live && S.radioTitle ? `Now playing: ${S.radioTitle}` : "";
    song.hidden = !song.textContent;
  }
}

// ---------- finding stations ------------------------------------------------
// Searches Radio Browser (radio-browser.info) through the player server.

// `source`: "" for Radio Browser, "somafm" for SomaFM's own channel list.
// Discover opens on the globe; List stays for the rest of that visit.
const F = { view: "globe",
  q: "", draft: null, tag: "", country: homeCountry(), order: "votes", source: "", results: null, loading: false,
  error: "", token: 0, asked: false, next: null, more: false, typing: null };
const FIND_COUNTRIES = ["GB", "IE", "US", "CA", "AU", "NZ", "DE", "FR", "NL", "BE", "ES", "PT", "IT", "CH", "AT", "SE",
  "NO", "DK", "FI", "PL", "CZ", "GR", "TR", "BR", "MX", "AR", "JM", "NG", "ZA", "KE", "IN", "JP", "KR"];

// Genres to browse (the directory's most used tags are mostly one
// country's, so these are picked by hand). "a / b" finds either.
const FIND_GENRES = ["techno / trance", "house / garage", "chart / pop", "asian / bhangra", "latin", "afrobeats",
  "jazz", "soul", "funk", "reggae", "hip hop", "drum and bass", "ambient",
  "chillout", "electronic", "rock", "indie", "metal", "folk", "blues", "country", "classical", "oldies", "80s",
  "90s", "news", "talk", "sport"];

// The country this computer is set to (en-GB: GB), searched by default.
function homeCountry() {
  const tag = (navigator.language || "").split(/[@.]/)[0];  // "en-US@posix" on some Linux setups
  const region = (tag.split(/[-_]/)[1] || "").toUpperCase();
  return /^[A-Z]{2}$/.test(region) ? region : "";
}

function countryName(code) {
  try { return new Intl.DisplayNames([], { type: "region" }).of(code) || code; } catch { return code; }
}

async function findStations(more = {}) {
  Object.assign(F, more);
  clearTimeout(F.typing);
  const token = ++F.token;
  F.loading = true;
  F.error = "";
  F.asked = true;
  F.next = null;
  F.more = false;  // a "Show more" for the last search no longer counts
  renderFinderResults();
  const params = new URLSearchParams({ q: F.q, tag: F.tag, country: F.country, order: F.order });
  try {
    const res = await api(F.source === "somafm" ? "/api/directory/somafm" : `/api/directory/search?${params}`,
      undefined, { quiet: true });
    if (token !== F.token) return;
    F.results = res.stations || [];
    F.next = res.next ?? null;
  } catch (err) {
    if (token !== F.token) return;
    F.results = null;
    F.error = err.message;
  }
  F.loading = false;
  renderFinderResults();
  const n = F.results ? F.results.length : 0;
  announce(F.error ? F.error : n ? `${plural(n, "station")} found` : "No stations found");
}

// The next page of the same search, added below what's shown.
async function moreStations() {
  if (F.next == null || F.more) return;
  const token = F.token;
  F.more = true;
  renderFinderResults();
  const params = new URLSearchParams({ q: F.q, tag: F.tag, country: F.country, order: F.order, offset: F.next });
  let added = 0;
  try {
    const res = await api(`/api/directory/search?${params}`, undefined, { quiet: true });
    if (token !== F.token) return;
    const have = new Set(F.results.map((x) => x.url));
    const fresh = (res.stations || []).filter((x) => !have.has(x.url));
    added = fresh.length;
    F.results = F.results.concat(fresh);
    F.next = res.next ?? null;
  } catch (err) {
    if (token !== F.token) return;
    toast(err.message, { error: true });
  } finally {
    if (token === F.token) F.more = false;
  }
  const first = F.results.length - added;
  renderFinderResults();
  announce(added ? `${plural(added, "more station")} shown` : "No more stations");
  // Keyboard users carry on from the first new result.
  if (added) document.querySelectorAll("#finder-results .find-card .listen")[first]?.focus();
}

function finderTagChips() {
  const chip = (label, pressed, onclick, cls = "chip") => el("button", { type: "button", class: cls, "aria-pressed": String(pressed), onclick }, label);
  return [
    chip("SomaFM", F.source === "somafm", () => {
      findStations({ source: F.source === "somafm" ? "" : "somafm", tag: "" });
      syncFinderChips();
    }, "chip somafm"),
    ...FIND_GENRES.map((g) => chip(g, F.source === "" && F.tag === g, () => {
      findStations({ source: "", tag: F.tag === g && F.source === "" ? "" : g });
      syncFinderChips();
    })),
  ];
}

function syncFinderChips() {
  for (const c of document.querySelectorAll("#finder-tags .chip")) {
    const on = c.classList.contains("somafm") ? F.source === "somafm" : F.source === "" && c.textContent === F.tag;
    c.setAttribute("aria-pressed", String(on));
  }
}

// Below these a stream tends to sound thin. AAC (and Opus) hold up at
// lower rates than MP3: BBC World Service's 48k AAC+ sounds fine.
function lowQuality(st) {
  const efficient = /aac|opus|ogg/i.test(st.codec || "");
  return st.bitrate > 0 && st.bitrate < (efficient ? 48 : 64);
}

function stationFacts(st) {
  // "United Kingdom", not the directory's "The United Kingdom Of Great
  // Britain And Northern Ireland"; left out when searching that country.
  const country = st.countrycode ? (st.countrycode === F.country ? "" : countryName(st.countrycode)) : st.country;
  const codec = /^unknown$/i.test(st.codec || "") ? "" : st.codec;  // the directory couldn't tell
  const text = [country, codec && st.bitrate ? `${codec} ${st.bitrate} kbps` : codec || (st.bitrate ? `${st.bitrate} kbps` : "")]
    .filter(Boolean).join(" · ");
  const low = lowQuality(st);
  return low ? [text, el("span", { class: "low-q", title: `${st.bitrate} kbps: may sound thin or muffled` }, "Low quality")] : [text];
}

function finderCard(st) {
  const saved = (S.lib.stations || []).some((x) => x.url === st.url);
  return el("li", { class: "find-card", "data-find": st.url },
    stationArt({ logo_url: st.logo }),
    el("div", { class: "info" },
      el("div", { class: "name" }, st.name),
      el("div", { class: "facts" }, ...stationFacts(st)),
      st.tags.length ? el("div", { class: "tag-row" }, st.tags.slice(0, 4).map((t) => el("button", {
        type: "button", class: "tag", title: `Find more ${t} stations`,
        onclick: () => {
          findStations({ source: "", tag: t, q: "", draft: "" });
          const box = $("#finder-q");
          if (box) box.value = "";
          syncFinderChips();
        },
      }, t))) : null,
      el("div", { class: "song", hidden: true })),
    el("div", { class: "actions" },
      el("button", {
        type: "button", class: "btn small listen", "aria-label": `Listen to ${st.name}`,
        onclick: () => {
          const live = S.mode === "radio" && S.station && S.station.url === st.url;
          if (live && !audio.paused) { audio.pause(); return; }
          const mine = (S.lib.stations || []).find((x) => x.url === st.url);
          playStation(mine || { name: st.name, url: st.url, logo_url: st.logo, preview: true });
          if (st.uuid) api("/api/directory/clicked", { uuid: st.uuid }, { quiet: true }).catch(() => {});
        },
      }, ...iconLabel("play", "Listen")),
      el("button", {
        type: "button", class: saved ? "btn small added" : "btn small primary add", disabled: saved || null,
        "aria-label": saved ? `${st.name} is in your stations` : `Add ${st.name} to your stations`,
        onclick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          try {
            const tags = F.source === "" && F.tag ? [F.tag] : [];
            await loadState(await api("/api/stations", { name: st.name, url: st.url, logo_url: st.logo || "", tags }, { quiet: true }));
            renderView();
            toast(`Added ${st.name} to your stations${tags.length ? `, tagged ${tags[0]}` : ""}`);
            refocus(`#finder-results .find-card[data-find="${CSS.escape(st.url)}"] .listen`);
          } catch (err) {
            btn.disabled = false;
            toast(err.message, { error: true });
          }
        },
      }, ...(saved ? iconLabel("check", "Added") : iconLabel("plus", "Add")))));
}

function renderFinderResults() {
  const box = $("#finder-results");
  if (!box) return;
  box.setAttribute("aria-busy", String(F.loading));
  if (F.loading) { box.replaceChildren(el("p", { class: "help finding" }, "Searching…")); return; }
  if (F.error) { box.replaceChildren(el("p", { class: "errors" }, F.error)); return; }
  if (!F.results) { box.replaceChildren(); return; }
  if (!F.results.length) {
    box.replaceChildren(el("p", { class: "help" }, "No stations found. Try fewer words, or another genre or country."));
    return;
  }
  const what = [F.q && `“${F.q}”`, F.tag].filter(Boolean).join(", ");
  const where = F.country ? ` in ${countryName(F.country)}` : "";
  const more = F.source !== "somafm" && F.next != null;
  box.replaceChildren(
    el("p", { class: "help" }, F.source === "somafm"
      ? "SomaFM: listener-supported radio from San Francisco, with no adverts"
      : what ? `${plural(F.results.length, "station")} for ${what}${where}` : `The most popular stations${where || " right now"}`),
    el("ul", { class: "find-list" }, F.results.map(finderCard)),
    more ? el("div", { class: "more-row" }, el("button", {
      type: "button", class: "btn", id: "btn-more", disabled: F.more || null, onclick: moreStations,
    }, F.more ? "Loading…" : "Show more")) : null);
  syncRadioCards();
}

function finderSection() {
  const q = el("input", {
    type: "search", id: "finder-q", value: F.draft ?? F.q, placeholder: "Station name, e.g. jazz fm", "aria-label": "Station name",
    oninput: (e) => {
      F.draft = e.target.value;  // kept if the page redraws
      // Search as you type, once typing pauses (and not on one letter).
      clearTimeout(F.typing);
      const typed = e.target.value.trim();
      if (typed === F.q || typed.length === 1) return;
      F.typing = setTimeout(() => {
        if ($("#finder-q") !== q) return;  // the page moved on
        findStations({ source: "", q: typed, country: country.value, order: order.value });
        syncFinderChips();
      }, 450);
    },
  });
  const here = homeCountry();
  const codes = here && !FIND_COUNTRIES.includes(here) ? [here, ...FIND_COUNTRIES] : FIND_COUNTRIES;
  const country = el("select", { id: "finder-country", "aria-label": "Country" },
    el("option", { value: "" }, "Any country"),
    ...codes.map((c) => el("option", { value: c }, countryName(c)))
      .sort((a, b) => a.textContent.localeCompare(b.textContent)));
  country.value = F.country;
  const order = el("select", { id: "finder-order", "aria-label": "Sort by" },
    el("option", { value: "votes" }, "Best liked"), el("option", { value: "popular" }, "Most played"), el("option", { value: "name" }, "A to Z"));
  order.value = F.order;
  const search = (e) => {
    if (e) e.preventDefault();
    finderView("list");
    findStations({ source: "", q: q.value.trim(), country: country.value, order: order.value });
    syncFinderChips();
  };
  country.addEventListener("change", () => search());
  order.addEventListener("change", () => search());
  queueMicrotask(() => {
    renderFinderResults();
    if (!F.asked) findStations();  // start with the most popular stations
  });
  return el("section", { class: "finder" },
    el("h2", {}, "Find stations"),
    el("p", { class: "help" }, "Search thousands of stations from around the world in the free Radio Browser directory (radio-browser.info), or browse SomaFM's ad-free channels. Listen first, then Add the ones you like."),
    el("form", { class: "form-row finder-form", role: "search", onsubmit: search },
      q, country, order, el("button", { class: "btn primary", id: "btn-find" }, ...iconLabel("search", "Search"))),
    el("div", { class: "finder-views", role: "group", "aria-label": "Show stations as" },
      ...[["list", "list", "List"], ["globe", "globe", "Globe"]].map(([view, ic, label]) => el("button", {
        type: "button", class: "btn small", "data-view": view, "aria-pressed": String(F.view === view),
        onclick: () => finderView(view, true),
      }, ...iconLabel(ic, label)))),
    el("div", { class: "finder-list", hidden: F.view !== "list" },
      el("div", { class: "tag-filter", id: "finder-tags", role: "group", "aria-label": "Genres" }, ...finderTagChips()),
      el("div", { id: "finder-results", "aria-live": "polite" })),
    F.view === "globe" ? globeSection() : null);
}

// The list of search results, or the globe.
function finderView(view, focus = false) {
  if (F.view === view) return;
  F.view = view;
  const finder = $(".finder");
  if (!finder) return;
  for (const b of finder.querySelectorAll(".finder-views [data-view]")) b.setAttribute("aria-pressed", String(b.dataset.view === view));
  finder.querySelector(".finder-list").hidden = view !== "list";
  finder.querySelector(".globe-box")?.remove();
  if (view === "globe") {
    finder.append(globeSection());
    if (focus) $("#globe").focus();
  }
}

