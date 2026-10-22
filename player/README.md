# Station Player

A music and radio player for the playlists Station Studio makes, built to
replace iTunes / Music as the thing that actually plays them.

It plays three kinds of thing:

- **Station Studio playlists**: the iTunes XML files Station Studio
  exports, plus any library you export from Music / iTunes
  (File › Library › Export Library). Tracks play in the order the station
  generated them.
- **Music folders**: any folder of audio files (mp3, m4a/aac, flac, wav,
  ogg/opus, aiff). Each folder shows up as its own playlist.
- **Internet radio**: any stream URL, including `.pls` and `.m3u` links.
  Rebirth Radio and Rebirth Classics are set up out of the box. When a station sends the name
  of the song it's playing, Station Player shows it. **Test** checks a
  link plays before you add it; the pencil on a station renames it,
  changes its link, moves it up or down or removes it.

Song details and cover art come from the files themselves (mp3, m4a,
flac, ogg / opus, wav, aiff), or from a `cover.jpg` / `folder.jpg` in
the album's folder.

It runs as a small local server on your computer, shown in a window of
its own (or a browser tab). It needs nothing beyond Python 3.11+ and listens only on
`127.0.0.1`, so nothing is reachable from your network.

## Install and run

### Without a terminal (Mac and Windows)

