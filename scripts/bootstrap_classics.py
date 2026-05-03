"""One-command bootstrap for Rebirth Classics.

Idempotent pipeline:
  1. Create DB schema if missing
  2. Seed rebirth-classics into the station table
  3. Import RG_RADIO.xml, RG_PARTY.xml, RG_MASTER.xml from from_user/
  4. Generate week 1 (configurable) and print a summary

Usage
-----
    # Default: from_user/, generates week 1
    python -m scripts.bootstrap_classics

    # Different directory
    python -m scripts.bootstrap_classics --from-dir /path/to/xmls

    # Generate a different week (e.g. 5)
    python -m scripts.bootstrap_classics --week 5

    # Skip generation if one or more content pools are missing
    python -m scripts.bootstrap_classics --skip-missing

Design: this script is a thin composition over scripts.seed_stations,
engine.importer, and engine.generator. Every step is independently testable.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.importer import import_source
from engine.generator import generate
from engine.models import Base, Station
from engine.persistence import load_pools, save_playlist
from engine.template import parse_template
from scripts.seed_stations import seed_station


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIONS_DIR = REPO_ROOT / "stations"
DEFAULT_FROM_DIR = REPO_ROOT / "from_user"
CLASSICS_SLUG = "rebirth-classics"
CLASSICS_JSON = STATIONS_DIR / "rebirth-classics.json"

# Map XML filename -> pool name. Order matters: reference pool last so Track
# Persistent-ID dedup resolves content tracks to their first-seen form.
EXPECTED_FILES = [
    ("RG_RADIO.xml", "RG_RADIO"),
    ("RG_PARTY.xml", "RG_PARTY"),
    ("RG_MASTER.xml", "RG_MASTER"),
]


@dataclass
class BootstrapResult:
    station_action: str        # "created" | "updated" | "unchanged"
    imported: list[dict]       # one summary dict per XML actually imported
    missing: list[str]         # pool names whose files weren't found
    playlist_id: int | None
    trace_id: str | None
    slot_count: int | None


def bootstrap(
    db: Session,
    *,
    from_dir: Path = DEFAULT_FROM_DIR,
    week: int = 1,
    skip_missing: bool = False,
    generate_playlist: bool = True,
) -> BootstrapResult:
    # 1. Seed the station
    station_summary = seed_station(db, CLASSICS_JSON)
    db.commit()

    # 2. Import whichever files are present
    imported: list[dict] = []
    missing: list[str] = []
    for filename, pool_name in EXPECTED_FILES:
        path = from_dir / filename
        if not path.exists():
            missing.append(pool_name)
            continue
        imported.append(import_source(db, path, pool_name))

    # 3. Decide whether to generate
    station = db.query(Station).filter_by(slug=CLASSICS_SLUG).one()
    template = parse_template(station.template)

    content_missing = [p for p in template.content_source_names if p in missing]
    if content_missing and not skip_missing:
        raise SystemExit(
            f"content pool(s) missing: {content_missing}. "
            f"Place the XML(s) in {from_dir}/ or pass --skip-missing to bootstrap "
            f"without generating."
        )

    if not generate_playlist or content_missing:
        return BootstrapResult(
            station_action=station_summary["action"],
            imported=imported,
            missing=missing,
            playlist_id=None,
            trace_id=None,
            slot_count=None,
        )

    # 4. Generate
    pools = load_pools(db, template)
    result = generate(template, pools, week=week)
    playlist = save_playlist(db, station, result, week=week)
    db.commit()

    return BootstrapResult(
        station_action=station_summary["action"],
        imported=imported,
        missing=missing,
        playlist_id=playlist.id,
        trace_id=result.trace.trace_id,
        slot_count=len(result.picks),
    )


# ---------- CLI ----------------------------------------------------------


def _print_report(result: BootstrapResult, *, from_dir: Path, week: int) -> None:
    print(f"Station:    rebirth-classics ({result.station_action})")
    print(f"Imports:    {len(result.imported)} of {len(EXPECTED_FILES)} from {from_dir}/")
    for s in result.imported:
        print(f"            {s['name']:<12} {s['tracks_imported']:>5} tracks / {s['entries_imported']:>5} entries")
    for name in result.missing:
        print(f"            {name:<12}     -  (not found)")
    if result.playlist_id is None:
        print("Playlist:   not generated")
    else:
        print(f"Playlist:   week {week}, {result.slot_count} slots, playlist_id={result.playlist_id}")
        print(f"Trace:      {result.trace_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from-dir", type=Path, default=DEFAULT_FROM_DIR,
                        help=f"Directory with RG_*.xml files (default: {DEFAULT_FROM_DIR})")
    parser.add_argument("--week", type=int, default=1, help="Week number to generate (default 1)")
    parser.add_argument("--skip-missing", action="store_true",
                        help="Skip generation if content pool(s) missing")
    parser.add_argument("--no-generate", action="store_true",
                        help="Seed + import only; don't generate a playlist")
    args = parser.parse_args(argv)

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)

    try:
        with SessionLocal() as db:
            result = bootstrap(
                db,
                from_dir=args.from_dir,
                week=args.week,
                skip_missing=args.skip_missing,
                generate_playlist=not args.no_generate,
            )
    except SystemExit as e:
        print(f"bootstrap_classics: {e}", file=sys.stderr)
        return 1

    _print_report(result, from_dir=args.from_dir, week=args.week)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
