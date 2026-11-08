// ---------- world globe ----------------------------------------------------
// Find stations on a globe. The directory knows where many stations are;
// the countries are Natural Earth's small-scale map (public domain),
// bundled as world.json and drawn here, with no map service.

const G = { world: null, loading: null, stations: new Map(), countries: new Set(),
  kind: ["music", "news", "sport"].includes(store("globe-kind")) ? store("globe-kind") : "all", genre: store("globe-genre") || "",
  lon: 0, lat: 20, zoom: 1, spin: !matchMedia("(prefers-reduced-motion: reduce)").matches, picked: null, raf: 0, last: 0, pointers: new Map(), moved: 0,
  pinch: 0, settle: 0, error: "", busy: 0, token: 0, hover: null };
const GLOBE_MAX_ZOOM = 24;
const GLOBE_NEAR = 2.5;  // zoomed in this far, it fetches the country in the middle too
const RAD = Math.PI / 180;

async function loadWorld() {
  if (G.world) return G.world;
  G.loading = G.loading || fetch("/world.json").then((r) => {
    if (!r.ok) throw new Error("The map didn't load");
    return r.json();
  }).then((data) => {
    G.world = data.countries.map((c) => {
      // Each outline in degrees, with its box (for finding the country
      // under the middle) and a spot for its name.
      const rings = c.r.map((flat) => {
        const pts = new Float32Array(flat.length);
        let x0 = 180, x1 = -180, y0 = 90, y1 = -90;
        for (let i = 0; i < flat.length; i += 2) {
          const x = (pts[i] = flat[i] / 10), y = (pts[i + 1] = flat[i + 1] / 10);
          x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
        }
        return { pts, box: [x0, y0, x1, y1] };
      });
      const big = rings.reduce((a, b) => ((b.box[2] - b.box[0]) * (b.box[3] - b.box[1]) > (a.box[2] - a.box[0]) * (a.box[3] - a.box[1]) ? b : a));
      return { cc: c.cc, rings, label: [(big.box[0] + big.box[2]) / 2, (big.box[1] + big.box[3]) / 2],
        size: Math.max(big.box[2] - big.box[0], big.box[3] - big.box[1]) };
    });
    return G.world;
  });
  try {
    return await G.loading;
  } catch (err) {
    G.loading = null;
    throw err;
  }
}

