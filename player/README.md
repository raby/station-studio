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
  Rebirth Radio is set up out of the box. When a station sends the name
  of the song it's playing, Station Player shows it.

Song details and cover art come from the files themselves (mp3, m4a,
flac, ogg / opus, wav, aiff), or from a `cover.jpg` / `folder.jpg` in
the album's folder.

It runs as a small local server on your computer, shown in a window of
its own (or a browser tab). It needs nothing beyond Python 3.11+ and listens only on
`127.0.0.1`, so nothing is reachable from your network.

## Install and run

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

In **Sources & settings › App**, turn on the shortcut and Station
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
tab. Turn the window off under **Sources & settings › App**.

That window still counts as one of the browser's: in the Dock and
Alt-Tab it shows as Chrome (or Edge, or Brave). **Install Station Player
as an app** under Sources & settings › App gives it its own name and icon
there, and Station Player opens that way from then on.
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

Open **Sources & settings** in the sidebar.

1. **Playlists (iTunes XML).** Add an exported `.xml` file, or a folder of
   them. On first run Station Player adds Station Studio's exports folder
   (`~/.station-studio/exports`) automatically when it exists, so every
   week you export appears without doing anything.
2. **Music folders.** Add the folder your music lives in. Besides
   appearing under *Folders*, these files are used to find playlist
   tracks whose saved location no longer exists (matched by file name).
3. **Path rewrites.** If the playlists were made on another computer, say
   a Mac library played back on a PC, rewrite the start of the path:
   from `/Users/rob/Music/iTunes/iTunes Media` to `D:/Music`.

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
  albums flow straight through. Or set **Crossfade** (Sources & settings
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
  keeps the current play order. They're kept in
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
- **Volume levelling** (Sources & settings › Playback) plays every track
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
  Recordings` (**Open recordings folder** on the Radio page), reconnect
  by themselves if the station drops, and stop after 12 hours. **▶ Play**
  beside a finished recording plays it here (the recordings folder joins
  your music folders, so they're all under *Folders* too).
- Radio plays through Station Player itself, which is how it can read
  the song titles stations send (the browser can't). If a station can't
  be relayed that way, it plays directly, just without song titles.

## New versions

Once a day Station Player asks pypi.org for the newest version number
(nothing else is sent). When there's a newer one, the sidebar says so;
update with `pipx upgrade station-player` and restart it. Turn this off
under Sources & settings › App.

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
