// Station Player in the car: a page for the phone that plays your radio
// stations over mobile data, with big buttons, and the steering-wheel
// (or car screen) buttons skipping between stations.
//
// Station Player on the computer makes the link to this page (Settings ›
// In the car). The stations travel in the link itself, after the #, so
// they never reach a server: "#s=" then base64url of raw-deflated JSON
// {"v": 1, "s": [[name, url, [tags]], ...]}. The link is kept in the
// address, so a home-screen icon made from it carries them too, and a
// copy is kept on the phone for when the icon opens without it.
"use strict";

const $ = (sel) => document.querySelector(sel);
const audio = $("#audio");
const KEY = "station-player-car";
const RETRY_SECONDS = [1, 2, 4, 8, 15, 30];

const S = {
  stations: [],      // [{name, url, tags}]
  tag: null,         // the tag being shown, or null for all
  current: -1,       // index into S.stations of the chosen station
  want: false,       // the listener wants sound (keep trying until there is)
  played: false,     // the current station has played since it was chosen
  tries: 0,
  timer: 0,
  wake: null,
};

function saved() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) || {};
  } catch {
    return {};
  }
}

function save(fields) {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...saved(), ...fields }));
  } catch { /* private browsing: nothing kept */ }
}

function b64urlBytes(text) {
  const bin = atob(text.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function decodeStations(text) {
  const stream = new Blob([b64urlBytes(text)]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  const data = JSON.parse(await new Response(stream).text());
  if (!data || data.v !== 1 || !Array.isArray(data.s)) throw new Error("not a station list");
  return data.s
    .filter((s) => Array.isArray(s) && typeof s[0] === "string" && typeof s[1] === "string" && /^https?:\/\//i.test(s[1]))
    .map(([name, url, tags]) => ({
      name: name.slice(0, 200),
      url,
      tags: Array.isArray(tags) ? tags.filter((t) => typeof t === "string").slice(0, 12) : [],
    }));
}

async function loadStations() {
  const hash = new URLSearchParams(location.hash.slice(1)).get("s");
  if (hash) {
    try {
      const stations = await decodeStations(hash);
      save({ stations });
      return stations;
    } catch {
      status("This link didn't work. Scan the code in Station Player again.");
    }
  }
  const kept = saved().stations;
  return Array.isArray(kept) ? kept : [];
}

// ---------- what's shown ----------------------------------------------------

function status(text) {
  $("#now-status").textContent = text;
}

function shown() {
  return S.stations.map((s, i) => [s, i]).filter(([s]) => !S.tag || s.tags.includes(S.tag));
}

function hue(text) {
  let h = 0;
  for (const c of text) h = (h * 31 + c.codePointAt(0)) % 360;
  return h;
}

function initials(name) {
  const words = name.replace(/[^\p{L}\p{N} ]/gu, " ").split(/\s+/).filter(Boolean);
  return (words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "?").slice(0, 2)).toUpperCase();
}

function render() {
  const list = $("#stations");
  list.replaceChildren(...shown().map(([s, i]) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "station";
    btn.dataset.index = i;
    if (i === S.current) btn.setAttribute("aria-current", "true");
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.style.setProperty("--hue", hue(s.name));
    badge.textContent = initials(s.name);
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = s.name;
    btn.append(badge, label);
    btn.addEventListener("click", () => choose(i));
    li.append(btn);
    return li;
  }));
  $("#empty").hidden = S.stations.length > 0;
  $("#btn-play").disabled = $("#btn-prev").disabled = $("#btn-next").disabled = S.stations.length === 0;

  const tags = [...new Set(S.stations.flatMap((s) => s.tags))].sort((a, b) => a.localeCompare(b));
  const nav = $("#tags");
  nav.hidden = tags.length === 0;
  nav.replaceChildren(...[null, ...tags].map((t) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tag";
    b.textContent = t || "All";
    b.setAttribute("aria-pressed", String(S.tag === t));
    b.addEventListener("click", () => {
      S.tag = t;
      save({ tag: t });
      render();
    });
    return b;
  }));

  const st = S.stations[S.current];
  $("#now-name").textContent = st ? st.name : "Station Player";
  document.title = st ? `${st.name} · Station Player` : "Station Player in the car";
  $("#ic-play").toggleAttribute("hidden", S.want); // SVG: no .hidden property
  $("#ic-stop").toggleAttribute("hidden", !S.want);
  $("#btn-play").setAttribute("aria-label", S.want ? "Stop" : "Play");
}

// ---------- playing -----------------------------------------------------------

// A page loaded over https can't play an http:// stream. Browsers try the
// same address over https instead; some stations answer there, some don't.
function playable(url) {
  return location.protocol === "https:" && /^http:/i.test(url) ? "https:" + url.slice(5) : url;
}

function choose(i) {
  S.current = i;
  S.played = false;
  save({ last: S.stations[i].url });
  start();
}

function start() {
  const st = S.stations[S.current];
  if (!st) return;
  S.want = true;
  S.tries = 0;
  connect();
  render();
  setMediaSession();
  keepAwake();
}

function connect() {
  clearTimeout(S.timer);
  const st = S.stations[S.current];
  if (!S.tries) status("Connecting…");
  audio.src = playable(st.url);
  audio.play().catch((err) => {
    if (err && err.name === "NotAllowedError") {
      S.want = false;
      status("Press play to start.");
      render();
    }
    // Anything else shows up as an error event, and is retried there.
  });
}

function stop(text = "") {
  S.want = false;
  clearTimeout(S.timer);
  audio.pause();
  audio.removeAttribute("src"); // stop downloading the stream
  audio.load();
  status(text);
  render();
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "paused";
  if (S.wake) S.wake.release().catch(() => {});
  S.wake = null;
}

function retry() {
  if (!S.want) return;
  const st = S.stations[S.current];
  if (!S.played && playable(st.url) !== st.url && navigator.onLine !== false) {
    stop("This station only has an http:// link, which phones can't play from here. Ask the station for an https:// link.");
    return;
  }
  const wait = RETRY_SECONDS[Math.min(S.tries, RETRY_SECONDS.length - 1)];
  S.tries += 1;
  status(navigator.onLine === false ? "No signal. Waiting to reconnect…"
    : S.played ? "Lost the signal. Reconnecting…" : "Can't reach the station. Trying again…");
  clearTimeout(S.timer);
  S.timer = setTimeout(connect, wait * 1000);
}

function skip(step) {
  const list = shown();
  if (!list.length) return;
  const at = list.findIndex(([, i]) => i === S.current);
  const [, next] = list[(at + step + list.length) % list.length];
  choose(next);
}

function toggle() {
  if (S.want) {
    stop();
  } else {
    if (S.current < 0) S.current = shown().length ? shown()[0][1] : 0;
    start();
  }
}

audio.addEventListener("playing", () => {
  S.played = true;
  S.tries = 0;
  status("Playing");
  // Sound is coming out, whatever paused it on the way (iPhones pause and
  // resume around a Bluetooth or CarPlay hand-over): show Stop, not Play.
  if (!S.want) {
    S.want = true;
    render();
  }
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = "playing";
});
audio.addEventListener("waiting", () => { if (S.want && S.played) status("Buffering…"); });
audio.addEventListener("error", () => { if (S.want && audio.getAttribute("src")) retry(); });
audio.addEventListener("ended", () => { if (S.want) retry(); }); // a live stream ends when it drops
// Paused from outside the page (a phone call, the car's own controls).
audio.addEventListener("pause", () => {
  if (S.want && audio.getAttribute("src") && !audio.error) {
    S.want = false;
    status("");
    render();
  }
});
addEventListener("online", () => {
  if (S.want && !S.played) { S.tries = 0; connect(); }
});

// ---------- the car's controls --------------------------------------------------

function setMediaSession() {
  if (!("mediaSession" in navigator)) return;
  const st = S.stations[S.current];
  const art = new URL("icon-512.png", location.href).href;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: st.name,
    artist: "Station Player",
    album: "Radio",
    artwork: [{ src: art, sizes: "512x512", type: "image/png" }],
  });
  const on = (action, fn) => {
    try {
      navigator.mediaSession.setActionHandler(action, fn);
    } catch { /* not offered by this phone */ }
  };
  on("play", () => { if (!S.want) start(); });
  on("pause", () => stop());
  on("stop", () => stop());
  on("nexttrack", () => skip(1));
  on("previoustrack", () => skip(-1));
  on("seekforward", null);
  on("seekbackward", null);
}

