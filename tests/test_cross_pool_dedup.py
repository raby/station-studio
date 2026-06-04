"""§16.14.t5 — cross-pool dedup as a station rule.

Covers:
1. ``RULE_LIST_DEFAULTS`` registry + ``get/set/clear_rule_list_value``
   accessors (parallel to the int-rule machinery).
2. ``apply_weighted(excluded_track_pids=...)`` filter behaviour:
   drops matching tracks, leaves non-matching tracks alone, and
   raises a specific diagnostic when the whole pool is excluded.
3. ``load_excluded_track_pids`` persistence helper: empty default,
   missing-source ValueError (D5 fail-loud), wrong-role TemplateError
   (D6 role=reference required), success returns frozenset of pids.
4. End-to-end smoke via ``apply_weighted`` with a real persistent_id
   string in the exclusion set.

The bundled-station demo + DESIGN entry come in c2/c3 — c1 is just
the engine + persistence wiring.
"""
from __future__ import annotations

import plistlib
import random
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from engine.database import make_engine, make_sessionmaker
from engine.importer import import_source
from engine.models import Base, Rule, Station
from engine.persistence import load_excluded_track_pids
from engine.rules import (
    InvalidRuleValueError, RULE_LIST_DEFAULTS, UnknownRuleError,
    clear_rule_list, get_rule_list_value, set_rule_list_value,
)
from engine.strategies.weighted import apply_weighted, build_lookups
from engine.template import TemplateError


# ---------- shared fixtures ---------------------------------------------


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{tmp_path}/dedup.db")
    Base.metadata.create_all(engine)
    return make_sessionmaker(engine)


@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/dedup-db.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


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


def _make_itunes_plist(path: Path, name: str, tracks: list[dict]) -> None:
    tracks_dict: dict[str, dict] = {str(t["Track ID"]): t for t in tracks}
    data = {
        "Library Persistent ID": "0000000000000001",
        "Tracks": tracks_dict,
        "Playlists": [
            {
                "Name": name,
                "Playlist Items": [{"Track ID": t["Track ID"]} for t in tracks],
            }
        ],
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def _track(track_id: int, pid: str, name: str, artist: str = "Artist") -> dict:
    return {
        "Track ID": track_id, "Persistent ID": pid,
        "Name": name, "Artist": artist,
    }


# =========================================================================
# 1. List-rule registry + accessors
# =========================================================================


def test_list_rule_registry_has_cross_pool_dedup_sources():
    """The first list-rule ships in the registry. Future list rules
    add entries here."""
    assert "cross_pool_dedup_sources" in RULE_LIST_DEFAULTS
    defn = RULE_LIST_DEFAULTS["cross_pool_dedup_sources"]
    assert defn.value == []
    assert "weighted" in defn.description.lower()


def test_get_rule_list_value_returns_empty_default(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == []


def test_get_rule_list_value_returns_copy_of_default(session_factory):
    """Callers mutating the returned list must not poison the
    registry default for subsequent calls."""
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        first = get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        )
        first.append("MASTER")
        second = get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        )
        assert second == []


def test_get_rule_list_value_unknown_key_raises(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        with pytest.raises(UnknownRuleError):
            get_rule_list_value(db, station_id=station_id, key="bogus_key")


def test_set_rule_list_value_persists_then_reads_back(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources",
            value=["MASTER", "OLDIES"],
        )
        db.commit()
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == ["MASTER", "OLDIES"]


def test_set_rule_list_value_upsert_replaces_existing(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources", value=["MASTER"],
        )
        db.commit()
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources", value=["OLDIES", "RARITIES"],
        )
        db.commit()
        # Single row replaced — not two rows accumulated.
        assert db.query(Rule).count() == 1
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == ["OLDIES", "RARITIES"]


def test_set_rule_list_value_empty_list_persists_as_explicit_override(session_factory):
    """An empty list IS persisted (explicit "no exclusion" override)
    so later code can tell "user explicitly cleared it" from "user
    never set it". Both reads return [] — the distinction is the
    presence of the Rule row."""
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources", value=[],
        )
        db.commit()
        assert db.query(Rule).count() == 1
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == []


def test_set_rule_list_value_strips_whitespace_and_drops_empties(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources",
            value=["  MASTER ", "", "  ", "OLDIES"],
        )
        db.commit()
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == ["MASTER", "OLDIES"]


