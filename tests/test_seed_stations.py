"""Tests for engine.seeding (the seed-stations library).

§16.13.t9: the library moved from `scripts/seed_stations.py` to
`engine/seeding.py`. This file imports from the canonical home so
the tests cover the library directly, not via the back-compat
shim. The shim has its own minimal contract pin near the bottom
of this file (`test_scripts_shim_re_exports_canonical_names`).

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
from engine.seeding import seed_station, seed_all, _collect_paths


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
    # slots_per_week sums block.size — weekly's 2,196 picks total.
    # (flat_slots count is 1,111 blocks after §14.37 c2 coalescing.)
    assert summary["slot_count"] == 2196

    row = db.query(Station).filter_by(slug="rebirth-radio").one()
    assert row.template["cadence"] == "weekly"
    assert row.template["cursor"]["persist"] is True
    assert row.template["layout"]["flat_slots"]  # flat_slots form, not row_template
    # 9 named cursors (Rebirth Radio's NIGHT_1..7 + WEEKEND_1..2 pattern).
    assert len(row.template["layout"]["cursors_init"]) == 9


def test_collect_paths_skips_pool_manifest_and_editor_files():
    """seed_stations's default glob picks up stations/*.json — but
    co-located metadata files MUST NOT be mis-parsed as templates:

      * `*.pool-manifest.json` (§14.36) — pool-import manifests.
      * `*.editor.json` (§16.2.1) — visual-editor sidecar metadata.

    Both have their own dedicated loaders (`scripts.import_weekly_pools`
    for manifests; `engine.editor_metadata.parse_sidecar` for
    sidecars) and would fail parse_template if mistakenly seeded.

    The default-glob filter excludes BOTH; explicit-argv mode
    bypasses the filter (tested separately).
    """
    paths = _collect_paths([])
    names = [p.name for p in paths]
    # Both station templates land in the glob.
    assert "rebirth-classics.json" in names
    assert "rebirth-radio.json" in names
    # Co-located pool manifest excluded.
    assert "rebirth-radio.pool-manifest.json" not in names
    assert all(not n.endswith(".pool-manifest.json") for n in names)
    # Co-located editor sidecars excluded (slice-review finding 4).
    assert "rebirth-radio.editor.json" not in names
    assert "rebirth-classics.editor.json" not in names
    assert all(not n.endswith(".editor.json") for n in names)


def test_collect_paths_honours_explicit_argv_for_pool_manifest():
    """Explicit argv mode bypasses the glob filter — useful for testing
    or one-off ops that intentionally want the manifest file."""
    paths = _collect_paths([str(REPO_ROOT / "stations" / "rebirth-radio.pool-manifest.json")])
    assert len(paths) == 1
    assert paths[0].name == "rebirth-radio.pool-manifest.json"


def test_scripts_shim_re_exports_canonical_names() -> None:
    """§16.13.t9 backward-compat: the `scripts.seed_stations` shim
    re-exports every public + private name from `engine.seeding`
    so the legacy `python -m scripts.seed_stations` invocation +
    any external code doing `from scripts.seed_stations import ...`
    keep working. Pin the contract — if a future contributor adds
    a new public name to engine.seeding, this test fails until
    the shim re-export catches up.
    """
    import engine.seeding as canonical
    import scripts.seed_stations as shim

    # Every name the shim claims to re-export must point at the SAME
    # object in engine.seeding.
    for name in (
        "REPO_ROOT", "STATIONS_DIR",
        "seed_station", "seed_all",
        "_collect_paths", "_resolve_stations_dir",
        "main",
    ):
        assert getattr(shim, name) is getattr(canonical, name), (
            f"shim's {name!r} diverged from engine.seeding's"
        )
