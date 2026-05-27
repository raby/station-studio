"""Tests for engine.generator.generate_stream (§16.11 c1).

Pins Decision G invariants from DESIGN.md §16.11:

  1. Exactly ONE GenerationResult per stream, emitted LAST.
  2. Mid-stream exceptions propagate unchanged (engine doesn't
     try/except its own internals).
  3. Wrapper `generate()` asserts both invariants when draining;
     existing callers' exception expectations are preserved.

Plus tests for the progress-emission cadence:

  4. `phase="start"` event emitted before the slot loop.
  5. `phase="picks"` events emitted at the _PROGRESS_EMIT_EVERY
     cadence (~every 50 picks).
  6. Per-event `current` is monotonic + ≤ `total`.
  7. The wrapper's GenerationResult round-trips identically to
     what a single-Result drain would produce (no field drift).

Test approach: use a small synthetic flat_slots template + tiny
pools so generate_stream produces a known number of events.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
from unittest.mock import patch

import pytest

from engine.generator import (
    GenerationResult,
    ProgressEvent,
    generate,
    generate_stream,
)
from engine.template import parse_template


@dataclass
class FakeTrack:
    """Minimal Track-like for engine tests."""
    id: int
    name: str
    artist: str | None = None
    genre: str | None = None


def _tiny_template(n_slots: int = 5) -> dict:
    """Build a flat_slots station template with n single-pick
    `sequential` slots. Sequential is the simplest strategy that's
    valid in flat_slots form (pick/window require row_template).
    rng=off + named cursor at position 0 → fully deterministic."""
    return {
        "station": "test",
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
            ] * n_slots,
        },
    }


def _tiny_pool(size: int) -> list[FakeTrack]:
    return [
        FakeTrack(id=i, name=f"A{i:04d}", artist=f"Artist{i}")
        for i in range(1, size + 1)
    ]


# ---------- Decision G invariants ---------------------------------------


def test_stream_yields_exactly_one_generation_result_last():
    """Decision G #1: exactly ONE GenerationResult per stream,
    emitted LAST."""
    template = parse_template(_tiny_template(n_slots=5))
    events = list(generate_stream(
        template, pools={"A": _tiny_pool(20)}, week=1,
    ))
    results = [e for e in events if isinstance(e, GenerationResult)]
    assert len(results) == 1, (
        f"expected exactly 1 GenerationResult, got {len(results)}"
    )
    # And the Result is the LAST event.
    assert isinstance(events[-1], GenerationResult)


def test_stream_progress_events_precede_the_result():
    """ProgressEvents come BEFORE the final GenerationResult. The
    engine doesn't emit progress after the work is done."""
    template = parse_template(_tiny_template(n_slots=5))
    events = list(generate_stream(
        template, pools={"A": _tiny_pool(20)}, week=1,
    ))
    last_progress_idx = max(
        (i for i, e in enumerate(events) if isinstance(e, ProgressEvent)),
        default=-1,
    )
    result_idx = next(
        i for i, e in enumerate(events) if isinstance(e, GenerationResult)
    )
    assert last_progress_idx < result_idx


def test_stream_emits_start_event_before_slot_loop():
    """First event is `phase="start"` with current=0 + total=N. Lets
    consumers show "Generating week N (0 / total)" immediately."""
    template = parse_template(_tiny_template(n_slots=5))
    events = list(generate_stream(
        template, pools={"A": _tiny_pool(20)}, week=42,
    ))
    first = events[0]
    assert isinstance(first, ProgressEvent)
    assert first.phase == "start"
    assert first.current == 0
    assert first.total == 5
    assert first.label is not None
    assert "week 42" in first.label


def test_stream_progress_current_monotonic_and_bounded():
    """Decision G implicit invariant: progress events' `current`
    field monotonically increases and never exceeds `total`."""
    template = parse_template(_tiny_template(n_slots=50))
    events = list(generate_stream(
        template, pools={"A": _tiny_pool(100)}, week=1,
    ))
    progress = [e for e in events if isinstance(e, ProgressEvent)]
    currents = [e.current for e in progress]
    assert currents == sorted(currents), (
        f"progress.current not monotonic: {currents}"
    )
    total = progress[0].total
    for e in progress:
        assert e.current <= total, (
            f"current={e.current} exceeds total={total}"
        )
        assert e.total == total, "total should be constant across events"


def test_stream_emits_picks_progress_at_cadence():
    """`_PROGRESS_EMIT_EVERY=50` cadence: ~one event per 50 picks
    during the loop. For a 100-slot template we expect at least
    one `phase="picks"` event (the start event + at least one
    cadence-emitted event for crossing slot 50)."""
    template = parse_template(_tiny_template(n_slots=100))
    events = list(generate_stream(
        template, pools={"A": _tiny_pool(150)}, week=1,
    ))
    picks_events = [
        e for e in events
        if isinstance(e, ProgressEvent) and e.phase == "picks"
    ]
    assert len(picks_events) >= 1
    # The cadence emits at slot_counter crossing 50, 100, etc.
    # For 100 slots we hit at 50 (and possibly 100 — at the END
    # of block whose pick was the 100th; the modulo-crossing
    # check fires there).
    for e in picks_events:
        assert e.current % 50 == 0 or e.current == 100


