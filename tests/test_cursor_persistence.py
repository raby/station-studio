"""Tests for the §14.30 stateful named-cursor model.

Covers load_cursor_positions + advance_cursors + the reset_cycle
semantics that match weekly's exact behaviour (wrap on source length,
reset every N generations).

Also covers the generator's branch on template.cursor.persist —
stateless templates continue to use the §14.22 stateless math;
persistent templates consult `named_cursor_positions` instead.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import sessionmaker

from engine.database import make_engine, make_sessionmaker
from engine.generator import generate
from engine.models import (
    Base, CursorState, SourcePlaylist, SourcePlaylistEntry, Station, Track,
)
from engine.persistence import (
    advance_cursors, load_cursor_positions, load_pools,
)
from engine.template import parse_template


@dataclass
class FakeTrack:
    id: int
    name: str
    artist: str | None = None
    genre: str | None = None


def _seq_pool(prefix: str, size: int) -> list[FakeTrack]:
    return [FakeTrack(id=i, name=f"{prefix}{i:04d}") for i in range(1, size + 1)]


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{tmp_path}/cursor.db")
    Base.metadata.create_all(engine)
    return make_sessionmaker(engine)


# ---------- load_cursor_positions ---------------------------------------


def _persist_template(*, persist: bool, cursors_init: dict[str, int]) -> dict:
    """Template with one sequential block per declared cursor."""
    return {
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "S", "colour": "#111", "role": "content"}],
        "cursor": {
            "base_advance_per_week": 0, "cycle_weeks": 1, "persist": persist,
        },
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "cursors_init": cursors_init,
            "row_template": [
                {"source": "S", "strategy": "sequential", "size": 1,
                 "cursor_id": list(cursors_init)[0]},
            ],
        },
    }


def _seed_station(Session: sessionmaker, template: dict) -> int:
    with Session() as db:
        st = Station(slug="rb", name="Rebirth", template=template)
        db.add(st)
        db.commit()
        return st.id


def test_load_cursor_positions_falls_back_to_init_when_no_rows(session_factory):
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"NIGHT_1": 7, "NIGHT_2": 11})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    with Session() as db:
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        assert positions == {"NIGHT_1": 7, "NIGHT_2": 11}


def test_load_cursor_positions_uses_persisted_when_present(session_factory):
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"NIGHT_1": 0})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    with Session() as db:
        db.add(CursorState(station_id=station_id, name="NIGHT_1", position=240))
        db.commit()
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        assert positions == {"NIGHT_1": 240}


def test_load_cursor_positions_mixed_persisted_and_initial(session_factory):
    """Two cursors declared; only one has a persisted row — the other
    falls back to its init value."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"A": 0, "B": 100})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    with Session() as db:
        db.add(CursorState(station_id=station_id, name="A", position=999))
        db.commit()
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        assert positions == {"A": 999, "B": 100}


# ---------- advance_cursors ---------------------------------------------


def test_advance_cursors_wraps_on_source_length(session_factory):
    """Matches weekly's `(position + step) % source_length` semantics."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"C1": 950})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 1000)
    with Session() as db:
        # Generation 1: cursor at 950, advance by 120 → wraps to 70.
        advance_cursors(
            db, station_id=station_id, template=tmpl, pools={"S": pool},
            advance_step=120, reset_cycle=0, generation_count=1,
        )
        db.commit()
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        assert positions["C1"] == (950 + 120) % 1000  # 70


def test_advance_cursors_zero_reset_cycle_never_resets(session_factory):
    """reset_cycle=0 means never reset — advances continue to wrap
    indefinitely. Matches weekly's default."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"C1": 0})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 100)
    with Session() as db:
        for gen in range(1, 20):
            advance_cursors(
                db, station_id=station_id, template=tmpl, pools={"S": pool},
                advance_step=15, reset_cycle=0, generation_count=gen,
            )
            db.commit()
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        # 19 advances of +15 from 0 wraps mod 100. Each advance applied
        # to the previous result, so the final position is:
        # ((0 + 15) % 100) iterated 19 times → 19*15 mod 100 = 285 mod 100 = 85.
        assert positions["C1"] == 85


