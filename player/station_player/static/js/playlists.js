// ---------- playlist view -----------------------------------------------

function matches(t, q) {
  if (!q) return true;
  return [t.name, t.artist, t.album, t.genre].some((v) => v && v.toLowerCase().includes(q));
}

function playlistView(pl) {
  // Rows are built once per view. Filtering hides rows and track changes
  // move the "playing" class, so a 10k-track library stays responsive.
  // Keyboard: one row is in the tab order at a time (roving tabindex);
  // arrow keys move between rows and Enter plays the focused one.
  const mine = isMine(pl);
  const rows = pl.tracks.map((t, i) => el("tr", {
    "data-index": i,
    "data-id": t.id,
    tabindex: "-1",
    draggable: mine ? "true" : null,
    class: t.available ? null : "missing",
    title: t.available ? "Double-click or press Enter to play" : "The audio file for this track wasn't found",
  },
    el("td", { class: "num" }, i + 1),
    el("td", { class: "title" }, t.name),
    el("td", { class: "artist" }, t.artist || ""),
    el("td", { class: "album" }, t.album || ""),
    el("td", { class: "dur" }, t.duration_ms ? fmtTime(t.duration_ms / 1000) : ""),
    el("td", { class: "more" }, t.available || mine
      ? el("button", { type: "button", class: "row-more", tabindex: "-1", "aria-label": `More for ${t.name}`, title: "Play next, add to a playlist and more" }, icon("more"))
      : null),
  ));
  const visibleRows = () => rows.filter((r) => !r.hidden);
  const focusRow = (tr) => {
    for (const r of rows) if (r.tabIndex === 0) r.tabIndex = -1;
    tr.tabIndex = 0;
    tr.focus();
  };
  const trackOf = (tr) => pl.tracks[Number(tr.dataset.index)];

  // Selecting several tracks: Cmd/Ctrl-click adds or takes away one,
  // Shift-click (or Shift+arrows) a run, Cmd/Ctrl+A all those showing.
  const sel = new Set();
  let anchor = null;
  const selected = () => [...sel].sort((a, b) => a - b).map((i) => pl.tracks[i]);
  const bar = el("div", {
    class: "select-bar", hidden: true, role: "toolbar", "aria-label": "Selected tracks",
    onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); clearSelection(); } },
  });
  const marked = new Set();  // rows shown as selected (only those change)
  const syncSelection = () => {
    const want = sel.size > 1 ? sel : new Set();
    for (const i of [...marked]) {
      if (want.has(i)) continue;
      rows[i].classList.remove("selected");
      rows[i].querySelector(".sel-note")?.remove();
      marked.delete(i);
    }
    for (const i of want) {
      if (marked.has(i)) continue;
      rows[i].classList.add("selected");
      rows[i].querySelector("td.title").append(el("span", { class: "sr-only sel-note" }, " (selected)"));
      marked.add(i);
    }
    if (sel.size < 2) { bar.hidden = true; return; }
    const group = selected();
    const playable = group.filter((t) => t.available);
    const what = plural(playable.length, "track");
    const addBtn = el("button", {
      type: "button", class: "btn small primary", disabled: playable.length ? null : true, "aria-haspopup": "menu",
      onclick: (e) => playlistChooser(playable.map((t) => t.id), what, e.currentTarget, e.currentTarget, null, pl.id),
    }, ...iconLabel("plus", "Add to playlist…"));
    bar.replaceChildren(...[  // (replaceChildren would show a null as "null")
      el("span", { class: "count" }, `${plural(sel.size, "track")} selected`),
      addBtn,
      el("button", { type: "button", class: "btn small", disabled: playable.length ? null : true, onclick: () => queueTracks(playable, pl, "next") }, "Play next"),
      el("button", { type: "button", class: "btn small", disabled: playable.length ? null : true, onclick: () => queueTracks(playable, pl, "end") }, "Add to queue"),
      mine ? el("button", { type: "button", class: "btn small danger", onclick: () => removeManyFromPlaylist(pl, [...sel]) }, "Remove") : null,
      el("span", { class: "spacer" }),
      el("button", { type: "button", class: "btn small ghost", onclick: () => { clearSelection(); } }, "Clear"),
    ].filter(Boolean));
    bar.hidden = false;
  };
  const clearSelection = () => {
    const back = bar.contains(document.activeElement);
    sel.clear();
    anchor = null;
    syncSelection();
    if (back) rows.find((r) => r.tabIndex === 0 && !r.hidden)?.focus();
  };
  const selectRun = (to) => {
    const vis = visibleRows();
    const a = vis.indexOf(rows[anchor ?? Number(to.dataset.index)]);
    const b = vis.indexOf(to);
    sel.clear();
    if (a < 0) { sel.add(Number(to.dataset.index)); } else {
      for (const r of vis.slice(Math.min(a, b), Math.max(a, b) + 1)) sel.add(Number(r.dataset.index));
    }
    syncSelection();
  };
  const pick = (tr, e) => {
    const i = Number(tr.dataset.index);
    if (e.shiftKey) { selectRun(tr); return; }
    if (e.metaKey || e.ctrlKey) {
      if (sel.has(i)) sel.delete(i); else sel.add(i);
      anchor = i;
      syncSelection();
      return;
    }
    sel.clear();
    sel.add(i);
    anchor = i;
    syncSelection();
  };
  const groupFor = (tr) => (sel.size > 1 && sel.has(Number(tr.dataset.index)) ? selected() : null);

  const tbody = el("tbody", {
    ondblclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (tr && !e.target.closest(".row-more") && !e.shiftKey && !e.metaKey && !e.ctrlKey) playPlaylist(pl, Number(tr.dataset.index));
    },
    onclick: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      focusRow(tr);
      if (e.target.closest(".row-more")) {
        openTrackMenu(trackOf(tr), pl, e.target.closest(".row-more"), tr, undefined, groupFor(tr));
        return;
      }
      pick(tr, e);
    },
    onmousedown: (e) => { if (e.shiftKey) e.preventDefault(); },  // no text selection on Shift-click
    oncontextmenu: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      const group = groupFor(tr);
      const useful = group ? mine || group.some((t) => t.available) : trackOf(tr).available || mine;
      if (!useful) return;  // nothing to offer: leave the browser's own menu
      e.preventDefault();
      focusRow(tr);
      openTrackMenu(trackOf(tr), pl, tr, tr, { x: e.clientX, y: e.clientY }, group);
    },
    onkeydown: (e) => {
      const tr = e.target.closest("tr[data-index]");
      if (!tr) return;
      if ((e.metaKey || e.ctrlKey) && (e.key === "a" || e.key === "A")) {
        e.preventDefault();
        for (const r of visibleRows()) sel.add(Number(r.dataset.index));
        syncSelection();
        announce(`${plural(sel.size, "track")} selected`);
        return;
      }
      if (e.key === "Escape" && sel.size > 1) {
        e.preventDefault();
        clearSelection();
        announce("Selection cleared");
        return;
      }
      if (e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && !e.altKey) {
        e.preventDefault();
        const vis = visibleRows();
        const next = vis[vis.indexOf(tr) + (e.key === "ArrowUp" ? -1 : 1)];
        if (!next) return;
        // A run starts from this track, unless it extends one it belongs to.
        if (anchor === null || rows[anchor].hidden || !sel.has(Number(tr.dataset.index))) anchor = Number(tr.dataset.index);
        focusRow(next);
        selectRun(next);
        announce(`${plural(sel.size, "track")} selected`);
        return;
      }
      if (e.key === " " && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        e.stopPropagation();  // not play/pause as well
        pick(tr, { metaKey: true });
        return;
      }
      // In my playlists: Alt+arrows move a track, Delete removes it.
      if (mine && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const i = Number(tr.dataset.index);
        moveInPlaylist(pl, i, i + (e.key === "ArrowUp" ? -1 : 1));
        return;
      }
      if (mine && (e.key === "Delete" || e.key === "Backspace")) {
        e.preventDefault();
        if (groupFor(tr)) removeManyFromPlaylist(pl, [...sel]);
        else removeFromPlaylist(pl, Number(tr.dataset.index));
        return;
      }
      const vis = visibleRows();
      const at = vis.indexOf(tr);
      let next = null;
      if (e.key === "ArrowDown") next = vis[at + 1];
      else if (e.key === "ArrowUp") next = vis[at - 1];
      else if (e.key === "Home") next = vis[0];
      else if (e.key === "End") next = vis[vis.length - 1];
      else if (e.key === "PageDown") next = vis[Math.min(at + 15, vis.length - 1)];
      else if (e.key === "PageUp") next = vis[Math.max(at - 15, 0)];
      else if (e.key === "Enter") {
        e.preventDefault();
        playPlaylist(pl, Number(tr.dataset.index));
        return;
      } else if ((e.key === "n" || e.key === "N") && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        if (groupFor(tr)) queueTracks(selected(), pl, "next"); else queueTrack(trackOf(tr), pl, "next");
        return;
      } else if ((e.key === "q" || e.key === "Q") && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        if (groupFor(tr)) queueTracks(selected(), pl, "end"); else queueTrack(trackOf(tr), pl, "end");
        return;
      } else if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
        e.preventDefault();
        if (groupFor(tr) || trackOf(tr).available || mine) openTrackMenu(trackOf(tr), pl, tr, tr, undefined, groupFor(tr));
        return;
      } else return;
      e.preventDefault();
      if (!next) return;
      focusRow(next);
      if (sel.size <= 1) {  // nothing picked yet: the selection follows focus
        sel.clear();
        sel.add(Number(next.dataset.index));
        anchor = Number(next.dataset.index);
      }
    },
  }, rows);
  if (mine) dragToReorder(tbody, pl);
  const empty = el("div", { class: "empty" },
    pl.search ? "Nothing in your library matches." : pl.count ? "No tracks match."
      : mine ? "This playlist is empty. Add tracks with the ⋯ button on any track, or by right-clicking it." : "This playlist is empty.");
  const table = el("table", { class: "tracks" },
    el("caption", { class: "sr-only" },
      `${pl.name}. Use the arrow keys to move between tracks and Enter to play. ` +
      "N plays the track next, Q adds it to the queue. Shift with the arrows selects several tracks, Control or Command A selects all." +
      (mine ? " Alt with the up or down arrow moves a track, Delete removes it." : "")),
    el("thead", {}, el("tr", {},
      el("th", { class: "num" }, "#"), el("th", { class: "title" }, "Title"),
      el("th", { class: "artist" }, "Artist"), el("th", { class: "album" }, "Album"),
      el("th", { class: "dur" }, "Time"), el("th", { class: "more" }, el("span", { class: "sr-only" }, "Actions")))),
    tbody);

  const applyFilter = () => {
    const q = S.filter.toLowerCase();
    let first = null;
    let shown = 0;
    pl.tracks.forEach((t, i) => {
      const hit = matches(t, q);
      rows[i].hidden = !hit;
      rows[i].tabIndex = -1;
      if (hit) { shown++; first = first || rows[i]; } else sel.delete(i);  // only what's showing stays selected
    });
    if (anchor !== null && rows[anchor].hidden) anchor = null;
    syncSelection();
    if (first) first.tabIndex = 0;
    table.hidden = shown === 0;
    empty.hidden = shown !== 0;
  };

  const playable = pl.tracks.filter((t) => t.available).length;
  let meta = `${pl.count} tracks` + (pl.missing ? ` · ${pl.missing} missing` : "");
  if (pl.search) {
    meta = pl.total > pl.count ? `Showing the first ${pl.count} of ${pl.total} tracks` : `${pl.count} tracks`;
  }
  const search = pl.search ? null : el("input", {
    type: "search", class: "search", placeholder: "Filter tracks", value: S.filter,
    "aria-label": "Filter tracks",
    oninput: (e) => { S.filter = e.target.value; applyFilter(); },
  });

  const ids = () => pl.tracks.filter((t) => t.available).map((t) => t.id);
  const small = (label, onclick, extra = {}) => el("button", { type: "button", class: "btn small", onclick, ...extra }, label);
  const exportLink = pl.search ? null : el("a", {
    class: "btn small", href: `/api/playlists/${encodeURIComponent(pl.id)}.m3u`, download: "",
    title: "Save this playlist as an .m3u file other players can open",
  }, "Export M3U");
  const actions = el("div", { class: "head-actions" },
    mine ? small("Rename", () => renamePlaylist(pl)) : null,
    mine ? small("Delete", () => deletePlaylist(pl)) : null,
    !mine && playable ? small(pl.search ? "Save as a playlist" : "Copy to My playlists", async () => {
      const pid = await newPlaylist(ids(), pl.search ? pl.name.replace(/^Search: /, "") : pl.name);
      if (pid) show({ type: "playlist", id: pid });
    }) : null,
    exportLink);

  const view = el("div", {},
    el("div", { class: "view-head" },
      el("div", {}, el("h1", {}, pl.name), el("div", { class: "meta" }, meta), actions),
      el("div", { class: "spacer" }),
      search,
      el("button", {
        type: "button", class: "btn primary", disabled: playable ? null : true,
        onclick: () => playPlaylist(pl, S.shuffle ? null : 0),
      }, ...iconLabel("play", "Play")),
    ),
    !pl.search && pl.missing && S.lib.missing && pl.kind !== "folder" ? missingBanner(pl.missing === pl.count
      ? "None of these tracks were found on this computer"
      : `${pl.missing} of these tracks weren't found on this computer`) : null,
    table,
    bar,
    empty,
  );
  applyFilter();
  queueMicrotask(markPlaying);
  return view;
}