Each [Station Player release](https://github.com/raby/station-studio/releases?q=player&expanded=true)
has downloads that carry everything they need, Python included:

- **Mac:** `StationPlayer-<version>-mac-apple-silicon.dmg` for Macs with
  an M chip (2020 on), `-mac-intel.dmg` for older ones. Open it and drag
  Station Player into Applications. It isn't signed with a paid Apple
  certificate, so the first time macOS says it can't check it: open
  **System Settings › Privacy & Security**, scroll down and press
  **Open Anyway** (once).
- **Windows:** `StationPlayer-<version>-windows-setup.exe`. If Windows
  says it protected your PC, press **More info** › **Run anyway**. It
  installs for the current user only (no administrator password), puts
  Station Player in the Start menu (and on the desktop, if you leave
  that ticked) and in Open With for audio files. Uninstall it from
  Settings › Apps.

To update, download the new version and install it the same way, over
the old one; the player says when one is out. An older copy still
running in the background (or a pipx install's) quits when the new one
opens, and start at login moves over to the downloaded app. These builds are made by
`.github/workflows/player-installers.yml` from
`packaging/station-player.spec` (PyInstaller) and
`packaging/windows/installer.iss` (Inno Setup).

### With pipx

You need Python 3.11 or newer and [pipx](https://pipx.pypa.io)
(macOS: `brew install pipx`; Windows: `py -m pip install --user pipx`,
then `py -m pipx ensurepath`). No checkout of this repo is needed.

```bash
pipx install station-player
station-player
```

Upgrade later with `pipx upgrade station-player`. Each
[Station Player release](https://github.com/raby/station-studio/releases)
also has the wheel attached, for installing without PyPI.

### Open it like a normal app

In **Settings › App**, turn on the shortcut and Station
Player appears in the Start menu and on the desktop (Windows), in
Applications, Launchpad and Spotlight (macOS), or in the apps menu
(Linux). Opening it starts the player in the background and opens its
window; opening it again while it runs just brings the window back.
Use **Quit Station Player** in the sidebar to stop it.

*Start when I log in* starts it quietly in the background at login, so
the shortcut opens instantly. The same switches exist on the command
line: `station-player --add-shortcut`, `--start-at-login` and
`--remove-shortcuts`. Turn them off (or run `--remove-shortcuts`)
before `pipx uninstall station-player`.

### Open With

With the shortcut turned on, Station Player is offered in **Open With**
for audio files: right-click a song in Finder (or Explorer, or your file
manager) › Open With › Station Player. It plays straight away, in the
window that's open or a new one, and joins *Opened files* under
*Folders*. Station Player never makes itself the default app for audio.
A shortcut made by an earlier version is brought up to date the next
time Station Player starts.

### Its own window

With Google Chrome, Microsoft Edge or Brave installed, Station Player
opens in a plain window of its own, with no tabs or address bar, kept
separate from your browsing. Without one of them it opens in a browser
tab. Turn the window off under **Settings › App**.

That window still counts as one of the browser's: in the Dock and
Alt-Tab it shows as Chrome (or Edge, or Brave). **Install Station Player
as an app** under Settings › App gives it its own name and icon
there, and Station Player opens that way from then on.
Installed that way, right-clicking its icon in the Dock or taskbar
offers **Play / Pause** and **Next** (an app installed by an earlier
version picks these up when Chrome next updates it, or install it again).
Keep it in the Dock or taskbar if you like: opened from there while the
player isn't running, it starts the player and opens once it's up (the
browser asks once whether to let it open Station Player; tick "Always
allow"). On Linux, start the player from its menu entry instead.

When started from a shortcut there's no terminal, so messages go to
`~/.station-player/station-player.log`.

From a checkout, for development:

```bash
cd player
python -m station_player
```

The player opens at <http://127.0.0.1:8765/>. Useful flags:

```bash
station-player ~/Music/Albums ~/exports/week-012.xml   # add sources on launch
station-player --port 9000 --no-browser
```

## Setting it up

The first time it opens with nothing set up, Station Player shows a
welcome screen listing the music it found on your computer (your Music
folder, Station Studio's exports, iTunes library exports): click **Add**
beside one, or **Add a music folder** / **Add iTunes or station-studio
playlists** to choose with your computer's own folder window. **Add
music** in the sidebar brings the same screen back later.

**Settings › Music** lists everything Station Player reads, with how many
tracks each has:

1. **Playlists (iTunes XML).** An exported `.xml` file, or a folder of
   them. On first run Station Player adds Station Studio's exports folder
   (`~/.station-studio/exports`) automatically when it exists, so every
   week you export appears without doing anything.
2. **Music folders.** Folders of audio files. Besides appearing under
   *Folders*, these files are used to find playlist tracks whose saved
   location no longer exists (matched by file name).
3. **Missing tracks.** When playlist tracks can't be found (the music
   moved to another drive, or the playlists were made on another
   computer), click **Find them…** and choose the folder the music is in
   now. Station Player works out the path rewrite itself. Rewrites can
   also be added by hand under **Advanced**: from
   `/Users/rob/Music/iTunes/iTunes Media` to `D:/Music`, say.

On Linux the folder window needs `zenity` or `kdialog`; without one,
type or paste paths under **Type or paste a path instead**.

Tracks whose audio file can't be found are greyed out and skipped.
Only audio files (mp3, m4a/aac, flac, wav, ogg/opus, aiff, webm) are
ever played or served, whatever a playlist file points at.

Settings live in `~/.station-player/config.json` (override the folder
with `$STATION_PLAYER_HOME`). Song details read from your files are
cached beside them in `tag-cache.json`, so only new or changed files are
read again on later scans.

## Playing

- Double-click a track to play from there, or **▶ Play** for the whole
  playlist. Shuffle and repeat (all / one) sit next to the transport.
- Keyboard: Tab into the track list, move with ↑ ↓ (Home / End /
  Page Up / Page Down), Enter plays. Space plays and pauses; ← and →
  skip 10 seconds. Keyboard media keys
  and the OS now-playing panel work through the browser's Media Session
  support.
- Tracks play back to back with no gap between them, so mixes and live
  albums flow straight through. Or set **Crossfade** (Settings
  › Playback, up to 12 seconds) to fade each track into the next.
  Tracks from the same album, short ones such as jingles, and repeat-one
  stay gapless, and Next skips straight away.
- **Up next**: the ⋯ button on a track (or right-click, or N / Q on a
  focused row) offers **Play next** and **Add to queue**. Queued tracks
  play before the rest of the playlist; **Up next** in the sidebar shows
  them, and lets you reorder, remove or clear them.
- **My playlists**: make your own with **+** next to *My playlists* in
  the sidebar, then add tracks from the ⋯ menu (**Add to playlist…**).
  In one of your playlists, drag tracks to reorder them (or Alt+↑ / ↓),
  and press Delete or use the ⋯ menu to take one out. Any playlist can
  be copied into My playlists, and **Save as a playlist** in Up next
  keeps the current play order.
  To add several tracks at once, Shift-click a run of them (or
  Shift+↑ / ↓), Cmd-click (Ctrl-click on Windows) to pick more or fewer,
  or Cmd/Ctrl+A for all; a bar shows **Add to playlist…**, **Play next**,
  **Add to queue** and, in your own playlists, **Remove**. Recordings
  have a **+** on the Recordings page to add one to a playlist. Your playlists are kept in
  `~/.station-player/my-playlists.json`, and a track whose file moves
  is found again by its location.
- **Export M3U** on any playlist saves it as an `.m3u` file other
  players (VLC, foobar2000, Winamp, Music) can open.
- **Search library** at the top of the sidebar (or press `/`) searches
  every track in every playlist and folder by title, artist, album or
  genre.
- Close the window and open it again and Station Player is where you
  left it: the same playlist, queue and track, paused at the same point
  (or the same radio station). Press play to carry on.
- **Volume levelling** (Settings › Playback) plays every track
  at about the same loudness. It uses ReplayGain or iTunes Sound Check
  information where a file has it, and otherwise measures the track in
  the browser the first time it plays (or while the track before it
  plays) and remembers the result. Files over 12 minutes are only
  levelled when they carry that information.
- Pausing a radio station and pressing play again reconnects to the live
  broadcast instead of resuming from where it stopped.
- **Record** on a station card records it to a file, whether or not
  you're listening to it, exactly as the station sends it (no
  re-encoding). Song titles, when the station sends them, go in a text
  file beside it. Recordings are saved in `Music/Station Player
  Recordings`, reconnect by themselves if the station drops, and stop
  after 12 hours.
- **Recordings** in the sidebar lists every finished recording, newest
  first, with its station, date, length and the songs it caught. The
  pencil gives one a label and tags (say *breakfast show, interview*);
  click a tag to see only those, or search labels, tags and songs.
  **Play** plays it under its label. Labels and tags are kept in
  `recordings.json` in the recordings folder, so they move with it.
- Radio plays through Station Player itself, which is how it can read
  the song titles stations send (the browser can't). If a station can't
  be relayed that way, it plays directly, just without song titles.
- Stations that broadcast as HLS (`.m3u8` playlists of short audio
  files, as BBC radio does) play and record too: Station Player follows
  the playlist and turns it into one continuous stream. Encrypted
  streams aren't supported.

- **Sleep timer**: the menu beside the volume stops playback after 15
  minutes to an hour and a half (fading out over the last 20 seconds),
  or at the end of the track playing.

## Finding radio stations

**Find stations** on the Radio page searches
[Radio Browser](https://www.radio-browser.info), a free community
directory of tens of thousands of stations. It opens on the most popular
stations in your country (from your computer's language settings; choose
*Any country* to see the world); search by name, pick a genre or a
country, or sort by most played. Some genre buttons cover two at once
(*techno / trance*, *house / garage*, *chart / pop*, *asian / bhangra*).
**SomaFM** lists the channels of
[SomaFM](https://somafm.com), listener-supported radio with no adverts.
Each result shows the station's logo, country, genres and stream
quality: **Listen** plays it straight away (through Station Player, so song titles show), and **Add** puts it in your
stations, where it can be recorded and set as an alarm. Searches go to
radio-browser.info (and somafm.com for SomaFM), which also hears which
stations you Listen to there (it ranks stations that way); logos come
from each station's own website. Station Player fetches them itself, so
the page never contacts other sites.

Results update as you type (or press Search), and **Show more** adds
the next 40 below. Streams likely to sound thin are marked **Low
quality**: MP3 under 64 kbps, AAC or Opus under 48 kbps.

**Tags**: Edit a station to give it your own tags, like *jazz* or
*Sunday morning*, with commas between them (tags you've used before are
one click away). The tags you've used show as buttons above your
stations: click one to see only those stations, **All** for every one.
A station added from the finder while a genre is picked is tagged with
that genre.

**Put on desktop** (Edit a station) puts an icon for it on your
desktop, with its logo: double-click it and Station Player opens and
plays that station, whether or not it was running. On the command
line, `station-player --station <stream link>` does the same for a saved
station.

**Recently played** at the top of the Radio page lists the last 8
stations you listened to, saved or not, so a station you tried from
the finder is easy to find again (and Add). **Clear** empties it.

**Station logos** show on your stations, in the now-playing bar and in
the computer's media controls (the Mac's Now Playing, Windows' media
panel). Stations added from the finder bring their logo with them; for
others, **Find logos** at the top of the Radio page looks them all up in
Radio Browser at once, or **Edit** a station and choose **Find logo** or **Choose picture…** (a PNG, JPEG, GIF or WebP up to
256 KB). Logos are kept in the `logos` folder beside the settings file,
so they show without asking the station's website each time.

**Help** in the sidebar is a short guide to everything above, for
anyone new to Station Player.

## Clock and alarms

**Clock & alarms** in the sidebar shows a big clock (digits, or a
clock face) with today's date and the next alarm.

- **Alarms**: each with a time, the days it repeats (or **Once** on a
  date, after which it turns itself off), a label and a playlist or
  station to wake up to. Switch each on and off without deleting it.
  They fade in over a minute.
- When one goes off, a full-screen alarm shows **Snooze 9 min** and
  **Stop** (or Escape to turn the alarm off and keep the music playing).
  The phone remote gets the same two buttons.
- **Night mode** fills the screen with a dim clock for the bedside and
  keeps the screen awake. Click or press any key to leave it.

Station Player has to be running at alarm time (turn on *Start at
login*) and the computer awake; if no player window is open, one opens.

**The tuner** across the top of the Radio page is an old radio's dial
with your stations along it: click a name, or drag the needle and let
go near a station (with a hiss of static on the way); Left and Right
tune to the next one. A line beside the song title moves with the
sound.

**Ambient** in the sidebar fills the screen with a calm scene and its
own sound, made as it plays: a fish tank, waves on a shore, or birds at
dawn. It plays alone or under the music, with its own volume, and the
sleep timer fades it out too.

**Schedule** on the Radio page switches station at set times, once or
every week: the news at 17:00, then back to your station at 17:10. A
switch only changes what's already playing, and never starts the player
when it's quiet. Alarms and switches together go up to 30.

## Play log (PRS / PPL returns)

Station Player keeps a log of every track played, in
`~/.station-player/play-log.jsonl`. **Download play log** (Settings ›
Play log) saves the plays between two dates as a CSV
spreadsheet with the date and time, title, artist, album, composer,
label and ISRC, the track's length and how long it played, ready to
copy into a PRS for Music or PPL music return. Composer, label and ISRC
come from the files' tags (ID3 `TCOM`, `TPUB`, `TSRC`; the `COMPOSER`,
`LABEL` / `ORGANIZATION` and `ISRC` fields elsewhere). Plays under 30
seconds (skips) and radio stations aren't included. Turn the log off
there too.

## Phone remote

Turn on **Phone remote** (Settings › Phone remote) and scan the QR code
with a phone on the same Wi-Fi: it shows what's playing and has
play/pause, next, previous, volume, the sleep timer, and your playlists
and stations, and Snooze / Stop when an alarm goes off. The sound still
plays on the computer. It's a separate small server on port 8766,
reachable only through the secret link; **Make a new link** stops the
old one working. The first time, your computer may ask whether to let
Station Player (or Python) accept incoming connections: allow it, or the
phone can't connect.

## In the car

**Settings › In the car** shows a QR code that opens a page on your
phone with your stations as big buttons. The phone plays them itself,
over mobile data, so the computer doesn't need to be on. Add the page
to the home screen and it opens like an app; through Bluetooth, CarPlay
or Android Auto the car's next / previous buttons skip between
stations, and a dropped stream reconnects by itself.

The page is `car/` in this folder, published with the docs on GitHub
Pages at <https://raby.github.io/station-studio/car/>. The stations go
in the link after the `#` (compressed JSON), so they never reach a
server; scan again after changing them. A page served over https can't
play `http://` streams, so stations with only an http link may not play
on the phone.

## New versions

Once a day Station Player asks pypi.org for the newest version number
(nothing else is sent). When there's a newer one, the sidebar says so;
update with `pipx upgrade station-player` and restart it. Turn this off
under Settings › App.

## A note on Station Studio's example stations

The example pools bundled with Station Studio are synthetic: their tracks
have no audio files behind them, so they load but show as missing. Point
Station Studio at an export of your own library and the playlists it
generates carry each track's file location through to this player.

## Development

```bash
cd player
python -m pytest
```

`tests/test_browser.py` drives the page in Chromium and is skipped unless
Playwright is installed (`pip install playwright && python -m playwright
install chromium`).

### Releasing

Bump `version` in `player/pyproject.toml` and merge to main, then run
**Actions → Publish Station Player → Run workflow**. It tests, builds,
smoke-installs the wheel and creates a `player-v<version>` GitHub
release with the files attached. Tick *Also publish to PyPI* to upload
the same files to pypi.org (one-time setup is described at the top of
`.github/workflows/publish-player.yml`).

The server is `station_player/server.py` (standard library
`http.server`), the library reader is `station_player/library.py`, file
tags and art are read by `station_player/tags.py`, the radio relay is
`station_player/radio.py`, shortcuts are `station_player/desktop.py`,
and the UI is plain HTML / CSS / JS in `station_player/static/`.
