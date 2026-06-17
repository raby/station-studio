"""Shared test helpers for the editor API + batch endpoint suites.

§16.2.6.t12 review P8 — extracted from `tests/test_editor_api.py` so
test modules don't import leading-underscore private helpers from
each other. `_seed_station` is renamed to the public `seed_station`
at the same time (the underscore was leftover from when it was a
single-file helper).
"""
from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from engine.models import Station


def minimal_template(slug: str = "ts") -> dict:
    """Minimal flat_slots template with 3 atomic slots — same shape
    used by the c1 jobs API tests. Deterministic + small."""
    return {
        "station": slug,
        "name": "Test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "A", "colour": "#abc", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"C": 0},
            "flat_slots": [
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C"}
            ] * 3,
        },
    }


def seed_station(Session: sessionmaker, slug: str = "ts") -> dict:
    """Insert a Station with the minimal template. Returns the template
    for follow-up assertions."""
    template = minimal_template(slug)
    with Session() as db:
        db.add(Station(slug=slug, name="Test", template=template))
        db.commit()
    return template
