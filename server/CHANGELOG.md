# Changelog

Station Server's releases: what each one changed for the people using
it. The newest is first. Help › What's new, each release's notes on
GitHub, and what Update now says about a new version all come from
here, so a release needs its section before it can go out (see
CONTRIBUTING, "Releasing").

## [Unreleased]

## [0.26.0] - 2026-11-27

### Added

- When a new version is out, What's new under the notice says what it
  changes (and what any versions in between changed), from its release
  notes.

### Changed

- Help › What's new is kept up to date: it's built from Station
  Server's changelog, which each release now adds to.

## [0.25.0] - 2026-11-27

### Added

- Help › Getting started › Report a problem: a button that opens a new
  GitHub issue with this copy's version, how it was installed and your
  computer already filled in, and where Station Server's log file is.

## [0.24.0] - 2026-11-24

### Added

- Help › Getting started › The other Station apps: what Station Studio
  and Station Player are for, buttons that open them when they're
  installed on this computer, and the docs site's "Which app do I
  need?".

## [0.23.3] - 2026-11-22

### Fixed

- A stream stopped straight after it started (as the Ready check does)
  no longer leaves an error in the log.
- On Linux, after a station went off air its live shows' ports stayed
  taken, so the next time it went on air presenters couldn't go live
  ("port … is in use by another program"). They're let go of now.
- Station Server closes the files, pipes and web responses it has
  finished with, rather than leaving them for Python to tidy up.

## [0.23.2] - 2026-11-21

### Fixed

- In the Mac app, Download CSV (the play log) saves to your Downloads
  folder instead of filling the window, and the menus always bring the
  window back.

## [0.23.1] - 2026-11-08

### Changed

- Start at login and the log file work the same way as in Station
  Studio and Station Player. On Windows too, an entry made by another
  copy or an older version is rewritten to start this one.

## [0.23.0] - 2026-11-08

### Changed

- The update check, Update now and the app window are shared with
  Station Studio and Station Player, so a fix to one reaches all three.

## [0.22.0] - 2026-11-01

### Changed

- The downloads carry Station Server's own ffmpeg, built from FFmpeg's
  and LAME's source (both LGPL) with only what it uses. The app's
  LICENSE.txt says where that source is.

### Fixed

- Settings, the Station Studio link and live shows' instructions no
  longer wait on the computer's network name being looked up, which on
  some Macs took over a minute.

## [0.20.0] - 2026-10-27

Station logos.

- Edit › Station › Logo: give a station a logo. It shows on the station
  page, on the stream's own page when this computer streams it (and as
  its icon in a browser tab), and on your website's now/next page
  (Settings › Website).
- Station Player 0.20 picks the logo up by itself for stations with a
  now/next feed, and Find logo there fetches it from a station this
  computer streams.

## [0.19.1] - 2026-10-27

- Presenter alerts: "has finished" now waits until the presenter has
  been gone for two minutes, so an encoder reconnecting, or the station
  reconnecting, no longer sends "finished" and then "is live" again. A
  presenter taking over from another sends one of each, and a show
  still live when the station is taken off air is told as finished.
- Also send to: titles reach the second server in order. Changing its
  address asks for its password again. The Ready check and Test the
  second server always check the server that's saved, and a test of
  the main server is never mistaken for the second one.

## [0.19.0] - 2026-10-27

A second server.

- Edit › Server › Also send to: the live stream goes to a second
  SHOUTcast or Icecast server at the same time, like a hosted account,
  while this computer (or the main server) carries on as before. Test
  the second server checks its address and password.
- If the second server can't be reached, the station stays on air and
  keeps trying it every few seconds; the station page and the Ready
  check say why. Rehearsals don't go there.

## [0.18.0] - 2026-10-27

Presenter alerts.

- Settings › Alerts › Tell me when a presenter goes live: a phone
  notification or email when a presenter goes live on a station, with
  their name, and another when they finish, saying how long they were
  on (not for rehearsals).
- On Windows, a server at localhost (Icecast or a DNAS on the same
  computer) no longer takes about two seconds for every connection and
  title update.

## [0.17.1] - 2026-10-27

- Settings › Internet: a DuckDNS name saved without its token no longer
  stops other settings saving, a new name is sent to DuckDNS straight
  away, and Use never suggests more than the 500 listeners a station
  can allow.
- Test connection: a station's saved password is used however its
  address is written, and a server saved while on air (not yet switched
  over to) has its password tested properly.
- Use keeps the rest of a station's server setting as it was, and the
  tester copes with IPv6 addresses.
- On a Mac, the window no longer waits while Station Server looks for
  the router at start-up.

## [0.17.0] - 2026-10-27

Icecast and SHOUTcast.