def test_set_rule_list_value_rejects_comma_in_entry(session_factory):
    """The TEXT storage format has no comma escape — entries with
    commas would silently split on read. Reject at write-time."""
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        with pytest.raises(InvalidRuleValueError, match="separator"):
            set_rule_list_value(
                db, station_id=station_id,
                key="cross_pool_dedup_sources",
                value=["Rare, Imports"],
            )


def test_set_rule_list_value_unknown_key_raises(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        with pytest.raises(UnknownRuleError):
            set_rule_list_value(
                db, station_id=station_id,
                key="bogus_list_key", value=["MASTER"],
            )


def test_clear_rule_list_reverts_to_default(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources", value=["MASTER"],
        )
        db.commit()
        assert clear_rule_list(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) is True
        db.commit()
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == []


def test_clear_rule_list_when_no_override_returns_false(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        assert clear_rule_list(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) is False


def test_clear_rule_list_unknown_key_raises(session_factory):
    station_id = _seed_station(session_factory)
    with session_factory() as db:
        with pytest.raises(UnknownRuleError):
            clear_rule_list(db, station_id=station_id, key="bogus")


# =========================================================================
# 2. apply_weighted excluded_track_pids filter
# =========================================================================


@dataclass
class _WTrack:
    """Stand-in for engine.models.Track with the fields the weighted
    strategy reads (id, name, artist, persistent_id). The strategy
    treats `pool` as duck-typed by getattr."""
    id: int
    name: str
    artist: str
    persistent_id: str | None


def _w_pool() -> list[_WTrack]:
    return [
        _WTrack(id=1, name="A1", artist="Artist A", persistent_id="PID0001"),
        _WTrack(id=2, name="B1", artist="Artist B", persistent_id="PID0002"),
        _WTrack(id=3, name="C1", artist="Artist C", persistent_id="PID0003"),
    ]


def test_weighted_excludes_matching_pids():
    """Tracks whose persistent_id is in the exclusion set must NEVER
    be picked. The rest are eligible as usual."""
    pool = _w_pool()
    lookups = build_lookups(pool)
    # Run a few picks with the exclusion set; PID0002 must not appear.
    picked_pids = []
    rng = random.Random(7)
    for _ in range(5):
        track, _ = apply_weighted(
            pool, lookups=lookups,
            recent_artist_keys=[], used_track_ids=[],
            rng=rng, source_name="C",
            excluded_track_pids=frozenset({"PID0002"}),
        )
        picked_pids.append(track.persistent_id)
    assert "PID0002" not in picked_pids
    assert set(picked_pids) <= {"PID0001", "PID0003"}


def test_weighted_no_exclusion_behaves_as_before():
    """Backward-compat: omitting the kwarg leaves every track
    eligible. The shuffle-bag rotation still covers the pool."""
    pool = _w_pool()
    lookups = build_lookups(pool)
    picked = set()
    for _ in range(3):
        track, _ = apply_weighted(
            pool, lookups=lookups,
            recent_artist_keys=[], used_track_ids=[t.id for t in pool if t.persistent_id in picked],
            rng=None, source_name="C",
        )
        picked.add(track.persistent_id)
    # With rng=None the picker walks pool order — all three appear.
    assert picked == {"PID0001", "PID0002", "PID0003"}


def test_weighted_empty_excluded_set_behaves_as_before():
    """Empty frozenset is a common pass-through case (no rule set).
    Must short-circuit cleanly and not change behaviour."""
    pool = _w_pool()
    lookups = build_lookups(pool)
    track, _ = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[], used_track_ids=[],
        rng=None, source_name="C",
        excluded_track_pids=frozenset(),
    )
    assert track.persistent_id == "PID0001"


def test_weighted_fully_excluded_pool_raises_specific_diagnostic():
    """All three tracks excluded → IndexError with the §16.14.t5
    diagnostic, NOT the bare "pool is empty" message. Operators
    misconfiguring the rule should see what went wrong."""
    pool = _w_pool()
    lookups = build_lookups(pool)
    with pytest.raises(IndexError, match="cross_pool_dedup_sources"):
        apply_weighted(
            pool, lookups=lookups,
            recent_artist_keys=[], used_track_ids=[],
            rng=None, source_name="C",
            excluded_track_pids=frozenset({"PID0001", "PID0002", "PID0003"}),
        )


def test_weighted_tracks_without_persistent_id_pass_through():
    """A pool of dataclass tracks lacking the persistent_id attr (as
    in test_strategies.py's existing CommercialTrack) must not crash —
    getattr falls back to None, which won't match string pids."""
    @dataclass
    class _NoPid:
        id: int
        name: str
        artist: str

    pool = [_NoPid(1, "X", "A"), _NoPid(2, "Y", "B")]
    lookups = build_lookups(pool)
    track, _ = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[], used_track_ids=[],
        rng=None, source_name="C",
        excluded_track_pids=frozenset({"PID0001"}),
    )
    assert track.id == 1


