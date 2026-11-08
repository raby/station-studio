// ---------- adding music ----------------------------------------------------

const plural = (n, word) => `${(n || 0).toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
let pickerMissing = false;  // this computer can't show a chooser: type paths instead

// Ask the computer for a folder or file with its own chooser. Returns the
// path, or null when cancelled (or no chooser could be shown).
async function pickPath(kind) {
  try {
    const res = await api("/api/pick", { kind }, { quiet: true });
    return res.cancelled ? null : res.path || null;
  } catch (err) {
    if (err.status === 501 || (err.data && err.data.unsupported)) {
      pickerMissing = true;
      renderView();
      const typed = $("#view details.typed");
      if (typed) { typed.open = true; const input = typed.querySelector("input"); if (input) input.focus(); }
      toast("This computer can't show a chooser here, so type the path instead", { error: true });
    } else if (err.status === 409) {
      toast("A chooser is already open. It may be behind this window.", { error: true });
    } else {
      toast(err.message, { error: true });
    }
    return null;
  }
}

async function addSource(kind, path) {
  try {
    await loadState(await api("/api/sources", { kind, path }));
  } catch (err) {
    toast(err.message, { error: true });
    return false;
  }
  for (const s of suggestions || []) if (s.kind === kind && samePath(s.path, path)) s.added = true;
  renderView();
  const src = (S.lib.sources || []).find((s) => s.kind === kind && s.path === path) ||
    (S.lib.sources || []).find((s) => s.kind === kind && samePath(s.path, path));
  toast(src ? `Added ${src.name}: ${sourceStat(src)}` : `Added ${baseName(path)}`);
  return true;
}

const samePath = (a, b) => a.replace(/[\\/]+$/, "") === b.replace(/[\\/]+$/, "");

// After the page is redrawn, put focus back where it was (by selector).
function refocus(selector) {
  const target = selector && document.querySelector(selector);
  if (target) target.focus();
  else focusHeading();
}

async function pickAndAdd(kind, pick, from) {
  const path = await pickPath(pick);
  if (path) await addSource(kind, path);
  refocus(from);
}

function sourceStat(src) {
  if (!src.exists) return "Not found. It may have moved, or its drive isn't plugged in.";
  if (src.kind === "music") return plural(src.tracks, "track");
  return `${plural(src.playlists, "playlist")}, ${plural(src.tracks, "track")}`;
}

function sourceCard(src) {
  const folder = src.kind === "music";
  // A source that has gone can be pointed at its new place.
  const relocate = async () => {
    const path = await pickPath(folder || !/\.xml$/i.test(src.path) ? "folder" : "file");
    if (!path) return;
    if (!(await addSource(src.kind, path))) return;
    // Chose the same place (its drive is back): nothing to swap.
    const both = (S.lib.sources || []).filter((s) => s.kind === src.kind && (s.path === src.path || samePath(s.path, path)));
    if (both.length > 1 && !samePath(path, src.path)) {
      await act("/api/sources/remove", { kind: src.kind, path: src.path }, `Now using ${baseName(path)}`);
    }
  };
  return el("div", { class: src.exists ? "source" : "source gone" },
    el("div", { class: src.exists ? "glyph" : "glyph danger" }, icon(src.exists ? (folder ? "folder" : "list") : "alert")),
    el("div", { class: "info" },
      el("div", { class: "name" }, src.name || baseName(src.path)),
      el("div", { class: "path", title: src.path }, src.path),
      el("div", { class: "stat" }, sourceStat(src))),
    el("div", { class: "actions" },
      src.exists ? null : el("button", { type: "button", class: "btn small primary", onclick: relocate }, "Find it…"),
      el("button", {
        type: "button", class: "btn small ghost", "aria-label": `Remove ${src.name || src.path}`,
        title: "Stop using this in Station Player. The files stay where they are.",
        onclick: () => act("/api/sources/remove", { kind: src.kind, path: src.path }, `Removed ${src.name || baseName(src.path)}`),
      }, ...iconLabel("x", "Remove"))));
}

// The two ways in: a folder of music, or playlists exported from iTunes / station-studio.
function addTiles() {
  const tile = (glyph, title, sub, onclick, id, cls = "") => el("button", { type: "button", class: `tile ${cls}`, id, onclick },
    el("span", { class: "glyph" }, icon(glyph)),
    el("span", {}, el("span", { class: "t-title" }, title), el("span", { class: "t-sub" }, sub)));
  return el("div", {},
    el("div", { class: "add-tiles" },
      tile("folder", "Add a music folder", "A folder of songs, such as your Music folder. It shows under Folders.",
        () => pickAndAdd("music", "folder", "#add-folder"), "add-folder"),
      tile("list", "Add iTunes or station-studio playlists", "An exported library (.xml). Station Player picks up its playlists.",
        () => pickAndAdd("playlist", "file", "#add-playlists"), "add-playlists", "studio-only")),
    el("p", { class: "help studio-only" },
      "Station-studio saves a new export every week? ",
      el("button", { type: "button", class: "btn small", id: "add-exports", onclick: () => pickAndAdd("playlist", "folder", "#add-exports") }, "Choose its exports folder"),
      " and every new one appears by itself."),
    typedPaths());
}

// For computers without a chooser, or anyone who'd rather paste a path.
function typedPaths() {
  return el("details", { class: "typed", open: pickerMissing || null },
    el("summary", {}, "Type or paste a path instead"),
    el("div", { class: "checks" },
      pathForm("music", "Path to a folder of music"),
      el("div", { class: "studio-only" }, pathForm("playlist", "Path to an .xml file, or a folder of them"))));
}

function pathForm(kind, placeholder) {
  const input = el("input", { placeholder, "aria-label": placeholder });
  const msg = el("div", { class: "form-msg", role: "status" });
  const submit = async (e) => {
    e.preventDefault();
    msg.textContent = "";
    if (!input.value.trim()) { msg.textContent = "Type a path first"; return; }
    try {
      await loadState(await api("/api/sources", { kind, path: input.value }));
      renderView();
      focusHeading();
      toast(`Added ${baseName(input.value.trim())}`);
    } catch (err) { msg.textContent = err.message; }
  };
  return el("div", {}, el("form", { class: "form-row", onsubmit: submit }, input,
    el("button", { class: "btn" }, "Add")), msg);
}

// Music folders and libraries found on this computer, to add with a click.
let suggestions = null;
const justAdded = new Set();  // suggestions added on this visit stay listed, ticked
let suggestionsToken = 0;
let suggestionsVisit = -1;
function suggestionList(onAdded) {
  const box = el("div", { class: "suggestions" });
  const draw = () => {
    const list = (suggestions || []).filter((s) => !s.added || justAdded.has(s.path));
    box.replaceChildren(...list.map((s) => el("div", { class: s.added ? "suggestion added" : "suggestion", "data-path": s.path },
      el("div", { class: "glyph" }, icon(s.kind === "music" ? "folder" : "list")),
      el("div", { class: "info" },
        el("div", { class: "name" }, s.label || baseName(s.path)),
        s.detail ? el("div", { class: "detail" }, s.detail) : null,
        el("div", { class: "path", title: s.path }, s.path)),
      s.added ? el("span", { class: "added-mark", tabindex: "-1" }, icon("check"), "Added")
        : el("button", {
            type: "button", class: "btn small primary", "aria-label": `Add ${s.label || s.path}`,
            onclick: async () => {
              justAdded.add(s.path);
              if (await addSource(s.kind, s.path)) {
                if (onAdded) onAdded();
                refocus(`#view .suggestion[data-path="${CSS.escape(s.path)}"] .added-mark`);
              } else {
                justAdded.delete(s.path);
              }
            },
          }, ...iconLabel("plus", "Add")))));
    box.hidden = list.length === 0;
    const section = box.closest("[data-suggestions]");
    if (section) section.hidden = list.length === 0;
  };
  // Looked for once each time the page is opened, not on every redraw.
  if (suggestionsVisit === showToken && suggestions) {
    queueMicrotask(draw);
    return box;
  }
  suggestionsVisit = showToken;
  const token = ++suggestionsToken;
  api("/api/suggestions").then((res) => {
    if (token !== suggestionsToken) return;
    suggestions = res.suggestions || [];
    draw();
  }).catch(() => {});
  queueMicrotask(draw);
  return box;
}

