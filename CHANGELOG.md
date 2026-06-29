# Changelog

All notable changes are listed here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project uses semantic versioning.

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
