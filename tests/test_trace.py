"""Trace/TraceSlot dataclass tests."""
from __future__ import annotations

from engine.trace import Trace, TraceSlot


def test_traceslot_to_dict_round_trips_all_fields():
    s = TraceSlot(
        slot=1, unit="Mon", row=1, block=1,
        source="A", strategy="pick", pool_index=3,
        track_id=42, track_name="Song", track_artist="Artist",
        reason="seeded",
    )
    d = s.to_dict()
    assert d == {
        "slot": 1, "unit": "Mon", "row": 1, "block": 1,
        "source": "A", "strategy": "pick", "pool_index": 3,
        "track_id": 42, "track_name": "Song", "track_artist": "Artist",
        "reason": "seeded",
    }


def test_trace_to_dict_serialises_slots_and_fields():
    t = Trace(station="alpha", week=3, rng="on", seed=7)
    t.slots.append(TraceSlot(
        slot=1, unit="Mon", row=1, block=1,
        source="A", strategy="pick", pool_index=1,
        track_id=1, track_name="X", track_artist=None,
        reason="r",
    ))
    d = t.to_dict()
    assert d["station"] == "alpha"
    assert d["week"] == 3
    assert d["rng"] == "on"
    assert d["seed"] == 7
    assert d["trace_id"] == t.trace_id
    assert d["generated_at"] == t.generated_at
    assert len(d["slots"]) == 1
    assert d["slots"][0]["track_name"] == "X"


def test_trace_autogenerates_unique_trace_ids():
    t1 = Trace(station="x", week=1)
    t2 = Trace(station="x", week=1)
    assert t1.trace_id != t2.trace_id
