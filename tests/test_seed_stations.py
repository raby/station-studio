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


# ---------- §16.14.t9 — declarative rules: block seeding -----------------


def _template_with_rules(rules: dict | None = None) -> dict:
    """Minimal template body, optionally carrying a rules: block."""
    body = {
        "station": "rb",
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
    if rules is not None:
        body["rules"] = rules
    return body


def test_seed_station_writes_template_declared_int_rule(db, tmp_path):
    """§16.14.t9 c2 happy path — int rule from `rules:` lands in
    the DB. Summary's rules_applied counts the write."""
    from engine.rules import get_rule_value

    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules({
        "artist_dedup_window": 15,
    })))

    summary = seed_station(db, path)
    db.commit()
    station = db.query(Station).filter_by(slug="rb").one()
    assert get_rule_value(
        db, station_id=station.id, key="artist_dedup_window",
    ) == 15
    assert summary["rules_applied"] == 1


def test_seed_station_writes_template_declared_list_rule(db, tmp_path):
    """Same as the int case, but for a list rule."""
    from engine.rules import get_rule_list_value

    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules({
        "cross_pool_dedup_sources": ["REF"],
    })))

    summary = seed_station(db, path)
    db.commit()
    station = db.query(Station).filter_by(slug="rb").one()
    assert get_rule_list_value(
        db, station_id=station.id, key="cross_pool_dedup_sources",
    ) == ["REF"]
    assert summary["rules_applied"] == 1


def test_seed_station_only_set_if_absent_for_int_rule(db, tmp_path):
    """§16.14.t9 D4 — re-seeding doesn't overwrite an existing Rule
    row. Simulates an operator's API edit by setting a different
    value AFTER the first seed; the second seed leaves it alone
    and rules_applied is 0."""
    from engine.rules import get_rule_value, set_rule_value

    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules({
        "artist_dedup_window": 15,
    })))

    seed_station(db, path)
    db.commit()
    station = db.query(Station).filter_by(slug="rb").one()
    # Operator customises via API.
    set_rule_value(
        db, station_id=station.id, key="artist_dedup_window", value=25,
    )
    db.commit()

    # Re-seed must NOT overwrite the customisation.
    summary = seed_station(db, path)
    db.commit()
    assert get_rule_value(
        db, station_id=station.id, key="artist_dedup_window",
    ) == 25
    assert summary["rules_applied"] == 0


def test_seed_station_only_set_if_absent_for_list_rule(db, tmp_path):
    """§16.14.t9 D4 — same operator-wins behaviour for list rules."""
    from engine.rules import get_rule_list_value, set_rule_list_value

    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules({
        "cross_pool_dedup_sources": ["REF_A"],
    })))

    seed_station(db, path)
    db.commit()
    station = db.query(Station).filter_by(slug="rb").one()
    set_rule_list_value(
        db, station_id=station.id, key="cross_pool_dedup_sources",
        value=["REF_B", "REF_C"],
    )
    db.commit()

    summary = seed_station(db, path)
    db.commit()
    assert get_rule_list_value(
        db, station_id=station.id, key="cross_pool_dedup_sources",
    ) == ["REF_B", "REF_C"]
    assert summary["rules_applied"] == 0


def test_seed_station_only_touches_declared_keys(db, tmp_path):
    """§16.14.t9 D5 — keys NOT in the template's `rules:` block
    are left alone. The operator's customisation for an undeclared
    key survives unconditionally."""
    from engine.rules import get_rule_value, set_rule_value

    path = tmp_path / "t.json"
    # Template declares ONLY artist_dedup_window.
    path.write_text(json.dumps(_template_with_rules({
        "artist_dedup_window": 15,
    })))

    seed_station(db, path)
    db.commit()
    station = db.query(Station).filter_by(slug="rb").one()
    # Operator customises a DIFFERENT key not declared by the
    # template. Re-seed must not touch it.
    set_rule_value(
        db, station_id=station.id, key="mix_exclusion_weeks", value=50,
    )
    db.commit()
    seed_station(db, path)
    db.commit()
    assert get_rule_value(
        db, station_id=station.id, key="mix_exclusion_weeks",
    ) == 50


