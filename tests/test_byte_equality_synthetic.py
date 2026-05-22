"""Multi-week chain byte-equality test (§14.37 commit 1).

Drives the synthetic station template + pool fixtures (built by
`scripts/generate_byte_eq_fixtures.py`) through the full API
persistence flow for 8 weeks, then asserts the per-week PID sequence
against committed fixtures at
`tests/fixtures/expected/synthetic_week{1,2,4,8}.json`.

What this protects against
---------------------------

Any change to ANY strategy (jingle/weighted/mix/prev_ref/sequential)
that perturbs the output for these inputs will fail this test. That
makes it the determinism floor — the "anti-regression" net under all
the strategy modules.

It is NOT a port-acceptance test (that's commit 2 — comparing
station-studio's output to weekly's). This file ships a self-
referential snapshot.

Bootstrap mode
--------------

Set the environment variable `BOOTSTRAP_BYTE_EQ_FIXTURES=1` to
regenerate the committed expected-output fixtures from the current
behaviour. Use this when:
  * The synthetic station template or pool fixtures change.
  * A known-good strategy fix produces a different (correct) output
    and the fixture needs to catch up.
Inspect the regenerated JSON before committing — a fixture refresh
should match an intentional behaviour change.

Format of the expected-output fixtures
--------------------------------------

  {
    "seed_base": 42,                          // each week uses 42 + week
    "weeks_asserted": [1, 2, 4, 8],
    "pids": ["JDAY...", "CCOM...", ...]      // 120 PIDs per week
  }

One file per asserted week. The seed convention (seed = 42 + week)
is mirrored in commit 2's real-XML test for symmetry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station
from engine.template import parse_template
from scripts.import_weekly_pools import import_manifest
from tests.byte_equality import (
    assert_chain_equal, expand_block_slot_refs, generate_chain,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
EXPECTED_DIR = FIXTURES / "expected"
STATION_TEMPLATE = FIXTURES / "synthetic_weekly_station.json"
POOL_MANIFEST = FIXTURES / "synthetic_pool_manifest.json"

# Weeks the fixture commits to. Chain is generated 1..8 (each week's
# output is needed for the NEXT week's prev_ref + cursor state); only
# the listed weeks have asserted fixtures.
ASSERTED_WEEKS = (1, 2, 4, 8)
CHAIN_LENGTH = 8

# Per-week seed = SEED_BASE + week. Each week is independently seeded
# (matches weekly's per-generation seed convention).
SEED_BASE = 42

# Cursor advance step (template-level rule). Small enough that the
# synthetic NIGHT/WEEKEND pools (100 tracks each) don't wrap.
ADVANCE_STEP = 3
RESET_CYCLE = 0


def _bootstrap_mode() -> bool:
    """Bootstrap (fixture-regeneration) mode is active when
    ``BOOTSTRAP_BYTE_EQ_FIXTURES`` is set to the literal string ``"1"``.

    Other truthy-looking values (`true`, `yes`, etc.) are silently
    treated as off — narrow accept is deliberate to keep accidental
    activation impossible.
    """
    return os.environ.get("BOOTSTRAP_BYTE_EQ_FIXTURES", "").strip() == "1"


def _is_ci() -> bool:
    """Detect whether we're running in CI (GitHub Actions and most
    other providers set ``CI=true``). Used to guard the bootstrap
    branch — a leaked env var in CI would silently re-record fixtures
    and skip-pass the build."""
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


@pytest.fixture
def chain_db(tmp_path) -> Session:
    """Create a fresh DB, import the synthetic pools, seed the
    synthetic station. Returns a Session ready for the chain loop."""
    engine = make_engine(f"sqlite:///{tmp_path}/chain.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    db = SessionLocal()
    try:
        # Import pools via the manifest CLI (matches what real operators do).
        import_manifest(db, POOL_MANIFEST)
        # Create the station row.
        tmpl_dict = json.loads(STATION_TEMPLATE.read_text())
        db.add(Station(
            slug=tmpl_dict["station"],
            name=tmpl_dict["name"],
            template=tmpl_dict,
        ))
        db.commit()
        yield db
    finally:
        db.close()


def _generate_chain(db: Session) -> dict[int, list[str]]:
    """Run the 8-week chain via the shared generate_chain helper.

    The shared helper (tests/byte_equality.py:generate_chain) is
    parameterised so commit 2's real-XML test can reuse the EXACT
    same chain wiring. The synthetic chain passes the small
    advance_step=3 because NIGHT/WEEKEND pools are only 100 tracks;
    weekly's real chain uses the engine default 120.
    """
    return generate_chain(
        db,
        station_slug="synthetic-byte-eq",
        weeks=range(1, CHAIN_LENGTH + 1),
        seed_base=SEED_BASE,
        advance_step=ADVANCE_STEP,
        reset_cycle=RESET_CYCLE,
    )


def _expected_path(week: int) -> Path:
    return EXPECTED_DIR / f"synthetic_week{week}.json"


def _load_expected(week: int) -> list[str]:
    data = json.loads(_expected_path(week).read_text())
    return data["pids"]


def _write_expected(week: int, pids: list[str]) -> None:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    _expected_path(week).write_text(json.dumps({
        "seed_base": SEED_BASE,
        "weeks_asserted": list(ASSERTED_WEEKS),
        "week": week,
        "pids": pids,
    }, indent=2) + "\n")


def test_synthetic_chain_matches_committed_fixtures(chain_db):
    """The slice's central test: run 8 weeks of generation against
    synthetic pools, assert the 4 fixtured weeks match committed
    output byte-for-byte (i.e. PID-for-PID, which IS the byte
    surface — see byte_equality.py docstring).

    On a fixture refresh (BOOTSTRAP_BYTE_EQ_FIXTURES=1), this test
    OVERWRITES the committed fixtures with the current run's output
    instead of asserting. Inspect + diff before committing the
    refreshed fixtures."""
    pids_by_week = _generate_chain(chain_db)

    # Slot refs are template-derived (same shape every week). All
    # synthetic blocks are size=1 so per-pick expansion is a no-op
    # here — but we use the same expand_block_slot_refs helper as
    # the port test for consistency. If a future synthetic template
    # grows size>1 blocks, the diagnostic stays accurate.
    tmpl = parse_template(json.loads(STATION_TEMPLATE.read_text()))

    if _bootstrap_mode():
        if _is_ci():
            # §14.37 mid-slice review fix Y1: a leaked env var in CI
            # would silently re-record fixtures and skip-pass the
            # build — silently corrupting the regression net. Fail
            # loud instead.
            pytest.fail(
                "BOOTSTRAP_BYTE_EQ_FIXTURES=1 detected in a CI "
                "environment (CI=$CI). Bootstrap mode rewrites the "
                "committed expected-output fixtures and is intended "
                "for explicit local dev workflow only. Unset the env "
                "var to run the assertion path."
            )
        for week in ASSERTED_WEEKS:
            _write_expected(week, pids_by_week[week])
        pytest.skip(
            f"bootstrapped {len(ASSERTED_WEEKS)} fixtures under "
            f"{EXPECTED_DIR}. Inspect the JSON + commit them, then "
            f"unset BOOTSTRAP_BYTE_EQ_FIXTURES."
        )

    expected_chain = {w: _load_expected(w) for w in ASSERTED_WEEKS}
    actual_chain = {w: pids_by_week[w] for w in ASSERTED_WEEKS}
    slot_refs_by_week = {
        w: expand_block_slot_refs(week=w, blocks=tmpl)
        for w in ASSERTED_WEEKS
    }

    assert_chain_equal(expected_chain, actual_chain, slot_refs_by_week)


def test_synthetic_chain_uses_persistent_cursors(chain_db):
    """Sanity check: the chain genuinely advances cursor_state row by
    row. Without this, the multi-week chain could silently degenerate
    into 8 identical runs (each from cursors_init)."""
    _generate_chain(chain_db)
    from engine.models import CursorState

    state = chain_db.query(CursorState).all()
    # 4 named cursors → 4 rows. Each position > its cursors_init value
    # (cursor advanced over 8 weeks).
    assert len(state) == 4
    by_name = {s.name: s for s in state}
    # Initial positions from the template: NIGHT_1=0, NIGHT_2=5,
    # WEEKEND_1=0, WEEKEND_2=10. After 8 advances of step 3 each,
    # cursors should be at init + 8*3 = init + 24 (modulo pool length).
    assert by_name["NIGHT_1"].position == 24
    assert by_name["NIGHT_2"].position == 29
    assert by_name["WEEKEND_1"].position == 24
    assert by_name["WEEKEND_2"].position == 34