# =========================================================================
# 4. Slice-review sweep pins (S3 + S4)
# =========================================================================


def test_set_rule_list_value_is_idempotent_under_repeated_seed(session_factory):
    """§16.14.t5 slice-review S3 — pin re-seed idempotence for
    list rules. `seed_station` re-runs the set on every call
    (action='unchanged' included); calling the underlying accessor
    twice with the same value must give exactly one Rule row.

    A regression where `set_rule_list_value` accidentally INSERTed
    rather than upserted would surface as `make seed-stations` (or
    the lifespan's auto-seed) accumulating duplicate Rule rows on
    every restart — silent until a primary-key constraint trips or
    a `get_all_rules` callsite groans about cardinality.
    """
    from engine.models import Rule

    station_id = _seed_station(session_factory)
    with session_factory() as db:
        for _ in range(3):  # simulate three boots of seed_station
            set_rule_list_value(
                db, station_id=station_id,
                key="cross_pool_dedup_sources",
                value=["MASTER"],
            )
            db.commit()
        # Exactly one Rule row, value preserved.
        assert db.query(Rule).filter_by(
            station_id=station_id, key="cross_pool_dedup_sources",
        ).count() == 1
        assert get_rule_list_value(
            db, station_id=station_id, key="cross_pool_dedup_sources",
        ) == ["MASTER"]


def test_rules_crud_endpoint_surfaces_list_rules_with_kind_discriminator(session_factory):
    """§16.14.t10: list rules ARE surfaced by `get_all_rules` (the
    GET /api/stations/{slug}/rules driver), tagged with
    `kind: "list"` and carrying their value as a list[str].

    Replaces the §16.14.t5 slice-review S4 test that pinned the
    OPPOSITE behavior (list rules invisible). t10 flips the
    contract; this test pins the new shape.
    """
    from engine.rules import RULE_DEFAULTS, RULE_LIST_DEFAULTS, get_all_rules

    station_id = _seed_station(session_factory)
    with session_factory() as db:
        # Set a list rule + an int rule to mirror the production
        # state after seed_station runs against the starter station.
        set_rule_list_value(
            db, station_id=station_id,
            key="cross_pool_dedup_sources", value=["REF"],
        )
        db.commit()

        rows = get_all_rules(db, station_id=station_id)
        keys = {r["key"] for r in rows}
        # D5: all int + list rules surface, alphabetically interleaved.
        assert keys == set(RULE_DEFAULTS) | set(RULE_LIST_DEFAULTS)
        assert "cross_pool_dedup_sources" in keys

        by_key = {r["key"]: r for r in rows}
        # D1: list rule rows are tagged kind="list".
        assert by_key["cross_pool_dedup_sources"]["kind"] == "list"
        # D2: list rule value is the JSON-native list[str].
        assert by_key["cross_pool_dedup_sources"]["value"] == ["REF"]
        assert isinstance(by_key["cross_pool_dedup_sources"]["value"], list)
        # max_value is always None for list rules.
        assert by_key["cross_pool_dedup_sources"]["max_value"] is None
        # int rule rows keep their kind + numeric value shape.
        assert by_key["artist_dedup_window"]["kind"] == "int"
        assert isinstance(by_key["artist_dedup_window"]["value"], int)


# =========================================================================
# 3. load_excluded_track_pids persistence helper
# =========================================================================


def test_load_excluded_track_pids_empty_when_rule_unset(db_session, tmp_path):
    """No rule row → empty frozenset. Callers can pass through
    unconditionally."""
    db_session.add(Station(slug="rb", name="rb", template={}))
    db_session.commit()
    station = db_session.query(Station).filter_by(slug="rb").one()
    pids = load_excluded_track_pids(db_session, station_id=station.id)
    assert pids == frozenset()


