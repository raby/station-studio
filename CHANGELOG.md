# Changelog

All notable changes are listed here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses semantic versioning.

## [1.9.0] - 2026-10-25

### Added

- **A real Mac app.** The downloaded Mac app shows Station Studio in a
  window of its own (WebKit, no Chrome needed), with its own Dock icon
  and menus: About, Settings (⌘,), Check for Updates, Edit, Go
  (Stations ⌘1, Source pools ⌘2, New station ⌘N), Window and Help.
  Closing the window leaves Station Studio running, and clicking its
  Dock icon brings the window back, as with Apple Music. ⌘Q (or Quit at
  the foot of the sidebar) stops Station Studio. Downloads go to your
  Downloads folder and links to other sites open in your browser.
- A Station Studio already open in a Chrome window or browser tab says
  once that Station Studio now has a window of its own, with **Open
  it**. Turning off **Open in Station Studio's own window** in Settings
  goes back to the browser.

## [1.8.0] - 2026-10-25

### Added

- **Settings** page (sidebar, under Setup), laid out like Station
  Player's Settings › App: the version with **Check for updates** and
  **Update now**, **Start Station Studio in the background when I log
  in** (a LaunchAgent on a Mac, the Run key on Windows, an autostart
  entry on Linux), **Open in a window of its own**, **Tell me when a new
  version is out**, where your data is kept, and **Install as an app**
  (moved here from the foot of the sidebar). The two switches are kept
  in `app-settings.json` in the data folder.
- **Help** page (sidebar, under Setup): getting started, source pools,
  stations, making playlists, and the app itself.
- **Not now** on the new-version note in the sidebar hides it until the
  next version, as in Station Player. Turning off new-version notices
  hides the one showing.

### Fixed

- Start at login refuses to point at a copy still on the disk image
  (move it to Applications first), which would be gone after a restart.

## [1.7.1] - 2026-10-25

### Added

- **Install as an app** at the foot of the sidebar (in Chrome, Edge or
  Brave) gives Station Studio its own icon in the Dock or taskbar, as
  Station Player has, with a Dock menu (New station, Source pools).
  The window then opens as that app. Opened from the Dock while Station
  Studio isn't running, the window starts it and waits, through the
  `station-studio://start` link the Mac app and Windows setup register
  (`web/static/`: manifest, service worker, offline page).

### Fixed

- After **Quit**, the "Station Studio has stopped" screen covers the
  whole window again instead of showing behind the station cards.

### Changed

- GitHub Actions workflows use the Node 24 versions of the standard
  actions, clearing the "Node.js 20 is deprecated" warnings.

## [1.7.0] - 2026-10-25

### Changed

- **A fresh look, matching Station Player.** Dark by default (light
  when the computer is set to light), with the amber accent, a sidebar
  (Stations, Source pools, New station) and the stations shown as
  cards. Colours come from `web/src/lib/theme.css`; pages use its
  tokens instead of fixed colours. On a phone the sidebar becomes a
  bar along the top.
- In the app, the version, **Quit** and **Update now** sit at the foot
  of the sidebar instead of floating over the page.

### Fixed

- The new-version notice turns up on a page that's been open a while
  (the page looks again every 5 minutes), not only after a reload.
- A version check that fails (offline, GitHub down) is tried again
  within the hour instead of waiting a day.

## [1.6.1] - 2026-10-25

### Changed

- The app opens in a window of its own (no tabs or address bar) when
  Chrome, Edge or Brave is installed, as Station Player does, with its
  own browser profile in the data folder's `window/`. Otherwise it opens
  a browser tab as before; `STATION_STUDIO_TAB=1` always uses a tab.
  Quit closes that window.

## [1.6.0] - 2026-10-25

### Added

- **Station Studio as an app.** Mac `.dmg` (Apple Silicon + Intel)
  and Windows setup `.exe` downloads that carry their own Python and
  web pages, so no terminal, Python or Docker is needed
  (`packaging/`, built by `.github/workflows/studio-installers.yml`,
  released as `studio-v<version>` by
  `.github/workflows/publish-studio-app.yml`). Opening the app starts
  the server on 127.0.0.1:8001 and opens the browser; opening it
  again just opens the page.
- A bar on the page in the app with the version, **Quit**, and
  **Update now** when a newer `studio-v` release is out (checked once
  a day). The update is downloaded from the release, checked against
  GitHub's SHA-256 for that file, then the Mac app is swapped in place
  or the Windows setup runs silently, and the page reloads into the
  new version. Data in `~/.station-studio` is never touched.
- `station-studio app`: the same from a terminal (pip installs).
- `GET /api/app` says whether the server is the app.

### Changed

- In the app, exports default to the app-home's `exports/` folder
  (`STATION_STUDIO_EXPORT_DIR`), where Station Player looks, instead
  of `out/` under wherever the process started. A folder set on the
  page still wins; `serve` and Docker are unchanged.

## [1.5.0] - 2026-06-28

First public PyPI release. Everything since the `0.1.0` seed
commit is folded into this changelog by feature area; the
detailed slice history (per-commit) lives in
[DESIGN.md](DESIGN.md).

### Added

#### Engine

- 8 sampling strategies: `pick`, `window`, `weighted`, `mix`,
  `sequential`, `jingle`, `archive_ref`, `prev_ref`. Each
  module owns its candidate-selection + exclusion bookkeeping.
- Persistent named cursors (§14.30) with per-station reset
  cycles + cross-pool dedup via the `cross_pool_dedup_sources`
  rule.
