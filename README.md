# Station Studio

**An open-source radio station playlist generator.** Describe how your
station should sound (jingle every N tracks, no artist repeats inside
a 10-track window, mix block of curated genres, weekend continuity
from a separate source pool) — and Station Studio generates iTunes
XML playlists you can drop straight into Music or iTunes.

Built for radio enthusiasts who run their own stations. Two real
stations ship as examples; you can fork either or build your own
from scratch.

## Why this exists

If you've ever:

- Curated a radio station for friends / a Twitch stream / your own
  shower
- Wanted "shuffle, but smarter" — never the same artist twice in a
  row, weekend feels different from weekday, jingles at the right
  intervals, mix blocks of specific genres
- Tried to build that in iTunes smart playlists and hit the wall

…Station Studio is that wall, removed. The engine handles the
constraint logic; you describe what you want.

## What it does

Given:

- **Your music**, in iTunes XML format (export from Music / iTunes)
- **A station template** — a small JSON file describing what plays
  when (or built visually via the web UI, coming soon)

It produces:

- **iTunes XML playlists** — drop them back into Music / iTunes
- **Per-pick traceability** — every track placement is explained
  ("this track was picked because cursor for NIGHT_1 is at position
  847 and this is the 12th slot of that block")
- **Reproducibility** — same template + same source files + same week
  number = same playlist, byte-for-byte

You can also **visualise a station's template** in the web UI — a
chip-per-pick canvas grouped into sections, color-coded by source
(or strategy), with click-through detail for every slot and macro.
Click "🗺 Visualise template" on any station page. (Read-only today;
in-canvas authoring is on the roadmap — see DESIGN.md §16.2.3+.)

## Install

Three install channels, all first-class. Pick whichever matches what
you've got installed:

| Channel | Prerequisites | Best for |
|---|---|---|
| **Docker** | docker | Trying it out, leaving it running on a NAS |
| **Pip wheel** | Python 3.11+ | Local-only / BYO-music, no Node setup |
| **Dev clone** | Python 3.11+, Node 22+, `make` | Hacking on Station Studio itself |

All three serve the same UI + API and the same auto-seeded example
stations. Pick one:

### 1. Docker (no Python, no Node)

```bash
git clone https://github.com/raby/station-studio.git
cd station-studio
docker compose up --build       # first run builds; later runs reuse
```

Open <http://localhost:8001>. App-home (your DB + imported pools +
exports) lives in a docker-managed volume named
`station-studio-data`, so state survives `docker compose down`.
Inspect with `docker volume inspect station-studio-data`; back up
with:

```bash
docker run --rm -v station-studio-data:/data -v $PWD:/backup \
  alpine tar czf /backup/data.tar.gz /data
```

The image is multi-stage (node-build → wheel-build → slim runtime,
non-root, healthchecked), ~312MB. Same SvelteKit static SPA as the
pip wheel — no divergent build path.

### 2. Pip wheel (Python only, BYO-music)

If you have Python 3.11+ and want a single-process app on
`localhost`, build a wheel and pip-install it:

```bash
git clone https://github.com/raby/station-studio.git
cd station-studio
make wheel                                # builds web/build + dist/*.whl
pip install dist/station_studio-*.whl
station-studio serve --host 127.0.0.1 --port 8001
```

`make wheel` runs the SvelteKit static build internally — you don't
need Node installed separately at *runtime*, only at build time. On
first run the CLI creates `~/.station-studio/` (override with
`$STATION_STUDIO_HOME`), copies the example stations, and runs
Alembic.

Once the first tagged release ships, this becomes:

```bash
pip install station-studio              # straight from PyPI, no clone
station-studio serve
```

