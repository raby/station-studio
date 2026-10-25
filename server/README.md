# Station Server

Puts a station-studio playlist on air. It plays the week's tracks in order,
in real time, and sends them straight to your SHOUTcast or Icecast server
with each song's title. That replaces iTunes playing out of the headphone
jack, the loopback cable, and Winamp with the SHOUTcast DSP plugin.

No sound card is involved: the music files are decoded, joined and encoded
inside Station Server. Nothing else playing on the computer (a YouTube
video, system sounds) can reach the broadcast, and one computer can run
both stations.

**Status: proof of concept (0.1).** A command-line tool to try side by
side with the Winamp setup. The background service, web page, second
station on one box and installers come next. See the
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

## Try it

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

### Trying it next to Winamp

Only one source can feed a stream at a time. To compare without taking the
station off air, send Station Server to a spare stream: a second DNAS on
another port, stream 2 on a DNAS 2.x (`?sid=2`, set up in its config), or a
test Icecast mount. Listen to both in station-player.

## Options

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