def test_seed_station_template_wins_over_registry_for_same_key(db, tmp_path):
    """§16.14.t9 D6 fallback — when both the registry and template
    declare the same key, the template wins. The registry value
    never gets written.

    Synthetic registry mutation is the cleanest path: monkey-
    patches `_STATION_LIST_RULES` to add an entry that conflicts
    with the template's `rules:` block.
    """
    from engine.rules import get_rule_list_value
    from engine import seeding

    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules({
        "cross_pool_dedup_sources": ["FROM_TEMPLATE"],
    })))

    original = seeding._STATION_LIST_RULES
    try:
        seeding._STATION_LIST_RULES = {
            "rb": {"cross_pool_dedup_sources": ["FROM_REGISTRY"]},
        }
        seed_station(db, path)
        db.commit()
    finally:
        seeding._STATION_LIST_RULES = original

    station = db.query(Station).filter_by(slug="rb").one()
    assert get_rule_list_value(
        db, station_id=station.id, key="cross_pool_dedup_sources",
    ) == ["FROM_TEMPLATE"]


def test_seed_station_registry_fallback_fills_undeclared_keys(db, tmp_path):
    """§16.14.t9 D6 — registry entries for keys NOT declared in
    the template DO get applied. Per D4 also only-set-if-absent."""
    from engine.rules import get_rule_list_value
    from engine import seeding

    # Template has NO rules: block.
    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules()))

    original = seeding._STATION_LIST_RULES
    try:
        seeding._STATION_LIST_RULES = {
            "rb": {"cross_pool_dedup_sources": ["FROM_REGISTRY"]},
        }
        summary = seed_station(db, path)
        db.commit()
    finally:
        seeding._STATION_LIST_RULES = original

    station = db.query(Station).filter_by(slug="rb").one()
    assert get_rule_list_value(
        db, station_id=station.id, key="cross_pool_dedup_sources",
    ) == ["FROM_REGISTRY"]
    assert summary["rules_applied"] == 1


def test_seed_station_rules_applied_counts_actual_writes(db, tmp_path):
    """§16.14.t9 + the c2 review F3 / sweep S2 contract: the
    summary's rules_applied is the count of Rule rows actually
    written by THIS call (NOT a static count of declared keys).
    Confirms the field's semantics for CLI consumers."""
    from engine.rules import set_rule_value

    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template_with_rules({
        "artist_dedup_window": 15,
        "mix_exclusion_weeks": 30,
        "cross_pool_dedup_sources": ["REF"],
    })))

    # First seed: all 3 declared keys land.
    first = seed_station(db, path)
    db.commit()
    assert first["rules_applied"] == 3

    # Customise one of them; re-seed should write 0 (all 3 keys
    # now have rows, none gets overwritten).
    station = db.query(Station).filter_by(slug="rb").one()
    set_rule_value(
        db, station_id=station.id, key="artist_dedup_window", value=99,
    )
    db.commit()
    second = seed_station(db, path)
    db.commit()
    assert second["rules_applied"] == 0


def test_starter_template_declares_cross_pool_dedup_in_rules_block():
    """§16.14.t9 c2 migration pin: the starter's
    cross_pool_dedup_sources rule lives in the JSON's `rules:`
    block (NOT in `_STATION_LIST_RULES` anymore). Catches a
    future contributor who moves the rule back into the registry
    by mistake."""
    starter_json = REPO_ROOT / "stations" / "starter.json"
    raw = json.loads(starter_json.read_text())
    assert raw.get("rules") == {
        "cross_pool_dedup_sources": ["REFERENCE_ARCHIVE"],
    }


def test_station_list_rules_no_longer_carries_starter():
    """§16.14.t9 c2 D6 migration pin — _STATION_LIST_RULES should
    not carry the starter entry (it's in the JSON now). The
    registry stays as a forward-extension point, but the starter
    is removed.

    If this test breaks because someone re-added the starter to
    the registry, they probably meant to amend the JSON instead.
    """
    from engine.seeding import _STATION_LIST_RULES
    assert "starter" not in _STATION_LIST_RULES