function inRing(pts, x, y) {
  let inside = false;
  for (let i = 0, j = pts.length - 2; i < pts.length; j = i, i += 2) {
    const xi = pts[i], yi = pts[i + 1], xj = pts[j], yj = pts[j + 1];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// The country at a place: from the map, or (for ones too small to be
// on it, like Malta) the nearest station's.
function countryAt(lon, lat) {
  for (const c of G.world || []) {
    if (c.cc && c.rings.some((r) => lon >= r.box[0] && lon <= r.box[2] && lat >= r.box[1] && lat <= r.box[3] && inRing(r.pts, lon, lat))) return c.cc;
  }
  let best = null, dist = 3;
  for (const st of G.stations.values()) {
    const d = Math.hypot(st.lon - lon, st.lat - lat);
    if (d < dist && st.countrycode) { best = st.countrycode; dist = d; }
  }
  return best;
}

async function globeStations(country = "") {
  const token = G.token;
  G.busy++;
  globeStatus();
  try {
    const params = new URLSearchParams({ country, kind: G.kind, genre: G.kind === "music" ? G.genre : "" });
    const res = await api(`/api/directory/map?${params}`, undefined, { quiet: true });
    if (token !== G.token) return;
    for (const st of res.stations || []) if (!G.stations.has(st.url)) G.stations.set(st.url, st);
    if (country) G.countries.add(country);
    G.error = "";
  } catch (err) {
    if (token === G.token) G.error = err.message;
  } finally {
    if (token === G.token) G.busy--;  // a switch to Music or News started counting again
  }
  if (token !== G.token) return;
  globeDraw();
  globeList();
}

// All stations, music only (any, or one genre), or news and talk.
function globeShow(kind, genre = G.genre) {
  G.kind = kind;
  G.genre = genre;
  store("globe-kind", kind);
  store("globe-genre", genre);
  G.token++;
  G.stations.clear();
  G.countries.clear();
  G.picked = null;
  G.hover = null;
  G.busy = 0;
  for (const b of document.querySelectorAll(".globe-kinds [data-kind]")) b.setAttribute("aria-pressed", String(b.dataset.kind === kind));
  const pick = $("#globe-genre");
  if (pick) pick.hidden = kind !== "music";
  globeStations();
  globeSettled();
}

// Orthographic: the globe as seen from far away, centred on G.lon, G.lat.
function globeView(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.width, h = cv.height;
  const r = (Math.min(w, h) / 2 - 6 * dpr) * G.zoom;
  const l0 = G.lon * RAD, p0 = G.lat * RAD;
  const sp0 = Math.sin(p0), cp0 = Math.cos(p0);
  return {
    dpr, w, h, r, cx: w / 2, cy: h / 2,
    // [x, y, facing]: facing < 0 is round the back.
    at(lon, lat) {
      const l = lon * RAD - l0, p = lat * RAD;
      const cp = Math.cos(p), sp = Math.sin(p), cl = Math.cos(l);
      return [w / 2 + r * cp * Math.sin(l), h / 2 - r * (cp0 * sp - sp0 * cp * cl), sp0 * sp + cp0 * cp * cl];
    },
  };
}

function globeColours() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return { sea: v("--panel-2"), land: v("--panel-3"), edge: v("--border-strong"), dot: v("--accent"), live: v("--ok"),
    text: v("--muted"), ring: v("--text"), label: v("--panel") };
}

function globeDraw() {
  const cv = $("#globe");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cv.clientWidth * dpr), h = Math.round(cv.clientHeight * dpr);
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const v = globeView(cv);
  const c = globeColours();
  const g = cv.getContext("2d");
  g.clearRect(0, 0, w, h);
  g.save();
  g.beginPath();
  g.arc(v.cx, v.cy, v.r, 0, Math.PI * 2);
  g.fillStyle = c.sea;
  g.fill();
  g.clip();
  // Countries: points round the back are pulled onto the rim, so a
  // country going over the edge keeps its shape this side.
  g.fillStyle = c.land;
  g.strokeStyle = c.edge;
  g.lineWidth = dpr;
  g.lineJoin = "round";
  for (const country of G.world || []) {
    for (const ring of country.rings) {
      let seen = false;
      g.beginPath();
      for (let i = 0; i < ring.pts.length; i += 2) {
        let [x, y, f] = v.at(ring.pts[i], ring.pts[i + 1]);
        if (f < 0) {
          const dx = x - v.cx, dy = y - v.cy, d = Math.hypot(dx, dy) || 1;
          x = v.cx + (dx / d) * v.r;
          y = v.cy + (dy / d) * v.r;
        } else seen = true;
        i ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      if (!seen) continue;
      g.closePath();
      g.fill();
      g.stroke();
    }
  }
  // Names, once it's close enough to have room for them.
  if (G.zoom >= GLOBE_NEAR) {
    g.fillStyle = c.text;
    g.font = `${11 * dpr}px system-ui, sans-serif`;
    g.textAlign = "center";
    for (const country of G.world || []) {
      if (!country.cc || country.size * RAD * v.r < 60 * dpr) continue;
      const [x, y, f] = v.at(country.label[0], country.label[1]);
      if (f > 0.2 && x > 0 && x < w && y > 0 && y < h) g.fillText(countryName(country.cc), x, y);
    }
  }
  // Stations.
  const live = S.mode === "radio" && S.station ? S.station.url : "";
  const size = (2.2 + Math.min(G.zoom, 8) * 0.3) * dpr;
  g.fillStyle = c.dot;
  g.globalAlpha = 0.85;
  for (const st of G.stations.values()) {
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f < 0 || x < -9 || y < -9 || x > w + 9 || y > h + 9) continue;
    g.beginPath();
    g.arc(x, y, size, 0, Math.PI * 2);
    g.fill();
  }
  g.globalAlpha = 1;
  for (const [url, colour] of [[G.picked?.url, c.ring], [live, c.live]]) {
    const st = url && G.stations.get(url);
    if (!st) continue;
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f < 0) continue;
    g.strokeStyle = colour;
    g.lineWidth = 2 * dpr;
    g.beginPath();
    g.arc(x, y, size + 4 * dpr, 0, Math.PI * 2);
    g.stroke();
  }
  // What a dot is, before it plays: the one picked, and the one the mouse is over.
  const named = new Set();
  for (const st of [G.picked, G.hover]) {
    if (!st || named.has(st.url) || !G.stations.has(st.url)) continue;
    named.add(st.url);
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f < 0) continue;
    globeLabel(g, v, c, x, y - size - 8 * dpr,
      st.name, st.countrycode ? countryName(st.countrycode) : "");
  }
  g.restore();
  g.strokeStyle = c.edge;
  g.lineWidth = dpr;
  g.beginPath();
  g.arc(v.cx, v.cy, v.r, 0, Math.PI * 2);
  g.stroke();
}

