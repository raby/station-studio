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

## Quick start

```bash
git clone https://github.com/raby/station-studio.git
cd station-studio
make install                 # python deps + node deps
make dev                     # FastAPI :8001 + SvelteKit :5173 in parallel
```

Open <http://localhost:5173>. You'll see the two example stations
(Rebirth Classics + Rebirth Radio) seeded automatically; pick one
and explore. To import your own iTunes XMLs, use the wizard's
*Sources → Import* flow.

Full install + first-station tutorial: see [docs/getting-started.md](docs/getting-started.md)
*(coming soon — for now, see `Makefile` + `DESIGN.md` §17.1).*

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

Five **strategies** for placing a track at a slot:

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
| **CLI** | Stable. Import / refresh / generate / backup all scripted in `Makefile`. |
| **Web UI** | Wizard + trace viewer ship; **visual block editor in design** (§16.2 — flagship feature). |
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
[`tests/byte_equality.py`](tests/byte_equality.py) for how the port
acceptance tests catch regressions.

## Acknowledgements

Station Studio is the next-generation replacement for `weekly/`
(Rebirth Radio's bespoke playlist generator, ~2017-present). The
strategies, template grammar, and most of the engine semantics are
ports of `weekly/`'s design — re-shaped to be expressive enough
for any station, not just Rebirth's specific shape.
