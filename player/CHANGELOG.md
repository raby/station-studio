# Changelog

Station Player's releases: what each one changed for the people using
it. The newest is first. Each release's notes on GitHub, and what
Update now says about a new version, come from here, so a release
needs its section before it can go out (see CONTRIBUTING, "Releasing").

## [0.26.0] - 2026-11-27

### Added

- Help › Report a problem: a button that opens a new GitHub issue with
  this copy's version, how it was installed and your computer already
  filled in, and where Station Player's log file is.

### Fixed

- A window left open now says when a new version is out, within a
  second or two of Station Player finding it, instead of the next time
  the window is opened.

## [0.25.0] - 2026-11-24

### Added

- The tuner numbers its stations 1, 2, 3… along the dial, like a car
  radio's presets, and the number keys tune to them (0 is the tenth).
  With lots of stations, only 1, 5, 10… show, and the one you're tuned
  to.
- Help › The other Station apps: what Station Studio and Station Server
  are for, buttons that open them when they're installed on this
  computer, and the docs site's "Which app do I need?".

### Changed

- Ambient › Fire is calmer: it flickers at half the speed and half as
  much, so it smoulders. The night sky's stars twinkle more gently too.

## [0.24.4] - 2026-11-22

### Fixed

- Station Player closes the web responses it has finished with, even
  the errors it only notes and carries on from, rather than leaving
  them for Python to tidy up.

## [0.24.3] - 2026-11-21

### Fixed

- In the Mac app, a download saves to your Downloads folder instead of
  opening in the window, and the menus always bring the window back.

## [0.24.2] - 2026-11-08

### Fixed

- Leaving night mode straight away (a quick second click, or a key) no
  longer leaves the window full screen on a hidden clock.
- Night and ambient mode let the screen sleep again when they close,
  and keep it awake again after the page has been hidden (a locked
  screen, another app over the window).

## [0.24.1] - 2026-11-08

### Changed

- Start at login and the log file work the same way as in Station
  Studio and Station Server. An entry made by another copy or an older
  version is rewritten to start this one, and it's never set up from
  the disk image.

## [0.24.0] - 2026-11-08

### Changed

- The update check, Update now and the app window are shared with
  Station Studio and Station Server, so a fix to one reaches all three.

## [0.23.2] - 2026-11-01

### Fixed

- Pausing while a station is still connecting keeps it paused, instead
  of it starting to play once it connects.
- Starting no longer waits on the computer's network name being looked
  up.

## [0.23.0] - 2026-10-30

### Added

- Ambient: Rain, Stream, Night and Fire scenes, and foam on the waves.
  Each scene plays a real recording, crossfading each time round, with
  its credit shown.
- Mini player: the player bar pops out into a small window that stays
  on top (in Chrome and Edge; in the Mac app, View › Mini Player), and
  goes back.

## [0.22.0] - 2026-10-30

### Added

- The tuner shows each station's logo beside its name, with a short
  line to its spot on the dial. When names would touch, only the logos
  show.
- Discover: a Sport switch on the globe, a "Sport around the world"
  tile on Home and Discover, and a sport chip in the list.

## [0.21.0] - 2026-10-29

### Added

- A new window opens on the Radio page. Settings › App › Open on can
  make it Home instead.

### Changed

- Home: a station's tile shows Play on hover and Pause while it plays,
  and a second click pauses it.
- Discover opens on the globe. The first tap on a dot names it and puts
  it first in the list, and a second tap plays it.
- The tuner's static between stations is louder, over a low valve-set
  hum, and its names and numbers are bigger.

## [0.20.0] - 2026-10-27

### Added

- Station logos: Find logo asks a station's what's-on feed, then its
  own Station Server, then Radio Browser. A station with a Station
  Server feed and no logo takes the feed's by itself.

## Earlier versions

0.1.0 to 0.19.2 (15 to 27 October 2026) are in
[the GitHub releases](https://github.com/raby/station-studio/releases?q=player)
and the commit history.