// A station's name (and country) in a little box above its dot, kept on the canvas.
function globeLabel(g, v, c, x, y, name, sub) {
  const dpr = v.dpr, pad = 6 * dpr, line = 15 * dpr;
  const title = name.length > 40 ? `${name.slice(0, 39)}…` : name;
  g.font = `600 ${13 * dpr}px system-ui, sans-serif`;
  const tw = g.measureText(title).width;
  g.font = `${11 * dpr}px system-ui, sans-serif`;
  const sw = sub ? g.measureText(sub).width : 0;
  const bw = Math.max(tw, sw) + pad * 2, bh = line * (sub ? 2 : 1) + pad;
  const bx = Math.max(2, Math.min(v.w - bw - 2, x - bw / 2));
  const by = Math.max(2, y - bh);
  g.fillStyle = c.label;
  g.strokeStyle = c.edge;
  g.lineWidth = dpr;
  g.beginPath();
  if (g.roundRect) g.roundRect(bx, by, bw, bh, 6 * dpr); else g.rect(bx, by, bw, bh);
  g.fill();
  g.stroke();
  g.textAlign = "left";
  g.textBaseline = "top";
  g.fillStyle = c.ring;
  g.font = `600 ${13 * dpr}px system-ui, sans-serif`;
  g.fillText(title, bx + pad, by + pad / 2 + 1 * dpr);
  if (sub) {
    g.fillStyle = c.text;
    g.font = `${11 * dpr}px system-ui, sans-serif`;
    g.fillText(sub, bx + pad, by + pad / 2 + line + 1 * dpr);
  }
  g.textBaseline = "alphabetic";
}

// Turning by itself until it's touched; drawn again only when it moves.
function globeTick(now) {
  G.raf = 0;
  const cv = $("#globe");
  if (!cv) return;
  if (G.spin && !calm.matches) {
    G.lon += Math.min(100, now - (G.last || now)) * 0.004;  // 4° a second
    G.last = now;
    G.raf = requestAnimationFrame(globeTick);
  }
  globeDraw();
}

function globeWake() {
  if (!G.raf) G.raf = requestAnimationFrame(globeTick);
}

window.addEventListener("resize", () => { if ($("#globe")) globeWake(); });

function globeStop() {
  G.spin = false;
}

function globeMove(dLon, dLat) {
  G.lon = ((G.lon + dLon + 540) % 360) - 180;
  G.lat = Math.max(-80, Math.min(80, G.lat + dLat));
  globeWake();
  globeSettled();
}

function globeZoom(by) {
  G.zoom = Math.max(1, Math.min(GLOBE_MAX_ZOOM, G.zoom * by));
  globeWake();
  globeSettled();
}

// Once it stops moving: list what's in view, and fetch the country in the
// middle if it's zoomed in.
function globeSettled() {
  clearTimeout(G.settle);
  G.settle = setTimeout(() => {
    globeList();
    if (G.zoom < GLOBE_NEAR || !G.world) return;
    const cc = countryAt(G.lon, G.lat);
    if (cc && !G.countries.has(cc)) {
      G.countries.add(cc);  // asked once, even if it fails
      globeStations(cc);
    }
  }, 300);
}

