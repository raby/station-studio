// ---------- settings view -----------------------------------------------

function listSection({ title, help, items, onRemove, form }) {
  return el("section", {},
    el("h2", {}, title),
    el("p", { class: "help" }, help),
    items.length
      ? el("ul", {}, items.map((label, i) => el("li", {},
          el("span", {}, label),
          el("button", {
            type: "button", class: "btn small ghost", "aria-label": `Remove ${label}`,
            onclick: () => onRemove(i),
          }, ...iconLabel("x", "Remove")))))
      : null,
    form);
}

function musicPage() {
  const cfg = S.lib.config;
  const sources = S.lib.sources || [];
  const from = el("input", { placeholder: "From, e.g. /Users/rob/Music/iTunes/iTunes Media", "aria-label": "Path from" });
  const to = el("input", { placeholder: "To, e.g. D:/Music", "aria-label": "Path to" });
  const remapMsg = el("div", { class: "form-msg", role: "status" });
  const addRemap = async (e) => {
    e.preventDefault();
    try {
      await loadState(await api("/api/remaps", { from: from.value, to: to.value }));
      renderView();
      toast("Path rewrite added");
    } catch (err) { remapMsg.textContent = err.message; }
  };
  const remaps = cfg.path_remaps || [];
  return el("div", {},
    S.lib.missing ? missingBanner(`${plural(S.lib.missing, "track")} in your playlists can't be found`) : null,
    el("section", {},
      el("h2", {}, "Your music"),
      el("p", { class: "help" }, "Where Station Player finds your songs and playlists. Removing one here leaves the files where they are."),
      sources.length ? el("div", { class: "sources" }, sources.map(sourceCard))
        : el("p", { class: "help" }, "Nothing yet. Add a folder or some playlists below."),
      addTiles()),
    el("section", { "data-suggestions": "", hidden: true },
      el("h2", {}, "Found on this computer"),
      suggestionList()),
    el("details", { class: "advanced", open: remaps.length ? true : null },
      el("summary", {}, "Advanced"),
      listSection({
        title: "Path rewrites",
        help: "Find them… above usually sets these up for you. When a playlist was made on another computer, the start of each track's path is rewritten so it points at your copy of the music.",
        items: remaps.map((r) => `${r.from}  →  ${r.to}`),
        onRemove: (i) => act("/api/remaps/remove", { from: remaps[i].from, to: remaps[i].to }, "Path rewrite removed"),
        form: el("div", {}, el("form", { class: "form-row", onsubmit: addRemap }, from, to,
          el("button", { class: "btn" }, "Add")), remapMsg),
      }),
      el("section", {},
        el("h2", {}, "Rescan"),
        el("p", { class: "help" }, "Station Player notices new music by itself. Rescan reads everything again now."),
        el("button", {
          type: "button", class: "btn", "data-busy-disable": true, disabled: pending > 0 || null,
          onclick: () => act("/api/rescan", {}, "Library rescanned"),
        }, ...iconLabel("refresh", "Rescan")))));
}

function alarmPage() {
  return el("div", {},
    el("section", {},
      el("h2", {}, "Alarms"),
      el("p", { class: "help" }, "Alarms now live on the Clock page, with a big clock and night mode for the bedside."),
      el("button", { type: "button", class: "btn", id: "btn-open-clock", onclick: () => show({ type: "clock" }) },
        ...iconLabel("clock", "Open the Clock page"))),
    el("section", {},
      el("h2", {}, "Sleep timer"),
      el("p", { class: "help" },
        "To fall asleep to music, choose a time from the sleep menu beside the volume, at the bottom right. ",
        "Music fades out over the last 20 seconds and stops. You can set it from the phone remote too.")));
}

const SETTINGS_TABS = [
  ["music", "Music", "music", musicPage],
  ["playback", "Playback", "headphones", () => playbackSection()],
  ["radio", "Radio", "radio", () => tunerSettings()],
  ["alarm", "Alarm & sleep", "clock", alarmPage],
  ["remote", "Phone remote", "phone", () => remoteSection()],
  ["car", "In the car", "car", () => carSection()],
  ["log", "Play log", "log", () => playLogSection()],
  ["app", "App", "app", () => appSection()],
];

