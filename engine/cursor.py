"""Per-station cursor state helpers.

For Classics, the base cursor is *derived* from the week number and doesn't
need to be stored. The helpers here are for stations that do want persistent
cursors (e.g. ported Rebirth Radio with its 9 independent cursors).

v1 has no live users; this module defines the interface and leaves storage
to callers via the CursorState ORM model.

TODO: wire these helpers into the generate endpoint and the station-detail
page when porting Rebirth Radio (which has 9 independent cursors for
COMMERCIAL, NIGHT, MIXES, etc.). At that point add tests in tests/test_cursor.py.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from engine.models import CursorState


def get_cursor(db: Session, *, station_id: int, name: str, default: int = 0) -> int:
    row = db.query(CursorState).filter_by(station_id=station_id, name=name).first()
    if row is None:
        row = CursorState(station_id=station_id, name=name, position=default)
        db.add(row)
        db.flush()
    return row.position


def set_cursor(db: Session, *, station_id: int, name: str, position: int) -> None:
    row = db.query(CursorState).filter_by(station_id=station_id, name=name).first()
    if row is None:
        row = CursorState(station_id=station_id, name=name, position=position)
        db.add(row)
    else:
        row.position = position
    db.flush()