// Stations in view, nearest the middle first.
function globeInView(limit) {
  const cv = $("#globe");
  if (!cv || !cv.width) return [];
  const v = globeView(cv);
  const out = [];
  for (const st of G.stations.values()) {
    const [x, y, f] = v.at(st.lon, st.lat);
    if (f >= 0 && x >= 0 && y >= 0 && x <= v.w && y <= v.h) out.push([Math.hypot(x - v.cx, y - v.cy), st]);
  }
  return out.sort((a, b) => a[0] - b[0]).slice(0, limit).map((x) => x[1]);
}

function globeStatus() {
  const line = $("#globe-status");
  if (!line) return;
  const n = G.stations.size;
  const what = G.kind === "news" ? "news and talk station" : G.kind === "sport" ? "sport station"
    : G.kind === "music" ? `${G.genre || "music"} station` : "station";
  line.textContent = G.error || (G.busy && !n ? "Finding stations…"
    : `${plural(n, what)} on the globe${G.busy ? ", finding more…" : ""}`);
  line.classList.toggle("errors", Boolean(G.error));
}

function globeList() {
  globeStatus();
  const box = $("#globe-list");
  if (!box) return;
  const near = globeInView(8).filter((st) => st.url !== G.picked?.url);
  const shown = (G.picked ? [G.picked, ...near] : near).slice(0, 8);
  const keep = document.activeElement && box.contains(document.activeElement)
    ? document.activeElement.closest(".find-card")?.dataset.find : null;
  box.replaceChildren(...shown.map(finderCard));
  if (G.picked) box.firstChild?.classList.add("picked");
  if (keep) box.querySelector(`.find-card[data-find="${CSS.escape(keep)}"] .listen`)?.focus();
  syncRadioCards();
}

// Tapping a dot shows what it is, at the top of the list below; tapping
// it again (or its Listen) plays it.
function globePick(st) {
  const again = G.picked?.url === st.url;
  G.picked = st;
  const where = st.countrycode ? `, ${countryName(st.countrycode)}` : "";
  if (!again) {
    announce(`${st.name}${where}. Choose it again, or Listen, to play it.`);
    globeDraw();
    globeList();
    return;
  }
  if (!stationLive(st.url)) {
    const mine = (S.lib.stations || []).find((x) => x.url === st.url);
    playStation(mine || { name: st.name, url: st.url, logo_url: st.logo, preview: true });
    if (st.uuid) api("/api/directory/clicked", { uuid: st.uuid }, { quiet: true }).catch(() => {});
  }
  announce(`Playing ${st.name}${where}`);
  globeDraw();
  globeList();
}

function globeHit(e) {
  const cv = e.currentTarget;
  const box = cv.getBoundingClientRect();
  const v = globeView(cv);
  const px = (e.clientX - box.left) * v.dpr, py = (e.clientY - box.top) * v.dpr;
  let best = null, dist = 16 * v.dpr;
  for (const st of G.stations.values()) {
    const [x, y, f] = v.at(st.lon, st.lat);
    const d = Math.hypot(x - px, y - py);
    if (f >= 0 && d < dist) { best = st; dist = d; }
  }
  return best;
}

