"""Dump weekly's GENERATED playlists for a (seed_base, weeks) sweep.

**Designed to run INSIDE a weekly/ checkout, NOT inside station-studio**
(parallel to `scripts/dump_weekly_template.py`). The output is the
"expected playlist sequence" fixture that station-studio's
`tests/test_port_byte_equality.py` compares against, locking down
station-studio's port to byte-equality with weekly's actual generator
output.

Workflow
--------

  1. Operator has a weekly/ checkout with `from_user/` containing
     JINGLES/COMMERCIAL/MIXES/NIGHT/WEEKEND/MASTER.xml + WEEKLY
     PLAYLIST 001.xml (the imported seed for prev_ref).
  2. Run this script inside that checkout's venv:

        PYTHONPATH=/path/to/weekly /path/to/weekly/.venv/bin/python \\
            scripts/dump_weekly_playlist.py \\
            --from-user /path/to/weekly/from_user \\
            --out tests/fixtures/expected/port_byte_eq.json

  3. The driver `make refresh-port-fixture WEEKLY_DIR=… WEEKLY_FROM_USER_DIR=…`
     wraps the invocation.

What gets captured (schema_version 2; §14.38)
---------------------------------------------

  {
    "schema_version": 2,
    "seed_base": 42,
    "weeks_asserted": [2, 3, 5, 9],
    "weeks_generated": [2, 3, 4, 5, 6, 7, 8, 9],
    "priors": [
      {
        "week": 1,
        "pids": ["...persistent IDs in playlist position order..."],
        "cursors": {"NIGHT_1": 120, "NIGHT_2": 130, ..., "WEEKEND_2": 240}
      }
    ],
    "weeks": [
      {"week": 2, "seed": 44, "pids": ["...", ...]},
      ...
    ]
  }

The `priors` list carries imported playlists (today: weekly's WEEKLY
001, length 1) — see DESIGN §14.38 for the contract. Each prior
declares its PERSISTED CURSOR STATE post-import; station-studio's
loader writes the values verbatim to CursorState, no derivation.
Weekly's quirk of hardcoding `advance_step=120` in
`initialize_cursors(post_first=True)` is captured in the dumped
values; station-studio is insulated from it.

Why weeks start at 2: weekly's WEEKLY 001 is the IMPORTED prior-week
playlist (slot_type="imported"), not a generated one. The first
GENERATED playlist is WEEKLY 002. To compare against weekly,
station-studio pre-seeds its own "week 1" with `priors[0].pids` (so
prev_ref reads the same prior) AND seeds `cursor_state` from
`priors[0].cursors` (so NIGHT_1 starts at 120 for the first
generation, matching weekly's post-initialize_cursors state). Then
generates weeks 2-9 with the same seeds.

Schema bump: v1 had `weekly_001_pids` at top level; v2 unifies under
`priors[]` and adds `cursors`. No production consumers; the breaking
change is internal-only.

Implementation note: imports weekly's `app.services.generator` lazily
so `--help` works outside a weekly checkout (mirrors
dump_weekly_template.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


# Convention: station-studio's chain uses seed = SEED_BASE + week.
# Weekly's dump must use the same scheme so the outputs are
# byte-comparable. Default 42 matches the synthetic floor's
# (§14.37 c1) convention.
DEFAULT_SEED_BASE = 42

# Weeks weekly generates in this dump (matches station-studio's
# real-XML test). Asserted weeks are a subset for diversity-of-state.
WEEKS_GENERATED = list(range(2, 10))    # 2, 3, 4, 5, 6, 7, 8, 9
WEEKS_ASSERTED = [2, 3, 5, 9]


def _import_weekly_modules():
    """Import the weekly modules. Errors with a clean message if
    running outside a weekly checkout."""
    try:
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        from app.models import (
            Base, CursorState, SourcePlaylist, Track,
            WeeklyPlaylist, WeeklyPlaylistEntry,
        )
        from app.services.importer import import_source
        from app.services.cursor import initialize_cursors
        from app.services.generator import generate
        return {
            "create_engine": create_engine, "event": event,
            "sessionmaker": sessionmaker, "StaticPool": StaticPool,
            "Base": Base, "SourcePlaylist": SourcePlaylist,
            "CursorState": CursorState,
            "Track": Track, "WeeklyPlaylist": WeeklyPlaylist,
            "WeeklyPlaylistEntry": WeeklyPlaylistEntry,
            "import_source": import_source,
            "initialize_cursors": initialize_cursors,
            "generate": generate,
        }
    except ImportError as e:
        print(
            f"error: failed to import weekly modules: {e}\n"
            f"This script must run inside a weekly/ checkout with weekly's "
            f"venv active (PYTHONPATH=/path/to/weekly /path/to/weekly/.venv/bin/python).",
            file=sys.stderr,
        )
        sys.exit(1)


def _setup_db_with_pools(from_user: Path, mods: dict) -> tuple:
    """Build an in-memory SQLite, import the 6 pools + WEEKLY 001,
    initialize cursors. Returns (engine, session)."""
    eng = mods["create_engine"](
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=mods["StaticPool"],
    )

    @mods["event"].listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    mods["Base"].metadata.create_all(bind=eng)
    Session = mods["sessionmaker"](bind=eng)
    session = Session()

    # Import the 6 content pools.
    mods["import_source"](session, from_user / "MASTER.xml", "MASTER")
    for name in ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND"]:
        mods["import_source"](session, from_user / f"{name}.xml", name)

    # Import + seed WEEKLY 001 as the prior playlist.
    mods["import_source"](session, from_user / "WEEKLY PLAYLIST 001.xml", "WEEKLY_001")
    wp = mods["WeeklyPlaylist"](number=1, seed=0)
    session.add(wp)
    session.flush()
    sp = (
        session.query(mods["SourcePlaylist"])
        .filter(mods["SourcePlaylist"].name == "WEEKLY_001")
        .one()
    )
    for entry in sp.entries:
        session.add(mods["WeeklyPlaylistEntry"](
            weekly_playlist_id=wp.id,
            track_id=entry.track_id,
            position=entry.position,
            slot_type="imported",
        ))
    session.commit()
    mods["initialize_cursors"](session)
    session.commit()

    return eng, session


def _weekly_001_pids(session, mods: dict) -> list[str]:
    """Read WEEKLY 001's tracks in playlist order. Used by station-studio
    as the pre-seeded prior-week playlist (so its prev_ref lookups
    match weekly's)."""
    wp = (
        session.query(mods["WeeklyPlaylist"])
        .filter(mods["WeeklyPlaylist"].number == 1)
        .one()
    )
    entries = (
        session.query(mods["WeeklyPlaylistEntry"])
        .filter(mods["WeeklyPlaylistEntry"].weekly_playlist_id == wp.id)
        .order_by(mods["WeeklyPlaylistEntry"].position)
        .all()
    )
    track_ids = [e.track_id for e in entries]
    tracks = (
        session.query(mods["Track"])
        .filter(mods["Track"].id.in_(track_ids))
        .all()
    )
    pid_by_id = {t.id: t.persistent_id for t in tracks}
    return [pid_by_id[tid] for tid in track_ids]


def _capture_cursors(session, mods: dict) -> dict[str, int]:
    """Read all CursorState rows. Called AFTER `initialize_cursors(
    post_first=True)` has run in `_setup_db_with_pools` — so the
    returned positions are weekly's post-WEEKLY-001-import state
    (== INITIAL_CURSORS[id] + advance_step for each cursor, with
    weekly's hardcoded advance_step=120).

    The dump captures whatever weekly's `initialize_cursors` produced
    verbatim. Station-studio's loader writes these to its own
    CursorState table without re-deriving — insulating station-studio
    from weekly's specific quirk where `initialize_cursors` ignores
    rule-configured advance_step. See DESIGN §14.38.
    """
    rows = session.query(mods["CursorState"]).all()
    return {r.cursor_id: r.position for r in rows}


def _capture_week(session, week: int, mods: dict) -> list[str]:
    """Read the entries for `week` (weekly's `number` column) in
    position order, return the persistent IDs."""
    wp = (
        session.query(mods["WeeklyPlaylist"])
        .filter(mods["WeeklyPlaylist"].number == week)
        .one()
    )
    entries = (
        session.query(mods["WeeklyPlaylistEntry"])
        .filter(mods["WeeklyPlaylistEntry"].weekly_playlist_id == wp.id)
        .order_by(mods["WeeklyPlaylistEntry"].position)
        .all()
    )
    track_ids = [e.track_id for e in entries]
    tracks = (
        session.query(mods["Track"])
        .filter(mods["Track"].id.in_(track_ids))
        .all()
    )
    pid_by_id = {t.id: t.persistent_id for t in tracks}
    return [pid_by_id[tid] for tid in track_ids]


def dump(from_user: Path, out_path: Path, seed_base: int) -> int:
    mods = _import_weekly_modules()

    if not from_user.is_dir():
        print(f"error: --from-user dir not found: {from_user}", file=sys.stderr)
        return 1
    required = ["MASTER.xml", "JINGLES.xml", "COMMERCIAL.xml", "MIXES.xml",
                "NIGHT.xml", "WEEKEND.xml", "WEEKLY PLAYLIST 001.xml"]
    missing = [f for f in required if not (from_user / f).is_file()]
    if missing:
        print(
            f"error: --from-user missing files: {missing}",
            file=sys.stderr,
        )
        return 1

    print(f"setting up weekly DB from {from_user} …", file=sys.stderr)
    _eng, session = _setup_db_with_pools(from_user, mods)

    # Capture the "prior seed" (§14.38): WEEKLY 001's playlist content
    # + the cursor state weekly's `initialize_cursors(post_first=True)`
    # produced post-import. Both must be captured BEFORE the first
    # generation runs — generation mutates cursor state.
    prior_pids = _weekly_001_pids(session, mods)
    prior_cursors = _capture_cursors(session, mods)
    print(
        f"WEEKLY 001 prior seed: {len(prior_pids)} tracks, "
        f"{len(prior_cursors)} cursors (e.g. NIGHT_1={prior_cursors.get('NIGHT_1', 'n/a')})",
        file=sys.stderr,
    )

    weeks_out = []
    for week in WEEKS_GENERATED:
        seed = seed_base + week
        print(f"generating WEEKLY {week:03d} (seed={seed}) …", file=sys.stderr)
        mods["generate"](session, seed=seed)
        # generate() commits internally.
        pids = _capture_week(session, week, mods)
        weeks_out.append({"week": week, "seed": seed, "pids": pids})
        print(f"  → {len(pids)} entries", file=sys.stderr)

    data = {
        "_comment": (
            "Captured from weekly's app.services.generator.generate via "
            "station-studio/scripts/dump_weekly_playlist.py. The `priors` "
            "list carries imported playlists (today: WEEKLY 001, length 1) "
            "with their post-import cursor state. Weeks 2-9 are generated. "
            "station-studio's test_port_byte_equality.py loads priors[0] "
            "(pids + cursors) then asserts station-studio weeks 2/3/5/9 "
            "match the listed PIDs byte-for-byte. See DESIGN §14.38 for "
            "the prior-seed contract; §14.37 for the harness."
        ),
        "schema_version": 2,
        "seed_base": seed_base,
        "weeks_generated": WEEKS_GENERATED,
        "weeks_asserted": WEEKS_ASSERTED,
        "priors": [
            {
                "week": 1,
                "pids": prior_pids,
                "cursors": prior_cursors,
            }
        ],
        "weeks": weeks_out,
    }
    out_path.write_text(json.dumps(data, indent=2) + "\n")
    print(
        f"wrote {out_path} — 1 prior seed ({len(prior_pids)} pids, "
        f"{len(prior_cursors)} cursors) + {len(weeks_out)} generated weeks",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dump weekly's generated playlist outputs for a "
            "(seed_base, weeks) sweep to JSON. Runs inside a weekly checkout."
        ),
    )
    parser.add_argument(
        "--from-user",
        type=Path,
        required=True,
        help="Path to weekly's from_user/ directory (contains the 6 XML "
        "pools + WEEKLY PLAYLIST 001.xml).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output path for the JSON fixture.",
    )
    parser.add_argument(
        "--seed-base",
        type=int,
        default=DEFAULT_SEED_BASE,
        help=f"Seed base (each week's seed = SEED_BASE + week). "
        f"Default {DEFAULT_SEED_BASE} matches the §14.37 floor convention.",
    )
    args = parser.parse_args(argv)
    return dump(args.from_user, args.out, args.seed_base)


if __name__ == "__main__":
    raise SystemExit(main())
