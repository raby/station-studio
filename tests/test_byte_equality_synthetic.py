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
from engine.generator import generate
from engine.models import Base, Station, Track
from engine.persistence import (
    advance_cursors, load_alias_map, load_cursor_positions,
    load_excluded_mix_ids, load_pools, load_previous_playlist,
    max_mix_exclusion_window, save_playlist,
)
from engine.template import parse_template
from scripts.import_weekly_pools import import_manifest
from tests.byte_equality import (
    SlotRef, assert_chain_equal, slot_ref_from_block,
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
    """Run the full multi-week chain. Returns week → list of PIDs.

    Mirrors `api/routers/generate.py`'s call sequence EXACTLY — the
    test's "determinism floor" claim hinges on it. Any deviation
    means the test passes while the API path silently regresses.
    The relevant API inputs threaded through here:

      * `load_alias_map(db)` — §14.26. The synthetic Commercials have
        no aliases today, but the path needs to be live so a future
        alias-related regression in `weighted` would fail.
      * `max_mix_exclusion_window(tmpl)` — §14.32 review fix; walks
        layout.all_blocks() to handle BOTH row_template + flat_slots
        forms. Hardcoding `window=5` would bypass exactly the kind of
        prior-slice-helper-staleness bug that's bitten this project.
      * `weighted_window_default=None` — no station-rule set in the
        chain DB, so the engine falls back to its DEFAULT_WEIGHTED_WINDOW
        constant. Passing None explicitly documents the path.
      * `previous_playlist=load_previous_playlist(current_week=week)` —
        prev_ref reads week N-1; week 1 falls back.
    """
    station = db.query(Station).filter_by(slug="synthetic-byte-eq").one()
    tmpl = parse_template(station.template)
    pools = load_pools(db, tmpl)
    alias_map = load_alias_map(db)
    # Build a track_id → persistent_id lookup once. Picks return Track
    # objects (with .persistent_id); we resolve through this dict to
    # defend against the stale-Track-in-identity-map bug class noted
    # in §14.26's persistence review (ORM identity map can hand back a
    # detached object after a re-import; the explicit lookup forces a
    # fresh read).
    track_pids = {
        t.id: t.persistent_id
        for t in db.query(Track).all()
    }

    pids_by_week: dict[int, list[str]] = {}
    for week in range(1, CHAIN_LENGTH + 1):
        positions = load_cursor_positions(db, station_id=station.id, template=tmpl)
        excluded_mix_ids = load_excluded_mix_ids(
            db, station_id=station.id,
            window=max_mix_exclusion_window(tmpl),
        )
        previous_playlist = load_previous_playlist(
            db, station_id=station.id, current_week=week,
        )
        result = generate(
            tmpl, pools,
            week=week,
            seed=SEED_BASE + week,
            excluded_mix_ids=excluded_mix_ids,
            alias_map=alias_map,
            previous_playlist=previous_playlist,
            weighted_window_default=None,
            named_cursor_positions=positions,
        )
        # Resolve via track_pids lookup (see comment above) rather than
        # result.picks[i].persistent_id direct attribute access.
        pids_by_week[week] = [track_pids[t.id] for t in result.picks]
        save_playlist(db, station=station, result=result, week=week, template=tmpl)
        advance_cursors(
            db, station_id=station.id, template=tmpl, pools=pools,
            advance_step=ADVANCE_STEP, reset_cycle=RESET_CYCLE,
            generation_count=week - 1,
        )
        db.commit()

    return pids_by_week


def _slot_refs_for_template(tmpl) -> list[SlotRef]:
    """Build the slot-ref list once from the template. Reused across
    all asserted weeks (same template, same slot shape per week).
    Week is patched in per-assertion."""
    return [
        slot_ref_from_block(week=0, slot_index=i, block=block)
        for i, block in enumerate(tmpl.layout.flat_slots)
    ]


def _patch_slot_refs(refs: list[SlotRef], week: int) -> list[SlotRef]:
    """Return a copy with the week field updated."""
    return [SlotRef(week=week, slot_index=r.slot_index,
                    strategy=r.strategy, source=r.source, extra=r.extra)
            for r in refs]


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

    # Slot refs are template-derived (same shape every week).
    tmpl = parse_template(json.loads(STATION_TEMPLATE.read_text()))
    base_refs = _slot_refs_for_template(tmpl)

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
    slot_refs_by_week = {w: _patch_slot_refs(base_refs, w) for w in ASSERTED_WEEKS}

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