The PyPI publish workflow is wired (`.github/workflows/publish.yml`,
trusted-publishing via OIDC, test.pypi rehearsal before real PyPI);
the first release lands when project trust is configured on PyPI.
See [CONTRIBUTING.md](CONTRIBUTING.md#releasing-to-pypi) for the
cut-a-release procedure.

### 3. Dev clone (hack on it)

If you want to modify Station Studio or contribute upstream:

```bash
git clone https://github.com/raby/station-studio.git
cd station-studio
make install                  # creates .venv, installs Python + Node deps
make dev                      # FastAPI :8001 + SvelteKit :5173 with HMR
```

Open <http://localhost:5173>. Hot-reload on both backend and
frontend; see [CONTRIBUTING.md](CONTRIBUTING.md) for the slice
pattern + test discipline.

> **Single-worker for now.** The async-generation job tracker
> (§16.11) holds job state in-memory per uvicorn worker. All three
> channels above use a single worker; if you reconfigure to
> `--workers N` (N>1), the frontend's polling request can land on
> a worker that doesn't know about the job and you'll get spurious
> 404s. Stick to single-worker until DB-backed job state lands
> (§16.21+).

## Loading your music

The two example stations (Rebirth Classics + Rebirth Radio) are
seeded automatically on first boot — pick one in the sidebar.

To import your own iTunes XMLs as source pools, open the **Sources**
page (or click "Source pools →" from the landing). Drag-and-drop or
pick an XML file, name the pool, choose a role (content / reference),
and hit Import. Re-import to refresh, Delete to remove (§16.25).

In Music (macOS), export a playlist as XML via:
**File → Library → Export Playlist… → choose "XML" format**.
(In iTunes on Windows, same path.)

**Advanced: many pools from a manifest** (CLI). The batch path still
ships for power users — drop a JSON manifest at the shape of
[`tests/fixtures/sample_pool_manifest.json`](tests/fixtures/sample_pool_manifest.json)
and run (from the dev clone):

```bash
make import-weekly-pools manifest=path/to/your-manifest.json
```

Useful when you have a fixed pool list to import in CI / on a fresh
box, or when re-bootstrapping a station from scratch. The UI path
above is the recommended hobbyist flow.

## Example stations included

### Rebirth Classics — `stations/rebirth-classics.json`

- 3,010 slots / week, 7 rows × 2 strategy blocks
- Single stateless cursor advancing 60 positions/week, cycling at 16
- Strategy: `window` only (the simplest case)
- **Read this first if you're new — it's the easiest template shape**

### Rebirth Radio — `stations/rebirth-radio.json`

- 2,196 slots / week, all five strategies (`jingle`, `weighted`,
  `mix`, `prev_ref`, `sequential`), nine stateful cursors with
  cross-week advance
- Imported from `weekly/` (a bespoke 1-station generator that
  Station Studio replaces)
- **Read this if you want to see how complex a template can get**

### Roll your own

The visual block editor (§16.2) will let you build a template by
dragging chips onto a timeline. Until that ships, edit one of the
example JSON files or read `DESIGN.md` §14.32 / §14.34 for the
template grammar.

## How it works

```
your iTunes XMLs ──► import into source pools
station template ──► describes what plays when
   │
   └──► engine.generator(template, pools, week, seed)
            │
            ├──► picks: ordered list of (slot, track)
            ├──► trace: per-slot explanation
            └──► persistence: Playlist + Trace rows
                       │
                       └──► export → iTunes XML
```

Seven **strategies** for placing a track at a slot:

- `window` — take N consecutive tracks from a source at a cursor position
- `pick` — one track from a source at a cursor position
- `jingle` — random pick from genre-tagged subset (e.g. "Daytime jingle")
- `weighted` — pick from a source with artist-dedup + recent-tracks shuffle
- `mix` — pick from a genre-tagged subset, excluding tracks used in last N weeks
- `prev_ref` — read this position's track from last week's playlist
- `sequential` — read N consecutive tracks at a named per-station cursor

Each strategy is a small pure-function module under `engine/strategies/`.

## Project status

| | |
|---|---|
| **Engine** | Stable. Byte-equality verified across 30-week chains for two structurally distinct stations. |
| **Backend API** | Stable. FastAPI; full CRUD on stations + sources + rules. |
| **Install** | Wheel + docker-compose both ship and smoke-pass (§16.13). PyPI publish queued (§16.13.t1). |
| **CLI** | `station-studio serve / import / seed` ships with the wheel. Full Makefile target set (refresh, generate, backup, etc.) for in-repo work. |
| **Web UI** | Wizard + trace viewer + Sources page + read-only template visualiser ship; **in-canvas template editor in design** (§16.2 — flagship feature). |
| **Docs** | Engineering docs in `DESIGN.md`; user-facing docs are growing. |
| **License** | [MIT](LICENSE) — fork freely. |

## Contributing

Yes please. See [CONTRIBUTING.md](CONTRIBUTING.md) for slice pattern,
test discipline, and how to propose new station shapes.

## Architecture deep-dive

Engineering details — engine internals, persistence model, slice
discipline, byte-equality test methodology — live in
[DESIGN.md](DESIGN.md). It's a long document; the table of contents
at the top lists every §14.x slice that shipped.

If you're contributing engine work, also see
[`engine/divergence.py`](engine/divergence.py) (slot-level diff
helpers) and [`engine/prior_seed.py`](engine/prior_seed.py) (the
prior-seed validator + the import-prior contract) for how the
port acceptance tests catch regressions. `tests/byte_equality.py`
is a thin re-export shim for backward compatibility.

## Acknowledgements

Station Studio is the next-generation replacement for `weekly/`
(Rebirth Radio's bespoke playlist generator, ~2017-present). The
strategies, template grammar, and most of the engine semantics are
ports of `weekly/`'s design — re-shaped to be expressive enough
for any station, not just Rebirth's specific shape.
