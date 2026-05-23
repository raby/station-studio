"""Generate N weeks of Rebirth Radio playlists as iTunes XML files.

For user-facing validation (NOT byte-equality). Drops real iTunes-XML
playlist files in an output directory so an operator can import them
into Apple Music/iTunes + play them to confirm the port sounds right.

Two modes — both run by default:

  * ``fresh``  — no prior seed. All `prev_ref` slots take their
    fallback path. Generates weeks 1..N. Tests station-studio in
    isolation; doesn't reflect real production cutover state.
  * ``prior`` — pre-seeds week 1 with weekly's WEEKLY 001 playlist
    (read from the committed `tests/fixtures/expected/port_byte_eq.json`).
    Generates weeks 2..N+1. Matches what would happen on a real
    cutover.

Use ``--mode fresh|prior|both`` to constrain. Default ``both`` writes
both sets to subdirectories of the output dir.

Usage
-----

    # Default: generate 25 weeks in both modes, output to output/user-test/.
    make generate-user-playlists \\
        WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user

    # Direct, with custom prefix:
    python -m scripts.generate_user_playlists \\
        --xmls /path/to/weekly/from_user \\
        --output-dir output/user-test \\
        --name-prefix "Rebirth Radio Test" \\
        --weeks 25

What lands on disk
------------------

    output/user-test/
      fresh/
        REBIRTH_RADIO_001.xml   # Display name: "Rebirth Radio 001"
        REBIRTH_RADIO_002.xml
        …
        REBIRTH_RADIO_025.xml
      prior/
        REBIRTH_RADIO_002.xml   # Display name: "Rebirth Radio 002"
        REBIRTH_RADIO_003.xml
        …
        REBIRTH_RADIO_026.xml

Each file is a complete iTunes XML plist iTunes/Music will accept on
File → Library → Import Playlist. The Playlist Persistent ID is
derived from the station slug + week, so re-importing replaces
cleanly.

Not committed to the repo; output is user-machine-specific. The
output directory is created if missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.exporter import export_playlist
from engine.importer import import_source
from engine.models import (
    Base, CursorState, Playlist, PlaylistEntry, Station, Track,
)
from tests.byte_equality import generate_chain, trace_id_for_prior


REPO_ROOT = Path(__file__).resolve().parent.parent
STATION_TEMPLATE = REPO_ROOT / "stations" / "rebirth-radio.json"
PORT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "expected" / "port_byte_eq.json"

REQUIRED_XMLS = [
    "MASTER.xml", "JINGLES.xml", "COMMERCIAL.xml",
    "MIXES.xml", "NIGHT.xml", "WEEKEND.xml",
]


def _setup_db_with_station(
    tmp_db: Path,
    xmls_dir: Path,
    station_name: str,
) -> tuple[object, Session]:
    """Build a fresh DB with imported pools + the rebirth-radio station.

    `station_name` is what shows up in the exported XML's Name field
    (the exporter formats playlist names as `{station.name} {week:03d}`).
    """
    engine = make_engine(f"sqlite:///{tmp_db}")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    db = SessionLocal()

    # Import the 6 pools.
    import_source(db, xmls_dir / "MASTER.xml", "MASTER", role="reference")
    for name in ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND"]:
        import_source(db, xmls_dir / f"{name}.xml", name, role="content")

    # Seed the rebirth-radio station, overriding `name` with the chosen
    # prefix so exported playlists carry that label.
    tmpl_dict = json.loads(STATION_TEMPLATE.read_text())
    db.add(Station(
        slug=tmpl_dict["station"],
        name=station_name,
        template=tmpl_dict,
    ))
    db.commit()
    return engine, db


def _seed_prior_from_fixture(db: Session) -> None:
    """Load priors[0] from the committed port fixture and write it as
    Playlist(week=1) + CursorState rows. Reuses the §14.38 prior-seed
    captured by `scripts/dump_weekly_playlist.py`.
    """
    if not PORT_FIXTURE.is_file():
        raise SystemExit(
            f"error: prior mode requires {PORT_FIXTURE}. Run "
            f"`make refresh-port-fixture` first."
        )
    fixture = json.loads(PORT_FIXTURE.read_text())
    priors = fixture.get("priors") or []
    if not priors:
        raise SystemExit(
            "error: port fixture has no priors. Did §14.38 ship yet?"
        )
    prior = priors[0]
    station = db.query(Station).filter_by(slug="rebirth-radio").one()

    # Resolve PIDs → Track rows. Tracks not present in the imported
    # pools are silently skipped (mirrors weekly's import; some
    # WEEKLY 001 tracks aren't in the active rotation).
    pid_to_track = {
        t.persistent_id: t
        for t in db.query(Track).filter(
            Track.persistent_id.in_(prior["pids"])
        ).all()
    }

    playlist = Playlist(
        station_id=station.id,
        week=prior["week"],
        trace_id=trace_id_for_prior(prior["week"]),
    )
    db.add(playlist)
    db.flush()
    for position, pid in enumerate(prior["pids"]):
        track = pid_to_track.get(pid)
        if track is None:
            continue
        db.add(PlaylistEntry(
            playlist_id=playlist.id,
            track_id=track.id,
            position=position,
            slot_type="imported",
        ))

    # Cursor state — the §14.38 prior carries post-WEEKLY-001-import
    # cursor positions. Writing them verbatim is the whole point.
    for cursor_name, position in prior["cursors"].items():
        db.add(CursorState(
            station_id=station.id, name=cursor_name, position=position,
        ))
    db.commit()


def _export_weeks_to_disk(
    db: Session,
    station: Station,
    weeks: list[int],
    out_dir: Path,
) -> None:
    """Export each generated week's playlist to `{out_dir}/{SLUG}_{NNN}.xml`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    slug_upper = station.slug.upper().replace("-", "_")
    for week in weeks:
        pl = (
            db.query(Playlist)
            .filter_by(station_id=station.id, week=week)
            .one()
        )
        xml_bytes = export_playlist(db, pl.id)
        path = out_dir / f"{slug_upper}_{week:03d}.xml"
        path.write_bytes(xml_bytes)
        print(
            f"  wrote {path.relative_to(REPO_ROOT)} "
            f"({len(xml_bytes) // 1024} KB)"
        )