def test_advance_cursors_resets_at_boundary(session_factory):
    """When `generation_count % reset_cycle == 0` (and >0), every
    cursor resets to its initial position. Matches weekly's lines
    667–672."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"C1": 0, "C2": 50})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 1000)
    with Session() as db:
        # Advance to non-trivial positions.
        for gen in range(1, 6):
            advance_cursors(
                db, station_id=station_id, template=tmpl, pools={"S": pool},
                advance_step=120, reset_cycle=10, generation_count=gen,
            )
            db.commit()
        # Generation 10 hits the reset boundary.
        advance_cursors(
            db, station_id=station_id, template=tmpl, pools={"S": pool},
            advance_step=120, reset_cycle=10, generation_count=10,
        )
        db.commit()
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        assert positions == {"C1": 0, "C2": 50}


def test_advance_cursors_generation_count_zero_does_not_reset(session_factory):
    """Weekly's logic: reset fires only when generation_count > 0.
    Generation 0 is the seed/import; it should NOT reset on its own."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"C1": 50})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 100)
    with Session() as db:
        # generation_count=0 with reset_cycle=any-non-zero must NOT reset.
        # Should advance normally (or be a no-op for gen 0?). Weekly
        # branches on `generation_count > 0`, so it ADVANCES at gen 0
        # too. Match that.
        advance_cursors(
            db, station_id=station_id, template=tmpl, pools={"S": pool},
            advance_step=10, reset_cycle=10, generation_count=0,
        )
        db.commit()
        positions = load_cursor_positions(db, station_id=station_id, template=tmpl)
        assert positions["C1"] == 60  # 50 + 10


def test_advance_cursors_noop_for_stateless_template(session_factory):
    """When template.cursor.persist is False, advance_cursors does
    nothing — the engine layer uses stateless math; cursor_state isn't
    consulted."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=False, cursors_init={"C1": 50})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 100)
    with Session() as db:
        advance_cursors(
            db, station_id=station_id, template=tmpl, pools={"S": pool},
            advance_step=10, reset_cycle=0, generation_count=1,
        )
        db.commit()
        # No row was written.
        assert db.query(CursorState).count() == 0


# ---------- generator: persist=True consults named_cursor_positions ----


def test_generator_persistent_cursor_uses_passed_positions():
    """When template.cursor.persist is True, the sequential dispatch
    reads from named_cursor_positions instead of computing from
    cursors_init + week."""
    tmpl_dict = _persist_template(persist=True, cursors_init={"C1": 0})
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 100)
    # Inject a position that DIFFERS from cursors_init + computed advance.
    r = generate(
        tmpl, {"S": pool}, week=5,
        named_cursor_positions={"C1": 42},
    )
    # Pool index is 1-indexed; pool[42] = X0043.
    assert r.picks[0].name == "X0043"


def test_generator_stateless_cursor_ignores_passed_positions():
    """When template.cursor.persist is False, the generator computes
    from cursors_init + week (§14.22). Passing named_cursor_positions
    is a no-op for these templates."""
    tmpl_dict = _persist_template(persist=False, cursors_init={"C1": 0})
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 100)
    # Template's cursor.base_advance_per_week=0, cycle_weeks=1 →
    # base_cursor_for_week always returns 0. So position = 0 + 0 = 0,
    # and pool[0] = X0001.
    r = generate(
        tmpl, {"S": pool}, week=5,
        named_cursor_positions={"C1": 42},  # ignored
    )
    assert r.picks[0].name == "X0001"


# ---------- end-to-end byte-equality with weekly's cursor model ---------


def test_persistent_cursor_round_trip_matches_weekly_semantics(session_factory):
    """End-to-end: a persistent cursor at position P advances by step
    after generation; next generation starts from the wrapped result.
    Verifies the API-equivalent flow (load → generate → advance) gives
    weekly-identical cursor evolution."""
    Session = session_factory
    tmpl_dict = _persist_template(persist=True, cursors_init={"C1": 0})
    station_id = _seed_station(Session, tmpl_dict)
    tmpl = parse_template(tmpl_dict)
    pool = _seq_pool("X", 100)

    # Mimic the API flow for 5 generations.
    picked_positions: list[int] = []
    for gen in range(1, 6):
        with Session() as db:
            positions = load_cursor_positions(
                db, station_id=station_id, template=tmpl,
            )
            r = generate(
                tmpl, {"S": pool}, week=gen,
                named_cursor_positions=positions,
            )
            picked_positions.append(positions["C1"])
            advance_cursors(
                db, station_id=station_id, template=tmpl, pools={"S": pool},
                advance_step=15, reset_cycle=0, generation_count=gen,
            )
            db.commit()

    # Weekly's behaviour: gen 1 picks at 0, gen 2 at 15, gen 3 at 30,
    # gen 4 at 45, gen 5 at 60.
    assert picked_positions == [0, 15, 30, 45, 60]
