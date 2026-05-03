"""Unit tests for engine/cursor.py.

cursor.py is currently used only when porting Rebirth Radio (which has 9
persistent cursors). These tests prove the interface is correct so the port
doesn't land with untested helpers.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from engine.cursor import get_cursor, set_cursor
from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station, CursorState


@pytest.fixture
def db(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/cursor.db")
    Base.metadata.create_all(engine)
    S = make_sessionmaker(engine)
    with S() as session:
        station = Station(slug="test", name="Test", template={})
        session.add(station)
        session.commit()
        yield session, station.id


def test_get_cursor_creates_row_on_first_call(db):
    session, sid = db
    pos = get_cursor(session, station_id=sid, name="A")
    assert pos == 0
    row = session.query(CursorState).filter_by(station_id=sid, name="A").first()
    assert row is not None
    assert row.position == 0


def test_get_cursor_respects_custom_default(db):
    session, sid = db
    pos = get_cursor(session, station_id=sid, name="B", default=42)
    assert pos == 42


def test_get_cursor_returns_existing_value(db):
    session, sid = db
    set_cursor(session, station_id=sid, name="C", position=99)
    pos = get_cursor(session, station_id=sid, name="C")
    assert pos == 99


def test_set_cursor_creates_row_when_absent(db):
    session, sid = db
    set_cursor(session, station_id=sid, name="D", position=7)
    row = session.query(CursorState).filter_by(station_id=sid, name="D").first()
    assert row is not None
    assert row.position == 7


def test_set_cursor_updates_existing_row(db):
    session, sid = db
    set_cursor(session, station_id=sid, name="E", position=1)
    set_cursor(session, station_id=sid, name="E", position=100)
    rows = session.query(CursorState).filter_by(station_id=sid, name="E").all()
    assert len(rows) == 1
    assert rows[0].position == 100


def test_cursors_are_independent_per_name(db):
    session, sid = db
    set_cursor(session, station_id=sid, name="X", position=10)
    set_cursor(session, station_id=sid, name="Y", position=20)
    assert get_cursor(session, station_id=sid, name="X") == 10
    assert get_cursor(session, station_id=sid, name="Y") == 20


def test_cursors_are_independent_per_station(db, tmp_path):
    """Two stations with the same cursor name must not share state."""
    engine = make_engine(f"sqlite:///{tmp_path}/cursor2.db")
    Base.metadata.create_all(engine)
    S = make_sessionmaker(engine)
    with S() as session:
        s1 = Station(slug="s1", name="S1", template={})
        s2 = Station(slug="s2", name="S2", template={})
        session.add_all([s1, s2])
        session.flush()
        set_cursor(session, station_id=s1.id, name="main", position=5)
        set_cursor(session, station_id=s2.id, name="main", position=50)
        assert get_cursor(session, station_id=s1.id, name="main") == 5
        assert get_cursor(session, station_id=s2.id, name="main") == 50
