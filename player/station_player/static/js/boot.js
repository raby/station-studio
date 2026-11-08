// ---------- boot --------------------------------------------------------

(async function boot() {
  syncButtons();
  updateNow();
  try {
    await loadState();
    // A station's desktop icon started the player: play it now, without
    // waiting for the music library to be read.
    try {
      for (const c of (await api("/api/inbox?only=station")).commands || []) await runCommand(c);
      for (const c of (await api("/api/inbox?only=notice")).commands || []) await runCommand(c);
    } catch { /* checked again below */ }
    // The first scan runs in the background; wait for it before
    // choosing which playlist to show.
    if (S.lib.scanning) {
      $("#view").replaceChildren(el("div", { class: "empty" }, "Reading your music library…"));
      setBusy(1);
      const started = Date.now();
      try {
        while (S.lib.scanning && Date.now() - started < 10 * 60 * 1000) {
          await new Promise((r) => setTimeout(r, 700));
          await loadState();
        }
      } finally {
        setBusy(-1);
      }
      if (S.lib.scanning) {
        renderErrors("Reading your music library is taking a long time. Playlists appear in the sidebar as soon as it finishes.");
        const wait = setInterval(async () => {
          try { await loadState(); } catch { return; }
          if (!S.lib.scanning) clearInterval(wait);
        }, 3000);
      }
    }
  } catch (err) {
    $("#view").replaceChildren(el("div", { class: "errors" }, `Can't reach the player server: ${err.message}`));
    return;
  }
  if (!S.lib.scanning) await restoreSession();
  if (showToken === 0) {  // the user hasn't opened something while it scanned
    // A new window opens on the start page (Radio, unless Settings › App
    // says Home); reloading stays where it was.
    let view = null;
    try { view = JSON.parse(sessionStorage.getItem("sp.view") || "null"); } catch { /* none */ }
    const known = view && view.type && !["search", "welcome"].includes(view.type)
      && (view.type !== "playlist" || S.lib.playlists.some((p) => p.id === view.id));
    // The very first time, with no music added yet: the welcome screen.
    const firstRun = !(S.lib.sources || []).length && !readJSON("view");
    await show(known ? view : firstRun ? { type: "welcome" } : { type: startPage() });
  }
  // A file opened just now (which may be why this window opened) plays;
  // an old request doesn't replay when the player is reopened later.
  await checkInbox({ fresh: 30 });
  setInterval(() => { if ($("#view")) checkInbox(); }, 1000);
  launch.ready = true;
  if (launch.todo) await runLaunch(launch.todo);
  offerMacWindow();
})();
