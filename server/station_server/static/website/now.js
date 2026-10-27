// What's on: reads the feeds Station Server publishes (feed.py) and shows
// now playing, coming up and just played. A feed holds the next few hours
// with the time each song goes out, so what's on now comes from the clock.
"use strict";

const NowFeed = (() => {
  const params = new URLSearchParams(location.search);

  function el(tag, cls, ...kids) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    for (const k of kids.flat()) if (k != null) n.append(k);
    return n;
  }

  const time = (iso) =>
    new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  async function get(path) {
    const resp = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`${path}: ${resp.status}`);
    return resp.json();
  }

  // A feed is published at least hourly while on air: one much older means
  // Station Server's computer is off or offline, so its times can't be trusted.
  const STALE_MS = 90 * 60_000;

  // What a feed says at `now`: {state: on|live|off|unknown, now, next[], recent[]}.
  function view(feed, now = new Date()) {
    if (!feed || !feed.on_air) return { state: "off", now: null, next: [], recent: feed?.recent || [] };
    if (now - new Date(feed.updated) > STALE_MS) return { state: "unknown", now: null, next: [], recent: [] };
    if (feed.live) return { state: "live", now: { live: feed.live }, next: [], recent: feed.recent || [] };
    const items = (feed.items || []).map((i) => ({
      ...i, start: new Date(i.at), end: new Date(new Date(i.at).getTime() + i.seconds * 1000),
    }));
    const at = items.findIndex((i) => i.start <= now && now < i.end);
    const played = items.filter((i) => i.end <= now && !i.show).reverse()
      .map((i) => ({ at: i.at, title: i.title, artist: i.artist }));
    const recent = [...played, ...(feed.recent || [])];
    if (at < 0) return { state: "unknown", now: null, next: [], recent };
    return { state: "on", now: items[at], next: items.slice(at + 1), recent };
  }

  const what = (i) => (i.show ? el("span", "title", i.show) : [
    el("span", "title", i.title || "Unknown"), i.artist ? el("span", "artist", i.artist) : null,
  ]);

  // The station's logo, published beside its feed (a plain file name).
  function logo(feed) {
    const name = feed?.logo;
    if (!name || !/^[a-z0-9-]+-logo\.(png|jpg|gif|webp|ico)$/.test(name)) return null;
    const img = el("img", "logo");
    img.src = name;
    img.alt = "";
    return img;
  }

  function renderStation(feed, opts = {}) {
    const v = view(feed);
    const next = opts.next ?? 5;
    const recent = opts.recent ?? 5;
    const badge = { on: "On air", live: "Live", off: "Off air", unknown: "On air" }[v.state];
    const box = el("section", "station",
      el("header", "head", logo(feed), el("h2", null, feed?.station || ""), el("span", `badge ${v.state}`, badge)));
    if (v.state === "live") {
      box.append(el("div", "now", el("div", "label", "Live now"), el("div", "big", v.now.live.presenter)));
    } else if (v.state === "on") {
      const n = v.now;
      const done = Math.min(1, Math.max(0, (Date.now() - n.start.getTime()) / (n.seconds * 1000)));
      const bar = el("div", "bar"); bar.style.width = `${(done * 100).toFixed(1)}%`;
      box.append(el("div", "now", el("div", "label", n.show ? "Show" : "Now playing"),
        el("div", "big", what(n)), el("div", "progress", bar)));
    } else if (v.state === "off") {
      box.append(el("div", "now", el("div", "label", "Off air")));
    }
    if (next && v.next.length) {
      box.append(el("h3", null, "Coming up"), el("ol", "list",
        v.next.slice(0, next).map((i) => el("li", i.show ? "show" : null, el("time", null, time(i.at)), el("span", "what", what(i))))));
    }
    if (recent && v.recent.length) {
      box.append(el("h3", null, "Just played"), el("ol", "list",
        v.recent.slice(0, recent).map((i) => el("li", null, el("time", null, time(i.at)), el("span", "what", what(i))))));
    }
    return box;
  }

  // Keeps `into` showing the stations, fetching the feeds every minute and
  // moving on with the clock in between.
  function show(into, slugs, opts) {
    const feeds = {};
    const draw = () => into.replaceChildren(...slugs.map((s) => renderStation(feeds[s], opts)));
    const fetchAll = async () => {
      await Promise.all(slugs.map(async (s) => { try { feeds[s] = await get(`${s}.json`); } catch (e) { console.warn(e); } }));
      draw();
    };
    fetchAll();
    setInterval(fetchAll, 60_000);
    setInterval(draw, 10_000);
  }

  function theme() {
    if (params.get("theme")) document.documentElement.dataset.theme = params.get("theme");
    const accent = params.get("accent");
    if (accent && /^[0-9a-fA-F]{3,8}$/.test(accent)) document.documentElement.style.setProperty("--accent", `#${accent}`);
  }

  return { get, view, renderStation, show, theme, params };
})();
