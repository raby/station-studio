"""Seed the database with station templates from JSON.

§16.13.t9: library half of what used to be `scripts/seed_stations.py`.
Moved here so the wheel doesn't need to ship the whole `scripts/`
package — the dev tools (bootstrap_classics, dump_weekly_template,
generate_byte_eq_fixtures, etc) stay in `scripts/`, the
runtime-needed code lives in `engine/` alongside the other library
modules. `scripts/seed_stations.py` becomes a thin CLI shim that
re-exports from here.

Idempotent: creates the station row on first run, updates the template
on subsequent runs if the checked-in JSON has changed. Never deletes.

Used by:
  * `api/main.py:lifespan` — auto-seed bundled stations on first boot.
  * `station_studio/bootstrap.py` (indirectly via api.main).
  * `scripts/seed_stations.py` — `python -m scripts.seed_stations` CLI.
  * Tests (`tests/test_seed_stations.py`, `tests/test_cli.py`,
    `tests/test_api.py`).

The `STATIONS_DIR` constant + `_resolve_stations_dir` helper honour
the `STATION_STUDIO_STATIONS_DIR` env var override (§16.13 c3) for
installed-wheel users.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station
from engine.template import parse_template, TemplateError


REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_STATIONS_DIR = REPO_ROOT / "stations"


def _resolve_stations_dir() -> Path:
    """Resolve the stations directory at call time.

    Reads `STATION_STUDIO_STATIONS_DIR` if set (installed-wheel +
    docker paths set this in the app-home bootstrap), otherwise
    falls back to the repo's `stations/` dir (in-repo dev /
    Makefile-driven workflows).
    """
    override = os.environ.get("STATION_STUDIO_STATIONS_DIR", "").strip()
    if override:
        return Path(override)
    return _REPO_STATIONS_DIR


# Back-compat shim: existing callers (lifespan, tests) read `STATIONS_DIR`
# as a module-level constant. Keep it as the repo default so backward
# compatibility holds; `_collect_paths` uses `_resolve_stations_dir()` now.
STATIONS_DIR = _REPO_STATIONS_DIR


def seed_station(db: Session, path: Path) -> dict:
    """Load one station template from JSON and upsert it.

    Returns a summary dict with slug, action
    (created|updated|unchanged), and slot_count.
    """
    with open(path) as f:
        raw = json.load(f)

    try:
        template = parse_template(raw)
    except TemplateError as e:
        raise SystemExit(f"invalid template at {path}: {e}")

    slug = template.station
    name = raw.get("name", slug)

    existing = db.query(Station).filter(Station.slug == slug).first()
    if existing is None:
        db.add(Station(slug=slug, name=name, template=raw))
        action = "created"
    elif existing.template != raw:
        existing.template = raw
        existing.name = name
        action = "updated"
    else:
        action = "unchanged"

    return {
        "slug": slug,
        "action": action,
        "slot_count": template.slots_per_week(),
    }


def seed_all(db: Session, paths: list[Path]) -> list[dict]:
    """Seed every template in `paths` and commit once at the end."""
    summaries = [seed_station(db, p) for p in paths]
    db.commit()
    return summaries


def _collect_paths(argv: list[str]) -> list[Path]:
    """Resolve the station JSONs to seed.

    Explicit args override the glob. Default: every `*.json` under
    `stations/` EXCEPT:

      * `*.pool-manifest.json` — §14.36 pool manifests co-located
        with their station's template (e.g. `rebirth-radio.json` +
        `rebirth-radio.pool-manifest.json`).

      * `*.editor.json` — §16.2.1 editor metadata sidecars co-located
        with their station's template (e.g. `rebirth-radio.json` +
        `rebirth-radio.editor.json`). Engine ignores the sidecar;
        seed_stations would otherwise mis-parse it as a template.

    Filtering by suffix keeps the manifest + sidecar next to their
    station without forcing a deeper directory structure.
    """
    if argv:
        return [Path(a).resolve() for a in argv]
    stations_dir = _resolve_stations_dir()
    return sorted(
        p for p in stations_dir.glob("*.json")
        if not (
            p.name.endswith(".pool-manifest.json")
            or p.name.endswith(".editor.json")
        )
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: load station JSONs + seed the DB. Mirrors the
    historical `python -m scripts.seed_stations` invocation."""
    argv = argv if argv is not None else sys.argv[1:]
    paths = _collect_paths(argv)
    if not paths:
        print("no station JSON files found", file=sys.stderr)
        return 1

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)

    with SessionLocal() as db:
        summaries = seed_all(db, paths)

    for s in summaries:
        print(f"  {s['action']:<9}  {s['slug']:<30}  {s['slot_count']:>6} slots/week")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
