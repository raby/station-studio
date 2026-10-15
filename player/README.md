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
  Rebirth Radio is set up out of the box.

It runs as a small local server on your computer with a browser window as
the UI. It needs nothing beyond Python 3.11+ and listens only on
`127.0.0.1`, so nothing is reachable from your network.

## Install and run

```bash
# from a station-studio checkout
pipx install ./player          # or: pip install ./player
station-player
```

or, without installing:

```bash
cd player
python -m station_player
```

A browser tab opens at <http://127.0.0.1:8765/>. Useful flags:

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

Settings live in `~/.station-player/config.json` (override the folder
with `$STATION_PLAYER_HOME`).

## Playing

- Double-click a track to play from there, or **▶ Play** for the whole
  playlist. Shuffle and repeat (all / one) sit next to the transport.
- Space plays and pauses; ← and → skip 10 seconds. Keyboard media keys
  and the OS now-playing panel work through the browser's Media Session
  support.
- Pausing a radio station and pressing play again reconnects to the live
  broadcast instead of resuming from where it stopped.

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

The server is `station_player/server.py` (standard library
`http.server`), the library reader is `station_player/library.py`, and
the UI is plain HTML / CSS / JS in `station_player/static/`.
