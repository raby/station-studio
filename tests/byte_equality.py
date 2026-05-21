"""Diagnostic comparison helpers for sub-slice #19's byte-equality
harness (§14.37).

A byte-equality test compares "expected" and "actual" playlists —
each a list of persistent IDs per slot, optionally with surrounding
context for diagnostics. The helpers here:

  * Render a clear failure message that pinpoints the FIRST diverging
    slot (typically the only useful one — once two RNG-driven
    sequences diverge, every downstream pick is unrelated).
  * Include ±N slots of context so the failure shows what the test
    was DOING when the divergence happened (slot type, source,
    strategy params).
  * Optionally compare across multiple weeks (chain test) — surfaces
    the first divergent (week, slot) coordinate.

Design notes
------------

* Comparison surface is PERSISTENT ID, not Track.id or pool position.
  Persistent ID is the cross-pool, cross-import-run, cross-station
  invariant; it's what weekly stores in its WeeklyPlaylistEntry rows
  and what station-studio stores in PlaylistEntry. Comparing by PID
  means a re-imported pool with reshuffled track IDs still produces
  the same expected fixture.

* The harness is pytest-agnostic — it raises `AssertionError` with a
  full message rather than using `pytest.fail` so it can be reused
  from a CLI ("did weekly's dump match station-studio's output?")
  or a manual REPL invocation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class SlotRef:
    """A slot reference used in failure messages.

    Holds just enough to make the failure narratively useful: which
    week, which slot index, what strategy + source + per-slot params
    were in play. Filled in from `BlockSpec` or its dict equivalent.
    """
    week: int
    slot_index: int  # 0-based
    strategy: str
    source: str
    extra: dict[str, Any] = field(default_factory=dict)

    def render(self, prefix: str = "") -> str:
        bits = [f"week={self.week}", f"slot={self.slot_index + 1}",
                f"strategy={self.strategy}", f"source={self.source}"]
        if self.extra:
            bits.append(f"params={self.extra!r}")
        return f"{prefix}{' '.join(bits)}"


def slot_ref_from_block(week: int, slot_index: int, block) -> SlotRef:
    """Convert a `BlockSpec` (or dict) into a SlotRef for diagnostics.

    Captures the per-slot fields that distinguish similar slots:
    genre for jingle/mix, cursor_id for sequential, position for
    prev_ref. The intent is failure messages like:

        week=2 slot=37 strategy=mix source=MIXES
            params={'genre': 'BetaMix', 'exclusion_window': 5}

    so the reader immediately knows what slot they're looking at.
    """
    extra: dict[str, Any] = {}
    for k in ("genre", "exclusion_window", "cursor_id",
              "position", "fallback_genre"):
        v = getattr(block, k, None) if not isinstance(block, dict) else block.get(k)
        if v is not None:
            extra[k] = v
    return SlotRef(
        week=week,
        slot_index=slot_index,
        strategy=getattr(block, "strategy", block.get("strategy") if isinstance(block, dict) else "?"),
        source=getattr(block, "source", block.get("source") if isinstance(block, dict) else "?"),
        extra=extra,
    )


def find_first_divergence(
    expected_pids: Sequence[str],
    actual_pids: Sequence[str],
) -> int | None:
    """Return the 0-indexed position of the first PID mismatch, or
    None if the sequences are identical up to the shared length.

    Length mismatch is handled separately by the caller — the
    diagnostic should distinguish "diverged at slot N" from
    "expected 120 slots, actual produced 119".
    """
    for i, (e, a) in enumerate(zip(expected_pids, actual_pids)):
        if e != a:
            return i
    return None


def render_divergence_context(
    expected_pids: Sequence[str],
    actual_pids: Sequence[str],
    slot_refs: Sequence[SlotRef],
    divergence_idx: int,
    context_radius: int = 3,
) -> str:
    """Build a multi-line failure message centred on the divergence.

    Shows N slots before + the divergence + N slots after, in a
    table-shaped layout. Used by `assert_pids_equal` and surfaced
    in the multi-week test's assertion failure.

    `slot_refs` parallels `expected_pids` / `actual_pids`. All three
    must have the same length and be in the same (week, slot) order;
    callers building a multi-week chain flatten the per-week outputs
    into one big sequence and pass parallel slot_refs.
    """
    start = max(0, divergence_idx - context_radius)
    end = min(len(expected_pids), divergence_idx + context_radius + 1)

    lines = [
        f"first divergence at index {divergence_idx} "
        f"({slot_refs[divergence_idx].render()})",
        "",
        f"{'idx':>5}  {'expected PID':<24}  {'actual PID':<24}  slot",
        f"{'-' * 5}  {'-' * 24}  {'-' * 24}  {'-' * 40}",
    ]
    for i in range(start, end):
        marker = " <-- DIFF" if i == divergence_idx else ""
        exp = expected_pids[i] if i < len(expected_pids) else "(missing)"
        act = actual_pids[i] if i < len(actual_pids) else "(missing)"
        lines.append(
            f"{i:>5}  {exp:<24}  {act:<24}  {slot_refs[i].render()}{marker}"
        )
    return "\n".join(lines)


def assert_pids_equal(
    expected_pids: Sequence[str],
    actual_pids: Sequence[str],
    slot_refs: Sequence[SlotRef],
    *,
    label: str = "",
) -> None:
    """Compare two PID sequences. Raise AssertionError with a
    diagnostic message on mismatch.

    `slot_refs` must be the same length as the LONGER of the two
    sequences (so length-mismatch messages can still cite the slot
    type at the truncation point).

    `label` is prepended to the failure message — useful when this
    assertion runs inside a multi-week chain to identify which
    week failed.
    """
    prefix = f"{label}: " if label else ""

    if len(expected_pids) != len(actual_pids):
        raise AssertionError(
            f"{prefix}sequence-length mismatch: "
            f"expected {len(expected_pids)} pids, got {len(actual_pids)}. "
            f"This is usually a template-slot-count drift, not an "
            f"engine bug — check the synthetic template hasn't been "
            f"edited without regenerating the fixture."
        )

    div = find_first_divergence(expected_pids, actual_pids)
    if div is None:
        return  # all good

    raise AssertionError(
        f"{prefix}{render_divergence_context(expected_pids, actual_pids, slot_refs, div)}"
    )


def assert_chain_equal(
    expected_chain: dict[int, list[str]],
    actual_chain: dict[int, list[str]],
    slot_refs_by_week: dict[int, list[SlotRef]],
) -> None:
    """Assert byte-equality across a multi-week chain.

    `expected_chain` and `actual_chain` map week → list of PIDs.
    `slot_refs_by_week` parallels the chain — week → list of SlotRef.
    Compares weeks in sorted order; raises on the FIRST mismatching
    week with the per-slot diagnostic from
    `render_divergence_context`. Useful when downstream weeks diverge
    too but the root cause is the first one.
    """
    assert set(expected_chain.keys()) == set(actual_chain.keys()), (
        f"week-set mismatch: expected weeks {sorted(expected_chain)}, "
        f"got {sorted(actual_chain)}"
    )
    for week in sorted(expected_chain.keys()):
        assert_pids_equal(
            expected_chain[week],
            actual_chain[week],
            slot_refs_by_week[week],
            label=f"week {week}",
        )
