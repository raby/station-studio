// ---------- radio view --------------------------------------------------

// The station being edited, if any (kept across redraws).
let editingStation = null;

// Check that a stream URL plays, showing the answer in `out`.
async function testStream(url, out) {
  out.className = "test-result";
  out.replaceChildren("Testing…");
  out.hidden = false;
  try {
    const res = await api("/api/stations/test", { url }, { quiet: true });
    if (res.ok) {
      out.className = "test-result ok";
      out.replaceChildren(icon("check"), el("span", {}, `It works${res.name ? `: ${res.name}` : ""}${res.type ? ` (${res.type})` : ""}`));
    } else {
      out.className = "test-result bad";
      out.replaceChildren(icon("alert"), el("span", {}, res.message || "It didn't play"));
    }
    return res;
  } catch (err) {
    out.className = "test-result bad";
    out.replaceChildren(icon("alert"), el("span", {}, err.message));
    return null;
  }
}

// A station's logo: one kept by the player (saved stations, Recently
// played), else the finder's (fetched through the player).
function logoSrc(st) {
  if (st && st.logo && /^[0-9a-f]{16}\.\w+$/.test(st.logo)) return `/logo/${st.logo}`;
  if (st && st.logo_url) return `/api/directory/logo?u=${encodeURIComponent(st.logo_url)}`;
  return null;
}

// The logo, or the radio glyph when there's none (or it won't load).
function stationArt(st, cls = "glyph") {
  const glyph = el("div", { class: cls }, icon("radio"));
  const src = logoSrc(st);
  if (!src) return glyph;
  const img = el("img", { class: "logo", alt: "", loading: "lazy", src });
  glyph.hidden = true;
  img.addEventListener("error", () => { img.remove(); glyph.hidden = false; });
  return el("div", { class: "station-art" }, img, glyph);
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => reject(new Error("That file couldn't be read"));
    r.readAsDataURL(file);
  });
}

function logoEditor(st, result, nameInput, urlInput) {
  const show = (ok, text) => {
    result.className = `test-result ${ok ? "ok" : "bad"}`;
    result.replaceChildren(icon(ok ? "check" : "alert"), el("span", {}, text));
    result.hidden = false;
  };
  const set = async (body, done) => {
    try {
      await loadState(await api("/api/stations/logo", { url: st.url, ...body }, { quiet: true }));
      // The form is drawn again: keep what was typed but not saved yet.
      renderKeepingEdits();
      refocus(`#view .card[data-url="${CSS.escape(st.url)}"] .logo-row button`);
      toast(done);
    } catch (err) { show(false, err.message); }
  };
  const file = el("input", {
    type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", hidden: true,
    onchange: async () => {
      const f = file.files && file.files[0];
      if (!f) return;
      if (f.size > 256 * 1024) { show(false, "That picture is too big (the limit is 256 KB)"); return; }
      try { await set({ image: await readFile(f) }, `New logo for ${st.name}`); } catch (err) { show(false, err.message); }
    },
  });
  return el("div", { class: "row logo-row" },
    stationArt(st),
    el("button", {
      type: "button", class: "btn small", title: "Ask the station's own Station Server, then Radio Browser",
      onclick: async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        show(true, "Looking…");
        await set({ find: true }, `Found a logo for ${st.name}`);
        btn.disabled = false;  // still here when nothing was found
      },
    }, ...iconLabel("search", "Find logo")),
    el("button", { type: "button", class: "btn small", onclick: () => file.click() }, "Choose picture…"),
    st.logo ? el("button", { type: "button", class: "btn small ghost", onclick: () => set({}, `Removed ${st.name}'s logo`) }, "No logo") : null,
    file);
}

// Your own tags on stations, like "jazz" or "late night": typed with
// commas between them, tidied the way the server keeps them.
function splitTags(text) {
  const out = [];
  for (const raw of text.split(",")) {
    const t = raw.split(/\s+/).filter(Boolean).join(" ").slice(0, 30).trim();
    if (t && !out.some((x) => x.toLowerCase() === t.toLowerCase())) out.push(t);
  }
  return out.slice(0, 12);
}