def _run_mode(
    *,
    mode: str,
    xmls_dir: Path,
    out_dir: Path,
    name_prefix: str,
    weeks: int,
    seed_base: int,
    tmp_db: Path,
) -> None:
    """Run one mode (fresh or prior). Each mode uses its own fresh DB."""
    print(f"\n[{mode}] setting up DB + importing pools …")
    engine, db = _setup_db_with_station(tmp_db, xmls_dir, name_prefix)
    try:
        if mode == "prior":
            print(f"[{mode}] seeding WEEKLY 001 prior from port fixture …")
            _seed_prior_from_fixture(db)
            # weeks 2..N+1 in prior mode (week 1 is the imported prior).
            week_range = list(range(2, weeks + 2))
        else:
            # weeks 1..N in fresh mode (no prior).
            week_range = list(range(1, weeks + 1))

        print(f"[{mode}] generating weeks {week_range[0]}..{week_range[-1]} …")
        generate_chain(
            db,
            station_slug="rebirth-radio",
            weeks=week_range,
            seed_base=seed_base,
        )

        print(f"[{mode}] exporting {len(week_range)} XMLs → {out_dir}/")
        station = db.query(Station).filter_by(slug="rebirth-radio").one()
        _export_weeks_to_disk(db, station, week_range, out_dir)
    finally:
        db.close()
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate N weeks of Rebirth Radio iTunes XML playlists "
            "for user-facing validation. NOT a byte-equality test — "
            "produces real artifacts you can play."
        ),
    )
    parser.add_argument(
        "--xmls",
        type=Path,
        required=True,
        help="Path to weekly's from_user/ directory containing "
        "JINGLES/COMMERCIAL/MIXES/NIGHT/WEEKEND/MASTER.xml.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "output" / "user-test",
        help="Output directory. Subdirectories `fresh/` and/or "
        "`prior/` are created depending on --mode (default: %(default)s).",
    )
    parser.add_argument(
        "--name-prefix",
        default="Rebirth Radio",
        help="Display name prefix for exported playlists (default: "
        "%(default)r). XML 'Name' = '{prefix} {NNN}'. Pass e.g. "
        "\"WEEKLY PLAYLIST\" to match weekly's naming for blind A/B.",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=25,
        help="Number of weeks per mode (default: %(default)s).",
    )
    parser.add_argument(
        "--seed-base",
        type=int,
        default=42,
        help="Seed base (each week's seed = SEED_BASE + week). "
        "Default matches the byte-equality test convention.",
    )
    parser.add_argument(
        "--mode",
        choices=["fresh", "prior", "both"],
        default="both",
        help="`fresh` = no prior (weeks 1..N), `prior` = WEEKLY 001 "
        "as prior (weeks 2..N+1), `both` = run both (default).",
    )
    args = parser.parse_args(argv)

    if not args.xmls.is_dir():
        print(f"error: --xmls dir not found: {args.xmls}", file=sys.stderr)
        return 1
    missing = [f for f in REQUIRED_XMLS if not (args.xmls / f).is_file()]
    if missing:
        print(
            f"error: missing required XMLs in {args.xmls}: {missing}",
            file=sys.stderr,
        )
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    modes_to_run = ["fresh", "prior"] if args.mode == "both" else [args.mode]
    for mode in modes_to_run:
        # Each mode uses its own ephemeral DB file (auto-cleaned on exit
        # via the `engine.dispose()` close). Place under output_dir so
        # rm -rf output_dir cleans everything.
        tmp_db = args.output_dir / f".{mode}.db"
        out_dir = args.output_dir / mode
        try:
            _run_mode(
                mode=mode,
                xmls_dir=args.xmls,
                out_dir=out_dir,
                name_prefix=args.name_prefix,
                weeks=args.weeks,
                seed_base=args.seed_base,
                tmp_db=tmp_db,
            )
        finally:
            # Clean up the ephemeral DB file once exported.
            tmp_db.unlink(missing_ok=True)

    print(f"\nDone. Import the XMLs under {args.output_dir}/ into "
          f"iTunes/Music via File → Library → Import Playlist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