// Highlight the playing row in the visible playlist, if it's the queue's.
function markPlaying() {
  for (const tr of document.querySelectorAll("#view tr.playing")) tr.classList.remove("playing");
  const t = current();
  if (S.mode !== "track" || !t || !S.detail || !S.view.type.match(/^(playlist|search)$/)) return;
  if (t._source !== S.detail.id) return;
  const tr = document.querySelector(`#view tr[data-index="${t._index}"]`);
  if (tr && tr.dataset.id === t.id) tr.classList.add("playing");
}

// Drag a row of one of my playlists to a new place.
function dragToReorder(tbody, pl) {
  let from = null;
  const clear = () => {
    for (const r of tbody.querySelectorAll(".drop-before, .drop-after")) r.classList.remove("drop-before", "drop-after");
  };
  const spot = (e) => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr) return null;
    const box = tr.getBoundingClientRect();
    return { tr, after: e.clientY > box.top + box.height / 2 };
  };
  tbody.addEventListener("dragstart", (e) => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr) return;
    from = Number(tr.dataset.index);
    tr.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", pl.tracks[from].name);
  });
  tbody.addEventListener("dragover", (e) => {
    if (from === null) return;
    const at = spot(e);
    if (!at) return;
    e.preventDefault();
    clear();
    at.tr.classList.add(at.after ? "drop-after" : "drop-before");
  });
  tbody.addEventListener("dragleave", (e) => { if (!tbody.contains(e.relatedTarget)) clear(); });
  tbody.addEventListener("drop", (e) => {
    const at = spot(e);
    clear();
    if (from === null || !at) return;
    e.preventDefault();
    let to = Number(at.tr.dataset.index) + (at.after ? 1 : 0);
    if (to > from) to -= 1;
    const start = from;
    from = null;
    moveInPlaylist(pl, start, to);
  });
  tbody.addEventListener("dragend", () => {
    from = null;
    clear();
    for (const r of tbody.querySelectorAll(".dragging")) r.classList.remove("dragging");
  });
}