function openSettingsTab(id) {
  S.view = { type: "settings", tab: id };
  store("view", JSON.stringify(S.view));
  renderNav();
  renderErrors();
  renderView();
  $("#main").scrollTop = 0;
  const tab = $(`#tab-${id}`);
  if (tab) tab.focus();
}

function settingsView() {
  const shown = SETTINGS_TABS.filter((t) => studio() || t[0] !== "log");
  const page = shown.find((t) => t[0] === S.view.tab) || shown[0];
  const ids = shown.map((t) => t[0]);
  const tabs = el("div", {
    class: "tabs", role: "tablist", "aria-label": "Settings pages",
    onkeydown: (e) => {
      const at = ids.indexOf(page[0]);
      let to = null;
      if (e.key === "ArrowRight") to = (at + 1) % ids.length;
      else if (e.key === "ArrowLeft") to = (at - 1 + ids.length) % ids.length;
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = ids.length - 1;
      if (to === null) return;
      e.preventDefault();
      openSettingsTab(ids[to]);
    },
  }, shown.map(([id, label, glyph]) => el("button", {
    type: "button", role: "tab", class: "tab", id: `tab-${id}`, "aria-controls": "settings-page",
    "aria-selected": String(id === page[0]), tabindex: id === page[0] ? "0" : "-1",
    onclick: () => openSettingsTab(id),
  }, ...iconLabel(glyph, label))));
  return el("div", { class: "settings" },
    el("div", { class: "view-head" }, el("h1", {}, "Settings")),
    tabs,
    el("div", { role: "tabpanel", id: "settings-page", "aria-labelledby": `tab-${page[0]}` }, page[3]()),
    el("p", { class: "help version" }, `Station Player ${S.lib.version || ""}`));
}

// A checkbox that redraws the view afterwards and keeps focus on itself.
function checkbox(key, label, checked, change) {
  return el("label", { class: "check" },
    el("input", {
      type: "checkbox", checked: checked || null, "data-key": key,
      onchange: async (e) => {
        await change(e.target.checked);
        renderView();
        const again = document.querySelector(`#view input[data-key="${key}"]`);
        if (again) again.focus();
      },
    }),
    el("span", {}, label));
}

