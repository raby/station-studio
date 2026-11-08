// ---------- up next view -------------------------------------------------

// The tracks that will play after the current one, from the playlist (not
// counting the queue), as far as `limit`.
function comingFromQueue(limit) {
  const out = [];
  const n = S.order.length;
  if (S.mode !== "track" || !n || S.pos < 0) return out;
  let pos = S.pos;
  for (let steps = 0; steps < n && out.length < limit; steps++) {
    pos += 1;
    if (pos >= n) {
      if (S.repeat !== "all") break;
      pos = 0;
    }
    if (pos === S.pos) break;  // round to the playing track again (repeat all)
    const t = S.queue[S.order[pos]];
    if (t.available) out.push(t);
  }
  return out;
}

function upNextView() {
  const t = S.mode === "track" ? current() : null;
  const line = (track) => el("span", {}, track.name, track.artist ? el("span", { class: "muted" }, ` · ${track.artist}`) : null);
  const queued = S.upNext.map((track, i) => el("li", {},
    line(track),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Move ${track.name} up`, disabled: i === 0 || null,
      onclick: () => moveQueued(i, -1),
    }, icon("up")),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Move ${track.name} down`, disabled: i === S.upNext.length - 1 || null,
      onclick: () => moveQueued(i, 1),
    }, icon("down")),
    el("button", {
      type: "button", class: "btn small", "aria-label": `Remove ${track.name} from the queue`,
      onclick: () => { S.upNext.splice(i, 1); queueChanged(); announce(`Removed ${track.name}`); },
    }, "Remove")));
  const coming = comingFromQueue(30);
  const all = [...(t ? [t] : []), ...S.upNext, ...comingFromQueue(Infinity)];
  return el("div", { class: "settings upnext" },
    el("div", { class: "view-head" }, el("h1", {}, "Up next"), el("div", { class: "spacer" }),
      all.length ? el("button", {
        type: "button", class: "btn",
        title: "The playing track, your queue and the rest of the playlist, in play order",
        onclick: async () => {
          const pid = await newPlaylist(all.map((x) => x.id), S.queueLabel && S.queueLabel !== "your queue" ? `${S.queueLabel} (my order)` : "");
          if (pid) show({ type: "playlist", id: pid });
        },
      }, "Save as a playlist") : null),
    el("section", {},
      el("h2", {}, "Now playing"),
      t ? el("p", {}, line(t)) : el("p", { class: "help" }, S.mode === "radio" ? `${S.station.name} (radio)` : "Nothing yet.")),
    el("section", {},
      el("h2", {}, "Your queue"),
      el("p", { class: "help" }, "Tracks you chose with Play next or Add to queue play before the rest of the playlist. ",
        "Use the ⋯ button on a track, right-click it, or press N or Q on it."),
      queued.length ? el("ul", {}, queued) : el("p", { class: "help" }, "Empty."),
      queued.length ? el("button", {
        type: "button", class: "btn",
        onclick: () => { S.upNext = []; queueChanged(); announce("Queue cleared"); },
      }, "Clear queue") : null),
    coming.length ? el("section", {},
      el("h2", {}, `Then from ${S.queueLabel}`),
      el("ol", { class: "coming" }, coming.map((c) => el("li", {}, line(c))))) : null,
  );
}

function moveQueued(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= S.upNext.length) return;
  [S.upNext[i], S.upNext[j]] = [S.upNext[j], S.upNext[i]];
  queueChanged();
  announce(`${S.upNext[j].name} moved ${dir < 0 ? "up" : "down"}`);
}

// After the queue or the playing track changes: redraw what shows it,
// line up the next track and remember where we are.
function queueChanged() {
  $("#upnext-count").textContent = S.upNext.length || "";
  if (S.view.type === "upnext") {
    const active = document.activeElement;
    const inView = active && $("#view").contains(active);
    const label = inView && active.getAttribute("aria-label");
    renderView();
    if (inView) {
      let again = label && [...document.querySelectorAll("#view button")].find((b) => b.getAttribute("aria-label") === label);
      // "Move up" on what is now the first item is disabled: use the
      // next button along on the same item.
      if (again && again.disabled) again = [...again.parentElement.querySelectorAll("button")].find((b) => !b.disabled);
      if (again) again.focus();
      else focusHeading();
    }
  }
  preloadNext();
  saveSession();
}

