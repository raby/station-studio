// ---------- recordings page -----------------------------------------------

// Finished recordings, with the label and tags you give them.
const R = { list: null, folder: "", tag: "", q: "", editing: null, token: 0 };

async function loadRecordings() {
  const token = ++R.token;
  try {
    const res = await api("/api/recordings/list");
    if (token !== R.token) return;
    R.list = res.recordings;
    R.folder = res.folder;
  } catch (err) {
    if (token !== R.token) return;
    R.list = R.list || [];
    renderErrors(err.message);
  }
  if (S.view.type !== "recordings") return;
  // Redrawn in place: whatever had focus keeps it, if it's still there.
  const active = document.activeElement;
  const key = active && $("#view").contains(active) ? (active.id || active.getAttribute("aria-label")) : null;
  renderView();
  if (key) refocus(`#view #${CSS.escape(key)}, #view [aria-label="${CSS.escape(key)}"]`);
}

const recTitle = (r) => r.label || `${r.station || "Radio"} · ${fmtWhen(r.started)}`;
function fmtWhen(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function recordingRow(r, i) {
  const editing = R.editing === r.file;
  const facts = [r.label ? r.station : null, r.label ? fmtWhen(r.started) : null,
    r.seconds ? fmtTime(r.seconds) : null, fmtBytes(r.bytes),
    r.songs.length ? plural(r.songs.length, "song") : null].filter(Boolean);
  const label = el("input", { value: r.label, id: `rec-label-${i}`, placeholder: recTitle({ ...r, label: "" }), maxlength: 200 });
  const tags = el("input", { value: r.tags.join(", "), id: `rec-tags-${i}`, placeholder: "e.g. breakfast show, interview" });
  const msg = el("div", { class: "form-msg", role: "status" });
  const close = () => { R.editing = null; renderView(); refocus(`#view [aria-label="Edit ${CSS.escape(recTitle(r))}"]`); };
  const save = async (e) => {
    e.preventDefault();
    const newTags = tags.value.split(",").map((t) => t.trim()).filter(Boolean);
    try {
      const res = await api("/api/recordings/label", { file: r.file, label: label.value, tags: newTags }, { quiet: true });
      R.list = res.recordings;
      R.editing = null;
      renderView();
      const now = R.list.find((x) => x.file === r.file);
      refocus(now ? `#view [aria-label="Edit ${CSS.escape(recTitle(now))}"]` : null);
      toast("Saved");
    } catch (err) { msg.textContent = err.message; }
  };
  return el("li", { class: "recording", "data-file": r.file },
    el("div", { class: "rec-main" },
      el("div", { class: "glyph" }, icon("radio")),
      el("div", { class: "info" },
        el("div", { class: "name" }, recTitle(r)),
        el("div", { class: "facts" }, facts.join(" · ")),
        r.tags.length ? el("div", { class: "tag-row" }, r.tags.map((t) => el("button", {
          type: "button", class: "tag", title: `Show recordings tagged ${t}`,
          onclick: () => { R.tag = t; renderView(); refocus(`#view .tag-filter [data-tag="${CSS.escape(t)}"]`); },
        }, t))) : null),
      el("div", { class: "actions" },
        el("button", {
          type: "button", class: "btn small primary", "aria-label": `Play ${recTitle(r)}`,
          onclick: () => playRecording(r.file, { stay: true }),
        }, ...iconLabel("play", "Play")),
        el("button", {
          type: "button", class: "btn small icon-only", "aria-label": `Add ${recTitle(r)} to a playlist`,
          title: "Add to playlist", "aria-haspopup": "menu",
          onclick: (e) => addRecordingToPlaylist(r, e.currentTarget),
        }, icon("plus")),
        el("button", {
          type: "button", class: "btn small ghost icon-only", "aria-label": `Edit ${recTitle(r)}`, title: "Label and tags",
          onclick: () => { R.editing = r.file; renderView(); refocus(`#rec-label-${i}`); },
        }, icon("edit")))),
    editing ? el("form", { class: "rec-edit", onsubmit: save,
      onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } } },
      el("label", { class: "field", for: `rec-label-${i}` }, "Label"), label,
      el("label", { class: "field", for: `rec-tags-${i}` }, "Tags, separated by commas"), tags,
      msg,
      el("div", { class: "row" },
        el("button", { class: "btn primary" }, "Save"),
        el("button", { type: "button", class: "btn ghost", onclick: close }, "Cancel"))) : null,
    r.songs.length ? el("details", { class: "songs" },
      el("summary", {}, `Songs (${r.songs.length})`),
      el("ol", {}, r.songs.map((s) => el("li", {}, s)))) : null);
}

function recordingsView() {
  if (R.list === null) {
    return el("div", {}, el("div", { class: "view-head" }, el("h1", {}, "Recordings")), el("div", { class: "empty" }, "Looking for your recordings…"));
  }
  const all = R.list;
  const tagCounts = new Map();
  for (const r of all) for (const t of r.tags) tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
  if (R.tag && !tagCounts.has(R.tag)) R.tag = "";
  const matching = () => {
    const q = R.q.trim().toLowerCase();
    return all.filter((r) => (!R.tag || r.tags.includes(R.tag)) &&
      (!q || [recTitle(r), r.station, ...r.tags, ...r.songs].some((v) => v && v.toLowerCase().includes(q))));
  };
  const shown = matching();
  const chip = (label, tag) => el("button", {
    type: "button", class: "chip", "data-tag": tag, "aria-pressed": String(R.tag === tag),
    onclick: () => { R.tag = tag; renderView(); refocus(`#view .tag-filter [data-tag="${CSS.escape(tag)}"]`); },
  }, label);
  const list = el("ul", { class: "rec-list" }, shown.map(recordingRow));
  const none = el("div", { class: "empty", hidden: shown.length > 0 }, "No recordings match.");
  // Typing in the search filters the list without redrawing the page.
  const filter = () => {
    const now = matching();
    list.replaceChildren(...now.map(recordingRow));
    none.hidden = now.length > 0;
  };
  return el("div", { class: "recordings" },
    el("div", { class: "view-head" },
      el("div", {}, el("h1", {}, "Recordings"),
        el("div", { class: "meta" }, all.length ? plural(all.length, "recording") : "")),
      el("div", { class: "spacer" }),
      all.length ? el("input", {
        type: "search", class: "search", id: "rec-search", placeholder: "Search labels, tags and songs", value: R.q,
        "aria-label": "Search recordings",
        oninput: (e) => { R.q = e.target.value; filter(); },
      }) : null,
      el("button", {
        type: "button", class: "btn",
        onclick: () => api("/api/recordings/open", {}, { quiet: true }).catch((err) => renderErrors(err.message)),
      }, ...iconLabel("folder", "Open folder"))),
    tagCounts.size ? el("div", { class: "tag-filter", role: "group", "aria-label": "Show recordings tagged" },
      chip("All", ""), ...[...tagCounts.keys()].sort((a, b) => a.localeCompare(b)).map((t) => chip(`${t} (${tagCounts.get(t)})`, t))) : null,
    all.length === 0 ? el("div", { class: "empty-state" },
      el("div", { class: "glyph big" }, icon("record")),
      el("h2", {}, "No recordings yet"),
      el("p", {}, "Press Record on any station on the Radio page. When it's finished it shows up here, ready to label, tag and play."),
      el("button", { type: "button", class: "btn primary", onclick: () => show({ type: "radio" }) }, ...iconLabel("radio", "Go to Radio")))
      : [list, none],
    activeRecordings().length ? el("p", { class: "help" }, "Recordings still going appear here once they're stopped.") : null);
}

