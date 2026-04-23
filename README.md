# Station Studio

A generalised radio-station playlist generator. Users describe a station (slot template, source pools, cursor rules) via a web wizard, and the engine generates weekly playlists from imported iTunes XML source files.

Sister project to `weekly/` (Rebirth Radio). First target station: **Rebirth Classics**.

## Layout

```
station-studio/
  engine/               pure Python library (no FastAPI, no UI)
    models.py           SQLAlchemy ORM
    database.py         engine setup
    importer.py         iTunes XML importer (lifted from weekly)
    template.py         station-template validator + expander
    cursor.py           per-station cursor state
    strategies/         slot-type primitives
      window.py
      pick.py
    generator.py        template + pools + week -> (playlist, trace)
    trace.py            trace dataclasses + trace_id

  api/                  FastAPI thin wrapper over engine
    main.py
    schemas.py
    routers/
      stations.py       CRUD station templates
      sources.py        import + list source playlists
      generate.py       generate + return playlist + trace

  stations/             checked-in station templates
    rebirth-classics.json

  tests/                pytest suite
    test_classics_generation.py

  web/                  SvelteKit app (wizard + trace viewer)
```

## Quick start

```bash
pip install -r requirements.txt
python -m pytest
python run.py                          # FastAPI at http://localhost:8001
```

## Bootstrapping Rebirth Classics

Once you have the three iTunes XMLs in `from_user/` (see `from_user/README.md`):

```bash
python -m scripts.bootstrap_classics           # seed + import + generate week 1
python -m scripts.bootstrap_classics --week 5  # different week
python -m scripts.bootstrap_classics --skip-missing   # ok if some files absent
python -m scripts.bootstrap_classics --no-generate    # seed + import only
```

Idempotent — re-run after replacing an XML and it re-imports cleanly.

## Design principles

- **Template JSON is the canonical artifact.** The UI reads and writes the same JSON the engine consumes; no hidden UI-only state.
- **Engine has small primitives.** `pick`, `window`. Future: `sequential`, `weighted`, `prev_ref`, `genre_pick`. If a station's structure can be expressed by shaping the source files (e.g. pre-embedded jingles), prefer that over adding an engine primitive.
- **Everything deterministic unless explicitly randomised.** `rng: "off"` means the playlist is fully determined by `template + pools + week_number`.
- **Every generated playlist carries a `trace_id`.** The trace explains, per slot, *why* that track was picked (source, pool index, strategy). Round-trippable — given a `trace_id` you can retrieve the full trace later.

## First station: Rebirth Classics

- 3 source imports: `RG_RADIO`, `RG_PARTY` (content), `RG_MASTER` (reference, not used by layout)
- 7 rows per week, one per day (Mon-Sun), 430 slots per row = 3,010 slots
- Each row: 215-track window from `RG_RADIO` followed by 215-track window from `RG_PARTY`
- Row start offsets: `[0, 10, 20, 30, 40, 50, 50]` (row 7 duplicates row 6 deliberately, to keep weekend cohesion)
- Cursor advances +60 per week; cycles at 16 weeks
- No RNG

See `stations/rebirth-classics.json`.
