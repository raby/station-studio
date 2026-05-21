"""Unit tests for the diagnostic harness in tests/byte_equality.py
(§14.37). Verifies the comparison helpers themselves before they're
used by the multi-week chain test (test_byte_equality_synthetic.py)
or the real-XML test (commit 2)."""
from __future__ import annotations

import pytest

from tests.byte_equality import (
    SlotRef,
    assert_chain_equal,
    assert_pids_equal,
    find_first_divergence,
    render_divergence_context,
    slot_ref_from_block,
)


def _slot_refs(n: int, week: int = 1) -> list[SlotRef]:
    return [
        SlotRef(week=week, slot_index=i, strategy="weighted", source="COMMERCIAL")
        for i in range(n)
    ]


# ---------- find_first_divergence --------------------------------------


def test_find_first_divergence_returns_none_when_identical():
    assert find_first_divergence(["A", "B", "C"], ["A", "B", "C"]) is None


def test_find_first_divergence_returns_first_index():
    assert find_first_divergence(["A", "B", "C"], ["A", "X", "C"]) == 1


def test_find_first_divergence_short_circuits_on_first_diff():
    """Catches downstream divergences too — but the first one is what
    the diagnostic reports. (After RNG sequences diverge, every
    downstream pick is unrelated; reporting all of them is noise.)"""
    assert find_first_divergence(["A", "X", "Y", "Z"], ["A", "B", "C", "D"]) == 1


# ---------- assert_pids_equal -----------------------------------------


def test_assert_pids_equal_passes_when_identical():
    """Identical sequences = no exception."""
    assert_pids_equal(["A", "B", "C"], ["A", "B", "C"], _slot_refs(3))


def test_assert_pids_equal_raises_on_pid_mismatch():
    with pytest.raises(AssertionError) as exc_info:
        assert_pids_equal(["A", "B", "C"], ["A", "X", "C"], _slot_refs(3))
    msg = str(exc_info.value)
    assert "first divergence at index 1" in msg
    assert "<-- DIFF" in msg


def test_assert_pids_equal_raises_on_length_mismatch():
    """Length mismatch is a distinct failure mode — usually template
    drift, not engine misbehaviour. The message should call that out."""
    with pytest.raises(AssertionError) as exc_info:
        assert_pids_equal(["A", "B", "C"], ["A", "B"], _slot_refs(3))
    msg = str(exc_info.value)
    assert "sequence-length mismatch" in msg
    assert "template-slot-count drift" in msg
    assert "3" in msg and "2" in msg  # counts cited


def test_assert_pids_equal_prepends_label_to_failure():
    """The label distinguishes "which week failed" in multi-week
    chain tests."""
    with pytest.raises(AssertionError, match="week 4:"):
        assert_pids_equal(["A"], ["B"], _slot_refs(1), label="week 4")


# ---------- render_divergence_context ----------------------------------


def test_render_divergence_context_centers_on_divergence():
    """Window is ±N slots; divergence sits in the middle of the window
    (or at the edge if near start/end)."""
    refs = _slot_refs(10)
    msg = render_divergence_context(
        ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"],
        ["A0", "A1", "A2", "A3", "X5", "A5", "A6", "A7", "A8", "A9"],
        refs,
        divergence_idx=4,
        context_radius=2,
    )
    # Indices 2, 3, 4, 5, 6 should appear; 0, 1, 7-9 should not.
    for keep in ["    2", "    3", "    4", "    5", "    6"]:
        assert keep in msg
    # Marker is on the divergent line only.
    diff_lines = [line for line in msg.split("\n") if "<-- DIFF" in line]
    assert len(diff_lines) == 1
    assert "X5" in diff_lines[0]


def test_render_divergence_context_clamps_to_sequence_bounds():
    """Divergence at index 0 → no context before; at last → no after.
    No off-by-ones."""
    refs = _slot_refs(3)
    msg = render_divergence_context(
        ["A", "B", "C"], ["X", "B", "C"],
        refs, divergence_idx=0, context_radius=5,
    )
    # All 3 slots fit in the window; divergence is at index 0.
    assert "<-- DIFF" in msg
    # First slot is the diff.
    lines = msg.split("\n")
    diff_line = next(l for l in lines if "<-- DIFF" in l)
    assert "    0" in diff_line


# ---------- slot_ref_from_block ----------------------------------------


class _FakeBlock:
    """Minimal stand-in for engine.template.BlockSpec — captures the
    fields slot_ref_from_block actually reads."""
    def __init__(self, **kwargs):
        self.strategy = kwargs.get("strategy", "weighted")
        self.source = kwargs.get("source", "COMMERCIAL")
        self.genre = kwargs.get("genre")
        self.exclusion_window = kwargs.get("exclusion_window")
        self.cursor_id = kwargs.get("cursor_id")
        self.position = kwargs.get("position")
        self.fallback_genre = kwargs.get("fallback_genre")


def test_slot_ref_from_block_captures_present_extras_only():
    """Only set fields land in `extra` — keeps the failure message
    uncluttered for strategies with no per-block params (commercial)."""
    block = _FakeBlock(strategy="weighted")
    ref = slot_ref_from_block(week=1, slot_index=0, block=block)
    assert ref.strategy == "weighted"
    assert ref.source == "COMMERCIAL"
    assert ref.extra == {}


def test_slot_ref_from_block_captures_mix_params():
    block = _FakeBlock(strategy="mix", source="MIXES",
                       genre="AlphaMix", exclusion_window=5)
    ref = slot_ref_from_block(week=2, slot_index=15, block=block)
    assert ref.extra == {"genre": "AlphaMix", "exclusion_window": 5}


def test_slot_ref_from_block_works_with_dicts():
    """flat_slots in the layout dict are dicts before parsing; the
    harness handles both."""
    block = {
        "strategy": "sequential",
        "source": "NIGHT",
        "cursor_id": "NIGHT_1",
    }
    ref = slot_ref_from_block(week=1, slot_index=0, block=block)
    assert ref.extra == {"cursor_id": "NIGHT_1"}


# ---------- assert_chain_equal -----------------------------------------


def test_assert_chain_equal_passes_when_all_weeks_match():
    chain_a = {1: ["A", "B"], 2: ["C", "D"]}
    chain_b = {1: ["A", "B"], 2: ["C", "D"]}
    refs = {1: _slot_refs(2, week=1), 2: _slot_refs(2, week=2)}
    assert_chain_equal(chain_a, chain_b, refs)


def test_assert_chain_equal_raises_on_week_set_mismatch():
    with pytest.raises(AssertionError, match="week-set mismatch"):
        assert_chain_equal(
            {1: ["A"], 2: ["B"]},
            {1: ["A"]},
            {1: _slot_refs(1, week=1), 2: _slot_refs(1, week=2)},
        )


def test_assert_chain_equal_reports_first_failing_week():
    """When weeks 2 AND 4 diverge, we want week 2's diagnostic — the
    root cause. Downstream weeks diverge BECAUSE of week 2's bug."""
    chain_a = {1: ["A"], 2: ["B"], 4: ["C"]}
    chain_b = {1: ["A"], 2: ["X"], 4: ["Y"]}  # both weeks wrong
    refs = {w: _slot_refs(1, week=w) for w in (1, 2, 4)}
    with pytest.raises(AssertionError) as exc_info:
        assert_chain_equal(chain_a, chain_b, refs)
    msg = str(exc_info.value)
    assert "week 2:" in msg
    assert "week 4" not in msg  # second failure NOT reported
