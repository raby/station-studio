"""Tests for scripts/seed_stations.py.

Proves:
  * Seeding a fresh DB creates the station row (action: created)
  * Re-seeding the same JSON is a no-op (action: unchanged)
  * Editing the template JSON between runs updates the row (action: updated)
  * The checked-in Classics JSON is valid and seeds end-to-end
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station
from scripts.seed_stations import seed_station, seed_all, _collect_paths


REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSICS_JSON = REPO_ROOT / "stations" / "rebirth-classics.json"
REBIRTH_RADIO_JSON = REPO_ROOT / "stations" / "rebirth-radio.json"


@pytest.fixture
def db(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/seed.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------- fixtures for writable copy of classics JSON ------------------


def _write_template(path: Path, station: str = "x", extra: dict | None = None) -> Path:
    data = {
        "station": station,
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "A", "colour": "#aaa", "role": "content"}],
        "cursor": {"base_advance_per_week": 10, "cycle_weeks": 4},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "window", "size": 3}],
        },
    }
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data, indent=2))
    return path


# ---------- tests --------------------------------------------------------


def test_seed_creates_row_on_first_run(db, tmp_path):
    path = _write_template(tmp_path / "t.json", station="alpha")
    summary = seed_station(db, path)
    db.commit()

    assert summary["action"] == "created"
    assert summary["slug"] == "alpha"
    assert summary["slot_count"] == 3  # 1 row * 1 block * size 3

    row = db.query(Station).filter_by(slug="alpha").one()
    assert row.template["cursor"]["base_advance_per_week"] == 10


def test_seed_is_idempotent_on_unchanged_json(db, tmp_path):
    path = _write_template(tmp_path / "t.json", station="beta")
    seed_station(db, path)
    db.commit()

    summary = seed_station(db, path)
    db.commit()
    assert summary["action"] == "unchanged"

    # Still exactly one row
    assert db.query(Station).filter_by(slug="beta").count() == 1


def test_seed_updates_row_when_json_changes(db, tmp_path):
    path = _write_template(tmp_path / "t.json", station="gamma")
    seed_station(db, path)
    db.commit()

    # Rewrite the JSON with a different cursor advance
    _write_template(
        path,
        station="gamma",
        extra={
            "cursor": {"base_advance_per_week": 99, "cycle_weeks": 4},
        },
    )
    # Note: _write_template takes extra as a merge on the base dict, but the
    # cursor value lives inside the merged dict. Easier to overwrite directly.
    raw = json.loads(path.read_text())
    raw["cursor"]["base_advance_per_week"] = 99
    path.write_text(json.dumps(raw, indent=2))

    summary = seed_station(db, path)
    db.commit()
    assert summary["action"] == "updated"

    row = db.query(Station).filter_by(slug="gamma").one()
    assert row.template["cursor"]["base_advance_per_week"] == 99


def test_seed_rejects_invalid_template(db, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"station": "bad", "not_a_valid": "template"}))
    with pytest.raises(SystemExit, match="invalid template"):
        seed_station(db, path)


def test_seed_classics_from_checked_in_json(db):
    """The real Classics JSON seeds successfully end-to-end."""
    summary = seed_station(db, CLASSICS_JSON)
    db.commit()

    assert summary["action"] == "created"
    assert summary["slug"] == "rebirth-classics"
    assert summary["slot_count"] == 3010

    row = db.query(Station).filter_by(slug="rebirth-classics").one()
    assert row.template["cursor"]["base_advance_per_week"] == 60
    assert row.template["cursor"]["cycle_weeks"] == 16
    assert row.template["layout"]["row_start_offsets"] == [0, 10, 20, 30, 40, 50, 50]


def test_seed_all_batches_multiple_files(db, tmp_path):
    paths = [
        _write_template(tmp_path / "one.json", station="one"),
        _write_template(tmp_path / "two.json", station="two"),
        _write_template(tmp_path / "three.json", station="three"),
    ]
    summaries = seed_all(db, paths)
    assert [s["slug"] for s in summaries] == ["one", "two", "three"]
    assert {s["action"] for s in summaries} == {"created"}
    assert db.query(Station).count() == 3


# ---------- §14.36 committed rebirth-radio.json + pool-manifest glob filter


def test_seed_rebirth_radio_from_checked_in_json(db):
    """The committed port artifact (§14.36) seeds successfully end-to-end.
    Pair to test_seed_classics_from_checked_in_json — proves the
    artifact is `parse_template`-valid AND that seed_stations's upsert
    path handles the weekly-shaped (flat_slots + cursor.persist=true)
    template just as well as Classics's row_template form."""
    summary = seed_station(db, REBIRTH_RADIO_JSON)
    db.commit()

    assert summary["action"] == "created"
    assert summary["slug"] == "rebirth-radio"
    assert summary["slot_count"] == 2196  # weekly's 2,196-slot template

    row = db.query(Station).filter_by(slug="rebirth-radio").one()
    assert row.template["cadence"] == "weekly"
    assert row.template["cursor"]["persist"] is True
    assert row.template["layout"]["flat_slots"]  # flat_slots form, not row_template
    # 9 named cursors (Rebirth Radio's NIGHT_1..7 + WEEKEND_1..2 pattern).
    assert len(row.template["layout"]["cursors_init"]) == 9


def test_collect_paths_skips_pool_manifest_files():
    """seed_stations's default glob picks up stations/*.json — but
    *.pool-manifest.json files (§14.36) sit alongside their template
    JSON and would fail parse_template if seeded. The glob filter
    excludes them; explicit-argv mode is unaffected."""
    paths = _collect_paths([])
    names = [p.name for p in paths]
    # Both station templates land in the glob.
    assert "rebirth-classics.json" in names
    assert "rebirth-radio.json" in names
    # The co-located pool manifest does NOT.
    assert "rebirth-radio.pool-manifest.json" not in names
    # No name in the result ends with .pool-manifest.json (defensive
    # check in case a new station gets one).
    assert all(not n.endswith(".pool-manifest.json") for n in names)


def test_collect_paths_honours_explicit_argv_for_pool_manifest():
    """Explicit argv mode bypasses the glob filter — useful for testing
    or one-off ops that intentionally want the manifest file."""
    paths = _collect_paths([str(REPO_ROOT / "stations" / "rebirth-radio.pool-manifest.json")])
    assert len(paths) == 1
    assert paths[0].name == "rebirth-radio.pool-manifest.json"