function globeSection() {
  const cv = el("canvas", {
    id: "globe", class: "globe", tabindex: 0, role: "application", "aria-roledescription": "globe",
    "aria-label": "World globe of stations. Drag to turn it, scroll or pinch to zoom, tap a dot to see what it is, and tap it again to listen. With the keyboard: arrow keys turn it, plus and minus zoom, Enter picks the station in the middle, and Enter again plays it.",
    onpointerdown: (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      cv.setPointerCapture(e.pointerId);
      G.pointers.set(e.pointerId, [e.clientX, e.clientY]);
      G.moved = 0;
      G.pinch = 0;
      globeStop();
    },
    onpointermove: (e) => {
      const was = G.pointers.get(e.pointerId);
      if (!was) {
        const over = e.pointerType === "mouse" ? globeHit(e) : null;
        cv.classList.toggle("over-dot", Boolean(over));
        if (over?.url !== G.hover?.url) { G.hover = over; globeDraw(); }
        return;
      }
      G.pointers.set(e.pointerId, [e.clientX, e.clientY]);
      if (G.pointers.size === 2) {
        const [a, b] = [...G.pointers.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (G.pinch) globeZoom(d / G.pinch);
        G.pinch = d;
        G.moved = 99;
        return;
      }
      const dx = e.clientX - was[0], dy = e.clientY - was[1];
      G.moved += Math.abs(dx) + Math.abs(dy);
      const r = globeView(cv).r / (window.devicePixelRatio || 1);
      globeMove(-dx / r / RAD, dy / r / RAD);
    },
    onpointerup: (e) => {
      const tap = G.pointers.size === 1 && G.moved < 6;
      G.pointers.delete(e.pointerId);
      G.pinch = 0;
      if (!tap) return;
      const st = globeHit(e);
      if (st) globePick(st);
    },
    onpointercancel: (e) => { G.pointers.delete(e.pointerId); G.pinch = 0; },
    onpointerleave: () => { if (G.hover) { G.hover = null; globeDraw(); } },
    onkeydown: (e) => {
      const step = 12 / G.zoom;
      const turn = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
      if (turn) {
        e.preventDefault();
        globeStop();
        globeMove(...turn);
      } else if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        globeStop();
        globeZoom(1.5);
      } else if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        globeZoom(1 / 1.5);
      } else if (e.key === "Enter") {
        const st = globeInView(1)[0];
        if (st) { e.preventDefault(); globePick(st); }
      }
    },
  });
  // Not passive: scrolling over the globe zooms it, not the page.
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    globeStop();
    globeZoom(Math.exp(-Math.max(-100, Math.min(100, e.deltaY)) * (e.ctrlKey ? 0.01 : 0.002)));
  }, { passive: false });
  queueMicrotask(async () => {
    try {
      await loadWorld();
    } catch (err) {
      G.error = err.message;
    }
    if (!G.stations.size && !G.busy) {
      // Start over home.
      const home = (G.world || []).find((c) => c.cc === homeCountry());
      if (home) { G.lon = home.label[0]; G.lat = Math.max(-60, Math.min(60, home.label[1])); }
      globeStations();
    }
    globeWake();
    globeList();
  });
  return el("div", { class: "globe-box" },
    el("div", { class: "globe-tools" },
      el("div", { class: "globe-kinds", role: "group", "aria-label": "Show" },
        ...[["all", "All"], ["music", "Music"], ["news", "News"], ["sport", "Sport"]].map(([kind, label]) => el("button", {
          type: "button", class: "chip", "data-kind": kind, "aria-pressed": String(G.kind === kind),
          title: { all: "Every station", music: "Music stations, leaving out news, talk and sport",
            news: "Only news and talk stations", sport: "Only sport stations: football, cricket, racing and more" }[kind],
          onclick: () => { if (G.kind !== kind) globeShow(kind); },
        }, label))),
      el("select", {
        id: "globe-genre", "aria-label": "Music genre", hidden: G.kind !== "music",
        onchange: (e) => globeShow("music", e.target.value),
      }, el("option", { value: "" }, "Any music"),
      ...FIND_GENRES.filter((g) => !["news", "talk", "sport"].includes(g)).map((g) => el("option", { value: g, selected: g === G.genre }, g))),
      el("p", { class: "help", id: "globe-status", role: "status" }),
      el("button", { type: "button", class: "btn ghost icon-only", "aria-label": "Zoom out", onclick: () => { globeStop(); globeZoom(1 / 1.6); } }, icon("minus")),
      el("button", { type: "button", class: "btn ghost icon-only", "aria-label": "Zoom in", onclick: () => { globeStop(); globeZoom(1.6); } }, icon("plus"))),
    cv,
    el("ul", { class: "find-list", id: "globe-list", "aria-label": "Stations in view" }));
}

