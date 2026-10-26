# Station Server

Puts a station-studio playlist on air. It plays the week's tracks in order,
in real time, and sends them straight to your SHOUTcast or Icecast server
with each song's title. That replaces iTunes playing out of the headphone
jack, the loopback cable, and Winamp with the SHOUTcast DSP plugin.

No sound card is involved: the music files are decoded, joined and encoded
inside Station Server. Nothing else playing on the computer (a YouTube
video, system sounds) can reach the broadcast, and one computer can run
both stations.

**Status: 0.4: an app you download and install, running as many stations
as you like on one computer, that updates itself.** Each station has its own playlist, server
and play log. See the
[plan](https://claude.ai/artifact/RMcRHeFUDnw2BTvhGskcmd).

## Install

**Download** (no Python or terminal needed): a Mac `.dmg` (Apple Silicon
or Intel) or a Windows setup `.exe`, each with its own ffmpeg. They're on
each `server-v…` release of this repository. They're also built for
every pull request that changes Station Server: open the pull request's
*Checks* › **Station Server installers** run, and download from its
*Artifacts*.

- **Mac:** open the `.dmg` and drag Station Server to Applications. The
  first time, macOS says it can't check the app: open System Settings ›
  Privacy & Security and click **Open Anyway** (the app isn't signed with
  a paid Apple certificate).
- **Windows:** run the setup. If SmartScreen stops it, click **More info**
  › **Run anyway**. It installs for you only, with no administrator
  password, and adds Station Server to the Start menu.

Then, on the station's computer, turn on **Settings › Start Station Server
when I log in**, so that after a restart, a power cut or Windows updates it
starts in the background and puts the stations that were on air back on
air. On a Mac it's also started again if it ever stops unexpectedly.

**Running unattended.** While a station is on air the computer is kept from
going to sleep by itself (the screen can still turn off). If the server
can't be reached, or the music's drive isn't there yet (the computer was
quicker than the router after a power cut), the station shows *Trying
again…* and keeps trying, after 15 seconds and then less often, until it's
on air; the same if the broadcast ever fails. For a computer nobody is at:

- set it to switch itself back on when the power returns (in the BIOS,
  often *Restore on AC power loss*);
- let Windows sign in by itself, since Station Server starts when you sign
  in;
- check Windows Update's *active hours*, so restarts happen when it suits.

**Help › Copy diagnostics** copies the version, each station's state and
the end of the log, with passwords taken out, to paste into a message
when asking for help.

