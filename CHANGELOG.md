# Changelog

All notable changes are listed here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses semantic versioning.

## [1.14.2] - 2026-10-31

### Changed

- **One look for the main button everywhere.** Sixteen screens each
  drew their own: corners of 8px or 4px, eight different paddings, two
  weights, and several that didn't change at all on hover. They now
  share the theme's main button (accent colour, 8px corners, darker on
  hover), and secondary buttons such as Cancel, Discard and Reset use
  the theme's secondary one. The empty station's big "Generate week 1"
  and the narrow-screen notice's large tap target keep their size.
- A button's own style now always decides its hover: the app-wide grey
  hover can no longer override it (the cause of the unreadable Rules
  page buttons fixed in 1.14.1).

## [1.14.1] - 2026-10-30

### Fixed

- **Buttons on a station's Rules page stay readable on hover.** Save
  and Add went white on light grey (light mode) or dark on dark grey
  (dark mode), contrast about 1.3:1: the app-wide hover style beat
  theirs. The Station Server buttons only escaped by stylesheet order,
  so they now set their own hover colour too. A new e2e check hovers
  every accent button on every page, in light and dark, and fails
  below 4.5:1 contrast.

## [1.14.0] - 2026-10-30

### Added

- **Change every identical rule at once.** When the rule you're
  changing has exact copies elsewhere in the station (the starter has
  21 identical "7 × weighted" macros, one per segment), the rule panel
  offers **Also change the 20 other rules just like this one (N songs
  a week)**. Tick it, and a settings change or a change of how it picks
  goes to all of them. "Just like" means the same kind of rule with the
  same settings: a macro matches macros, a single slot matches single
  slots, and a slot inside a macro never matches. It's unticked each
  time you pick a rule. On a week's page it's one change in the list
  ("…, and the 20 rules just like it"), undone with one Undo. In the station editor they queue with
  live preview on; otherwise they save together in one batch, all or
  nothing.

### Fixed

- The rule panel's Save / Apply / Change rule / Move buttons went
  white-on-light-grey when hovered (the app-wide hover style won).

## [1.13.1] - 2026-10-30

### Changed

- **The rule panel speaks plainly** (on a week's page and in the
  station editor). It opens with the rule in words ("Shuffled from POP,
  no artist twice in 10"); says where it is ("Rule 2 of every day",
  "Slot 4", "7 songs, one rule") with the template's address on hover;
  names strategies the way you'd say them, engine name in brackets
  ("Shuffled (weighted)"); and labels settings by what they do ("No
  artist twice in", "Only songs also in", "Not reused for (weeks)").
  Gone: "Atomic block", "row_idx=0, block_idx=1", "per-block positive
  filter", "expanded blocks". On the week page the button that queues a
  change says **Apply**, since the bar's **Save changes** is what saves.
- A rule's number is the same in the panel ("Rule 2 of 3 in this
  section") and in the list of queued changes ("Evening, rule 2").
- Rules in words use the station's own defaults: a shuffled rule that
  doesn't set its artist window says the station's
  `artist_dedup_window`, not always 10, and a mix says the station's
  `mix_exclusion_weeks`.

## [1.13.0] - 2026-10-30

### Added

- **Search in the Songs view.** One box finds songs by title, artist,
  the rule in words ("NIGHT", "Shuffled"), the macro or the section;
  every word has to match somewhere. While you're previewing a change,
  **Only changed songs** shows just the ones that move. The search is
  kept while the preview comes and goes.

### Changed

- **The first change to a rule previews sooner.** The week under the
  saved rules, which a change is compared with, is now worked out as
  soon as you open a song's rule, not after your first change. If it's
  done by the time you change something (usually: it takes a few
  seconds on a 168-song week), your change needs one preview instead
  of two. A change made while it's still running waits for it instead
  of starting over.

## [1.12.0] - 2026-10-30

### Added

- **Regenerate the later weeks too.** After you save a rule change on a
  week's page, the bar says which later weeks were made with the old
  rules and offers **Regenerate this week and the N after**. They're
  remade one at a time, in order, with progress; if one fails it
  stops there and says which. When they're done, the page says so.

### Fixed

- **Regenerating a past week keeps its place in the station's
  sequences.** On stations with persistent cursors, a regenerate used
  to start from where the latest week left the cursors and then move
  them on again, so the week came out different and every later new
  week skipped ahead. Each generation now records the cursor positions
  it started from. Regenerating a week starts from those, and only the
  latest week moves the live cursors. Previews of a past week use the
  same positions, so they match. Weeks generated before this version
  have no record, so a past one still starts from the live cursors (it
  no longer moves them), and the latest one is moved on once more when
  regenerated. From then on, every week is recorded.
- Regenerating a week of an `rng: on` station without a seed reuses
  the week's own seed (on both `/generate` and `/jobs/generate`); a week
  made before the station was `rng: on` uses its week number, as its
  preview does, so the preview shows what regenerating gives. Before,
  the request failed.
- **Regenerate** on a week's page waits if another generate is already
  running for the station (a week started from the station page, say)
  and then does its own week. Before, it took that other job's finishing
  as its own week being done.

## [1.11.0] - 2026-10-30

### Added

- **Songs view on a week's page.** A Songs | Table switch (remembered)
  lists the week in playing order under the station's section names.
  Each song shows the rule that put it there in plain words ("Next in
  order from NIGHT", "Shuffled from POP, no artist twice in 10") and
  why the engine picked it. Songs from a macro say which; songs picked
  by a rule that has since changed say so.
- **Change a song's rule from the week.** Click a song: every song made
  by the same rule is marked, and the rule panel opens. Change the
  rule's settings, strategy or source, and the week is previewed with
  the change, songs that differ marked against the week as saved. Save
  applies it to every week (the bar says so); Discard drops it.
  **Regenerate this week** then remakes the week with the saved rules.
  Changes not yet saved are kept for the next visit. Older versions of
  a week are read-only.
- The bar lists each unsaved change in plain words ("The day, rule 2:
  Picked from A"), each with **Undo**, and says how many of the week's
  songs it moves. Moving to another week keeps your changes and
  previews them on that week.

### Fixed

- Changing a macro's settings (in the station editor or from a week)
  now shows in the preview: the preview used to run with the macro's
  songs unchanged until you saved.

## [1.10.1] - 2026-10-30

### Changed

- The web UI runs on **Svelte 5** (from 4), with Vite 7 and the
  matching SvelteKit, vite-plugin-svelte, svelte-check and Testing
  Library. Existing pages run unchanged in Svelte 5's compatibility
  mode; new ones can use runes. npm audit goes from 19 advisories (7
  high) to 6 low, all in build tools.

### Fixed

- Settings › Station Server no longer replaces an address you typed
  while your saved settings were still loading.
- The Mac app keeps working back to macOS 11: the web UI is built for
  Safari 14 again (Vite 7 would have needed Safari 16), and the
  narrow-screen notice and the station form's Reset no longer use
  functions that need Safari 15.4.

## [1.10.0] - 2026-10-26

### Added

- **Send weeks straight to Station Server.** Settings › Station Server
  takes Station Server's address and link code (from its Settings ›
  Station Studio), with **Test**. Each station's page chooses the
  Station Server station it sends to; **Send to Station Server** on a
  week's page sends it, and **Also send each week when I export it to
  disk** sends each week as it's exported. Station Server answers with
  how many of the week's songs it has, any it can't find, and when the
  week goes on air. No shared folder is needed.
- **On Station Server**, on each station's page: whether it's on air
  there, the song playing, the week playing and the next, the weeks it
  has, and a warning (with **Send week N now**) when it has nothing
  after this week.

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
