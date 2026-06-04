"""Tests for the §14.29 per-station rules subsystem.

Covers the engine.rules accessors (get/set/clear/get_all), the
RULE_DEFAULTS registry, and the precedence flow through the generator
(block field → station rule → engine constant).
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Rule, Station
from engine.rules import (
    InvalidRuleValueError, RULE_DEFAULTS, UnknownRuleError,
    clear_rule, get_all_rules, get_rule_value, set_rule_value,
)


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{tmp_path}/rules.db")
    Base.metadata.create_all(engine)
    return make_sessionmaker(engine)


def _seed_station(Session: sessionmaker, slug: str = "rb") -> int:
    """Insert a minimal station; return its id."""
    with Session() as db:
        st = Station(
            slug=slug, name=slug,
            template={
                "station": slug, "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.commit()
        return st.id


# ---------- registry ---------------------------------------------------------


def test_rule_defaults_registry_has_expected_keys():
    """All four weekly-equivalent rules ship in the registry, even
    though cursor_advance_step and cursor_reset_cycle aren't wired
    into the engine yet (carried for forward compat)."""
    assert set(RULE_DEFAULTS) == {
        "artist_dedup_window",
        "mix_exclusion_weeks",
        "cursor_advance_step",
        "cursor_reset_cycle",
    }


def test_rule_defaults_match_weekly():
    """Byte-equality with weekly's RULE_DEFAULTS (value + max_value)."""
    assert RULE_DEFAULTS["artist_dedup_window"].value == 10
    assert RULE_DEFAULTS["artist_dedup_window"].max_value == 1000
    assert RULE_DEFAULTS["mix_exclusion_weeks"].value == 20
    assert RULE_DEFAULTS["mix_exclusion_weeks"].max_value == 1000
    assert RULE_DEFAULTS["cursor_advance_step"].value == 120
    assert RULE_DEFAULTS["cursor_reset_cycle"].value == 0


# ---------- get_rule_value ---------------------------------------------------