function stationTags() {
  const counts = new Map();
  for (const st of S.lib.stations || []) for (const t of st.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
  return counts;
}

let stationTag = store("station-tag") || "";
function setStationTag(tag) {
  stationTag = tag;
  store("station-tag", tag);
  renderKeepingEdits();
  refocus(`#view #station-tags [data-tag="${CSS.escape(tag)}"]`);
}

// Draw the Radio page again, keeping what's typed in a station being
// edited but not saved yet.
function renderKeepingEdits() {
  const sel = editingStation === null ? null : `#view .card[data-url="${CSS.escape(editingStation)}"] .edit input:not([type=file])`;
  const typed = sel ? [...document.querySelectorAll(sel)].map((x) => x.value) : [];
  renderView();
  if (!sel) return;
  const inputs = document.querySelectorAll(sel);
  if (inputs.length === typed.length) inputs.forEach((x, n) => { x.value = typed[n]; x.dispatchEvent(new Event("input")); });
}

async function putOnDesktop(st, btn) {
  btn.disabled = true;
  try {
    await api("/api/stations/shortcut", { url: st.url }, { quiet: true });
    toast(`${st.name} is on your desktop: double-click it to listen`);
  } catch (err) { toast(err.message, { error: true }); }
  btn.disabled = false;
}

function stationCard(st, i, count) {
  const isLive = () => S.mode === "radio" && S.station && S.station.url === st.url;
  const name = el("input", { value: st.name, "aria-label": "Station name", required: true });
  const url = el("input", { value: st.url, "aria-label": "Stream URL", required: true });
  const tags = el("input", { value: (st.tags || []).join(", "), "aria-label": "Tags", placeholder: "jazz, late night", autocomplete: "off" });
  const feed = el("input", { value: st.feed || "", "aria-label": "What's on feed", type: "url", autocomplete: "off",
    placeholder: "https://…/rebirth-classics.json" });
  const suggest = el("div", { class: "tag-row tag-suggest" });
  const syncSuggest = () => {
    const have = splitTags(tags.value).map((t) => t.toLowerCase());
    const others = [...stationTags().keys()].filter((t) => !have.includes(t.toLowerCase())).sort((a, b) => a.localeCompare(b));
    suggest.replaceChildren(...others.map((t) => el("button", {
      type: "button", class: "tag", "aria-label": `Add the tag ${t}`,
      onclick: () => { tags.value = [...splitTags(tags.value), t].join(", "); syncSuggest(); tags.focus(); },
    }, `+ ${t}`)));
    suggest.hidden = !others.length;
  };
  tags.addEventListener("input", syncSuggest);
  syncSuggest();
  const result = el("div", { class: "test-result", role: "status", hidden: true });
  const editButton = () => `#view .card[data-url="${CSS.escape(st.url)}"] .card-top button`;
  const close = () => { editingStation = null; renderView(); refocus(editButton()); };
  const save = async (e) => {
    e.preventDefault();
    const body = { url: st.url };
    if (name.value.trim() !== st.name) body.name = name.value;
    if (url.value.trim() !== st.url) body.new_url = url.value;
    const newTags = splitTags(tags.value);
    if (newTags.join("\n") !== (st.tags || []).join("\n")) body.tags = newTags;
    if (feed.value.trim() !== (st.feed || "")) body.feed = feed.value;
    if (!body.name && !body.new_url && !body.tags && body.feed === undefined) { close(); return; }
    try {
      await loadState(await api("/api/stations/update", body, { quiet: true }));
      editingStation = null;
      renderView();
      const moved = (S.lib.stations || []).find((x) => x.name === name.value.trim());
      refocus(moved ? `#view .card[data-url="${CSS.escape(moved.url)}"] .card-top button` : null);
      toast(`Saved ${name.value.trim()}`);
    } catch (err) {
      result.className = "test-result bad";
      result.replaceChildren(icon("alert"), el("span", {}, err.message));
      result.hidden = false;
    }
  };
  const move = async (to) => {
    try {
      await loadState(await api("/api/stations/move", { url: st.url, to }, { quiet: true }));
      renderView();
      announce(`${st.name} moved to number ${to + 1}`);
      const again = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] .move-${to < i ? "up" : "down"}`);
      const anyMove = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] .moves button:not([disabled])`);
      (again && !again.disabled ? again : anyMove)?.focus();
    } catch (err) { renderErrors(err.message); }
  };
  const editing = editingStation === st.url;
  const card = el("div", { class: "card", "data-url": st.url },
    el("div", { class: "view-part", hidden: editing },
      el("div", { class: "card-top" },
        stationArt(st),
        el("div", { class: "who" }, el("div", { class: "name" }, st.name),
          (st.tags || []).length ? el("div", { class: "tag-row" }, st.tags.map((t) => el("button", {
            type: "button", class: "tag", "data-tag": t, title: `Show only stations tagged ${t}`,
            onclick: () => setStationTag(t),
          }, t))) : null),
        el("button", {
          type: "button", class: "btn ghost icon-only", "aria-label": `Edit ${st.name}`, title: "Edit, test, move or remove",
          onclick: () => { editingStation = st.url; renderView(); const n = document.querySelector(`#view .card[data-url="${CSS.escape(st.url)}"] input`); if (n) n.focus(); },
        }, icon("edit"))),
      el("div", { class: "song", hidden: true }),
      st.feed ? el("div", { class: "whatson", "data-station": st.id, hidden: true }) : null,
      el("div", { class: "rec", hidden: true }, el("span", { class: "rec-text" }),
        el("button", { type: "button", class: "btn small rec-play", hidden: true }, ...iconLabel("play", "Play"))),
      el("div", { class: "row" },
        el("button", {
          type: "button", class: "btn primary listen",
          onclick: () => (isLive() && !audio.paused ? audio.pause() : playStation(st)),
        }, ...iconLabel("play", "Listen")),
        el("button", { type: "button", class: "btn rec-btn studio-only", onclick: () => toggleRecording(st) }, ...iconLabel("record", "Record")))),
    editing ? el("form", { class: "edit", onsubmit: save },
      el("label", {}, "Name", name),
      el("label", {}, "Stream URL", url),
      el("label", {}, "Tags", el("span", { class: "help" }, "Your own labels, with commas between them"), tags),
      suggest,
      el("label", {}, "What's on", el("span", { class: "help" },
        "Optional: the feed its Station Server publishes (Settings › Website), for on now, next and just played"), feed),
      logoEditor(st, result, name, url),
      result,
      el("div", { class: "row" },
        el("button", { class: "btn primary" }, "Save"),
        el("button", { type: "button", class: "btn", onclick: () => testStream(url.value, result) }, "Test"),
        el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel")),
      el("div", { class: "row moves" },
        el("button", { type: "button", class: "btn small move-up", disabled: i === 0 || null, "aria-label": `Move ${st.name} up`, onclick: () => move(i - 1) }, ...iconLabel("up", "Up")),
        el("button", { type: "button", class: "btn small move-down", disabled: i === count - 1 || null, "aria-label": `Move ${st.name} down`, onclick: () => move(i + 1) }, ...iconLabel("down", "Down")),
        S.lib.desktop && S.lib.desktop.supported ? el("button", {
          type: "button", class: "btn small desktop-icon", title: "Double-click the icon to start the player and listen",
          onclick: (e) => putOnDesktop(st, e.currentTarget),
        }, ...iconLabel("app", "Put on desktop")) : null,
        el("span", { class: "spacer" }),
        el("button", {
          type: "button", class: "btn small danger",
          onclick: () => {
            if (!confirm(`Remove ${st.name}?`)) return;
            editingStation = null;
            act("/api/stations/remove", { url: st.url }, `Removed ${st.name}`);
          },
        }, ...iconLabel("trash", "Remove")))) : null);
  card.addEventListener("keydown", (e) => { if (e.key === "Escape" && editingStation === st.url) { e.preventDefault(); close(); } });
  return card;
}