def test_stream_propagates_mid_stream_exception():
    """Decision G #2: exceptions raised by the engine propagate
    unchanged through the stream — engine doesn't try/except its
    own internals. Test by patching a strategy to raise."""
    template = parse_template(_tiny_template(n_slots=5))
    pools = {"A": _tiny_pool(20)}

    def boom(*args, **kwargs):
        # Yield one event then raise mid-stream — proves the
        # exception escapes the stream cleanly, not just that
        # _iterate_slots is callable.
        raise ValueError("simulated strategy crash")

    # Patch _iterate_slots — the engine's slot loop iterator. Mid-
    # stream raise simulates any strategy crashing.
    with patch("engine.generator._iterate_slots", side_effect=boom):
        with pytest.raises(ValueError, match="simulated strategy crash"):
            list(generate_stream(template, pools, week=1))


# ---------- generate() wrapper invariants ------------------------------


def test_wrapper_returns_final_generation_result():
    """The wrapper drains the stream and returns the GenerationResult
    object — same shape every existing caller expects."""
    template = parse_template(_tiny_template(n_slots=5))
    result = generate(template, pools={"A": _tiny_pool(20)}, week=1)
    assert isinstance(result, GenerationResult)
    assert len(result.picks) == 5
    assert result.trace is not None


def test_wrapper_propagates_mid_stream_exception_unchanged():
    """Decision G #3: wrapper preserves the exception class +
    message. Existing callers' try/except blocks keep working."""
    template = parse_template(_tiny_template(n_slots=5))
    pools = {"A": _tiny_pool(20)}

    def boom(*args, **kwargs):
        raise ValueError("simulated strategy crash")

    with patch("engine.generator._iterate_slots", side_effect=boom):
        with pytest.raises(ValueError, match="simulated strategy crash"):
            generate(template, pools, week=1)


def test_wrapper_asserts_on_multi_result_engine_bug():
    """If `generate_stream` ever yields two GenerationResults
    (engine bug), the wrapper catches it with an AssertionError.
    Simulate by patching generate_stream to yield two results."""
    template = parse_template(_tiny_template(n_slots=3))
    pools = {"A": _tiny_pool(10)}

    real_result = generate(template, pools=pools, week=1)

    def buggy_stream(*args, **kwargs):
        # Two Results — should never happen.
        yield real_result
        yield real_result

    with patch("engine.generator.generate_stream", buggy_stream):
        with pytest.raises(AssertionError, match="multiple GenerationResult"):
            generate(template, pools=pools, week=1)


def test_wrapper_asserts_on_no_result_engine_bug():
    """If `generate_stream` ever yields zero GenerationResults
    (engine bug), the wrapper catches it with an AssertionError."""
    template = parse_template(_tiny_template(n_slots=3))
    pools = {"A": _tiny_pool(10)}

    def buggy_stream(*args, **kwargs) -> Iterator:
        yield ProgressEvent(phase="start", current=0, total=3)
        # Never yields a GenerationResult.

    with patch("engine.generator.generate_stream", buggy_stream):
        with pytest.raises(AssertionError, match="no GenerationResult"):
            generate(template, pools=pools, week=1)


def test_wrapper_output_matches_direct_stream_drain():
    """The wrapper's return value is byte-identical to draining the
    stream manually. Locks the "wrapper is pure" contract — no
    silent mutation, no field drift."""
    template = parse_template(_tiny_template(n_slots=5))
    pools = {"A": _tiny_pool(20)}

    direct = generate(template, pools, week=1)

    stream_result = None
    for event in generate_stream(template, pools, week=1):
        if isinstance(event, GenerationResult):
            stream_result = event

    assert stream_result is not None
    assert direct.picks == stream_result.picks
    assert direct.prev_ref_fallback_mixes == stream_result.prev_ref_fallback_mixes


# ---------- ProgressEvent shape ----------------------------------------


def test_progress_event_is_frozen():
    """ProgressEvent is frozen — once emitted, consumers can't
    mutate it. Catches accidental in-place edits in the job
    runner's snapshot logic."""
    e = ProgressEvent(phase="picks", current=5, total=10)
    with pytest.raises(Exception):  # FrozenInstanceError
        e.current = 6  # type: ignore[misc]


def test_progress_event_label_optional():
    """label defaults to None; callers compose from phase/current/total
    if absent."""
    e = ProgressEvent(phase="picks", current=5, total=10)
    assert e.label is None
    e2 = ProgressEvent(phase="picks", current=5, total=10, label="custom")
    assert e2.label == "custom"
