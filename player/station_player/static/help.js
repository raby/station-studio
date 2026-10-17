// The Help page: a short guide to everything Station Player does, for
// someone using it for the first time. Uses el(), icon() and show()
// from app.js, which is loaded after this file.

const HELP = [
  ["start", "Getting started", [
    "Station Player plays your music, the playlists Station Studio makes and internet radio, all in one window.",
    ["Add music in the sidebar shows the music it found on your computer. Click ", ["Add"], " beside a folder, or choose one yourself with ", ["Add a music folder"], "."],
    "Each music folder appears under Folders in the sidebar as a playlist of its own. Station Studio's weekly playlists appear by themselves once Station Studio has exported them.",
    "Close the window and open it again and you're back where you left off: the same playlist and track, paused at the same point.",
    ["To open it like any other app, turn on the shortcut in ", ["Settings › App"], ". Turn on ", ["Start Station Player in the background when I log in"], " too and it opens instantly, and alarms work."],
  ], { label: "Add music", view: { type: "welcome" } }],
  ["music", "Playing music", [
    ["Double-click a track to play from there, or press ", ["Play"], " for the whole playlist. Shuffle and repeat sit beside the play button."],
    "Space plays and pauses, and the left and right arrows skip 10 seconds. Your keyboard's media keys and the computer's own media controls work too.",
    ["Search library at the top of the sidebar (or press /) looks through every track you have by title, artist, album or genre."],
    ["Up next: the ⋯ button on a track has ", ["Play next"], " and ", ["Add to queue"], ". Up next in the sidebar shows what's queued and lets you reorder it."],
    ["Settings › Playback has ", ["Crossfade"], " (one track fades into the next) and ", ["Volume levelling"], " (every track at about the same loudness)."],
    "The sleep timer beside the volume stops the music after a while, fading out gently.",
    ["Installed as an app (", ["Install Station Player as an app"], " in Settings › App), right-click its icon in the Dock or taskbar for ", ["Play / Pause"], " and ", ["Next"], "."],
  ]],
  ["playlists", "Your own playlists", [
    ["Make one with + next to My playlists in the sidebar, then use ", ["Add to playlist…"], " from a track's ⋯ menu."],
    "To add several tracks at once, Shift-click the first and last, or Cmd-click (Ctrl-click on Windows) each one. A bar appears with what you can do with them.",
    "In your own playlists, drag tracks to put them in order, and press Delete to take one out.",
    ["Export M3U saves a playlist as a file other players, like VLC or Music, can open."],
  ]],
  ["radio", "Radio", [
    "Stations in the sidebar lists your radio stations. Rebirth Radio and Rebirth Classics are there to start with.",
    ["Find stations searches thousands of stations from around the world by name, genre or country. ", ["Listen"], " tries one, and ", ["Add"], " keeps it. A station you add while browsing a genre is tagged with that genre."],
    "When a station sends the name of the song it's playing, you'll see it on the station and in the bar at the bottom.",
    ["To add a station by its web address, paste its stream link under ", ["Add a station by its link"], ". ", ["Test"], " checks it plays first."],
    ["The pencil on a station renames it, changes its link, gives it a logo, moves it up or down, or removes it."],
    "Recently played lists the last few stations you listened to, so one you tried is easy to find again.",
  ], { label: "Go to Stations", view: { type: "radio" } }],
  ["tags", "Tagging stations", [
    ["Give your stations your own labels, like ", ["jazz"], ", ", ["chill"], " or ", ["Sunday morning"], ". Edit a station with the pencil and type its tags with commas between them, or click a tag you've used before to add it."],
    "Your tags appear as buttons above your stations. Click one to see only the stations with that tag, and All to see them all again.",
  ]],
  ["desktop", "A desktop icon for a station", [
    ["Edit a station with the pencil and press ", ["Put on desktop"], ". An icon with the station's logo appears on your desktop."],
    "Double-click it and Station Player opens and plays that station straight away, even when the player wasn't running.",
    "To get rid of one, drag it to the Bin (the Recycle Bin on Windows) like any other icon.",
  ]],
  ["record", "Recording radio", [
    ["Record on a station records it to a file, whether or not you're listening. Press it again to stop. A recording stops by itself after 12 hours."],
    ["Recordings in the sidebar lists everything you've recorded, with the songs it caught. Give one a label and tags with the pencil, ", ["Play"], " it, or add it to a playlist with +."],
  ], { label: "Go to Recordings", view: { type: "recordings" } }],
  ["clock", "Clock and alarms", [
    "Clock & alarms in the sidebar shows a big clock and your alarms. Wake up to any playlist or station, on the days you choose.",
    ["When an alarm goes off, press ", ["Snooze 9 min"], " for nine more minutes or ", ["Stop"], ". Night mode shows a dim clock for the bedside."],
    "Station Player has to be running and the computer awake at alarm time, so turn on the start-at-login switch in Settings › App.",
  ], { label: "Go to Clock & alarms", view: { type: "clock" } }],
  ["remote", "Phone remote", [
    ["Turn on ", ["Phone remote"], " in Settings and scan the code with your phone's camera. Your phone becomes a remote control for the player: play, pause, skip, volume, your playlists and stations."],
    "The phone has to be on the same Wi-Fi. The first time, your computer may ask whether to allow incoming connections: say yes. The sound still comes from the computer.",
  ]],
  ["log", "Play log for PRS and PPL", [
    ["Station Player notes every track it plays. ", ["Download play log"], " in Settings saves the plays between two dates as a spreadsheet, ready for a PRS for Music or PPL return. Radio isn't included, nor tracks skipped within 30 seconds."],
  ]],
  ["trouble", "When something isn't right", [
    ["Tracks greyed out: their music files have moved. In ", ["Settings › Music"], ", press ", ["Find them…"], " and choose the folder the music is in now."],
    ["A station won't play: edit it and press ", ["Test"], ". Stations change their links from time to time; Find stations usually has the new one."],
    "The window says it can't reach the player: Station Player was quit. Open it again from its icon.",
    "Updates: when a new version is out, the sidebar says so. To update, open Terminal (on Windows, Command Prompt), type pipx upgrade station-player, press Return, then open Station Player again.",
  ]],
];

function helpText(part) {
  // A paragraph is a string, or a list of text and [names of buttons],
  // which are shown in bold.
  if (typeof part === "string") return [part];
  return part.map((bit) => (Array.isArray(bit) ? el("strong", {}, bit[0]) : bit));
}

function helpView() {
  const jump = (id) => {
    const target = document.getElementById(`help-${id}`);
    if (!target) return;
    target.scrollIntoView({ block: "start" });
    target.querySelector("h2").focus();
  };
  return el("div", { class: "help-page" },
    el("div", { class: "view-head" }, el("h1", {}, "Help")),
    el("p", { class: "help-intro" }, "Everything Station Player does, in a few lines each. Click a topic to jump to it."),
    el("nav", { class: "help-toc", "aria-label": "Help topics" },
      HELP.map(([id, title]) => el("button", { type: "button", class: "chip", onclick: () => jump(id) }, title))),
    HELP.map(([id, title, parts, go]) => el("section", { class: "help-section", id: `help-${id}` },
      el("h2", { tabindex: "-1" }, title),
      parts.map((p) => el("p", {}, ...helpText(p))),
      go ? el("button", { type: "button", class: "btn", onclick: () => show(go.view) }, go.label) : null)),
    el("p", { class: "help-version" }, `Station Player ${S.lib && S.lib.version ? S.lib.version : ""}`));
}