// Shortcuts and start-at-login, worded for the computer it runs on.
function appSection() {
  const d = S.lib.desktop;
  const where = d && {
    windows: "Add Station Player to the Start menu and the desktop",
    mac: "Add Station Player to your Applications folder (find it in Launchpad and Spotlight)",
    linux: "Add Station Player to your apps menu and the desktop",
  }[d.platform];
  const names = { shortcut: "Shortcut", login: "Start at login" };
  // Redraw from what the server reports afterwards (unticking a failed change).
  const desktopChange = (key) => async (on) => {
    try {
      await loadState(await api("/api/desktop", { [key]: on }));
      toast(`${names[key]} turned ${on ? "on" : "off"}`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const updatesChange = async (on) => {
    try {
      await loadState(await api("/api/settings", { check_updates: on }));
      toast(`New version notices turned ${on ? "on" : "off"}`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const windowChange = async (on) => {
    try {
      await loadState(await api("/api/settings", { own_window: on }, { quiet: true }));
      toast(`Own window turned ${on ? "on" : "off"}. This applies the next time Station Player opens.`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  const browser = S.lib.window_browser;
  // Installed as an app, the player gets its own name and icon in the
  // Dock / taskbar and Alt-Tab, instead of being one of the browser's windows.
  const install = installPrompt ? el("p", {},
    el("button", {
      type: "button", class: "btn", id: "btn-install",
      onclick: async () => {
        const prompt = installPrompt;
        installPrompt = null;  // each offer can be used once
        prompt.prompt();
        await prompt.userChoice.catch(() => null);
        if (S.view.type === "settings") renderView();
      },
    }, "Install Station Player as an app"),
    el("span", { class: "help" }, " so it has its own icon in the Dock (or taskbar) and in Alt-Tab.")) : null;
  return el("section", {},
    el("h2", {}, "App"),
    install,
    d && d.supported && !d.bundled ? el("p", { class: "help" },
      "Open Station Player like any other app, without a terminal. Turn these off before uninstalling.") : null,
    el("div", { class: "checks" },
      d && d.supported && !d.bundled ? checkbox("shortcut", where, d.shortcut, desktopChange("shortcut")) : null,
      d && d.supported ? checkbox("login", "Start Station Player in the background when I log in", d.login, desktopChange("login")) : null,
      S.lib.mac_app ? checkbox("window", "Open in Station Player's own window, not a browser tab",
        S.lib.config.own_window !== false, windowChange)
        : browser ? checkbox("window", `Open in a window of its own (using ${browser}), not a browser tab`,
          S.lib.config.own_window !== false, windowChange) : null,
      checkbox("updates", "Tell me when a new version is out (asks GitHub once a day)",
        S.lib.config.check_updates !== false, updatesChange)),
    el("div", { class: "form-row" },
      el("label", { for: "start-page" }, "Open on"),
      el("select", {
        id: "start-page",
        onchange: (e) => { store("start-page", e.target.value); toast(`Station Player will open on ${e.target.selectedOptions[0].textContent}`); },
      }, ...[["radio", "the Radio page"], ["home", "Home"]].map(([v, label]) => el("option", { value: v, selected: startPage() === v }, label)))),
    updateSettings(),
    browser || S.lib.mac_app ? null : el("p", { class: "help" },
      "Station Player opens in a browser tab. With Google Chrome, Microsoft Edge or Brave installed it opens in a window of its own instead."),
    el("h2", { class: "spaced" }, "Studio tools"),
    el("p", { class: "help" },
      "For people who make a radio station: playlists exported from station-studio or iTunes, recording radio, ",
      "the Recordings page, and the play log for PRS and PPL. Off, Station Player is a simple radio and music player."),
    el("div", { class: "checks" },
      checkbox("studio", "Show the studio tools", studio(), async (on) => {
        try {
          await loadState(await api("/api/settings", { studio: on }, { quiet: true }));
          toast(on ? "Studio tools turned on" : "Studio tools turned off. Your recordings and playlists are kept.");
        } catch (err) {
          renderErrors(err.message);
        }
      })));
}

// Where a new window opens: the Radio page, or Home.
const startPage = () => (store("start-page") === "home" ? "home" : "radio");

// Settings › App: this version, Check for updates, and Update to the new one.
function updateSettings() {
  const latest = S.lib.update;
  const u = S.lib.updating;
  const busy = u && u.stage !== "failed";
  let line;
  if (busy) line = updateStageText(u);
  else if (latest) line = `Station Player ${latest} is out. You have ${S.lib.version}.`;
  else if (U.checked) line = `You have the newest version, ${S.lib.version}.`;
  else line = `You have Station Player ${S.lib.version}.`;
  return el("div", { id: "update-settings", class: "update-settings" },
    el("p", { role: "status" }, line),
    u && u.stage === "failed" ? updateFailure(u) : null,
    el("div", { class: "row" },
      latest && !busy ? el("button", {
        type: "button", class: "btn primary", id: "btn-update", onclick: () => startUpdate(latest),
      }, `Update to ${latest}`) : null,
      busy ? null : el("button", {
        type: "button", class: "btn", id: "btn-check-update",
        onclick: async (e) => {
          e.currentTarget.disabled = true;
          try {
            await loadState(await api("/api/update/check", {}, { quiet: true }));
            U.checked = true;
            if (!S.lib.update) toast("Station Player is up to date");
          } catch (err) {
            toast(err.message, { error: true });
          }
          syncUpdateSettings();
          $("#btn-update, #btn-check-update")?.focus();
        },
      }, "Check for updates")),
    el("p", { class: "help" }, S.lib.bundled
      ? "Updating downloads the new version from Station Player's GitHub page, checks it's the genuine file, and restarts Station Player. Your stations, playlists and settings are kept."
      : "Updating runs pip to install the new version, then restarts Station Player. Your stations, playlists and settings are kept."));
}
const U = { checked: false };

function syncUpdateSettings() {
  const old = $("#update-settings");
  if (old) old.replaceWith(updateSettings());
}

// Which of your stations sit on the Radio page's tuner dial.
const onTuner = (st) => st.tuner !== false;
function tunerSettings() {
  const stations = S.lib.stations || [];
  const change = (st) => async (on) => {
    try {
      await loadState(await api("/api/stations/update", { url: st.url, tuner: on }, { quiet: true }));
      toast(on ? `${st.name} is on the tuner` : `${st.name} is off the tuner`);
    } catch (err) {
      renderErrors(err.message);
    }
  };
  return el("section", {},
    el("h2", {}, "On the tuner"),
    el("p", { class: "help" },
      "The dial across the top of the Radio page. Tick the stations you want on it; the rest still have their cards below it. ",
      "They sit in the order of your stations: change it with Up and Down when you edit a station. The tuner shows once two or more are ticked."),
    stations.length
      ? el("div", { class: "checks tuner-checks" }, stations.map((st, i) => checkbox(`tuner-${i}`, st.name, onTuner(st), change(st))))
      : el("p", { class: "help" }, "Add stations on the Radio page first."));
}

function playbackSection() {
  const label = (v) => (v ? `${v} s` : "Off");
  const out = el("output", { for: "crossfade" }, label(X.secs));
  const slider = el("input", {
    type: "range", id: "crossfade", min: 0, max: MAX_CROSSFADE, step: 1, value: X.secs,
    "aria-valuetext": label(X.secs),
    oninput: (e) => {
      setCrossfade(Number(e.target.value));
      out.textContent = label(X.secs);
      e.target.setAttribute("aria-valuetext", X.secs ? `${X.secs} seconds` : "Off");
    },
  });
  return el("section", {},
    el("h2", {}, "Playback"),
    el("p", { class: "help" },
      "Tracks play back to back with no gap, or, with crossfade, the next one fades in as the last one fades out. ",
      "Tracks from the same album and short ones (jingles, idents) are never crossfaded."),
    el("div", { class: "range-row" }, el("label", { for: "crossfade" }, "Crossfade"), slider, out),
    el("p", { class: "help" },
      "Volume levelling plays every track at about the same loudness, ",
      "using ReplayGain or Sound Check information where a file has it and measuring the rest the first time they play."),
    el("div", { class: "checks" },
      checkbox("level", "Even out the volume between tracks", L.on, async (on) => {
        setLevelling(on);
        toast(`Volume levelling turned ${on ? "on" : "off"}`);
      })));
}

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

async function saveSetting(body, message) {
  try {
    await loadState(await api("/api/settings", body, { quiet: true }));
    toast(message);
    return true;
  } catch (err) {
    renderErrors(err.message);
    return false;
  }
}

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// The play log: what played and when, as a spreadsheet for music returns.
function playLogSection() {
  const now = new Date();
  // Last month, the usual reporting period.
  const from = el("input", { type: "date", id: "log-from", value: isoDate(new Date(now.getFullYear(), now.getMonth() - 1, 1)) });
  const to = el("input", { type: "date", id: "log-to", value: isoDate(new Date(now.getFullYear(), now.getMonth(), 0)) });
  const link = el("a", { class: "btn", id: "log-download", download: "" }, "Download play log (CSV)");
  const update = () => { link.href = `/api/playlog.csv?from=${from.value}&to=${to.value}`; };
  from.addEventListener("change", update);
  to.addEventListener("change", update);
  update();
  return el("section", {},
    el("h2", {}, "Play log"),
    el("p", { class: "help" },
      "A record of every track played, for PRS for Music and PPL returns: date and time, title, artist, album, ",
      "composer, label and ISRC (from the file's tags) and how long it played. Plays under 30 seconds and radio aren't included. ",
      "It stays on this computer."),
    el("div", { class: "checks" },
      checkbox("play_log", "Keep a log of the tracks I play", S.lib.config.play_log !== false,
        (on) => saveSetting({ play_log: on }, `Play log turned ${on ? "on" : "off"}`))),
    el("div", { class: "fields" },
      el("label", { class: "field" }, "From", from),
      el("label", { class: "field" }, "To", to),
      link));
}

// The phone remote: a link (and QR code) for a phone on the same Wi-Fi.
function carSection() {
  // The car page lives on the web, so it works on mobile data far from
  // this computer; the stations go to it inside the link (see car/car.js).
  const more = el("div", {});
  let link = null;
  const copy = el("button", {
    type: "button", class: "btn", id: "btn-car-copy", disabled: true,
    onclick: async () => {
      // Copied straight from the click (no fetch first), as Safari wants.
      try {
        await navigator.clipboard.writeText(link);
        toast("Link copied: send it to your phone by message or email");
      } catch { toast("Couldn't copy the link", { error: true }); }
    },
  }, "Copy the link");
  api("/api/car").then(({ url, http_only: httpOnly }) => {
    link = url;
    copy.disabled = false;
    if (httpOnly && httpOnly.length) {
      more.replaceChildren(el("p", { class: "help" },
        `${httpOnly.join(", ")} ${httpOnly.length > 1 ? "have" : "has"} only an http:// link, which phones may not play from the car page. `,
        "If the station has an https:// link, edit the station and use that."));
    }
  }).catch(() => {});
  return el("section", {},
    el("h2", {}, "In the car"),
    el("p", { class: "help" },
      "Take your radio stations in the car. Scan this code with your phone's camera: it opens a page with big buttons that plays your stations over the phone's mobile data, ",
      "so the computer doesn't need to be on. Then add it to the phone's home screen."),
    el("p", { class: "help" },
      "Connect the phone to the car by Bluetooth, CarPlay or Android Auto and the sound comes through the car's speakers. The steering-wheel buttons skip between stations."),
    el("div", { class: "remote-box car-box" },
      el("img", { src: `/api/car/qr.svg?${Date.now()}`, alt: "QR code for the car page with your stations" }),
      el("div", {},
        el("p", {}, "Can't scan it? Copy the link and send it to your phone."),
        copy,
        el("p", { class: "help" }, "Added or changed stations? Scan the new code to update the phone."))),
    more);
}

function remoteSection() {
  const r = S.lib.remote || {};
  const box = [];
  if (r.on && r.url) {
    box.push(el("div", { class: "remote-box" },
      el("img", { src: `/api/remote/qr.svg?${encodeURIComponent(r.url.slice(-8))}`, alt: "QR code for the phone remote link" }),
      el("div", {},
        el("p", {}, "Scan this with your phone's camera, or open:"),
        el("p", { class: "remote-link" }, r.url),
        el("button", {
          type: "button", class: "btn", id: "btn-new-link",
          onclick: async () => {
            try {
              await loadState(await api("/api/remote/new-link", {}, { quiet: true }));
              renderView();
              toast("New link made; the old one no longer works");
              const again = $("#btn-new-link");
              if (again) again.focus();
            } catch (err) { renderErrors(err.message); }
          },
        }, "Make a new link"),
        el("p", { class: "help" }, "The old link stops working."))));
  } else if (r.on && r.error) {
    box.push(el("p", { class: "errors" }, r.error));
  } else if (r.on) {
    box.push(el("p", { class: "help" }, "This computer doesn't seem to be on a network right now."));
  }
  return el("section", {},
    el("h2", {}, "Phone remote"),
    el("p", { class: "help" },
      "Play, pause, skip, change the volume and choose what plays from a phone on the same Wi-Fi. ",
      "The music still plays here. Anyone with the link can use it while they're on your network, so keep it to people you trust. ",
      "Your computer may ask whether to allow incoming connections: allow them."),
    el("div", { class: "checks" },
      checkbox("remote", "Turn on the phone remote", Boolean(r.on),
        (on) => saveSetting({ remote: on }, `Phone remote turned ${on ? "on" : "off"}`))),
    ...box);
}

