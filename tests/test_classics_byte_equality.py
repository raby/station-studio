"""Classics byte-equality (§14.40 — Phase B closeout slice).

Drives Rebirth Classics's committed station template
(`stations/rebirth-classics.json`) through 16 weeks of generation
against the synthetic Classics pool fixtures, then asserts the per-
week PID sequence matches the committed
`tests/fixtures/expected/classics_byte_eq.json`.

Why this test exists (Phase B's "weekly is one instance" affirmation)
---------------------------------------------------------------------

The §14.37 byte-equality harness proved that station-studio produces
the SAME OUTPUT as weekly for the rebirth-radio template. §14.40
proves the engine works for a STRUCTURALLY DIFFERENT station —
Classics — through the same code path. Concrete differences:

  * Layout form: `row_template` (not `flat_slots`).
  * Cursor mode: stateless (`persist=False`).
  * Strategy: `window` only (no jingle/weighted/mix/prev_ref/sequential).
  * RNG: `off` (deterministic — seed is irrelevant).
  * Cross-week state: none (each week is independent).
  * Reference vs comparison: Classics has no external generator to
    compare against; the fixture is "freeze-against-self" — captured
    once from station-studio itself, then regression-locked.

The test runs the full 16-week cycle (matches Classics's
`cycle_weeks=16`), exercising the stateless cursor advance formula
`(week-1) * base_advance_per_week % (cycle_weeks * base_advance)`
across the whole cycle. Week 17+ would wrap to week 1's positions
— that wrap is not asserted here (Phase B's job is the cycle, not
the wrap; a follow-on could lock the wrap if needed).

Bootstrap mode
--------------

Set `BOOTSTRAP_BYTE_EQ_FIXTURES=1` to regenerate the committed
fixture from the current behaviour. Use after intentional engine
or template changes. Inspect the diff before committing.

CI guard: `BOOTSTRAP_BYTE_EQ_FIXTURES=1` + `CI=true` fails LOUD
instead of skipping. A leaked env var in CI must not silently
re-record the regression net.
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
EXPECTED_FIXTURE = EXPECTED_DIR / "classics_byte_eq.json"
POOL_MANIFEST = FIXTURES / "classics_pool_manifest.json"
CLASSICS_TEMPLATE = REPO_ROOT / "stations" / "rebirth-classics.json"

# Classics's `cycle_weeks=16` — the full cycle. Each week independently
# generated (Classics has no cross-week state).
WEEKS_GENERATED = list(range(1, 17))
WEEKS_ASSERTED = list(range(1, 17))

# Classics is rng=off; seed is irrelevant to output. We pass a fixed
# value so the chain helper's signature is satisfied + the seed
# convention is documented.
SEED_BASE = 0


def _bootstrap_mode() -> bool:
    """Bootstrap mode active when ``BOOTSTRAP_BYTE_EQ_FIXTURES=1``."""
    return os.environ.get("BOOTSTRAP_BYTE_EQ_FIXTURES", "").strip() == "1"


def _is_ci() -> bool:
    """Detect CI (most providers set ``CI=true``)."""
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}


@pytest.fixture
def classics_db(tmp_path) -> Session:
    """Build a DB with synthetic Classics pools + the committed
    Classics station seeded. No prior-seed playlist (Classics is
    stateless — no prev_ref dependencies).
    """
    engine = make_engine(f"sqlite:///{tmp_path}/classics.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    db = SessionLocal()
    try:
        # Import synthetic pools via the manifest CLI.
        import_manifest(db, POOL_MANIFEST)
        # Seed the station row from the committed template.
        tmpl_dict = json.loads(CLASSICS_TEMPLATE.read_text())
        db.add(Station(
            slug=tmpl_dict["station"],
            name=tmpl_dict.get("name", tmpl_dict["station"]),
            template=tmpl_dict,
        ))
        db.commit()
        yield db
    finally:
        db.close()


def _write_fixture(pids_by_week: dict[int, list[str]]) -> None:
    """Write the combined fixture (one file, all weeks).

    Format mirrors port_byte_eq.json: schema_version + priors + weeks.
    Classics has no priors (stateless); `priors: []` is the
    §14.38 R4-supported empty case.
    """
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "_comment": (
            "Classics byte-equality fixture (§14.40). Generated from "
            "station-studio's own engine output against synthetic "
            "Classics pools — freeze-against-self regression lock. "
            "Refresh via `make refresh-classics-fixture` after "
            "intentional behaviour changes."
        ),
        "schema_version": 2,
        "seed_base": SEED_BASE,
        "weeks_generated": WEEKS_GENERATED,
        "weeks_asserted": WEEKS_ASSERTED,
        "priors": [],
        "weeks": [
            {"week": w, "pids": pids_by_week[w]} for w in WEEKS_GENERATED
        ],
    }
    EXPECTED_FIXTURE.write_text(json.dumps(data, indent=2) + "\n")


def test_classics_chain_matches_committed_fixture(classics_db):
    """Classics's 16-week cycle generates byte-identical output to
    the committed fixture. Proves the engine handles row_template
    layout + stateless cursors + window-only strategies through the
    same code path as weekly's flat_slots + stateful + 5-strategy mix.

    The "weekly is one instance" affirmation. If the engine ever
    regresses for ANY station shape covered by Classics, this test
    fires.
    """
    pids_by_week = generate_chain(
        classics_db,
        station_slug="rebirth-classics",
        weeks=WEEKS_GENERATED,
        seed_base=SEED_BASE,
        # Classics's template carries its own cursor advance config
        # (base_advance_per_week=60, cycle_weeks=16) used by the
        # STATELESS cursor formula. `advance_cursors` is a no-op for
        # stateless templates (engine/persistence.py:365). The values
        # passed here are inert — pass 0 to document that.
        advance_step=0,
        reset_cycle=0,
    )

    if _bootstrap_mode():
        if _is_ci():
            pytest.fail(
                "BOOTSTRAP_BYTE_EQ_FIXTURES=1 detected in a CI "
                "environment. Bootstrap mode rewrites the committed "
                "expected-output fixture and is intended for explicit "
                "local dev workflow only. Unset the env var to run "
                "the assertion path."
            )
        _write_fixture(pids_by_week)
        pytest.skip(
            f"bootstrapped {EXPECTED_FIXTURE}. Inspect the JSON + "
            f"commit it, then unset BOOTSTRAP_BYTE_EQ_FIXTURES."
        )

    fixture = json.loads(EXPECTED_FIXTURE.read_text())
    expected_by_week = {w["week"]: w["pids"] for w in fixture["weeks"]}

    tmpl = parse_template(json.loads(CLASSICS_TEMPLATE.read_text()))
    expected_chain = {w: expected_by_week[w] for w in WEEKS_ASSERTED}
    actual_chain = {w: pids_by_week[w] for w in WEEKS_ASSERTED}
    # Per-PICK slot refs (template-aware; handles row_template).
    slot_refs_by_week = {
        w: expand_block_slot_refs(week=w, blocks=tmpl)
        for w in WEEKS_ASSERTED
    }
    assert_chain_equal(expected_chain, actual_chain, slot_refs_by_week)