def test_get_rule_value_returns_default_when_no_override(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        assert get_rule_value(
            db, station_id=station_id, key="artist_dedup_window",
        ) == 10
        assert get_rule_value(
            db, station_id=station_id, key="mix_exclusion_weeks",
        ) == 20


def test_get_rule_value_unknown_key_raises(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        with pytest.raises(UnknownRuleError, match="bogus_key"):
            get_rule_value(db, station_id=station_id, key="bogus_key")


# ---------- set_rule_value ---------------------------------------------------


def test_set_rule_value_upserts_override(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        set_rule_value(
            db, station_id=station_id, key="artist_dedup_window", value=15,
        )
        db.commit()
        assert get_rule_value(
            db, station_id=station_id, key="artist_dedup_window",
        ) == 15
        # Idempotent update via upsert path.
        set_rule_value(
            db, station_id=station_id, key="artist_dedup_window", value=25,
        )
        db.commit()
        assert get_rule_value(
            db, station_id=station_id, key="artist_dedup_window",
        ) == 25
        # Still only ONE row for this (station_id, key).
        rows = db.query(Rule).filter(Rule.station_id == station_id).all()
        assert len(rows) == 1


def test_set_rule_value_negative_rejected(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        with pytest.raises(InvalidRuleValueError, match="non-negative"):
            set_rule_value(
                db, station_id=station_id, key="artist_dedup_window", value=-1,
            )


def test_set_rule_value_above_max_rejected(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        with pytest.raises(InvalidRuleValueError, match="at most 1000"):
            set_rule_value(
                db, station_id=station_id, key="artist_dedup_window", value=5000,
            )


def test_set_rule_value_unknown_key_rejected(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        with pytest.raises(UnknownRuleError):
            set_rule_value(
                db, station_id=station_id, key="bogus_key", value=10,
            )


def test_set_rule_value_is_station_scoped(session_factory):
    """Two stations' overrides don't bleed into each other."""
    Session = session_factory
    a_id = _seed_station(Session, slug="a")
    b_id = _seed_station(Session, slug="b")
    with Session() as db:
        set_rule_value(db, station_id=a_id, key="artist_dedup_window", value=7)
        db.commit()
        assert get_rule_value(db, station_id=a_id, key="artist_dedup_window") == 7
        # Station B should still see the default.
        assert get_rule_value(db, station_id=b_id, key="artist_dedup_window") == 10


# ---------- clear_rule -------------------------------------------------------


def test_clear_rule_reverts_to_default(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        set_rule_value(db, station_id=station_id, key="artist_dedup_window", value=99)
        db.commit()
        deleted = clear_rule(db, station_id=station_id, key="artist_dedup_window")
        db.commit()
        assert deleted is True
        assert get_rule_value(
            db, station_id=station_id, key="artist_dedup_window",
        ) == 10  # back to default


def test_clear_rule_when_no_override_returns_false(session_factory):
    """Idempotent: clearing a non-existent override is a no-op, not an error."""
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        deleted = clear_rule(db, station_id=station_id, key="artist_dedup_window")
        assert deleted is False


def test_clear_rule_unknown_key_rejected(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        with pytest.raises(UnknownRuleError):
            clear_rule(db, station_id=station_id, key="bogus_key")


# ---------- get_all_rules ----------------------------------------------------


def test_get_all_rules_lists_every_known_rule(session_factory):
    """§16.14.t10: returns one entry per RULE_DEFAULTS + RULE_LIST_DEFAULTS
    key, alphabetically interleaved (D5), with `kind` discriminator
    + is_override flags reflecting persistence state."""
    from engine.rules import RULE_LIST_DEFAULTS

    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        rows = get_all_rules(db, station_id=station_id)
        keys = [r["key"] for r in rows]
        # D5: alphabetical interleave across both registries.
        assert keys == sorted(set(RULE_DEFAULTS) | set(RULE_LIST_DEFAULTS))
        # No overrides yet → all defaults.
        assert all(r["is_override"] is False for r in rows)
        assert all(r["value"] == r["default_value"] for r in rows)
        # D1: every row carries a kind discriminator.
        kinds = {r["key"]: r["kind"] for r in rows}
        for k in RULE_DEFAULTS:
            assert kinds[k] == "int"
        for k in RULE_LIST_DEFAULTS:
            assert kinds[k] == "list"


def test_get_all_rules_marks_overrides_correctly(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        set_rule_value(db, station_id=station_id, key="artist_dedup_window", value=42)
        db.commit()
        rows = get_all_rules(db, station_id=station_id)
        by_key = {r["key"]: r for r in rows}
        assert by_key["artist_dedup_window"]["is_override"] is True
        assert by_key["artist_dedup_window"]["value"] == 42
        assert by_key["artist_dedup_window"]["default_value"] == 10
        # Other rules untouched.
        assert by_key["mix_exclusion_weeks"]["is_override"] is False


def test_get_all_rules_carries_description_and_max_value(session_factory):
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        rows = get_all_rules(db, station_id=station_id)
        by_key = {r["key"]: r for r in rows}
        assert by_key["artist_dedup_window"]["max_value"] == 1000
        assert by_key["cursor_reset_cycle"]["max_value"] is None
        assert "artist" in by_key["artist_dedup_window"]["description"].lower()


# ---------- cascade on station deletion --------------------------------------


def test_rule_rows_cascade_on_station_delete(session_factory):
    """Rule.station_id FK ON DELETE CASCADE — deleting a station drops
    its rule rows along with everything else (playlists, MixUsage, etc).
    """
    Session = session_factory
    station_id = _seed_station(Session)
    with Session() as db:
        set_rule_value(db, station_id=station_id, key="artist_dedup_window", value=7)
        db.commit()
        assert db.query(Rule).count() == 1

        station = db.query(Station).filter_by(id=station_id).one()
        db.delete(station)
        db.commit()
        assert db.query(Rule).count() == 0


# ---------- §16.14.t10 — validator extraction --------------------------------


def test_validate_int_rule_value_accepts_in_range():
    from engine.rules import validate_int_rule_value
    # Returns None; raises on failure.
    validate_int_rule_value("artist_dedup_window", 0)
    validate_int_rule_value("artist_dedup_window", 1000)
    validate_int_rule_value("artist_dedup_window", 42)


def test_validate_int_rule_value_rejects_unknown_key():
    from engine.rules import UnknownRuleError, validate_int_rule_value
    with pytest.raises(UnknownRuleError, match="bogus"):
        validate_int_rule_value("bogus_key", 5)


def test_validate_int_rule_value_rejects_bool():
    """Python's `isinstance(True, int)` is True; the validator
    explicitly rejects bool because a `true` in JSON is almost
    certainly an operator typo (meant 'enabled', not 1)."""
    from engine.rules import InvalidRuleValueError, validate_int_rule_value
    with pytest.raises(InvalidRuleValueError, match="must be int"):
        validate_int_rule_value("artist_dedup_window", True)


def test_validate_int_rule_value_rejects_non_int():
    from engine.rules import InvalidRuleValueError, validate_int_rule_value
    with pytest.raises(InvalidRuleValueError, match="must be int"):
        validate_int_rule_value("artist_dedup_window", "5")
    with pytest.raises(InvalidRuleValueError, match="must be int"):
        validate_int_rule_value("artist_dedup_window", 5.5)


def test_validate_int_rule_value_rejects_negative():
    from engine.rules import InvalidRuleValueError, validate_int_rule_value
    with pytest.raises(InvalidRuleValueError, match="non-negative"):
        validate_int_rule_value("artist_dedup_window", -1)


def test_validate_int_rule_value_rejects_above_max():
    """RULE_DEFAULTS['artist_dedup_window'].max_value = 1000."""
    from engine.rules import InvalidRuleValueError, validate_int_rule_value
    with pytest.raises(InvalidRuleValueError, match="at most 1000"):
        validate_int_rule_value("artist_dedup_window", 1001)


def test_validate_int_rule_value_no_max_accepts_large():
    """Rules with max_value=None accept arbitrarily large values."""
    from engine.rules import validate_int_rule_value
    # cursor_reset_cycle has max_value=None.
    validate_int_rule_value("cursor_reset_cycle", 10**9)


def test_validate_list_rule_value_returns_cleaned_list():
    """Validator strips whitespace per entry + returns the
    canonical form. Callers persist the returned list."""
    from engine.rules import validate_list_rule_value
    cleaned = validate_list_rule_value(
        "cross_pool_dedup_sources", ["  MASTER  ", "ARCHIVE"],
    )
    assert cleaned == ["MASTER", "ARCHIVE"]


def test_validate_list_rule_value_accepts_empty_list():
    """Explicit `[]` is a valid "no exclusion" override."""
    from engine.rules import validate_list_rule_value
    assert validate_list_rule_value("cross_pool_dedup_sources", []) == []


def test_validate_list_rule_value_rejects_unknown_key():
    from engine.rules import UnknownRuleError, validate_list_rule_value
    with pytest.raises(UnknownRuleError, match="bogus"):
        validate_list_rule_value("bogus_list_key", ["X"])


def test_validate_list_rule_value_rejects_non_list():
    from engine.rules import InvalidRuleValueError, validate_list_rule_value
    with pytest.raises(InvalidRuleValueError, match="must be a list"):
        validate_list_rule_value("cross_pool_dedup_sources", "MASTER")


def test_validate_list_rule_value_rejects_non_string_entry():
    from engine.rules import InvalidRuleValueError, validate_list_rule_value
    with pytest.raises(InvalidRuleValueError, match="must be a string"):
        validate_list_rule_value(
            "cross_pool_dedup_sources", ["MASTER", 42],
        )


def test_validate_list_rule_value_rejects_empty_entry():
    """Validator-vs-accessor: this is STRICTER than
    set_rule_list_value which silently drops empty entries. The
    validator REJECTS them because empty-after-strip is almost
    always an operator typo at the parser / API edit site."""
    from engine.rules import InvalidRuleValueError, validate_list_rule_value
    with pytest.raises(InvalidRuleValueError, match="non-empty"):
        validate_list_rule_value(
            "cross_pool_dedup_sources", ["MASTER", "  "],
        )


def test_validate_list_rule_value_rejects_comma_entry():
    from engine.rules import InvalidRuleValueError, validate_list_rule_value
    with pytest.raises(InvalidRuleValueError, match="separator"):
        validate_list_rule_value(
            "cross_pool_dedup_sources", ["Rare, Imports"],
        )
