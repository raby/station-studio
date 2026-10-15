// The phone remote (see remote.py). Everything is relative to the secret
// link this page was opened from.
const $ = (sel) => document.querySelector(sel);
let view = null;
let busyVolume = 0;

async function call(body) {
  try {
    const res = await fetch("api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Station-Player": "1" },
      body: JSON.stringify(body),
    });
    if (res.ok) render(await res.json());
    else if (res.status === 404) gone();
  } catch {
    offline("Can't reach Station Player. Is the computer on, and this phone on the same Wi-Fi?");
  }
  setTimeout(refresh, 600);  // the player picks commands up within a second
}

function offline(text) {
  const note = $("#offline");
  note.textContent = text;
  note.hidden = !text;
}

function gone() {
  offline("This link doesn't work any more. Scan the new one in Station Player's settings.");
}

function item(label, onclick, on) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  if (on) { b.className = "on"; b.setAttribute("aria-current", "true"); }
  b.addEventListener("click", onclick);
  const li = document.createElement("li");
  li.append(b);
  return li;
}

function render(data) {
  view = data;
  const s = data.status || {};
  offline(data.page_open ? "" : "Station Player's window is closed on the computer. Choose something to play and it opens.");
  const radio = s.mode === "radio";
  $("#title").textContent = radio ? s.station : s.title || "Nothing playing";
  $("#sub").textContent = radio ? (s.title || "Live radio") : [s.artist, s.queue].filter(Boolean).join(" · ");
  const art = $("#art");
  const src = s.mode === "track" && s.track ? `art/${encodeURIComponent(s.track)}` : "";
  if (src && art.getAttribute("src") !== src) { art.hidden = true; art.src = src; }
  if (!src) { art.hidden = true; art.removeAttribute("src"); }
  const t = $("#toggle");
  t.textContent = s.playing ? "❚❚" : "▶";
  t.setAttribute("aria-label", s.playing ? "Pause" : "Play");
  $("#prev").disabled = $("#next").disabled = s.mode !== "track";
  if (typeof s.volume === "number" && Date.now() - busyVolume > 2000) $("#volume").value = s.volume;
  const sleep = $("#sleep");
  if (document.activeElement !== sleep) {
    const left = s.sleep_left;
    let opt = sleep.querySelector('option[value="on"]');
    if (typeof left === "number" && left > 0) {
      if (!opt) { opt = document.createElement("option"); opt.value = "on"; sleep.prepend(opt); }
      opt.textContent = `In ${Math.ceil(left / 60)} min`;
      sleep.value = "on";
    } else {
      if (opt) opt.remove();
      sleep.value = left === "track" ? "track" : "0";
    }
  }
  $("#stations").replaceChildren(...data.stations.map((st) =>
    item(st.name, () => call({ cmd: "station", id: st.id }), radio && s.station === st.name)));
  $("#playlists").replaceChildren(...data.playlists.map((p) =>
    item(p.name, () => call({ cmd: "playlist", id: p.id }), !radio && s.queue === p.name)));
}

async function refresh() {
  try {
    const res = await fetch("api/status", { cache: "no-store" });
    if (res.status === 404) return gone();
    render(await res.json());
  } catch {
    offline("Can't reach Station Player. Is the computer on, and this phone on the same Wi-Fi?");
  }
}

$("#art").addEventListener("load", () => { $("#art").hidden = false; });
$("#art").addEventListener("error", () => { $("#art").hidden = true; });
$("#toggle").addEventListener("click", () => call({ cmd: "toggle" }));
$("#next").addEventListener("click", () => call({ cmd: "next" }));
$("#prev").addEventListener("click", () => call({ cmd: "prev" }));
$("#volume").addEventListener("change", (e) => { busyVolume = Date.now(); call({ cmd: "volume", value: Number(e.target.value) }); });
$("#volume").addEventListener("input", () => { busyVolume = Date.now(); });
$("#sleep").addEventListener("change", (e) => {
  const v = e.target.value;
  if (v !== "on") call({ cmd: "sleep", minutes: v === "track" ? "track" : Number(v) });
});
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 2000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
