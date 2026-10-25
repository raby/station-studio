# Station Server

Puts a station-studio playlist on air. It plays the week's tracks in order,
in real time, and sends them straight to your SHOUTcast or Icecast server
with each song's title. That replaces iTunes playing out of the headphone
jack, the loopback cable, and Winamp with the SHOUTcast DSP plugin.

No sound card is involved: the music files are decoded, joined and encoded
inside Station Server. Nothing else playing on the computer (a YouTube
video, system sounds) can reach the broadcast, and one computer can run
both stations.

**Status: 0.2, one station on air from an app.** Station Server opens a
window in station-player's style where you set up a station, put it on
air and see what's playing. Running both stations on one box, and
installers, come next. See the
[plan](https://claude.ai/artifact/RMcRHeFUDnw2BTvhGskcmd).

## What you need

- Python 3.11 or newer.
- **ffmpeg** with its MP3 encoder (the normal builds have it).
  macOS: `brew install ffmpeg`. Windows: `winget install ffmpeg`, then open
  a new terminal. Or point Station Server at one with `--ffmpeg PATH` or
  `$STATION_SERVER_FFMPEG`.
- The music on this computer, and the playlist exported from
  station-studio (or any iTunes / Music XML).

## Install

From a checkout of this repo:

```bash
python -m venv .venv
.venv/bin/pip install ./player ./server      # Windows: .venv\Scripts\pip
```

Station Server reads playlists with station-player's code, so both are
installed. Then run `.venv/bin/station-server` (Windows:
`.venv\Scripts\station-server`).

## The app

```bash
.venv/bin/station-server
```

opens Station Server in a window of its own (with Chrome, Edge or Brave;
otherwise a browser tab). It keeps running in the background: closing the
window doesn't take a station off air, and running the command again just
opens the window. **Quit** in the sidebar stops it.

1. **Add a station.** Give it a name and its playlist: a station-studio
   export, or better its exports folder
   (`~/.station-studio/exports/rebirth-classics`), so the newest week is
   played and next week's is picked up by itself at the end of a track.
2. **Server.** SHOUTcast or Icecast, the address and port, and the source
   password. **Test connection** checks them without going on air.
3. **Go on air.** The station page shows the track playing, the running
   order with the time each track goes out, and any tracks whose files
   can't be found.

**Joining the week by the clock.** The playlist's first track is pinned to
the start of the week (Monday 00:00 unless you change it), and the
tracks' lengths say when each of the others airs. Go on air on Wednesday
afternoon, or restart after a power cut, and the station joins where the
clock says, part way into a track. Choose *From the top* to start at
track 1 instead. A playlist shorter than a week loops.

A station that was on air when the app quit goes back on air when it
starts. If no track in the playlist can be read, it sends silence and
keeps trying rather than dropping off the server.

**Play log.** Every track played is logged; download a date range as CSV
for PRS and PPL returns (the same format as station-player's).

**Dock and taskbar icon.** In the app window, Settings → *Install as app*
gives Station Server its own icon in the Dock or taskbar, and it opens that
way from then on.

Settings, passwords and play logs are kept in `~/.station-server`
(`$STATION_SERVER_HOME` to change it); the settings file is readable by
you only. `station-server app --port N` uses another port than 8770.

## From the command line

Check that ffmpeg is found and the playlist's music is all there:

```bash
station-server check ~/.station-studio/exports/rebirth-classics-week-12.xml
```

Record what listeners would hear to an MP3 (and the titles to a .txt beside it):

```bash
station-server play week-12.xml --to test.mp3 --once
```

## Go on air

The password is the **source** password (the one Winamp's DSP plugin uses).
Give it with `--password`, or set `STATION_SERVER_PASSWORD` so it isn't
typed on the command line.

**SHOUTcast** (DNAS 1.9.x or 2.x). Use the listener port, the one in the
station's link; Station Server connects to the port above it, as Winamp does.

```bash
station-server play week-12.xml --to shoutcast://81.150.147.206:8000 \
  --name "Rebirth Classics" --genre "Vintage Oldies / Club Classics" \
  --url https://www.rebirthclassics.uk
```

For stream 2 on a DNAS 2.x add `?sid=2`: `shoutcast://host:8002?sid=2`.

**Icecast** (2.4 or later), to a mount:

```bash
station-server play week-12.xml --to icecast://source@my.server:8000/classics
```

While it plays, the window shows each track as it starts. Type `s` and
Enter to skip a track; Ctrl+C stops.

### Trying it on a Mac with your own Icecast

No station needed: run Icecast on the same Mac and listen in station-player.

```bash
brew install ffmpeg icecast
mkdir -p "$(brew --prefix)/var/log/icecast"      # Homebrew doesn't make it
icecast -c "$(brew --prefix)/etc/icecast.xml"    # leave running; Ctrl+C stops
```

http://localhost:8000 shows Icecast's status page. Its source password is
`hackme` until you change `<source-password>` (and `<admin-password>`) in
that `icecast.xml`. In a second terminal run `.venv/bin/station-server`,
add a station with the playlist folder
`~/.station-studio/exports/rebirth-classics`, server **Icecast**,
`localhost`, port `8000`, mount `/classics`, password `hackme`, and go on
air. Or from the command line:

```bash
.venv/bin/station-server play ~/.station-studio/exports/rebirth-classics/012.xml \
  --to icecast://localhost:8000/classics --password hackme --name "Rebirth Classics"
```

Then add the station `http://localhost:8000/classics` in station-player.
The title changes with each track, and the status page shows the stream.

### Trying it next to Winamp

Only one source can feed a stream at a time. To compare without taking the
station off air, send Station Server to a spare stream: a second DNAS on
another port, stream 2 on a DNAS 2.x (`?sid=2`, set up in its config), or a
test Icecast mount. Listen to both in station-player.

## Command-line options

| Option | What it does |
| --- | --- |
| `--playlist NAME` | Which playlist, when the XML has several (a full library export) |
| `--music-folder DIR` | Where to look for tracks that have moved since the XML was made (repeatable) |
| `--remap FROM=TO` | Rewrite a path prefix, e.g. a Mac library played on a PC: `--remap "/Users/rob/Music=D:/Music"` |
| `--start N` | Start at track N |
| `--once` | Stop at the end of the playlist instead of starting again |
| `--bitrate KBPS` | MP3 bitrate, 128 by default |
| `--no-level` | Turn off volume levelling |
| `--name`, `--genre`, `--url` | What the server shows about the station |
| `--public` | Ask the server to list the station in its directory |

## How it works

```
playlist XML ─► each track decoded by ffmpeg ─► mixer (gapless, levelled)
             ─► held to real time ─► one MP3 encoder ─► SHOUTcast / Icecast
```

- **Gapless.** Tracks are joined as raw audio before one encoder, so there's
  no gap and no reconnect between songs.
- **Volume levelling** uses the ReplayGain or iTunes Sound Check values in
  each file (read by station-player), never boosting more than 10 dB or past
  the track's peak. Files without them play as they are for now.
- **Titles** reach the server when the track is actually heard, not when its
  audio is first sent.
- **Dropped connection.** If the server goes away, Station Server keeps
  playing and reconnects every 5 seconds. A track it can't read is skipped.

## Development

```bash
cd server
python -m pytest -q
```

The tests run without ffmpeg, skipping the end-to-end ones; with ffmpeg
installed they also play real audio through fake SHOUTcast and Icecast
servers.