// The screen may sleep; this only keeps it on while a station plays and
// the page is in front, for a phone in a dashboard holder.
async function keepAwake() {
  try {
    if ("wakeLock" in navigator && document.visibilityState === "visible" && !S.wake) {
      S.wake = await navigator.wakeLock.request("screen");
      S.wake.addEventListener("release", () => { S.wake = null; });
    }
  } catch { /* not allowed: fine */ }
}
document.addEventListener("visibilitychange", () => { if (S.want) keepAwake(); });

$("#btn-play").addEventListener("click", toggle);
$("#btn-next").addEventListener("click", () => skip(1));
$("#btn-prev").addEventListener("click", () => skip(-1));

function installTip() {
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (standalone || !S.stations.length) return "";
  if (/iPhone|iPad|iPod/.test(navigator.userAgent)) {
    return "To keep this on your home screen, press Share, then Add to Home Screen.";
  }
  return "To keep this on your home screen, open the browser menu and choose Add to Home screen (or Install app).";
}

async function boot() {
  S.stations = await loadStations();
  const kept = saved();
  S.tag = S.stations.some((s) => s.tags.includes(kept.tag)) ? kept.tag : null;
  S.current = S.stations.findIndex((s) => s.url === kept.last);
  $("#tip-install").textContent = installTip();
  render();
  if (S.current >= 0) status("Press play for " + S.stations[S.current].name + ".");
  if ("serviceWorker" in navigator && location.protocol === "https:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
}

window.addEventListener("hashchange", boot);
boot();