**With pipx** (Linux, or if you'd rather): `pipx install station-server`,
then run `station-server`; `pipx upgrade station-server` updates it. You
need Python 3.11 or newer and ffmpeg (below).

**From a checkout** (for development): you need Python 3.11 or
newer and **ffmpeg** with its MP3 encoder (macOS: `brew install ffmpeg`;
Windows: `winget install ffmpeg`; or point Station Server at one with
`--ffmpeg PATH` or `$STATION_SERVER_FFMPEG`).

```bash
python -m venv .venv
.venv/bin/pip install ./player ./server      # Windows: .venv\Scripts\pip
```

Station Server reads playlists with station-player's code, so both are
installed. Then run `.venv/bin/station-server` (Windows:
`.venv\Scripts\station-server`).

You'll also need the music on the computer, and the playlists exported from
station-studio (or any iTunes / Music XML).

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
   (`~/.station-studio/exports/rebirth-classics`), so each week is played
   in turn. Next week's export can be made any time: it waits for the start
   of its week (the station page says when) and goes on at the end of the
   track playing then. A week exported late goes on within a minute, and
   exporting the week on air again picks up the changes.
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

**Levels and gaps.** With *Even out the volume* on, tracks are played at
about the same loudness: from their ReplayGain or Sound Check tags, or for
tracks without them, from measuring them (EBU R128). Station Server
measures each track once, in the background at low priority, coming tracks
first, and remembers the results in `analysis.json`. Measuring also finds
the silence at the start and end of each track, which is left out so songs
follow on without dead air (the week's clock uses the trimmed lengths).

**Fallback.** In a station's Edit page, *Fallback* names a folder of
jingles or songs (or a playlist) that plays instead of silence when none of
the playlist's music can be read, for example when its drive isn't there.
The playlist is tried again after each pass, and comes back as soon as it
can be read.

**Switching over.** A station can have a *test stream* (Edit › Server): a
spare port, stream 2 of a SHOUTcast 2 server, or a spare Icecast mount.
*Rehearse* plays the station there while Winamp carries on, without
touching the play log or sending alerts; *Go live* then switches it to the
live server. *Ready check* on the station's page checks everything it
needs in one go: ffmpeg, the playlist and how many tracks are found, the
live and test servers and their passwords, the fallback, the shows, start
at login and alerts. If another source (Winamp's SHOUTcast plugin, say) is
already on the server, Station Server says so rather than knocking it off.
Help › Switching over from Winamp walks through it.

**Keeping an eye on it.** On air, a station's page shows how many are
listening (from the SHOUTcast server's `7.html` or Icecast's
`status-json.xsl`; listeners to a relay aren't counted), and *Listen*
plays the broadcast exactly as it goes to the server.

*Settings › Alerts* tells you when a station you've put on air has been off
air, unable to reach its server, or playing silence or its fallback for a
few minutes (3 unless you change it), and again when it's back: as a phone
notification through [ntfy](https://ntfy.sh) (install the app and subscribe
to the same topic), by email through your mail server, or both. *Send a
test* checks the settings. Station Server can't send anything if the
computer itself is off or offline, so an outside check of the stream is
still worth having.

*Settings › Phone access* opens a second address (port 8771 unless you
change it) to phones and other computers on the same network, behind a
password: they can see the stations, listen, and put them on or off air,
skip and restart. Settings and editing stay on the station's computer.
Allow Station Server through the firewall when Windows or macOS asks. For
use away from the network, use a VPN such as Tailscale.

**Shows at set times.** In a station's Edit page, *Shows* pins a show to
times in the week: "Saturday Night Mix, Saturday 20:00" or "The News,
weekdays 09:00". A show is a recording, a station-studio (or iTunes)
playlist, or a folder of recordings played in name order. It starts
exactly on time, cutting the song playing, or after that song ends. When
it finishes, the music comes back where the week's clock says, as if it
had played all along, so the times in the running order stay right; the
running order shows the shows at their times. Restart part-way through a
show and it joins the show where it should be. Show times are wall-clock
times, so a 20:00 show stays at 20:00 when the clocks change. A new
week's playlist waits until a show has finished.

**Crossfades and jingles.** *Edit › Playback › Crossfade* fades the end
of each song out under the start of the next, over 1 to 10 seconds (off
by default). *Edit › Jingles* names a folder of jingles or station idents,
one played after every few songs (4 unless you change it), shuffled, each
once before any plays again. The song before fades out under the jingle,
and the next song starts as it ends. Jingles aren't played during shows
or the fallback, don't go in the play log, are levelled like the songs, and send the
station's name as the title. The week's clock allows for both.

A station that was on air when the app quit goes back on air when it
starts. If no track in the playlist can be read, it sends silence and
keeps trying rather than dropping off the server.

**Play log.** Every track played is logged; download a date range as CSV
for PRS and PPL returns (the same format as station-player's).

**The Mac app.** The downloaded Mac app is an app of its own, like Apple
Music: its own window (no Chrome needed), Dock icon and menus (Go,
Settings ⌘,, Check for Updates, Quit ⌘Q). Closing the window leaves the
stations on air; clicking the Dock icon brings it back, and right-clicking
it has Add a station and Play log. ⌘Q asks first when a station is on air.

**Dock and taskbar icon.** Elsewhere, in the app window, Settings → *Install as app*
gives Station Server its own icon in the Dock or taskbar, with Add a
station and Play log on its menu, and it opens that way from then on.
Opened from there while Station Server isn't running, the window starts it
(through the `station-server://` link the app registers) and waits.

**New versions.** As in Station Player and Station Studio, a note at the
foot of the sidebar says when a new version is out, and Settings ›
Updates has *Check for updates* and *Update now*: the downloaded app
fetches the new version from its GitHub release, checks it's the genuine
file, and restarts; stations that were on air go back on air. It asks
GitHub once a day; turn that off in Settings.

**Two stations on one computer.** Add a station for each; they run side
by side, each with its own playlist, server, clock and play log, and go on
and off air separately. Rebirth Classics to `shoutcast://…:8000` and
Rebirth Radio to `shoutcast://…:8002`, say, from one PC.

Settings, passwords, play logs and the log file `station-server.log` are kept in `~/.station-server`
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

No music to hand? `python server/tools/make_test_station.py` makes a
folder of short test songs and a week's station-studio export in
`~/station-server-test`, and prints the exports folder to use as the
playlist. `--week 13` adds next week's, to watch the switch-over;
`--missing` adds a song whose file isn't there.

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
playlist XML ─► each track decoded by ffmpeg ─► mixer (gapless or crossfaded,
             levelled, jingles)
             ─► held to real time ─► one MP3 encoder ─► SHOUTcast / Icecast
```

- **Gapless.** Tracks are joined as raw audio before one encoder, so there's
  no gap and no reconnect between songs. A crossfade holds back each song's
  last few seconds and mixes them, fading, under the start of the next.
  Where the two add up past full scale the peaks are eased, not clipped.
  Skip fades out over at most 2 seconds.
- **Volume levelling** uses the ReplayGain or iTunes Sound Check values in
  each file (read by station-player), never boosting more than 10 dB or past
  the track's peak. Files without them play as they are for now.
- **Titles** reach the server when the track is actually heard, not when its
  audio is first sent.
- **Dropped connection.** If the server goes away, Station Server keeps
  playing and reconnects every 5 seconds. A track it can't read is skipped.

## Releasing

Bump the version in `server/pyproject.toml` and
`server/station_server/__init__.py`, merge, then run Actions ›
**Publish Station Server** on main. It tests, makes a draft
`server-v<version>` release, builds the Mac and Windows downloads onto
it, and publishes it once they're all there. Tick **Also publish to
PyPI** to put the same version on pypi.org as well (or run it again with
the box ticked for a version already released).

## Development

```bash
cd server
python -m pytest -q
```

The tests run without ffmpeg, skipping the end-to-end ones; with ffmpeg
installed they also play real audio through fake SHOUTcast and Icecast
servers.
