"""Byte-equality verification against weekly's real generator output
(§14.37 commit 2 — closes Phase B).

This is the port's ACCEPTANCE TEST. Given:
  * Weekly's real 6-pool XMLs (from a checkout's `from_user/`)
  * The committed port artifact `stations/rebirth-radio.json`
  * The committed expected fixture
    `tests/fixtures/expected/port_byte_eq.json` (produced by
    `scripts/dump_weekly_playlist.py`)

…station-studio's generator should produce the same persistent-ID
sequence as weekly's for matching `(seed, week)` inputs, weeks 2-9.

Skip-when-missing semantics
---------------------------

Real XML files are user data (copyrighted track metadata; see §14.35
+ §14.36 punt notes). They are not in the repo. The test skips
cleanly when the XML dir isn't configured:

  * Set `WEEKLY_FROM_USER_DIR=/path/to/weekly/from_user` to enable.
  * Set `REQUIRE_WEEKLY_XMLS=1` to FAIL instead of skip when the
    dir is missing — same pattern as weekly's REQUIRE_XML_FIXTURES.
    Recommended for the local dev workflow that's authoring the port;
    CI doesn't have the data so leaves it unset.

If station-studio matches weekly byte-for-byte across the 4 asserted
weeks, the port is done — Phase B closes out cleanly.

If it doesn't match, the diagnostic from
`tests/byte_equality.py:assert_chain_equal` pinpoints the first
divergent (week, slot) coordinate with surrounding context. From
there: file a follow-on slice per divergence; this test stays in
the codebase, will go green when each fix lands, and Phase B closes
properly when it does.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.importer import import_source
from engine.models import Base, Playlist, PlaylistEntry, Station, Track
from engine.template import parse_template
from tests.byte_equality import (
    assert_chain_equal, expand_block_slot_refs, generate_chain,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
STATION_TEMPLATE = REPO_ROOT / "stations" / "rebirth-radio.json"
EXPECTED_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "expected" / "port_byte_eq.json"


def _from_user_dir() -> Path | None:
    """Resolve the weekly XML directory from env, or None if unset."""
    raw = os.environ.get("WEEKLY_FROM_USER_DIR", "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _require_xmls() -> bool:
    return os.environ.get("REQUIRE_WEEKLY_XMLS", "").strip() == "1"


def _check_xmls_or_skip(from_user: Path | None) -> Path:
    """Return the validated from_user path, or skip / fail per env config."""
    required_files = [
        "MASTER.xml", "JINGLES.xml", "COMMERCIAL.xml",
        "MIXES.xml", "NIGHT.xml", "WEEKEND.xml",
    ]
    if from_user is None or not from_user.is_dir():
        if _require_xmls():
            pytest.fail(
                "REQUIRE_WEEKLY_XMLS=1 but WEEKLY_FROM_USER_DIR is unset "
                f"or not a directory (got: {from_user!r}). Set the env var "
                "to /path/to/weekly/from_user."
            )
        pytest.skip(
            "WEEKLY_FROM_USER_DIR not set (or directory missing). "
            "Set it to a weekly checkout's from_user/ to enable the "
            "real-XML byte-equality test. Skipping in this env."
        )
    missing = [f for f in required_files if not (from_user / f).is_file()]
    if missing:
        if _require_xmls():
            pytest.fail(
                f"REQUIRE_WEEKLY_XMLS=1 but missing XMLs in {from_user}: "
                f"{missing}"
            )
        pytest.skip(
            f"missing XMLs in {from_user}: {missing}. Skipping."
        )
    return from_user


@pytest.fixture
def port_db(tmp_path) -> Session:
    """Build a DB matching weekly's `loaded_db` fixture shape:

      1. Import the 6 content pools (JINGLES/COMMERCIAL/MIXES/NIGHT/
         WEEKEND content, MASTER reference).
      2. Seed the rebirth-radio station from the committed template.
      3. Pre-seed Playlist(week=1) with the committed
         `weekly_001_pids` fixture — analogous to weekly's
         `loaded_db` importing WEEKLY PLAYLIST 001 as the imported
         prior. This is the prior that station-studio's prev_ref
         lookups (in weeks 2+) read.

    The chain then runs weeks 2-9 (matching weekly's WEEKLY 002-009).
    """
    from_user = _check_xmls_or_skip(_from_user_dir())

    # Confirm the fixture is committed (we always commit it; ship it
    # broken is the failure mode).
    if not EXPECTED_FIXTURE.is_file():
        pytest.fail(
            f"missing fixture {EXPECTED_FIXTURE}. Regenerate it via "
            f"`make refresh-port-fixture WEEKLY_DIR=… WEEKLY_FROM_USER_DIR=…`"
        )
    fixture = json.loads(EXPECTED_FIXTURE.read_text())

    engine = make_engine(f"sqlite:///{tmp_path}/port.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    db = SessionLocal()
    try:
        # Step 1 — import pools. MASTER is reference; the 5 content
        # pools are samplable. import_source is idempotent.
        import_source(db, from_user / "MASTER.xml", "MASTER", role="reference")
        for name in ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND"]:
            import_source(db, from_user / f"{name}.xml", name, role="content")

        # Step 2 — seed the station from the committed template.
        tmpl_dict = json.loads(STATION_TEMPLATE.read_text())
        station = Station(
            slug=tmpl_dict["station"],
            name=tmpl_dict["name"],
            template=tmpl_dict,
        )
        db.add(station)
        db.flush()

        # Step 3 — pre-seed Playlist(week=1) with WEEKLY 001's PIDs.
        # Look up Track ids by persistent_id (the comparison-stable
        # cross-pool identifier). Some WEEKLY 001 tracks aren't in the
        # 6 imported pools (weekly's WEEKLY 001 references tracks from
        # earlier playlists that aren't in the active pools); those
        # entries are skipped, mirroring weekly's import_source which
        # silently skips unresolved track refs.
        weekly_001_pids = fixture["weekly_001_pids"]
        pid_to_track = {
            t.persistent_id: t
            for t in db.query(Track).filter(
                Track.persistent_id.in_(weekly_001_pids)
            ).all()
        }
        # Sentinel trace_id: the imported seed playlist has no real
        # trace (it's not a station-studio generation; it's weekly's
        # imported WEEKLY 001 standing in as the prior). The Playlist
        # row requires a non-null trace_id, so we use a marker string
        # that's clearly not a UUID. prev_ref doesn't read the trace.
        prior = Playlist(
            station_id=station.id, week=1,
            trace_id="imported-weekly-001",
        )
        db.add(prior)
        db.flush()
        for position, pid in enumerate(weekly_001_pids):
            track = pid_to_track.get(pid)
            if track is None:
                # Defensive: weekly's WEEKLY 001 XML references some
                # tracks that aren't in the 6 active pools.
                # Skipping mirrors weekly's own import behaviour.
                continue
            db.add(PlaylistEntry(
                playlist_id=prior.id,
                track_id=track.id,
                position=position,
                slot_type="imported",
            ))
        db.commit()
        yield db
    finally:
        db.close()


def test_rebirth_radio_chain_matches_weekly_byte_for_byte(port_db):
    """The port's acceptance criterion. Station-studio generates
    weeks 2-9 against the same pools + same seeds as weekly; weeks
    2/3/5/9 are asserted byte-for-byte (PID-for-PID) against the
    committed fixture.

    This test FAILS LOUDLY today on the §14.38 cursor-bootstrap model
    gap (see DESIGN §14.37). It's intentionally not marked xfail —
    Phase B's port acceptance is not closed, and silently green CI
    on the acceptance test would hide that. The skip-when-missing
    pattern (no XMLs → pytest.skip) keeps CI green where real
    weekly data isn't available; local dev with XMLs gets the
    failure surfaced unmistakably.

    The diagnostic from `assert_chain_equal` surfaces the first
    divergent (week, slot, pick_in_block) coordinate. When the
    §14.38 / §14.39 / §14.40 follow-on slices land + the test goes
    green, that signals genuine Phase B closure.
    """
    fixture = json.loads(EXPECTED_FIXTURE.read_text())
    seed_base = fixture["seed_base"]
    weeks_generated = fixture["weeks_generated"]  # [2, 3, 4, 5, 6, 7, 8, 9]
    weeks_asserted = fixture["weeks_asserted"]    # [2, 3, 5, 9]

    pids_by_week = generate_chain(
        port_db,
        station_slug="rebirth-radio",
        weeks=weeks_generated,
        seed_base=seed_base,
        # Engine defaults — match weekly's cursor_advance_step=120,
        # cursor_reset_cycle=0 rule defaults. Explicit to document.
        advance_step=120,
        reset_cycle=0,
    )

    # Build expected chain from the fixture.
    expected_by_week = {w["week"]: w["pids"] for w in fixture["weeks"]}

    # Build slot refs PER PICK (not per block). After §14.37 c2's
    # sequential coalescing, blocks can have size>1 — a per-block
    # ref list would label the wrong template position when the
    # divergence is inside a multi-pick block. expand_block_slot_refs
    # expands each block to size=N refs sharing block-level metadata
    # but distinguished by `pick_in_block`, so the diagnostic accurately
    # identifies which pick diverged.
    tmpl = parse_template(json.loads(STATION_TEMPLATE.read_text()))

    expected_chain = {w: expected_by_week[w] for w in weeks_asserted}
    actual_chain = {w: pids_by_week[w] for w in weeks_asserted}
    slot_refs_by_week = {
        w: expand_block_slot_refs(week=w, blocks=tmpl.layout.flat_slots)
        for w in weeks_asserted
    }

    assert_chain_equal(expected_chain, actual_chain, slot_refs_by_week)
