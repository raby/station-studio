# Design

How Station Studio, Station Player and Station Server are put together,
and the rules they keep. It describes the design as it is now and is kept
up to date with it. Each part has its own longer page on the
[docs site](https://raby.github.io/station-studio/), linked below.

> **The design log.** Until November 2026 this file was the project's
> running log: every slice's design, decisions and reviews, about 16,000
> lines. It's kept, unchanged, as
> [`docs/history/design-log.md`](https://github.com/raby/station-studio/blob/main/docs/history/design-log.md).
> **Section numbers in code comments** ("DESIGN.md §16.2.6", "§14.25")
> point into it: the numbers and the text are as they were.

## The three apps

One repository, three apps, each released on its own:

| App | What it's for | Where | Data folder |
|---|---|---|---|
| **Station Studio** | Make a station's weekly playlists from its rules, and change them | `engine/`, `api/`, `web/`, `station_studio/` | `~/.station-studio` |
| **Station Player** | Play radio and music; for station makers, their playlists, recordings and the play log | `player/` | `~/.station-player` |
| **Station Server** | Put a station on air: play its week, live, to a SHOUTcast or Icecast server | `server/` | `~/.station-server` |

Each runs a small web server on the person's own computer and shows its
pages in the browser, or in its own window in the downloaded Mac and
Windows apps. Nothing listens beyond `127.0.0.1` unless the person turns
it on (Station Player's phone remote, Station Server's phone access and
its Station Studio link, or Station Studio run in Docker, whose container
listens on all addresses).

```text
music library (iTunes XML)
   │ import
   ▼
Station Studio ─┬─ exports folder ──► Station Player
                │  (each week, as iTunes XML)
                │
                └─ the link ──► Station Server
                   (send a week,     ├─► SHOUTcast / Icecast
                    what's on air)   │      └─► listeners
                                     └─► now/next feed
                                            └─► website,
                                                Station Player
```

- **Studio → Player:** Studio writes each week as iTunes XML to its
  exports folder; Station Player finds that folder and offers the weeks
  as playlists (its Studio tools).
- **Studio → Server:** with the link on, Studio sends a week straight to
  Station Server (`PUT /api/link/stations/<id>/weeks/<NNN>.xml`) and asks
  what's on air (`GET /api/link`), with a link code
  ([`engine/stationserver.py`](https://github.com/raby/station-studio/blob/main/engine/stationserver.py),
  [`server/station_server/studiolink.py`](https://github.com/raby/station-studio/blob/main/server/station_server/studiolink.py)).
  Without it, Station Server reads the weeks from a folder.
- **Server → Player:** Station Server's stream is a radio station like
  any other; its now/next feed tells Station Player (and the station's
  website) what's on and what's next.
- **Shared code:** all three depend on **station-appkit**
  ([`appkit/`](https://github.com/raby/station-studio/tree/main/appkit)):
  the new-version check, the self-updater and the app window (a Chrome
  or Edge app window, or on the Mac the app's own WebKit window). Each
  app describes itself once (`appinfo.py`: name, releases, port, Mac
  helper) and its `updates.py`, `selfupdate.py`, `window.py` and
  `macwindow.py` are thin, adding only what's its own (Station Player's
  pip updates and mini player, Station Server's "stations go off air"
  question). Station Server also depends on `station-player` for reading
  tags, loudness, the play log and station logos, so both read music the
  same way.

## Station Studio

### The engine (`engine/`)

A **station** is a template: its sources (pools of songs) and its
layout, a list of **rules** (blocks), either flat (`flat_slots`) or a row
repeated for each day (`row_template`). Each rule picks a number of songs
with one of eight **strategies** (`window`, `pick`, `sequential`,
`jingle`, `weighted`, `mix`, `prev_ref`, `archive_ref`): small pure
functions in `engine/strategies/`, except `sequential`, which the
generator reads straight from its cursor.

`engine.generator.generate(template, pools, week, seed, …)` plays the
rules in order and returns the week's songs and a **trace**: for every
song, the rule that put it there and why (and, for some strategies, the
songs it passed over). Everything the engine needs comes in as
arguments: the database is read before and written after
(`engine/persistence.py`), never during.

- **Reproducible.** The same template, pools, week, seed and inputs give
  the same week, byte for byte. The byte-equality suites
  (`tests/test_classics_byte_equality.py`, `tests/test_port_byte_equality.py`,
  `tests/test_byte_equality_synthetic.py`) hold the engine to the
  playlists of `weekly/`, the generator it replaced.
- **State follows the rules.** What carries from week to week (persistent
  cursors, the mix rotation, last week's playlist for `prev_ref`) comes
  from what the rules did, recorded with each week, so regenerating a past
  week doesn't change the weeks after it, and starts from where that week
  started.
- **Station rules** (`engine/rules.py`): per-station settings such as the
  artist-dedup window, mix exclusion weeks and cursor advance, with
  defaults, editable on the Rules page.

More: [Engine pipeline](https://raby.github.io/station-studio/architecture/engine-pipeline/),
[Template grammar](https://raby.github.io/station-studio/reference/template-grammar/),
[Strategies](https://raby.github.io/station-studio/reference/strategies/).

### Storage

SQLite, through SQLAlchemy, in the data folder. Every change to the
models comes with an **Alembic migration**, with a downgrade, and CI
checks that the migrations build exactly what the models describe
(`make migration-parity`).

- **Weeks keep every version.** A `Playlist` is a station's week;
  generating it again, rolling it back or changing a song in it appends a
  `PlaylistVersion` (its trace, compressed) rather than replacing it.
  Rollback is another new version.
- **The editor's sidecar.** How a station's rules are grouped into
  sections and macros for the editor is a second JSON document beside the
  template, stored in the database and validated against it.
- **Just-this-week changes** (`week_change`): one song of a week changed
  for that week only, kept whenever the week is generated again.

More: [Trace versioning](https://raby.github.io/station-studio/architecture/trace-versioning/),
[Sidecar + DB-as-truth](https://raby.github.io/station-studio/architecture/sidecar-db-as-truth/),
[Just-this-week changes](https://raby.github.io/station-studio/architecture/week-changes/),
[Migration policy](https://raby.github.io/station-studio/architecture/migration-policy/).

### The API (`api/`)

FastAPI. Stations, source pools, rules, weeks and their versions, the
editor's edits (one batch, all or nothing, against the version the page
loaded), just-this-week changes, and the Station Server link. Anything
that can take seconds (generating, previewing) is a **job**: started with
a POST, then watched (`GET /api/jobs/{id}`), with progress, and
cancellable. A preview runs the engine on the page's unsaved rules and
writes nothing.

More: [API reference](https://raby.github.io/station-studio/reference/api/),
[Live preview](https://raby.github.io/station-studio/architecture/live-preview/).

### The pages (`web/`)

SvelteKit, built as static files the API serves. The station editor and a
week's page share one way of changing rules: edits are **queued** (kept
in the browser until saved), previewed as you go, saved in one batch,
and undone one at a time. The song list shows every song with its rule
and why it's there; songs can be dragged to play elsewhere, and on a
week's page changed just for that week.

The pages are written for people, not for the code: plain words, no
internal names; usable from the keyboard; readable in light and dark
(checked with axe in the end-to-end tests).

### The app and the command line (`station_studio/`)

`station-studio` serves the pages and API on `127.0.0.1:8001`, and has
commands to import, generate, export and back up. The downloaded Mac and
Windows apps run the same server in a window of their own; once a day
they ask GitHub for a newer `studio-v*` release and offer to update
themselves (the download is checked against its published SHA-256).

## Station Player (`player/`)

A Python standard-library HTTP server (no dependencies but station-appkit, itself standard library) on
`127.0.0.1:8765` and a page of plain HTML, CSS and JavaScript. It plays
music folders, iTunes playlists and radio stations (relayed, so the page
can play them), with alarms, schedules and ambient mode. Its **Studio
tools** (on from the start when Station Studio is on the same computer;
a setting either way) add Station Studio's weeks, recording and the
play log for PRS / PPL returns.

- **Local by design.** It answers only requests for `localhost`, and
  every change needs a header other websites can't send, so a page in the
  same browser can't drive it. The phone remote is a separate port,
  turned on by the person.
- **The car page** (`player/car/`) and the **Home page picks** are
  published with the docs site, so they change without a new version.

More: [Station Player](https://raby.github.io/station-studio/player/).

## Station Server (`server/`)

Plays each station's week, in real time, to a SHOUTcast or Icecast
server. Per station: each track decoded by ffmpeg, then a mixer (gapless
or crossfaded, levelled, jingles every few songs), held to real time,
into one MP3 encoder and a sender. A presenter can go live over it.

- **The week on air.** Weeks arrive as `<NNN>.xml` (exported, or sent
  over the link). A newer week waits for the station's week start; one
  that arrives late goes on at once.
- **Looks after itself.** Nobody may be watching: a lost server is
  retried every 5 seconds, an unreadable track is skipped, and the
  computer is kept awake while a station is on air.
- **The now/next feed.** Because it knows the running order, it publishes
  the next few hours with times, about once an hour, to the station's own
  GitHub Pages site; pages work out what's on from the clock.

More: [Station Server](https://raby.github.io/station-studio/server/).

## How work is done

- **Slices.** Work is planned in small slices, each its own branch and
  pull request: designed first (what it does, what it doesn't, the
  decisions), then built with tests, reviewed in full before merging,
  and reviewed again if anything is found.
- **Where things are written down.** The pull request says what changed
  and what was checked; `CHANGELOG.md` says it for people using the apps;
  the docs site's Architecture pages hold the design of each part, and
  this file the whole. A decision that changes how a part works updates
  its page here in the same pull request.
- **Tests.** pytest for the engine and API (with coverage gated), Vitest
  for the pages, Playwright end to end with axe for accessibility, and
  each app's own suite. Shared cases keep the server and the pages in
  step where the pages work something out themselves (moving rules,
  following a rule through queued edits).
  More: [Test pyramid](https://raby.github.io/station-studio/architecture/test-pyramid/).
- **Dependencies.** Each app's `pyproject.toml` asks for ranges; the
  repository's `uv.lock` pins the exact versions (and hashes) all three
  are tested and shipped with, and CI, the installers and the Docker
  image install from it.
- **Updates are checked.** Update now uses a new version's installer
  only when its size and SHA-256 match what GitHub publishes for it, and
  only from the project's own releases (`station_appkit.selfupdate`, one
  copy for all three apps).
- **Releases.** Each app has its own version and release:
  Station Studio's PyPI package from a `v<version>` tag, and its Mac and
  Windows apps (`studio-v<version>`), Station Player (`player-v<version>`)
  and Station Server (`server-v<version>`) from their Publish workflows.
  See [CONTRIBUTING.md](https://github.com/raby/station-studio/blob/main/CONTRIBUTING.md).