- Help › Icecast & SHOUTcast: what they are, setting up Icecast for
  free on a Mac, Windows or Linux, the icecast.xml settings that
  matter, and SHOUTcast's source port and stream numbers.
- Test a server there, step by step, without a station or going on air:
  the address, the port, which kind of server it is, the source port,
  the password and the listener count, each saying what to change.
  Edit › Server › Test connection shows the same steps.
- Nothing already sending to a server (Winamp, say) is knocked off by a
  test.

## [0.16.7] - 2026-10-27

- On a Mac, Station Server starts as it did up to 0.16.3 again, so the
  window comes back after an update. If Look at the router is blocked,
  quit and open Station Server from Applications (once: the router
  keeps the forward). The window also gets macOS to ask about the local
  network.

## [0.16.6] - 2026-10-27

- On a Mac, Station Server gets macOS to ask whether it may find
  devices on your local network. (Kept in 0.16.7, from the window.)

## [0.16.5] - 2026-10-27

- Settings › Internet › Try it from a phone shows two codes: one for a
  phone away from home (on mobile data, like a listener) and one for a
  phone on the same Wi-Fi as this computer, with why the internet
  address often doesn't play at home.

## [0.16.4] - 2026-10-27

- On a Mac, Station Server always starts through the app (opened, at
  login and after an update), which stays on quietly in the background,
  so macOS lets it reach the router and servers on this network.
  (Undone in 0.16.7: the window didn't always come back after an
  update.)

## [0.16.3] - 2026-10-27

- Look at the router searches out of this computer's own network even
  with a VPN on, and also asks the router directly, so more routers are
  found.

## [0.16.2] - 2026-10-26

- Help › Streaming is rewritten, with diagrams: how listeners reach the
  station, ports and the router, DuckDNS, how many listeners, this
  computer or a hosted server, and what to do when it doesn't work.
- On a Mac, Look at the router says when macOS needs Station Server
  allowed in System Settings › Privacy & Security › Local Network, and
  the app now asks for it.
- Settings › Internet warns when the internet provider shares one
  address between customers, so a forwarded port can't work.
- Check my internet finds this connection's outside address a second
  way, if Cloudflare's speed test page doesn't give it.

## [0.16.0] - 2026-10-26

Settings › Internet.

- Check my internet measures the upload and suggests how many listeners
  each station streaming from this computer can take, with Use to set
  it.
- Opens each station's port on the router (UPnP), keeps a free DuckDNS
  name pointing at this connection, and shows a code to scan from a
  phone on mobile data.
- The Ready check says when a station allows more listeners than the
  upload suits.
- Changing a station's server while it's on air offers to switch over
  straight away, and the status line shows where it's really streaming
  until then.

## [0.15.0] - 2026-10-26

Streaming from this computer.

- Station Server can be the station's server: choose This computer in
  Edit › Server, and listeners connect straight to it. No DNAS, Icecast
  or hosting needed (Help › Streaming).

## [0.14.0] - 2026-10-26

On your website.

- Settings › Website publishes each station's now playing, coming up
  and just played to a GitHub Pages site, with a box to embed on your
  website.

## [0.13.0] - 2026-10-26

The Station Studio link.

- Station Studio sends each week straight here, with a link code: no
  shared folder.
- Station Studio's station page shows what's on air here, the weeks it
  has, and warns when next week is missing.

## [0.12.1] - 2026-10-26

- A station's settings, Settings and Help are in tabs, like Station
  Player's.

## [0.12.0] - 2026-10-26

Live shows.

- Presenters go live from BUTT, Mixxx or Winamp, connecting to this
  computer: the song fades out under them and the music comes back when
  they leave.
- Each presenter has their own password, which names them on the page,
  in the play log and on the recording.
- Presenters' volume is evened out to match the music.
- Each live show is recorded; End live show takes a presenter off air.

## [0.11.0] - 2026-10-26

Crossfades and jingles.

- A crossfade between songs, of 1 to 10 seconds.
- Jingles or idents after every few songs, shuffled.
- Skip fades out quickly, whatever the crossfade.

## [0.10.0] - 2026-10-26

The switch-over kit.

- Ready check: everything a station needs, with what to fix.
- Rehearse on a test stream while Winamp carries on, then Go live.
- It notices Winamp (or anything else) still sending to the server.

## [0.9.0] - 2026-10-26

Keeping an eye on it.

- The listener count, and Listen to hear the broadcast itself.
- Alerts to your phone or email when a station is off air.
- Phone access from a phone on the same network.

## Earlier versions

0.4.0 to 0.8.0 (25 and 26 October 2026) are in
[the GitHub releases](https://github.com/raby/station-studio/releases?q=server)
and the commit history.