// "Some tracks can't be found": point Station Player at where they are now.
function missingBanner(title) {
  return el("div", { class: "banner", role: "region", "aria-label": "Missing tracks" },
    el("div", { class: "glyph" }, icon("locate")),
    el("div", { class: "info" },
      el("strong", {}, title),
      el("span", { class: "sub" }, "If the music is on another drive, or came from another computer, show Station Player the folder it's in now.")),
    el("button", { type: "button", class: "btn primary", onclick: locateMissing }, "Find them…"));
}

async function locateMissing() {
  const folder = await pickPath("folder");
  if (!folder) return;
  let res;
  try {
    res = await api("/api/locate", { folder });
  } catch (err) {
    toast(err.message, { error: true });
    return;
  }
  await loadState(res);
  if (S.view.type === "playlist" && S.view.id) await refreshDetail(undefined, S.view.id);
  else renderView();
  refocus("#view .banner button");
  if (res.found) toast(`Found ${plural(res.found, "track")}${res.missing ? `. ${plural(res.missing, "track")} still missing.` : ""}`);
  else toast("None of the missing tracks are in that folder", { error: true });
}

// The first thing a new user sees, and where "Add music" leads.
function welcomeView() {
  const sources = S.lib.sources || [];
  const fresh = sources.length === 0;
  const finish = el("div", { class: "finish" });
  const drawFinish = () => {
    const first = S.lib.playlists[0];
    finish.replaceChildren((S.lib.sources || []).length && first ? el("button", {
      type: "button", class: "btn primary big", id: "btn-start-listening",
      onclick: () => show({ type: "playlist", id: first.id }),
    }, ...iconLabel("play", "Start listening")) : "");
  };
  drawFinish();
  return el("div", { class: "welcome" },
    el("div", { class: "hero" },
      el("img", { src: "/icon-192.png", alt: "" }),
      el("h1", {}, fresh ? "Welcome to Station Player" : "Add music"),
      el("p", {}, fresh
        ? "Let's find your music. Show Station Player where it lives and it does the rest: playlists, folders, artwork and all."
        : "Add another folder of music, or more playlists from iTunes or station\u2011studio.")),
    el("section", { "data-suggestions": "", hidden: true },
      el("h2", {}, "Found on this computer"),
      suggestionList(drawFinish)),
    el("section", {},
      el("h2", {}, fresh ? "Or choose it yourself" : "Choose it yourself"),
      addTiles()),
    finish,
    el("p", { class: "later" }, fresh ? "Just here for the radio? " : "Your music sources are listed under ",
      el("button", {
        type: "button", class: "btn small",
        onclick: () => show(fresh ? { type: "radio" } : { type: "settings", tab: "music" }),
      }, fresh ? "Go to Radio" : "Settings › Music"),
      fresh ? " You can add music any time with Add music in the sidebar." : "."));
}