def test_load_excluded_track_pids_returns_frozenset_of_pids(db_session, tmp_path):
    """Happy path: a reference pool's tracks become the exclusion set."""
    xml_path = tmp_path / "master.xml"
    _make_itunes_plist(xml_path, "MASTER", [
        _track(1, "MMMM000000000001", "T1"),
        _track(2, "MMMM000000000002", "T2"),
    ])
    import_source(db_session, xml_path, "MASTER", role="reference")
    db_session.add(Station(slug="rb", name="rb", template={}))
    db_session.commit()
    station = db_session.query(Station).filter_by(slug="rb").one()
    set_rule_list_value(
        db_session, station_id=station.id,
        key="cross_pool_dedup_sources", value=["MASTER"],
    )
    db_session.commit()

    pids = load_excluded_track_pids(db_session, station_id=station.id)
    assert isinstance(pids, frozenset)
    assert pids == frozenset({"MMMM000000000001", "MMMM000000000002"})


def test_load_excluded_track_pids_unions_multiple_sources(db_session, tmp_path):
    """Multiple reference pools → the union of their tracks."""
    a = tmp_path / "a.xml"
    _make_itunes_plist(a, "REF_A", [_track(1, "AAAA000000000001", "TA")])
    import_source(db_session, a, "REF_A", role="reference")
    b = tmp_path / "b.xml"
    _make_itunes_plist(b, "REF_B", [_track(2, "BBBB000000000002", "TB")])
    import_source(db_session, b, "REF_B", role="reference")

    db_session.add(Station(slug="rb", name="rb", template={}))
    db_session.commit()
    station = db_session.query(Station).filter_by(slug="rb").one()
    set_rule_list_value(
        db_session, station_id=station.id,
        key="cross_pool_dedup_sources", value=["REF_A", "REF_B"],
    )
    db_session.commit()

    pids = load_excluded_track_pids(db_session, station_id=station.id)
    assert pids == frozenset({"AAAA000000000001", "BBBB000000000002"})


def test_load_excluded_track_pids_missing_source_raises_value_error(db_session):
    """D5: missing source pool is a setup error — fail loud rather
    than silently dropping it. Same exception type as load_pools'
    missing-pool error."""
    db_session.add(Station(slug="rb", name="rb", template={}))
    db_session.commit()
    station = db_session.query(Station).filter_by(slug="rb").one()
    set_rule_list_value(
        db_session, station_id=station.id,
        key="cross_pool_dedup_sources", value=["NEVER_IMPORTED"],
    )
    db_session.commit()

    with pytest.raises(ValueError, match="NEVER_IMPORTED"):
        load_excluded_track_pids(db_session, station_id=station.id)


def test_load_excluded_track_pids_content_role_raises_template_error(db_session, tmp_path):
    """D6: only role='reference' pools are allowed. Naming a content
    pool would footgun the operator (excluding the same pool you're
    sampling from). Raises TemplateError (distinct type from the
    missing-pool ValueError)."""
    xml_path = tmp_path / "content.xml"
    _make_itunes_plist(xml_path, "STARTER_CONTENT", [
        _track(1, "CCCC000000000001", "T1"),
    ])
    import_source(db_session, xml_path, "STARTER_CONTENT", role="content")

    db_session.add(Station(slug="rb", name="rb", template={}))
    db_session.commit()
    station = db_session.query(Station).filter_by(slug="rb").one()
    set_rule_list_value(
        db_session, station_id=station.id,
        key="cross_pool_dedup_sources", value=["STARTER_CONTENT"],
    )
    db_session.commit()

    with pytest.raises(TemplateError, match="role='content'.*reference|role=.content.*not.*reference"):
        load_excluded_track_pids(db_session, station_id=station.id)


def test_load_excluded_track_pids_explicit_empty_list_is_empty(db_session):
    """Explicit empty override (no exclusion) still returns empty
    frozenset — both default and explicit-empty collapse here."""
    db_session.add(Station(slug="rb", name="rb", template={}))
    db_session.commit()
    station = db_session.query(Station).filter_by(slug="rb").one()
    set_rule_list_value(
        db_session, station_id=station.id,
        key="cross_pool_dedup_sources", value=[],
    )
    db_session.commit()
    assert load_excluded_track_pids(db_session, station_id=station.id) == frozenset()
