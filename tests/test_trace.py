"""Trace/TraceSlot dataclass tests."""
from __future__ import annotations

from engine.trace import ExcludedCandidate, Trace, TraceSlot


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
        "excluded": [],
    }


def test_traceslot_excluded_defaults_to_empty_list_per_instance():
    """field(default_factory=list) — each TraceSlot gets its own list."""
    s1 = TraceSlot(
        slot=1, unit="Mon", row=1, block=1, source="A", strategy="pick",
        pool_index=1, track_id=1, track_name="X", track_artist=None, reason="r",
    )
    s2 = TraceSlot(
        slot=2, unit="Mon", row=1, block=1, source="A", strategy="pick",
        pool_index=2, track_id=2, track_name="Y", track_artist=None, reason="r",
    )
    s1.excluded.append(ExcludedCandidate(
        pool_index=5, track_id=99, track_name="Z", track_artist=None, reason="dedup",
    ))
    assert s1.excluded != s2.excluded
    assert s2.excluded == []


def test_excluded_candidate_round_trips_through_traceslot_dict():
    s = TraceSlot(
        slot=1, unit="Mon", row=1, block=1, source="A", strategy="weighted",
        pool_index=10, track_id=10, track_name="Picked", track_artist="A1",
        reason="highest weight",
        excluded=[
            ExcludedCandidate(
                pool_index=7, track_id=7, track_name="RejA", track_artist="A2",
                reason="artist already in window",
            ),
            ExcludedCandidate(
                pool_index=8, track_id=8, track_name="RejB", track_artist="A3",
                reason="recently used (week -2)",
            ),
        ],
    )
    d = s.to_dict()
    assert d["excluded"] == [
        {
            "pool_index": 7, "track_id": 7, "track_name": "RejA",
            "track_artist": "A2", "reason": "artist already in window",
        },
        {
            "pool_index": 8, "track_id": 8, "track_name": "RejB",
            "track_artist": "A3", "reason": "recently used (week -2)",
        },
    ]


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
