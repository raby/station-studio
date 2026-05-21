"""Seed the database with one or more station templates from JSON.

Idempotent: creates the station row on first run, updates the template on
subsequent runs if the checked-in JSON has changed. Never deletes.

Usage
-----
    # Seed all templates in stations/ (default)
    python -m scripts.seed_stations

    # Seed a specific file
    python -m scripts.seed_stations stations/rebirth-classics.json

    # Use a different DB (e.g. in tests)
    STATION_STUDIO_DB_URL=sqlite:///tmp.db python -m scripts.seed_stations

Why have this at all? The stations/ JSON files are the checked-in source of
truth; the DB is just a cache. A fresh machine needs one command to become
useful — this is it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station
from engine.template import parse_template, TemplateError


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIONS_DIR = REPO_ROOT / "stations"


def seed_station(db: Session, path: Path) -> dict:
    """Load one station template from JSON and upsert it.

    Returns a summary dict with slug, action (created|updated|unchanged), and slot_count.
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

    return {"slug": slug, "action": action, "slot_count": template.slots_per_week()}


def seed_all(db: Session, paths: list[Path]) -> list[dict]:
    summaries = [seed_station(db, p) for p in paths]
    db.commit()
    return summaries


def _collect_paths(argv: list[str]) -> list[Path]:
    """Resolve the station JSONs to seed.

    Explicit args override the glob. Default: every `*.json` under
    `stations/` EXCEPT `*.pool-manifest.json` — those are §14.36 pool
    manifests co-located with their station's template JSON (e.g.
    `rebirth-radio.json` + `rebirth-radio.pool-manifest.json`).
    Filtering by suffix here keeps the manifest next to its station
    without it being mis-parsed as a template.
    """
    if argv:
        return [Path(a).resolve() for a in argv]
    return sorted(
        p for p in STATIONS_DIR.glob("*.json")
        if not p.name.endswith(".pool-manifest.json")
    )


def main(argv: list[str] | None = None) -> int:
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