// ---------- my playlists ---------------------------------------------------

const isMine = (pl) => Boolean(pl && pl.kind === "mine");
const myPlaylists = () => S.lib.playlists.filter((p) => p.kind === "mine");

// Ask for a name and make a playlist of `ids`. Returns its id, or null.
async function newPlaylist(ids = [], suggested = "") {
  const name = prompt("Name for the new playlist:", suggested);
  if (name === null || !name.trim()) return null;
  try {
    const res = await api("/api/my-playlists", { name, ids }, { quiet: true });
    await loadState(res);
    announce(ids.length ? `Saved ${ids.length} track${ids.length === 1 ? "" : "s"} as ${name.trim()}` : `Made the playlist ${name.trim()}`);
    return res.created;
  } catch (err) {
    renderErrors(err.message);
    return null;
  }
}

async function addToPlaylist(pid, ids, what) {
  const target = S.lib.playlists.find((p) => p.id === pid);
  try {
    await loadState(await api(`/api/my-playlists/${pid}/add`, { ids }, { quiet: true }));
    toast(`Added ${what} to ${target ? target.name : "the playlist"}`);
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  await refreshDetail(undefined, pid);
}

// Save a new order (or fewer tracks) for one of my playlists. Each edit
// is worked out from the list on screen, so further edits wait until the
// list has been shown again.
let savingPlaylist = false;
async function setPlaylistTracks(pid, ids, focusIndex, message) {
  savingPlaylist = true;
  try {
    await loadState(await api(`/api/my-playlists/${pid}/set`, { ids }, { quiet: true }));
    announce(message);
  } catch (err) {
    renderErrors(err.message);
  }
  try {
    await refreshDetail(focusIndex, pid);
  } finally {
    savingPlaylist = false;
  }
}

function moveInPlaylist(pl, from, to) {
  if (savingPlaylist || to < 0 || to >= pl.tracks.length || from === to) return;
  const ids = pl.tracks.map((t) => t.id);
  const [moved] = ids.splice(from, 1);
  ids.splice(to, 0, moved);
  setPlaylistTracks(pl.id, ids, to, `${pl.tracks[from].name} moved to number ${to + 1}`);
}

function removeFromPlaylist(pl, index) {
  if (savingPlaylist) return;
  const ids = pl.tracks.map((t) => t.id);
  ids.splice(index, 1);
  setPlaylistTracks(pl.id, ids, Math.min(index, ids.length - 1), `Removed ${pl.tracks[index].name} from ${pl.name}`);
}

function removeManyFromPlaylist(pl, indexes) {
  if (savingPlaylist || !indexes.length) return;
  const gone = new Set(indexes);
  const ids = pl.tracks.map((t) => t.id).filter((_, i) => !gone.has(i));
  const first = Math.min(...indexes);
  setPlaylistTracks(pl.id, ids, Math.min(first, ids.length - 1), `Removed ${plural(gone.size, "track")} from ${pl.name}`);
}

async function renamePlaylist(pl) {
  const name = prompt("New name for this playlist:", pl.name);
  if (name === null || !name.trim() || name.trim() === pl.name) return;
  try {
    await loadState(await api(`/api/my-playlists/${pl.id}/rename`, { name }, { quiet: true }));
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  await refreshDetail(undefined, pl.id);
  focusHeading();
  announce(`Renamed to ${name.trim()}`);
}

async function deletePlaylist(pl) {
  if (!confirm(`Delete the playlist ${pl.name}? The music itself stays where it is.`)) return;
  try {
    await loadState(await api(`/api/my-playlists/${pl.id}/delete`, {}, { quiet: true }));
  } catch (err) {
    renderErrors(err.message);
    return;
  }
  announce(`Deleted ${pl.name}`);
  const first = S.lib.playlists[0];
  await show(first ? { type: "playlist", id: first.id } : { type: "settings" });
  focusHeading();
}

// Fetch playlist `pid` again after a change, if it's still the one on
// show, keeping the scroll position and (optionally) focusing a row.
async function refreshDetail(focusIndex, pid) {
  if (S.view.type !== "playlist" || S.view.id !== pid) return;
  const token = ++showToken;
  let detail = null;
  try { detail = await api(`/api/playlists/${S.view.id}`); } catch { detail = null; }
  if (token !== showToken || S.view.id !== pid) return;
  if (!detail) { show({ type: "settings" }); return; }
  const top = $("#main").scrollTop;
  S.detail = detail;
  renderView();
  $("#main").scrollTop = top;
  if (focusIndex === undefined) return;
  // That row, else the nearest one showing, else the heading.
  const rows = [...document.querySelectorAll("#view tbody tr")].filter((r) => !r.hidden);
  const tr = rows.find((r) => Number(r.dataset.index) >= focusIndex) || rows[rows.length - 1];
  if (!tr) { focusHeading(); return; }
  for (const r of document.querySelectorAll("#view tbody tr")) r.tabIndex = -1;
  tr.tabIndex = 0;
  tr.focus();
}

// ---------- track menu ------------------------------------------------------

let menuReturn = null;

function closeTrackMenu() {
  const menu = $("#track-menu");
  if (menu.hidden) return;
  menu.hidden = true;
  if (menuReturn && document.contains(menuReturn)) menuReturn.focus();
  menuReturn = null;
}

function menuItem(label, fn) {
  return el("button", {
    type: "button", role: "menuitem", tabindex: "-1",
    onclick: () => { closeTrackMenu(); fn(); },
  }, label);
}

// Show the menu with `items`, by `anchor` (or at the pointer, `at`).
function showMenu(items, label, anchor, returnTo, at) {
  const menu = $("#track-menu");
  menuReturn = returnTo;
  menu.replaceChildren(...items);
  menu.setAttribute("aria-label", label);
  menu.hidden = false;
  const box = anchor.getBoundingClientRect();
  const x = at ? at.x : box.right - menu.offsetWidth;
  const y = at ? at.y : box.bottom;
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - menu.offsetHeight - 8))}px`;
  menu.querySelector("button").focus();
}

// The list of my playlists to add `ids` to (`what` names them).
function playlistChooser(ids, what, anchor, returnTo, at, exclude = null) {
  showMenu([
    el("div", { class: "menu-label", "aria-hidden": "true" }, "Add to playlist"),
    ...myPlaylists().filter((p) => p.id !== exclude).map((p) => menuItem(p.name, () => addToPlaylist(p.id, ids, what))),
    menuItem("New playlist…", () => newPlaylist(ids)),
  ], `Add ${what} to a playlist`, anchor, returnTo, at);
}

// The ⋯ menu for one track, or for `group` (several selected tracks).
function openTrackMenu(track, pl, anchor, returnTo, at, group = null) {
  if (group && group.length > 1) return openGroupMenu(group, pl, anchor, returnTo, at);
  const index = pl.tracks.indexOf(track);
  const items = [];
  if (track.available) {
    items.push(menuItem("Play next", () => queueTrack(track, pl, "next")),
      menuItem("Add to queue", () => queueTrack(track, pl, "end")),
      el("button", {
        type: "button", role: "menuitem", tabindex: "-1", "aria-haspopup": "menu",
        onclick: () => playlistChooser([track.id], track.name, anchor, returnTo, at, pl.id),
      }, "Add to playlist…"));
  }
  if (isMine(pl)) {
    if (items.length) items.push(el("hr", { role: "separator" }));
    if (index > 0) items.push(menuItem("Move up", () => moveInPlaylist(pl, index, index - 1)));
    if (index < pl.tracks.length - 1) items.push(menuItem("Move down", () => moveInPlaylist(pl, index, index + 1)));
    items.push(menuItem("Remove from this playlist", () => removeFromPlaylist(pl, index)));
  }
  if (!items.length) return;
  showMenu(items, track.name, anchor, returnTo, at);
}

function openGroupMenu(group, pl, anchor, returnTo, at) {
  const playable = group.filter((t) => t.available);
  const what = plural(playable.length, "track");
  const items = [];
  if (playable.length) {
    items.push(menuItem(`Play ${what} next`, () => queueTracks(playable, pl, "next")),
      menuItem(`Add ${what} to the queue`, () => queueTracks(playable, pl, "end")),
      el("button", {
        type: "button", role: "menuitem", tabindex: "-1", "aria-haspopup": "menu",
        onclick: () => playlistChooser(playable.map((t) => t.id), what, anchor, returnTo, at, pl.id),
      }, `Add ${what} to a playlist…`));
  }
  if (isMine(pl)) {
    if (items.length) items.push(el("hr", { role: "separator" }));
    items.push(menuItem(`Remove ${plural(group.length, "track")} from this playlist`,
      () => removeManyFromPlaylist(pl, group.map((t) => pl.tracks.indexOf(t)))));
  }
  if (!items.length) return;
  showMenu(items, `${plural(group.length, "track")} selected`, anchor, returnTo, at);
}

$("#track-menu").addEventListener("keydown", (e) => {
  const items = [...$("#track-menu").querySelectorAll("button")];
  const at = items.indexOf(document.activeElement);
  if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); closeTrackMenu(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); items[(at + 1) % items.length].focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); items[(at - 1 + items.length) % items.length].focus(); }
});
document.addEventListener("mousedown", (e) => {
  if (!e.target.closest("#track-menu")) closeTrackMenu();
});
$("#main").addEventListener("scroll", closeTrackMenu);

