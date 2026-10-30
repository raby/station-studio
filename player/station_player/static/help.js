// The Help page: a short guide to everything Station Player does, for
// someone using it for the first time. Uses el(), icon() and show()
// from app.js, which is loaded after this file.

const HELP = [
  ["start", "Getting started", [
    "Station Player plays your music, the playlists Station Studio makes and internet radio, all in one window.",
    ["Add music in the sidebar shows the music it found on your computer. Click ", ["Add"], " beside a folder, or choose one yourself with ", ["Add a music folder"], "."],
    "Each music folder appears under Folders in the sidebar as a playlist of its own. Station Studio's weekly playlists appear by themselves once Station Studio has exported them.",
    "Station Player opens on the Radio page (Settings › App › Open on can make it Home instead). Home has what's on now from Rebirth (it changes with the time of day), the stations you played lately, your stations, and your music. Click a tile to play it.",
    "Close the window and open it again and the same track is ready, paused at the same point.",
    ["To open it like any other app, turn on the shortcut in ", ["Settings › App"], ". Turn on ", ["Start Station Player in the background when I log in"], " too and it opens instantly, and alarms work."],
    ["The Mac app works like Music: closing its window keeps the music playing, clicking Station Player in the Dock brings the window back, and ", ["Quit"], " (⌘Q) stops it. Right-click its Dock icon for ", ["Play / Pause"], " and ", ["Next"], "."],
  ], { label: "Add music", view: { type: "welcome" } }],
  ["music", "Playing music", [
    ["Double-click a track to play from there, or press ", ["Play"], " for the whole playlist. Shuffle and repeat sit beside the play button."],
    "Space plays and pauses, and the left and right arrows skip 10 seconds. Your keyboard's media keys and the computer's own media controls work too.",
    ["Search library at the top of the sidebar (or press /) looks through every track you have by title, artist, album or genre."],
    ["Up next: the ⋯ button on a track has ", ["Play next"], " and ", ["Add to queue"], ". Up next in the sidebar shows what's queued and lets you reorder it."],
    ["Settings › Playback has ", ["Crossfade"], " (one track fades into the next) and ", ["Volume levelling"], " (every track at about the same loudness)."],
    "The sleep timer beside the volume stops the music after a while, fading out gently.",
    ["The ", ["Pop out the player"], " button at the right of the player bar takes the bar out into a small window of its own that stays on top of your other windows. Move it anywhere and make it bigger or smaller; ", ["Put the player back"], " (or closing it) brings it home. In the Mac app it's also View › Mini Player (⇧⌘M). It needs the Mac app, Chrome or Edge."],
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
    ["Discover in the sidebar finds new stations: turn the globe to hear the news, talk, music or sport from anywhere in the world, or search thousands of stations by name, genre or country. ", ["Listen"], " tries one, and ", ["Add"], " keeps it. A station you add while browsing a genre is tagged with that genre."],
    "When a station sends the name of the song it's playing, you'll see it on the station and in the bar at the bottom.",
    ["A station run by Station Server can also say what's on now, what's next and what's just played, even before you listen. Edit it with the pencil and give ", ["What's on"], " the feed address from its Station Server (Settings › Website): it shows on the station, and the next song shows in the bar at the bottom."],
    ["To add a station by its web address, paste its stream link under ", ["Add a station by its link"], ". ", ["Test"], " checks it plays first."],
    ["The pencil on a station renames it, changes its link, gives it a logo, moves it up or down, or removes it."],
    "Recently played lists the last few stations you listened to, so one you tried is easy to find again.",
  ], { label: "Go to Stations", view: { type: "radio" } }],
  ["tuner", "The tuner", [
    "The dial at the top of the Stations page is an old radio's tuner, with your stations along it, each with its logo and a short line to its spot on the dial. With lots of stations only the logos show; point at one to see its name. Click a station, or drag the needle along the dial and let go near a station; you'll hear static on the way.",
    "With the tuner selected, Left and Right tune to the station before or after. The line moving beside the song title at the bottom follows the sound.",
    "To choose which stations are on the dial, go to Settings › Radio and tick the ones you want.",
  ]],
  ["globe", "The globe", [
    ["Under Find stations, ", ["Globe"], " shows stations as dots on a world globe. Discover opens on it. Drag to turn it, and scroll or pinch to zoom. Tap a dot to see its name and country, with the station first in the list underneath; tap it again, or its ", ["Listen"], ", to hear it. The stations nearest the middle are listed underneath, with ", ["Add"], " to keep one."],
    ["Zoom in on a country and it finds more stations there. ", ["Music"], " leaves out news and talk, and can show just one genre, like techno or Afrobeats around the world. ", ["News"], " shows only news and talk stations, so you can hop from London to Tokyo for the headlines, and ", ["Sport"], " shows sport stations: football, cricket, racing and more."],
    "With the globe selected, the arrow keys turn it, plus and minus zoom, and Enter picks the station in the middle, and Enter again plays it.",
  ]],
  ["ambient", "Ambient mode", [
    ["Ambient in the sidebar fills the screen with a calm scene and its sound: ", ["Waves"], " on a shore, ", ["Rain"], " on the window, a ", ["Stream"], " in the woods, ", ["Birds"], " at dawn, a summer ", ["Night"], " with crickets, a log ", ["Fire"], ", or a ", ["Fish tank"], "."],
    "Where Station Player has a real recording for a scene it plays that, with who recorded it shown underneath; otherwise it makes the sound itself as it plays.",
    "It plays on its own or quietly under the radio, with its own volume. Set the sleep timer and it fades out with everything else.",
    "Press Escape or Close to leave. The controls hide after a few seconds; move the mouse to bring them back.",
  ]],
  ["schedule", "Switching station at set times", [
    ["Under ", ["Schedule"], " on the Stations page, ", ["Add a switch"], " changes to a station or playlist at a time you choose, every week on the days you pick or ", ["Once"], " on a date."],
    "For the news at five and then back to your station, add two: 17:00 to the news station, and 17:10 back to yours.",
    "A switch only changes what's already playing; it doesn't start anything when Station Player is quiet.",
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
  ["car", "Radio in the car", [
    ["In ", ["Settings › In the car"], ", scan the code with your phone's camera. It opens a page on the phone with your stations as big buttons, and plays them over the phone's mobile data, so the computer can stay at home."],
    "Add the page to the phone's home screen (Share › Add to Home Screen on an iPhone) and it opens like an app.",
    "Connect the phone to the car by Bluetooth, CarPlay or Android Auto and the sound comes through the car. The next and previous buttons on the steering wheel or the car's screen skip between stations, and the station reconnects by itself after a tunnel or a patch with no signal.",
    "Added or changed stations? Scan the code again. Stations with only an http:// link may not play on the phone; edit the station and use its https:// link if it has one.",
  ], { label: "Go to In the car", view: { type: "settings", tab: "car" } }],
  ["record", "Recording radio", [
    ["Record on a station records it to a file, whether or not you're listening. Press it again to stop. A recording stops by itself after 12 hours."],
    "A station you've just found plays at the top of the Radio page under Recently played, with its own Record button, so you can record it without saving it first.",
    ["Recordings in the sidebar lists everything you've recorded, with the songs it caught. Give one a label and tags with the pencil, ", ["Play"], " it, or add it to a playlist with +."],
  ], { label: "Go to Recordings", view: { type: "recordings" } }],
  ["clock", "Clock and alarms", [
    "Clock & alarms in the sidebar shows a big clock and your alarms. Wake up to any playlist or station, on the days you choose.",
    ["Set ", ["Repeat"], " to ", ["Once"], " for an alarm that goes off on one date only. It turns itself off afterwards."],
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
  ["studio", "Studio tools", [
    ["For people who make a radio station, ", ["Settings › App"], " has ", ["Show the studio tools"], ": playlists exported from station-studio or iTunes, recording radio, the Recordings page and the play log. With them off, Station Player is a simple radio and music player; turning them off keeps your recordings and playlists."],
    "A new install starts with them on only when station-studio is on the same computer.",
  ]],
  ["install", "Installing it on another computer", [
    ["On a Mac: download ", ["Station Player for Mac"], " from the download page, open it and drag Station Player into Applications. The first time you open it, macOS says it can't check it for malicious software, because it comes from a person rather than the App Store: open ", ["System Settings › Privacy & Security"], ", scroll down and press ", ["Open Anyway"], ". You only do this once."],
    ["On Windows: download ", ["Station Player for Windows"], " and run it. If Windows says it protected your PC, press ", ["More info"], " then ", ["Run anyway"], ". It installs for you alone, with no administrator password, and puts Station Player in the Start menu."],
    "To remove it: on a Mac, turn off start-at-login in Settings › App, then drag Station Player from Applications to the Bin. On Windows, use Settings › Apps like any other program.",
  ], { label: "Open the download page", href: "https://github.com/raby/station-studio/releases?q=player&expanded=true" }],
  ["trouble", "When something isn't right", [
    ["Tracks greyed out: their music files have moved. In ", ["Settings › Music"], ", press ", ["Find them…"], " and choose the folder the music is in now."],
    ["A station won't play: edit it and press ", ["Test"], ". Stations change their links from time to time; Find stations usually has the new one."],
    "The window says it can't reach the player: Station Player was quit. Open it again from its icon.",
    ["Updates: when a new version is out, the sidebar says so. Press ", ["Update now"], " and Station Player downloads it, installs it and restarts, keeping your stations and settings. Settings › App has ", ["Check for updates"], " too."],
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
  const shown = HELP.filter(([id]) => studio() || !STUDIO_HELP.includes(id));
  return el("div", { class: "help-page" },
    el("div", { class: "view-head" }, el("h1", {}, "Help")),
    el("p", { class: "help-intro" }, "Everything Station Player does, in a few lines each. Click a topic to jump to it."),
    el("nav", { class: "help-toc", "aria-label": "Help topics" },
      shown.map(([id, title]) => el("button", { type: "button", class: "chip", onclick: () => jump(id) }, title))),
    shown.map(([id, title, parts, go]) => el("section", { class: "help-section", id: `help-${id}` },
      el("h2", { tabindex: "-1" }, title),
      parts.map((p) => el("p", {}, ...helpText(p))),
      go && go.href ? el("a", { class: "btn", href: go.href, target: "_blank", rel: "noopener" }, go.label)
        : go ? el("button", { type: "button", class: "btn", onclick: () => show(go.view) }, go.label) : null)),
    el("p", { class: "help-version" }, `Station Player ${S.lib && S.lib.version ? S.lib.version : ""}`));
}