function radioView() {
  const msg = el("div", { class: "form-msg", role: "status" });
  const result = el("div", { class: "test-result", role: "status", hidden: true });
  const name = el("input", { placeholder: "Station name", "aria-label": "Station name" });
  const url = el("input", { placeholder: "https://… stream, .pls or .m3u link", "aria-label": "Stream URL", type: "url" });
  const add = async (e) => {
    e.preventDefault();
    msg.textContent = "";
    try {
      await loadState(await api("/api/stations", { name: name.value, url: url.value }));
      renderView();
      focusHeading();
      toast(`Added ${name.value.trim() || "the station"}`);
    } catch (err) { msg.textContent = err.message; }
  };
  const test = async () => {
    msg.textContent = "";
    const res = await testStream(url.value, result);
    if (res && res.ok && res.name && !name.value.trim()) name.value = res.name;
  };

  const stations = S.lib.stations || [];
  const counts = stationTags();
  if (stationTag && !counts.has(stationTag)) { stationTag = ""; store("station-tag", ""); }
  // Filtered cards are hidden, not left out, so Up and Down still move a
  // station past the ones you can't see.
  const cards = stations.map((st, i) => {
    const card = stationCard(st, i, stations.length);
    card.hidden = Boolean(stationTag) && !(st.tags || []).includes(stationTag) && editingStation !== st.url;
    return card;
  });
  const chip = (label, tag) => el("button", {
    type: "button", class: "chip", "data-tag": tag, "aria-pressed": String(stationTag === tag), onclick: () => setStationTag(tag),
  }, label);

  queueMicrotask(syncRadioCards);
  queueMicrotask(syncRecordings);
  if (stations.some((st) => st.feed)) queueMicrotask(refreshWhatsOn);
  const visible = stations.filter((st) => !stationTag || (st.tags || []).includes(stationTag));
  return el("div", {},
    el("div", { class: "view-head" }, el("h1", {}, "Radio"), el("div", { class: "spacer" }),
      stations.some((x) => !x.logo) ? el("button", {
        type: "button", class: "btn", id: "btn-find-logos", title: "Ask each station's own Station Server, then Radio Browser",
        onclick: findAllLogos,
      }, ...iconLabel("search", "Find logos")) : null),
    counts.size ? el("div", { class: "tag-filter", id: "station-tags", role: "group", "aria-label": "Show stations tagged" },
      chip("All", ""), ...[...counts.keys()].sort((a, b) => a.localeCompare(b)).map((t) => chip(`${t} (${counts.get(t)})`, t))) : null,
    visible.filter(onTuner).length > 1 ? tuner(visible.filter(onTuner)) : null,
    cards.length ? el("div", { class: "cards" }, cards) : el("div", { class: "empty-state" },
      el("div", { class: "glyph big" }, icon("radio")),
      el("h2", {}, "No stations yet"),
      el("p", {}, "Find stations in Discover, or add one with its stream link below. Most stations list one on their website, often as a .pls or .m3u file.")),
    el("div", { class: "settings" }, scheduleSection(), recentSection(), discoverLink(), el("section", { class: "add-station" },
      el("h2", {}, "Add a station by its link"),
      el("p", { class: "help" }, "Test checks the link plays, and fills in the name when the station sends one."),
      el("form", { class: "form-row", onsubmit: add }, name, url,
        el("button", { type: "button", class: "btn", onclick: test }, "Test"),
        el("button", { class: "btn primary" }, ...iconLabel("plus", "Add"))),
      result,
      msg),
    el("section", { class: "studio-only" },
      el("h2", {}, "Recordings"),
      el("p", { class: "help" },
        "Record records a station to a file, whether or not you're listening to it, exactly as the station sends it. ",
        "When the station sends song titles, they're saved in a text file beside the recording. ",
        `Recordings go in ${S.lib.recordings_folder || "your Music folder"}. A recording stops by itself after 12 hours.`),
      el("button", { type: "button", class: "btn", onclick: () => show({ type: "recordings" }) },
        ...iconLabel("record", "See your recordings")))),
  );
}

// The finder lives on Discover now; the Radio page links to it.
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
