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
              "position", "fallback_genre", "size"):
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
            f"Usually a template-slot-count drift (the synthetic template "
            f"was edited without regenerating the fixture), or a strategy "
            f"raised mid-loop and the chain truncated. Check both."
        )

    div = find_first_divergence(expected_pids, actual_pids)
    if div is None:
        return  # all good

    raise AssertionError(
        f"{prefix}{render_divergence_context(expected_pids, actual_pids, slot_refs, div)}"
    )


def generate_chain(
    db: "Session",
    *,
    station_slug: str,
    weeks: Iterable[int],
    seed_base: int,
    advance_step: int = 120,
    reset_cycle: int = 0,
) -> dict[int, list[str]]:
    """Run a multi-week chain through the full API persistence flow.

    Returns week → list of persistent IDs. Mirrors
    ``api/routers/generate.py``'s call sequence EXACTLY — the
    byte-equality slice's correctness hinges on the test path
    matching the production path. Any deviation means a test passes
    while the API regresses.

    Threaded inputs (each maps to an API helper):

      * ``load_alias_map`` (§14.26) — artist alias normalisation in
        `weighted`. Live even when the DB has no aliases.
      * ``max_mix_exclusion_window`` (§14.32 review fix) — walks
        ``layout.all_blocks()``; works for row_template AND flat_slots.
        Hardcoding the window would bypass exactly the stale-helper
        bug class that's bitten this project repeatedly.
      * ``weighted_window_default=None`` — no station-rule seeded in
        the chain DB; engine falls back to its constant. The path
        documents the rule-precedence flow.
      * ``previous_playlist=load_previous_playlist(current_week=week)``
        — prev_ref reads the most-recent-prior; week 1 falls back
        unless the caller pre-seeded a prior playlist (the §14.37 c2
        real-XML test does this with WEEKLY 001).

    Cursor advance defaults (120 / 0) match weekly's rules. Override
    via ``advance_step`` / ``reset_cycle`` for tests using compressed
    pools (e.g. the §14.37 c1 synthetic test).

    Importing the SQLAlchemy + engine modules lazily keeps this
    helper importable without a DB context — useful for the harness
    tests that don't need a session.
    """
    from engine.generator import generate
    from engine.models import Station, Track
    from engine.persistence import (
        advance_cursors, load_alias_map, load_cursor_positions,
        load_excluded_mix_ids, load_pools, load_previous_playlist,
        max_mix_exclusion_window, save_playlist,
    )
    from engine.template import parse_template

    station = db.query(Station).filter_by(slug=station_slug).one()
    tmpl = parse_template(station.template)
    pools = load_pools(db, tmpl)
    alias_map = load_alias_map(db)
    # Explicit track_id → persistent_id resolution (defends against the
    # stale-Track-in-identity-map bug class noted in §14.26's review).
    track_pids = {t.id: t.persistent_id for t in db.query(Track).all()}

    pids_by_week: dict[int, list[str]] = {}
    for week in weeks:
        positions = load_cursor_positions(db, station_id=station.id, template=tmpl)
        excluded_mix_ids = load_excluded_mix_ids(
            db, station_id=station.id,
            window=max_mix_exclusion_window(tmpl),
        )
        previous_playlist = load_previous_playlist(
            db, station_id=station.id, current_week=week,
        )
        result = generate(
            tmpl, pools,
            week=week,
            seed=seed_base + week,
            excluded_mix_ids=excluded_mix_ids,
            alias_map=alias_map,
            previous_playlist=previous_playlist,
            weighted_window_default=None,
            named_cursor_positions=positions,
        )
        pids_by_week[week] = [track_pids[t.id] for t in result.picks]
        save_playlist(db, station=station, result=result, week=week, template=tmpl)
        advance_cursors(
            db, station_id=station.id, template=tmpl, pools=pools,
            advance_step=advance_step, reset_cycle=reset_cycle,
            generation_count=week - 1,
        )
        db.commit()

    return pids_by_week


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
