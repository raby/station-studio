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
from engine.models import (
    Base, CursorState, Playlist, PlaylistEntry, Station, Track,
)
from engine.template import parse_template
from tests.byte_equality import (
    PriorSeedError,
    assert_chain_equal,
    expand_block_slot_refs,
    generate_chain,
    trace_id_for_prior,
    validate_prior_cursor_names,
    validate_prior_seed_fixture,
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


# §14.38 prior-seed validator + loader. The strict-validation helpers
# (validate_prior_seed_fixture, validate_prior_cursor_names,
# trace_id_for_prior, PriorSeedError) live in tests/byte_equality.py
# for cross-station reuse — see the §14.40 review sweep-up that hoisted
# them. Only port-specific knobs (the drop-rate threshold) stay here.


# Max fraction of PID-resolution drops before the loader fails. A few
# drops are expected (weekly's WEEKLY 001 references historical tracks
# no longer in active pools — matches weekly's importer silent-skip).
# Many drops mean the fixture is stale OR pools were imported from a
# different XML revision. R2 sweep-up.
_MAX_PID_DROP_FRACTION = 0.05


@pytest.fixture
def port_db(tmp_path) -> Session:
    """Build a DB with weekly's imported prior seed (§14.38):

      1. Import the 6 content pools (JINGLES/COMMERCIAL/MIXES/NIGHT/
         WEEKEND content, MASTER reference).
      2. Seed the rebirth-radio station from the committed template.
      3. Load `priors[0]` from the fixture (§14.38 prior-seed contract):
         - Create Playlist(week=1) + PlaylistEntry rows from
           `priors[0].pids` (prev_ref reads these).
         - Create CursorState rows from `priors[0].cursors` (so
           NIGHT_1 starts at the post-WEEKLY-001-import position
           weekly's `initialize_cursors(post_first=True)` produced,
           = 120 by default — captured verbatim in the fixture).

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

    # Strict validation (§14.38 R1 sweep-up) — lifts the §14.32/.34/.35
    # pattern. A fixture typo (`cursor` for `cursors`, missing field,
    # wrong types) now fails loud with a clear message.
    try:
        validate_prior_seed_fixture(fixture)
    except PriorSeedError as e:
        pytest.fail(f"fixture validation: {e}")

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

        # Step 3 — load priors (0 or 1 entries; multi-prior is §14.38b).
        priors = fixture["priors"]
        if priors:
            prior = priors[0]
            # Cross-validate prior's cursor names against the template's
            # declared cursors (R1 sweep-up).
            template_cursors = set(
                tmpl_dict.get("layout", {}).get("cursors_init", {}).keys()
            )
            try:
                validate_prior_cursor_names(prior, template_cursors)
            except PriorSeedError as e:
                pytest.fail(f"fixture validation: {e}")

            # 3a. Pre-seed Playlist(week=N) + PlaylistEntry rows. Some
            # WEEKLY 001 tracks aren't in the 6 active pools (weekly
            # has historical tracks no longer in rotation); a small
            # fraction of drops is expected and matches weekly's
            # importer. But a HIGH drop rate means the fixture is
            # stale or pools drifted — fail loud (R2 sweep-up).
            prior_week = prior["week"]
            prior_pids = prior["pids"]
            prior_cursors = prior["cursors"]

            pid_to_track = {
                t.persistent_id: t
                for t in db.query(Track).filter(
                    Track.persistent_id.in_(prior_pids)
                ).all()
            }
            dropped = sum(1 for p in prior_pids if p not in pid_to_track)
            if prior_pids and dropped / len(prior_pids) > _MAX_PID_DROP_FRACTION:
                pytest.fail(
                    f"prior PID resolution dropped {dropped} of "
                    f"{len(prior_pids)} ({dropped / len(prior_pids):.0%}) "
                    f"— more than {_MAX_PID_DROP_FRACTION:.0%}. Fixture "
                    f"is likely stale or pools were imported from a "
                    f"different XML revision. Run `make refresh-port-"
                    f"everything WEEKLY_DIR=… WEEKLY_FROM_USER_DIR=…`."
                )

            prior_playlist = Playlist(
                station_id=station.id, week=prior_week,
                trace_id=trace_id_for_prior(prior_week),
            )
            db.add(prior_playlist)
            db.flush()
            for position, pid in enumerate(prior_pids):
                track = pid_to_track.get(pid)
                if track is None:
                    # Sub-threshold drop (see fail-loud above).
                    continue
                db.add(PlaylistEntry(
                    playlist_id=prior_playlist.id,
                    track_id=track.id,
                    position=position,
                    slot_type="imported",
                ))

            # 3b. Seed CursorState from prior['cursors']. Loader is
            # dumb: writes verbatim, no derivation. Weekly's quirk of
            # hardcoding advance_step=120 in
            # `initialize_cursors(post_first=True)` lives in the
            # dumped values, not here.
            for cursor_name, position in prior_cursors.items():
                db.add(CursorState(
                    station_id=station.id,
                    name=cursor_name,
                    position=position,
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

    **CURRENTLY PASSING** post-§14.38 (cursor-bootstrap closed) +
    §14.39 (engine model generalised; same OUTPUT preserved). The
    test is intentionally NOT marked xfail — Phase B's port
    acceptance now passes against real weekly. The skip-when-missing
    pattern (no XMLs → pytest.skip) keeps CI green where real
    weekly data isn't available; local dev with XMLs gets the
    full byte-equality assertion.

    If this test EVER turns red, the diagnostic from
    `assert_chain_equal` surfaces the first divergent (week, slot,
    pick_in_block) coordinate. The per-PICK SlotRef expansion
    (§14.37 sweep-up) ensures the labelled position matches the
    actually-divergent pick — useful for any future regression.

    One slice (§14.40) remains in Phase B: add a second station
    fixture (Classics through the same engine path) proving "weekly
    is one instance, not the shape." After §14.40, Phase B closes.
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
        w: expand_block_slot_refs(week=w, blocks=tmpl)
        for w in weeks_asserted
    }

    assert_chain_equal(expected_chain, actual_chain, slot_refs_by_week)


# ---------- §14.38 R1 / R3 / R4 / R5 sweep-up tests ---------------------
#
# These always run (no XML data required) — they cover the prior-seed
# validator + the helper functions. The byte-equality test above is the
# integration check; these are unit-level locks for the strictness
# pattern.


def _valid_minimal_fixture(priors: list[dict] | None = None) -> dict:
    return {
        "schema_version": 2,
        "seed_base": 42,
        "weeks_generated": [2],
        "weeks_asserted": [2],
        "priors": priors if priors is not None else [],
        "weeks": [{"week": 2, "seed": 44, "pids": []}],
    }


def test_validate_accepts_minimal_zero_prior_fixture():
    """R4: priors=[] is valid (§14.40 stateless-station case)."""
    validate_prior_seed_fixture(_valid_minimal_fixture(priors=[]))


def test_validate_accepts_one_prior_with_empty_cursors():
    """R5: cursors={} is valid (stateless station's prior seed
    declaration). Loader writes zero rows; runtime falls back to
    template's cursors_init."""
    validate_prior_seed_fixture(_valid_minimal_fixture(priors=[
        {"week": 1, "pids": [], "cursors": {}},
    ]))


def test_validate_rejects_unknown_schema_version():
    bad = _valid_minimal_fixture()
    bad["schema_version"] = 1
    with pytest.raises(PriorSeedError, match="schema_version=1"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_unknown_top_level_keys():
    bad = _valid_minimal_fixture()
    bad["bogus_field"] = 42
    with pytest.raises(PriorSeedError, match="unknown top-level keys.*bogus_field"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_multi_prior_pending_14_38b():
    """R4: 2+ priors is queued as §14.38b; loader rejects loudly."""
    bad = _valid_minimal_fixture(priors=[
        {"week": 1, "pids": [], "cursors": {}},
        {"week": 2, "pids": [], "cursors": {}},
    ])
    with pytest.raises(PriorSeedError, match="2 priors.*supports 0 or 1"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_prior_missing_required_field():
    bad = _valid_minimal_fixture(priors=[{"week": 1, "pids": []}])
    with pytest.raises(PriorSeedError, match="missing required keys.*cursors"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_prior_with_unknown_field():
    bad = _valid_minimal_fixture(priors=[{
        "week": 1, "pids": [], "cursors": {}, "typo_field": True,
    }])
    with pytest.raises(PriorSeedError, match="unknown keys.*typo_field"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_pids_not_list():
    """R1: a string `pids` field would silently iterate per-char
    without this check."""
    bad = _valid_minimal_fixture(priors=[
        {"week": 1, "pids": "ABCDEF", "cursors": {}},
    ])
    with pytest.raises(PriorSeedError, match="pids must be a list"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_non_string_pid():
    bad = _valid_minimal_fixture(priors=[
        {"week": 1, "pids": ["GOOD_PID", 12345], "cursors": {}},
    ])
    with pytest.raises(PriorSeedError, match=r"pids\[1\] must be a string"):
        validate_prior_seed_fixture(bad)


def test_validate_rejects_non_int_cursor_position():
    """R1: a string `position` would write to CursorState.position
    without crashing on SQLite, surfacing as wrong picks much later."""
    bad = _valid_minimal_fixture(priors=[
        {"week": 1, "pids": [], "cursors": {"NIGHT_1": "120"}},
    ])
    with pytest.raises(PriorSeedError, match="cursors.*must be int"):
        validate_prior_seed_fixture(bad)


def test_validate_cursor_names_against_template_catches_typos():
    """R1: a misspelled cursor name (NITE_1 instead of NIGHT_1) would
    write a dead row that runtime silently ignores. Cross-validation
    catches it loud."""
    prior = {"week": 1, "pids": [],
             "cursors": {"NIGHT_1": 120, "NITE_2": 130}}
    template_cursors = {"NIGHT_1", "NIGHT_2"}
    with pytest.raises(PriorSeedError, match=r"cursors \['NITE_2'\].*NOT in"):
        validate_prior_cursor_names(prior, template_cursors)


def test_validate_cursor_names_passes_subset_of_template():
    """R1: declaring a SUBSET of the template's cursors is valid.
    Runtime falls back to template's cursors_init for the missing
    ones — verified separately by load_cursor_positions's behavior."""
    prior = {"week": 1, "pids": [], "cursors": {"NIGHT_1": 120}}
    template_cursors = {"NIGHT_1", "NIGHT_2", "WEEKEND_1"}
    validate_prior_cursor_names(prior, template_cursors)  # no raise


def testtrace_id_for_prior_uses_explicit_prefix():
    """R3: trace_id is clearly-non-UUID so future filters/code paths
    that pattern-match UUID shape skip imported priors correctly."""
    assert trace_id_for_prior(1) == "imported-prior:week1"
    assert trace_id_for_prior(42) == "imported-prior:week42"
    # Doesn't look anything like a UUID — failure mode is "future
    # code expects UUID, breaks loud" rather than "silently matches
    # a real generation."
    assert "-" in trace_id_for_prior(1)
    assert len(trace_id_for_prior(1)) != 36  # UUIDs are 36 chars
