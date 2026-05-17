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
from dataclasses import dataclass, field
from typing import Any, Mapping

from engine.template import Template, BlockSpec
from engine.trace import Trace, TraceSlot
from engine.strategies import (
    apply_window, apply_pick, apply_jingle, apply_mix,
    apply_weighted, build_lookups, WeightedLookups,
)


# Default artist-dedup window for the `weighted` strategy when a block
# doesn't set its own `window`. Matches weekly's Rule.artist_dedup_window
# default (10). Sub-slice #13's Rule table will eventually let a station
# override this; until then, the constant is the source of truth.
DEFAULT_WEIGHTED_WINDOW = 10


@dataclass
class GenerationResult:
    picks: list[Any]     # list of Track-like objects, one per slot
    trace: Trace


@dataclass
class _GenState:
    """Mutable cross-slot state for one generation pass (§14.27).

    `weighted` slots both READ this (to apply track + artist dedup) and
    WRITE this (to record the pick for subsequent slots in the same
    pass). Other strategies (window/pick/sequential/jingle/mix) neither
    read nor write — their decisions are independent of prior picks
    within the same generation, so they leave the state untouched.

    Lives on the stack inside generate(); resets to empty for every
    call. Weekly's behaviour: dedup state does NOT persist across
    playlist generations. Cross-week mix exclusion is its own thing
    (MixUsage table, §14.25).
    """
    recent_artist_keys: list[frozenset[str]] = field(default_factory=list)
    used_track_ids: list[int] = field(default_factory=list)


def generate(
    template: Template,
    pools: Mapping[str, list[Any]],
    *,
    week: int,
    seed: int | None = None,
    excluded_mix_ids: set[int] | frozenset[int] | None = None,
    alias_map: dict[str, str] | None = None,
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
    excluded_mix_ids : set[int] | None
        Track ids to exclude from `mix` strategy candidates (§14.25).
        Populated by the persistence layer from the station's recent
        MixUsage rows (window = block.exclusion_window). None = empty set;
        templates with no mix blocks never read this anyway.
    alias_map : dict[str, str] | None
        Artist alias lookup ({lowercase_alias: lowercase_canonical})
        from `engine.persistence.load_alias_map` (§14.26). Only the
        `weighted` strategy (§14.27) reads it, for resolving artist
        keys before applying the dedup window. None = empty dict;
        templates with no weighted blocks never read this anyway.
    """
    _check_pools_present(template, pools)

    rng = None
    if template.rng == "on":
        if seed is None:
            raise ValueError("rng='on' template requires a seed")
        rng = random.Random(seed)

    # Normalise None → frozenset so the mix branch can pass it as-is to
    # apply_mix without a per-call truthy check. Frozen because the
    # generator never writes to it (the strategy reads only).
    excluded_mix_ids = frozenset(excluded_mix_ids or ())
    alias_map = alias_map or {}

    # Cross-slot state for the weighted strategy (§14.27). Reset per
    # generation; see _GenState's docstring for the rationale.
    state = _GenState()

    # Lazy per-pool lookups for weighted (§14.27). Built on first
    # weighted block encounter for that pool; reused for every
    # subsequent slot from the same pool in this generation.
    weighted_lookups: dict[str, WeightedLookups] = {}

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
            elif block.strategy == "mix":
                # §14.25 Weekly-port: genre-filtered random pick with cross-
                # week exclusion. Caller pre-builds excluded_mix_ids from
                # MixUsage of the station's last block.exclusion_window
                # playlists. apply_mix relaxes the exclusion if it would
                # drain the candidate set; we surface that in the trace
                # reason so authoring can spot exclusion-window saturation.
                for _ in range(block.size):
                    pool_idx, track, relaxed, n_cand, n_elig = apply_mix(
                        pool,
                        genre=block.genre,  # type: ignore[arg-type]
                        excluded_ids=excluded_mix_ids,
                        rng=rng,
                        source_name=block.source,
                    )
                    slot_counter += 1
                    picks.append(track)
                    mode = "random" if rng else "first match"
                    # Report genre-specific filtering pressure rather than the
                    # opaque total exclusion-set size. `eligible N of M` is
                    # the actionable number — when N approaches 1 the user
                    # knows to bump exclusion_window or add tracks.
                    excl_note = (
                        " (relaxed — exclusion saturated genre)"
                        if relaxed else ""
                    )
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=pool_idx,
                        track=track,
                        reason=f"mix from '{block.source}' "
                               f"genre='{block.genre}' "
                               f"eligible {n_elig} of {n_cand} ({mode}){excl_note}",
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
            elif block.strategy == "weighted":
                # §14.27 Weekly-port: two-tier dedup (track shuffle-bag +
                # artist sliding window), squared-remaining weighting.
                # State (recent_artist_keys, used_track_ids) is shared
                # across ALL weighted picks in this generation; READ by
                # apply_weighted, WRITTEN here after each successful pick.
                if block.source not in weighted_lookups:
                    weighted_lookups[block.source] = build_lookups(
                        pool, alias_map=alias_map,
                    )
                effective_window = (
                    block.window if block.window is not None
                    else DEFAULT_WEIGHTED_WINDOW
                )
                for _ in range(block.size):
                    track, info = apply_weighted(
                        pool,
                        lookups=weighted_lookups[block.source],
                        recent_artist_keys=state.recent_artist_keys,
                        used_track_ids=state.used_track_ids,
                        window=effective_window,
                        rng=rng,
                        source_name=block.source,
                    )
                    # Mutate state AFTER the pick (the strategy only reads).
                    state.recent_artist_keys.append(
                        weighted_lookups[block.source].track_keys.get(
                            getattr(track, "id", -1), frozenset()
                        )
                    )
                    state.used_track_ids.append(getattr(track, "id", -1))
                    slot_counter += 1
                    picks.append(track)
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=info.pool_index,
                        track=track,
                        reason=_weighted_reason(info, effective_window),
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


def _weighted_reason(info, window: int) -> str:
    """Compose the trace reason string for a weighted pick.

    Captures the tier + the filtering pressure (how many tracks each
    constraint blocked). When sub-slice #11 follow-on ships, the
    per-candidate ExcludedCandidate entries land alongside this
    summary — the reason stays the at-a-glance overview.
    """
    if info.tier == 1:
        return (
            f"weighted tier 1: eligible {info.eligible_count}, "
            f"blocked artist={info.artist_blocked_count} "
            f"track={info.track_blocked_count} "
            f"both={info.both_blocked_count} "
            f"(window={window})"
        )
    return (
        f"weighted tier 2 (artist constraint relaxed): "
        f"eligible {info.eligible_count}, "
        f"blocked artist={info.artist_blocked_count} "
        f"track={info.track_blocked_count} "
        f"both={info.both_blocked_count} "
        f"(window={window})"
    )
