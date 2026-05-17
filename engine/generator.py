"""Playlist generator.

Consumes:
    * a Template (parsed)
    * source pools as a plain dict[str, list[Track-like]] — the engine is
      DB-agnostic; callers are responsible for loading pools from wherever.
    * a week number (1-indexed, monotonically increasing)

Emits:
    * a list of picked tracks in slot order
    * a Trace with per-slot rationale and a trace_id

Pure function. No DB, no I/O, no globals. That makes it trivially testable
with synthetic pools (see tests/test_classics_generation.py).

A Track-like is anything with attributes: id, name, artist. It can be a
SQLAlchemy Track model or a dataclass. The generator only reads; it does not
mutate.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Mapping

from engine.template import Template, BlockSpec
from engine.trace import Trace, TraceSlot
from engine.strategies import apply_window, apply_pick, apply_jingle


@dataclass
class GenerationResult:
    picks: list[Any]     # list of Track-like objects, one per slot
    trace: Trace


def generate(
    template: Template,
    pools: Mapping[str, list[Any]],
    *,
    week: int,
    seed: int | None = None,
) -> GenerationResult:
    """Generate one playlist for the given station + week.

    Parameters
    ----------
    template : Template
        Parsed station template.
    pools : dict[str, list[Track-like]]
        Source pools keyed by source name. Must include every 'content' source
        referenced by the layout.
    week : int
        1-indexed week number.
    seed : int | None
        Only used if template.rng == 'on'. Ignored otherwise.
    """
    _check_pools_present(template, pools)

    rng = None
    if template.rng == "on":
        if seed is None:
            raise ValueError("rng='on' template requires a seed")
        rng = random.Random(seed)

    base_cursor = template.base_cursor_for_week(week)

    trace = Trace(
        station=template.station,
        week=week,
        rng=template.rng,
        seed=seed if template.rng == "on" else None,
    )
    picks: list[Any] = []
    slot_counter = 0

    # Pool-addressing convention. `window` + `pick` use row_start_in_pool
    # (base cursor + per-row offset). `sequential` reads from a named
    # cursor (§14.22) and ignores the row offset; `jingle` filters by
    # genre tag (§14.24) and likewise ignores it. A template author who
    # mixes both styles within a row sees the offset apply only to
    # window/pick blocks — by design.
    for row_idx, row_offset in enumerate(template.layout.row_start_offsets):
        unit_label = template.layout.unit_labels[row_idx]
        row_start_in_pool = base_cursor + row_offset

        for block_idx, block in enumerate(template.layout.row_template):
            pool = pools[block.source]
            if block.strategy == "window":
                picked = apply_window(
                    pool,
                    start=row_start_in_pool,
                    size=block.size,
                    source_name=block.source,
                )
                reason_prefix = (
                    f"window size={block.size} from '{block.source}' "
                    f"at pool_index {row_start_in_pool + 1}"
                )
                for (pool_idx, track) in picked:
                    slot_counter += 1
                    picks.append(track)
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=pool_idx,
                        track=track,
                        reason=reason_prefix,
                    ))
            elif block.strategy == "sequential":
                # §14.22 Weekly-port: same algorithm as window, but the start
                # position comes from a NAMED cursor (declared in
                # layout.cursors_init) instead of the row-offset list.
                # row_start_offsets[row_idx] is ignored for sequential blocks —
                # the cursor is the addressing mechanism.
                seq_start = template.cursor_position(block.cursor_id, week)  # type: ignore[arg-type]
                picked = apply_window(
                    pool,
                    start=seq_start,
                    size=block.size,
                    source_name=block.source,
                )
                reason_prefix = (
                    f"sequential size={block.size} from '{block.source}' "
                    f"cursor='{block.cursor_id}' at pool_index {seq_start + 1}"
                )
                for (pool_idx, track) in picked:
                    slot_counter += 1
                    picks.append(track)
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=pool_idx,
                        track=track,
                        reason=reason_prefix,
                    ))
            elif block.strategy == "jingle":
                # §14.24 Weekly-port: genre-filtered pick from the pool.
                # Pool position is determined by which entries carry the
                # requested genre tag, not by the row/cursor cursor —
                # row_start_offsets[row_idx] is ignored. block.size loops
                # in case a template ever wants N back-to-back jingles
                # from the same genre, but size=1 is the typical case.
                for _ in range(block.size):
                    pool_idx, track = apply_jingle(
                        pool,
                        genre=block.genre,  # type: ignore[arg-type]
                        rng=rng,
                        source_name=block.source,
                    )
                    slot_counter += 1
                    picks.append(track)
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=pool_idx,
                        track=track,
                        reason=f"jingle from '{block.source}' "
                               f"genre='{block.genre}' "
                               f"({'random' if rng else 'first match'})",
                    ))
            elif block.strategy == "pick":
                cursor = row_start_in_pool
                for _ in range(block.size):
                    pool_idx, track, cursor = apply_pick(
                        pool, cursor=cursor, rng=rng, source_name=block.source
                    )
                    slot_counter += 1
                    picks.append(track)
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=pool_idx,
                        track=track,
                        reason=f"pick from '{block.source}' "
                               f"({'random' if rng else 'sequential'})",
                    ))
            else:
                # template.parse_template should have rejected this already
                raise RuntimeError(f"unreachable: unknown strategy {block.strategy!r}")

    return GenerationResult(picks=picks, trace=trace)


# ---------- internals ----------------------------------------------------


def _check_pools_present(template: Template, pools: Mapping[str, list[Any]]) -> None:
    required = set()
    for b in template.layout.row_template:
        required.add(b.source)
    missing = required - set(pools.keys())
    if missing:
        raise KeyError(
            f"pools missing for content sources: {sorted(missing)}. "
            f"Provided: {sorted(pools.keys())}"
        )
    for name in required:
        if not pools[name]:
            raise ValueError(f"pool '{name}' is empty")


def _make_trace_slot(
    *,
    slot: int,
    unit: str,
    row: int,
    block: int,
    block_spec: BlockSpec,
    pool_index: int,
    track: Any,
    reason: str,
    excluded: list | None = None,
) -> TraceSlot:
    return TraceSlot(
        slot=slot,
        unit=unit,
        row=row,
        block=block,
        source=block_spec.source,
        strategy=block_spec.strategy,
        pool_index=pool_index,
        track_id=getattr(track, "id", None),
        track_name=getattr(track, "name", "?"),
        track_artist=getattr(track, "artist", None),
        reason=reason,
        excluded=excluded or [],
    )