- Byte-equality test harness: same template + same pools +
  same week + same seed = same trace, verified against
  committed PID-sequence JSON snapshots.
- iTunes XML export with stable Persistent IDs (§14.11)
  derived from `(slug, week, slot_index)`.

#### Visual block editor (§16.2)

- `/stations/{slug}/visualise` — drag-and-drop block editor
  rendering the template as section-grouped chips, colour-
  coded by source.
- Atomic + structural edits: chip retyping, macro patch +
  fanout (one edit applies to N expanded blocks),
  allowlist_sources chip-list editor, section CRUD, block
  move + resize + add + remove.
- Live preview: staged edits POST to `/api/stations/{slug}/jobs/preview`,
  generation runs off-thread via the §16.11 job-id transport,
  trace renders next to the canvas. Up to 13 follow-up
  promotions land alongside (recovery via localStorage,
  cross-tab sync, jump-to-chip from trace row, transactional
  all-or-nothing Save, per-strategy CSS diff classes,
  per-section + multi-week diff aggregates, semantic
  structural diff).
- Compare against saved (§16.2.7): "show what changed since
  Save" toggle in the trace pane with per-slot
  changed/added/removed/unchanged classification.
- Fork action (§16.2.8): deep-copy a station's template +
  sidecar under a new slug.

#### Trace versioning (§16.4)

- Non-destructive regen: every regeneration appends a new
  `PlaylistVersion`; `Playlist.trace_id` becomes a denormalised
  pointer to the current version.
- `?v=N` URL parameter selects any historical version in the
  trace viewer; picker dropdown lists every version
  oldest-first.
- Side-by-side trace diff (§16.4.t1) between any two
  versions OR between consecutive weeks (§16.23) — shared
  walker, two routes.
- Historical-version XML export (§16.4.t2), per-playlist
  retention cap (§16.4.t3), rollback button (§16.4.t4),
  operator-applied version tags (§16.4.t5), versioned
  disk filenames (§16.4.t6, opt-in per station).

#### Accessibility (§16.5)

- Codebase-wide ARIA pass: skip-link, focus-visible ring,
  `<main id="main-content">` landmark on every route.
- New `<ErrorBanner>` + `<StatusBanner>` components with
  role + aria-live semantics tied to severity.
- `@axe-core/playwright` CI gate at 4 routes (landing,
  wizard, station detail, trace viewer) — `serious` and
  `critical` violations fail CI.
- Per-field error association on the wizard: backend returns
  structured `{errors: [{field, message}]}` payload; frontend
  wires `aria-invalid` + `aria-describedby` on the tagged
  inputs.
- Codebase severity policy locked: assertive for hard-stop
  load failures, polite for every recoverable inline error.

#### Mobile / narrow-viewport (§16.20)

- 40rem breakpoint; pages stack to single-column below.
- `<NarrowScreenNotice>` component for the visualise editor
  (drag-and-drop is desktop-only).
- Tables rotate to cards via `data-label::before` pseudo-
  elements; SR users navigate by labelled card sections.
- Narrow-viewport axe smokes in CI at 375x812.

#### Disk-export prune (§16.16)

- `POST /api/playlists/export/prune` walks the configured
  output directory, classifies `.xml` files into 5 orphan
  reasons (unknown slug, unexpected filename, no Playlist
  row, no PlaylistVersion row, stray-at-root), reports them.
- Dry-run by default; `?confirm=true` deletes with
  canonicalize + TOCTOU re-lstat safety. `make
  prune-exports[-confirm]` for ops convenience.

#### Trace viewer (§16.18)

- Virtualisation via `content-visibility: auto` + per-row
  `contain-intrinsic-size` — Classics's 3,010-row trace
  renders without scroll lag, all interactions preserved.

#### Docs site (§16.15)

- MkDocs-Material site published at
  `https://raby.github.io/station-studio/` via GitHub Pages.
- Tutorial walkthrough ("your first station in 10 minutes").
- Reference: template grammar, strategies,
  auto-rendered API (Swagger UI from `app.openapi()`),
  auto-rendered CLI (`station-studio --help` for every
  subcommand).
- Architecture: 6 curated entry-point pages
  (engine-pipeline, trace-versioning, sidecar-db-as-truth,
  live-preview, test-pyramid, migration-policy).
- Dark-mode palette toggle, custom 404, per-page H1 smoke
  guard.

#### Install (§16.13)

- Three install channels:
  - **Docker** (`docker compose up --build`, persistent
    volume, multi-stage image ~312MB, non-root,
    healthchecked).
  - **Pip wheel** (`pip install dist/station_studio-*.whl`
    + `station-studio serve`).
  - **Dev clone** (`make dev`).
- `station-studio` CLI with 6 subcommands: `serve`, `import`,
  `seed`, `generate`, `export`, `backup`.
- CI release-blocker gates: `make wheel-smoke` (pip-installs
  the wheel into a temp venv, curls `/` + `/api/stations` +
  `/sources` for HTTP-200), `make image-smoke` (same against
  the docker image, push-to-main only).

### Notes

This is the first PyPI release. The trusted-publisher pipeline
(`.github/workflows/publish.yml`) is wired but requires
one-time PyPI UI setup (see
[CONTRIBUTING.md](CONTRIBUTING.md) → "Releasing to PyPI") before
a tagged release will publish.

[1.5.0]: https://github.com/raby/station-studio/releases/tag/v1.5.0
