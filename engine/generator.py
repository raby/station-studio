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
from typing import Any, Iterator, Mapping

from engine.template import Template, BlockSpec
from engine.trace import Trace, TraceSlot, ExcludedCandidate
from engine.strategies import (
    apply_window, apply_pick, apply_jingle, apply_mix,
    apply_weighted, build_lookups, WeightedLookups,
    try_prev_ref_mirror, PrevRefPositionOutOfRangeError,
)


# Engine-level fallback for the `weighted` strategy's artist-dedup
# window when neither a per-block BlockSpec.window nor a station-level
# `artist_dedup_window` rule is set. Matches weekly's
# Rule.artist_dedup_window default (10). §14.29 added the rule layer
# between BlockSpec and this constant; the constant stays as the
# last-resort fallback so the engine remains DB-agnostic.
DEFAULT_WEIGHTED_WINDOW = 10


@dataclass
class GenerationResult:
    picks: list[Any]     # list of Track-like objects, one per slot
    trace: Trace
    # §14.28 — slots whose prev_ref dispatch fell back to a mix-style
    # pick (rather than mirroring an existing previous-playlist track).
    # Each entry: (1-indexed slot number, fallback_genre, picked track).
    # Persistence reads this to record MixUsage rows for the fallback
    # picks; the trace's "strategy" field can't distinguish mirror from
    # fallback-mix because both still record strategy='prev_ref'.
    prev_ref_fallback_mixes: list[tuple[int, str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ProgressEvent:
    """One progress signal yielded by `generate_stream` (§16.11).

    Consumers (job runners, CLI status, tests) treat the stream as
    `Iterator[ProgressEvent | GenerationResult]` and react to either.
    The Decision G contract from DESIGN §16.11:

      * `generate_stream` yields ProgressEvent objects + exactly ONE
        GenerationResult at the end. Multi-Result streams are an
        engine bug.
      * Mid-stream exceptions propagate unchanged (the engine doesn't
        try/except its own internals); the wrapper / job-runner
        catches and surfaces them.

    Fields:

      * `phase` — what the engine is doing. The CURRENT v1 vocabulary
        (slice review 🟢-6 — pinned here so frontends + future
        engine work don't drift):

          - "queued" — runner-emitted initial state; the worker thread
            has not yet pulled the first event from the engine.
            JobStore sets this when the _Job is constructed.
          - "start"  — engine's first yield, emitted BEFORE the slot
            loop runs. current=0, total=total_slots. Frontends treat
            as "indeterminate progress, show generic spinner".
          - "picks"  — emitted from inside the slot loop at the
            `_PROGRESS_EMIT_EVERY` cadence. current advances toward
            total. Frontends render the determinate progress bar.

        Implicit "done" is signalled by the GenerationResult yield
        terminating the stream (no phase value needed). Future phases
        ("load-pools", "resolve-prev-ref", or §16.2.6 preview's
        "generate-slots") can be added — frontends ignore unknown
        phases via the `ProgressIndicator`'s indeterminate fallback.
      * `current`, `total` — work-units within the phase.
        1-indexed for human messages; consumers compute progress
        as `current / total`.
      * `label` — optional human string. If absent, callers build
        from `f"{phase}: {current}/{total}"`.
    """
    phase: str
    current: int
    total: int
    label: str | None = None


# §16.11 c1: progress emission cadence inside the slot loop. The loop
# fires ONE event per block iteration where the cumulative pick counter
# crosses a multiple of N (modulo-crossing check) — not literally one
# per N picks. For all real station templates today block.size ≤ N so
# the cadence approximates "every N picks" closely (~44 events for
# Rebirth Radio's 2,196 picks, ~60 for Classics' 3,010). A station
# with chunky blocks (size > N) would emit fewer events per N picks
# but still ≥1 per block — acceptable for v1's smooth-progress goal.
_PROGRESS_EMIT_EVERY = 50


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
    previous_playlist: list[Any] | None = None,
    weighted_window_default: int | None = None,
    named_cursor_positions: dict[str, int] | None = None,
    excluded_track_pids: frozenset[str] | set[str] | None = None,
) -> GenerationResult:
    """Generate one playlist for the given station + week.

    Thin wrapper around `generate_stream`: drains the event stream
    and returns the final `GenerationResult`. Existing callers
    (tests, dump scripts, the synchronous API endpoint) use this
    signature unchanged.

    Decision G invariants (DESIGN.md §16.11) are enforced here:

      * Exactly ONE `GenerationResult` yielded by the stream,
        emitted LAST. Multi-Result or no-Result streams raise
        AssertionError (engine bug, not user error).
      * Mid-stream exceptions from `generate_stream` propagate
        unchanged — existing callers' exception expectations
        preserved (a strategy's `ValueError` still surfaces as
        `ValueError` to the caller).
    """
    final: GenerationResult | None = None
    for event in generate_stream(
        template, pools,
        week=week, seed=seed,
        excluded_mix_ids=excluded_mix_ids,
        alias_map=alias_map,
        previous_playlist=previous_playlist,
        weighted_window_default=weighted_window_default,
        named_cursor_positions=named_cursor_positions,
        excluded_track_pids=excluded_track_pids,
    ):
        if isinstance(event, GenerationResult):
            assert final is None, (
                "engine bug: generate_stream yielded multiple "
                "GenerationResult objects"
            )
            final = event
        else:
            # Decision G: NO events after the GenerationResult. Today
            # the generator's body has nothing past `yield Result`, so
            # this can't fire from the engine — but if a future change
            # adds a post-Result yield (cleanup phase? metrics?) this
            # catches it before the job runner / frontend sees malformed
            # streams. Progress events BEFORE the Result are ignored
            # by the wrapper; stream-direct consumers (§16.11 job
            # runner) handle them.
            assert final is None, (
                "engine bug: generate_stream yielded an event after "
                f"the GenerationResult: {event!r}"
            )
    assert final is not None, (
        "engine bug: generate_stream yielded no GenerationResult"
    )
    return final


def generate_stream(
    template: Template,
    pools: Mapping[str, list[Any]],
    *,
    week: int,
    seed: int | None = None,
    excluded_mix_ids: set[int] | frozenset[int] | None = None,
    alias_map: dict[str, str] | None = None,
    previous_playlist: list[Any] | None = None,
    weighted_window_default: int | None = None,
    named_cursor_positions: dict[str, int] | None = None,
    excluded_track_pids: frozenset[str] | set[str] | None = None,
) -> Iterator["ProgressEvent | GenerationResult"]:
    """Generate one playlist as a STREAM of progress events + a
    final result (§16.11 Decision B).

    Generator function. Yields zero or more `ProgressEvent` objects
    during work + exactly ONE `GenerationResult` at the end.
    Consumers that want progress (job runner, future preview
    endpoint) drive this directly; consumers that just want the
    result (most existing callers) use `generate(...)` which is a
    thin wrapper that drains this stream.

    Progress emission cadence (§16.11 c1): one event per
    `_PROGRESS_EMIT_EVERY` picks during the main slot loop, plus
    a `phase="start"` event before the loop. For Rebirth Radio's
    2,196 picks that's ~44 events; for Classics' 3,010 picks ~60.
    Sub-millisecond engine overhead per event; meaningful UI
    granularity for the job runner's progress bar.

    Decision G invariants (DESIGN.md §16.11) — pinned for the
    wrapper + job runner to rely on:

      * Yields exactly ONE GenerationResult, LAST. Once the
        Result is yielded, the generator is exhausted.
      * Mid-stream exceptions propagate unchanged (engine doesn't
        try/except its own internals).
      * No events after the Result. The wrapper asserts this on
        consumption.

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
    previous_playlist : list[Track-like] | None
        Ordered tracks of the most recent prior playlist for this
        station (§14.28). Only the `prev_ref` strategy reads it;
        templates without prev_ref blocks ignore it. None = no
        previous playlist exists (first generation for this
        station) — prev_ref then takes its fallback path.
    weighted_window_default : int | None
        Station-level default for the weighted strategy's artist-
        dedup window (§14.29). When a `weighted` block doesn't set
        its own `BlockSpec.window`, this value is used; falls back
        to the engine constant DEFAULT_WEIGHTED_WINDOW when None.
        Populated by the API layer from the `artist_dedup_window`
        rule.
    named_cursor_positions : dict[str, int] | None
        Pre-loaded positions for `sequential` named cursors (§14.30).
        ONLY consulted when `template.cursor.persist` is True; for
        stateless templates the generator computes positions directly
        from `cursors_init + week`, matching §14.22. The API layer
        populates this from the `cursor_state` table via
        `engine.persistence.load_cursor_positions` BEFORE generation;
        the API also calls `engine.persistence.advance_cursors` AFTER
        save_playlist to mutate the table for the next generation.
        Engine stays DB-agnostic — for tests without a DB, pass the
        dict directly.
    """
    _check_pools_present(template, pools)

    rng = None
    if template.rng == "on":
        if seed is None:
            raise ValueError("rng='on' template requires a seed")
        rng = random.Random(seed)

    # Mutable set so within-generation mix picks ACCUMULATE — matches
    # weekly's behaviour (weekly mutates excluded_mix_ids in-place after
    # each mix and prev_ref-fallback-mix pick; see weekly's generator.py
    # lines 555 and 590). Two mix slots same genre in one generation
    # pick DIFFERENT tracks because slot 2 sees slot 1's pick in the
    # exclusion set. The strategy itself (apply_mix) reads but doesn't
    # mutate; the generator mutates after each successful pick.
    #
    # Prior §14.25 design claimed "purely cross-week" — that was wrong
    # about weekly's behaviour. Surfaced during §14.28 mid-slice review.
    excluded_mix_ids = set(excluded_mix_ids or ())
    alias_map = alias_map or {}

    # Cross-slot state for the weighted strategy (§14.27). Reset per
    # generation; see _GenState's docstring for the rationale.
    state = _GenState()

    # Lazy per-pool lookups for weighted (§14.27). Built on first
    # weighted block encounter for that pool; reused for every
    # subsequent slot from the same pool in this generation. Caching
    # by source name is safe because `alias_map` is immutable per
    # generation (no per-block alias overrides exist); if that ever
    # changes, the cache key needs to grow an alias_map dimension.
    weighted_lookups: dict[str, WeightedLookups] = {}

    # §14.28 — prev_ref slots that fell back to a mix-style pick get
    # appended here. Persistence reads it post-generation to record
    # MixUsage rows (mirrors don't enter MixUsage; only fallback picks
    # do, matching weekly). Each entry: (slot_counter, fallback_genre,
    # picked track).
    prev_ref_fallback_mixes: list[tuple[int, str, Any]] = []

    # §14.39 — per-cursor within-generation offset accumulator.
    # Sequential blocks sharing a cursor_id advance through the pool
    # consecutively across the WHOLE generation, not just within one
    # contiguous block. This is the architectural primitive that
    # replaces §14.37's `_coalesce_sequential_runs` translator patch:
    # any template (including those with non-contiguous same-cursor
    # blocks interleaved with other strategies) reads disjoint pool
    # slices for each block. RESET per generate() call; cross-
    # generation cursor advance is the separate `cursor_advance_step`
    # rule-driven mechanism (see engine.persistence.advance_cursors).
    seq_offsets: dict[str, int] = {}

    base_cursor = template.base_cursor_for_week(week)

    trace = Trace(
        station=template.station,
        week=week,
        rng=template.rng,
        seed=seed if template.rng == "on" else None,
    )
    picks: list[Any] = []
    slot_counter = 0

    # §16.11 c1 — progress emission. Compute total slot count up-front
    # (template.slots_per_week is XOR-aware for flat_slots/row_template).
    # Emit a `phase="start"` event before the loop so consumers can
    # show "Generating week N (0 / total)" immediately on response.
    total_slots = template.slots_per_week()
    yield ProgressEvent(
        phase="start", current=0, total=total_slots,
        label=f"Generating week {week}",
    )

    # Pool-addressing convention. `window` + `pick` use row_start_in_pool
    # (base cursor + per-row offset). `sequential` reads from a named
    # cursor (§14.22) and ignores the row offset; `jingle` filters by
    # genre tag (§14.24) and likewise ignores it. A template author who
    # mixes both styles within a row sees the offset apply only to
    # window/pick blocks — by design.
    #
    # §14.32 — the iteration helper below yields tuples in BOTH layout
    # forms (row_template vs flat_slots), so the dispatch body stays a
    # single loop. For flat_slots: row_idx=0 (→ trace.row = 1), unit
    # label = "", no row offset (row_start_in_pool = base_cursor; the
    # parser forbids window/pick in flat_slots so this value is
    # untouched in the dispatch branches flat templates actually use).
    for row_idx, unit_label, row_start_in_pool, block_idx, block in (
        _iterate_slots(template, base_cursor)
    ):
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
            #
            # §14.30 — when template.cursor.persist is True and the
            # caller passed `named_cursor_positions`, use the loaded
            # value (which the API got from cursor_state). Otherwise
            # fall back to the §14.22 stateless computation.
            if template.cursor.persist and named_cursor_positions is not None:
                seq_start = named_cursor_positions[block.cursor_id]  # type: ignore[index]
            else:
                seq_start = template.cursor_position(block.cursor_id, week)  # type: ignore[arg-type]
            # §14.39: within-generation offset accumulator (per cursor)
            # advances through the pool across NON-CONTIGUOUS blocks
            # sharing a cursor. The §14.37 c2 coalescing patch worked
            # only because weekly happens to use each cursor in one
            # contiguous run; this primitive generalises to any
            # template shape (e.g. NIGHT_1, COMMERCIAL, NIGHT_1, ...).
            # offset starts at 0 for each cursor + each generation.
            #
            # §14.37 c2 wrap-around (modular) preserved: sequential
            # WRAPS at pool boundary (unlike `window`/`pick` which
            # fail-loud). Mirrors weekly's `_resolve_sequential_block`
            # `(cursor_pos + i) % n`.
            pool_len = len(pool)
            if pool_len == 0:
                raise WindowOutOfRangeError(
                    f"source '{block.source}': pool is empty"
                )
            offset = seq_offsets.get(block.cursor_id, 0)
            picked = [
                (
                    (seq_start + offset + i) % pool_len + 1,
                    pool[(seq_start + offset + i) % pool_len],
                )
                for i in range(block.size)
            ]
            seq_offsets[block.cursor_id] = offset + block.size
            reason_prefix = (
                f"sequential size={block.size} from '{block.source}' "
                f"cursor='{block.cursor_id}' at pool_index "
                f"{(seq_start + offset) % pool_len + 1}"
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
                # Accumulate within-generation: subsequent mix +
                # prev_ref-fallback-mix slots see this pick excluded.
                # Matches weekly's `excluded_mix_ids.add(track.id)`
                # after every successful mix pick.
                picked_id = getattr(track, "id", None)
                if picked_id is not None:
                    excluded_mix_ids.add(picked_id)
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
            # §14.29 precedence: per-block field → station rule
            # (via weighted_window_default kwarg) → engine constant.
            effective_window = _resolve_weighted_window(
                block_window=block.window,
                station_default=weighted_window_default,
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
                    # §16.14.t5: threaded from generate_stream's kwarg.
                    excluded_track_pids=excluded_track_pids,
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
                    # §14.23 ExcludedCandidate population for weighted
                    # (commit 2 of sub-slice #11). Only artist-blocked
                    # rejections are recorded; the shuffle-bag-only
                    # rejections are mechanical noise.
                    excluded=info.excluded_candidates,
                ))
        elif block.strategy == "prev_ref":
            # §14.28 — three outcomes per slot:
            #   MIRROR: previous playlist had a track at the position
            #     AND it's in this slot's source pool → use it.
            #   FALLBACK_MIX: mirror missed AND block.fallback_genre
            #     is set → apply_mix from this source filtered to
            #     fallback_genre. Records into prev_ref_fallback_mixes
            #     so persistence can write a MixUsage row.
            #   FALLBACK_WEIGHTED: mirror missed AND no fallback_genre
            #     → defer to weighted strategy. Defensive — weekly's
            #     actual templates never trigger this (every real
            #     prev_ref has a fallback_genre) but the port keeps
            #     the branch for byte-equality.
            #
            # On fallback (either kind), the failed-mirror context is
            # surfaced via a single §14.23 ExcludedCandidate entry
            # describing what the previous-playlist resolution turned
            # up. For FALLBACK_WEIGHTED the slot's `excluded` list
            # combines that one entry with whatever
            # apply_weighted populated (artist-blocked rejections from
            # the dedup window — see §14.27 commit 2).
            for _ in range(block.size):
                try:
                    mirror, failure = try_prev_ref_mirror(
                        previous_playlist,
                        position=block.position,  # type: ignore[arg-type]
                        source_pool=pool,
                    )
                except PrevRefPositionOutOfRangeError as e:
                    raise ValueError(
                        f"template error in prev_ref slot "
                        f"(row {row_idx + 1}, block {block_idx + 1}): {e}"
                    )

                if mirror is not None:
                    # MIRROR outcome.
                    # Pool position lookup matches weekly's expectation:
                    # the slot reports where the track is in THIS slot's
                    # source pool, not the previous-playlist position
                    # (that's in the trace reason).
                    #
                    # The `next()` is guaranteed to find a match here:
                    # `try_prev_ref_mirror` only returns a non-None
                    # mirror when `mirror.id` was confirmed present in
                    # `source_pool` (it builds the id-set from
                    # source_pool, then checks membership before
                    # returning). If a future refactor breaks that
                    # invariant — e.g. by passing a different pool
                    # object to the lookup vs the strategy — this
                    # would raise StopIteration; the strategy's
                    # docstring is the canonical contract.
                    pool_idx = next(
                        i + 1 for i, t in enumerate(pool)
                        if getattr(t, "id", None) == getattr(mirror, "id", None)
                    )
                    slot_counter += 1
                    picks.append(mirror)
                    trace.slots.append(_make_trace_slot(
                        slot=slot_counter,
                        unit=unit_label,
                        row=row_idx + 1,
                        block=block_idx + 1,
                        block_spec=block,
                        pool_index=pool_idx,
                        track=mirror,
                        reason=(
                            f"prev_ref mirror from previous playlist "
                            f"position {block.position}"
                        ),
                    ))
                    continue

                # Fallback path — build the failed-mirror diagnostic
                # entry once; both fallback branches surface it in
                # the slot's `excluded` list.
                failure_excluded = _prev_ref_failure_excluded(
                    failure, position=block.position, source_name=block.source,
                )
                miss_note = (
                    "no previous playlist"
                    if failure and failure.kind == "no_previous"
                    else f"resolved track not in source '{block.source}'"
                )

                if block.fallback_genre is not None:
                    # FALLBACK_MIX outcome — invoke apply_mix on the
                    # same source filtered to fallback_genre. Reuses
                    # the cross-week excluded_mix_ids set the
                    # generator already threads through.
                    pool_idx, track, relaxed, n_cand, n_elig = apply_mix(
                        pool,
                        genre=block.fallback_genre,
                        excluded_ids=excluded_mix_ids,
                        rng=rng,
                        source_name=block.source,
                    )
                    # Within-generation accumulation: prev_ref-fallback-
                    # mix picks count toward subsequent slots' exclusion,
                    # matching weekly's behaviour (line 590 in weekly's
                    # generator). Mirrors do NOT accumulate (no apply_mix
                    # call, no MixUsage row).
                    picked_id = getattr(track, "id", None)
                    if picked_id is not None:
                        excluded_mix_ids.add(picked_id)
                    slot_counter += 1
                    picks.append(track)
                    prev_ref_fallback_mixes.append(
                        (slot_counter, block.fallback_genre, track)
                    )
                    excl_note = (
                        " (mix relaxed — exclusion saturated genre)"
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
                        reason=(
                            f"prev_ref fallback (position {block.position}: "
                            f"{miss_note}); picked mix of genre "
                            f"'{block.fallback_genre}' eligible {n_elig} "
                            f"of {n_cand}{excl_note}"
                        ),
                        excluded=failure_excluded,
                    ))
                else:
                    # FALLBACK_WEIGHTED outcome (§14.28 commit 2).
                    # Defer to the weighted strategy using the SAME
                    # state machinery a regular weighted dispatch
                    # would: per-source lookup cache, _GenState
                    # (recent_artist_keys + used_track_ids),
                    # DEFAULT_WEIGHTED_WINDOW. Weekly's templates
                    # don't trigger this path, but the port keeps
                    # the branch implemented for byte-equality.
                    if block.source not in weighted_lookups:
                        weighted_lookups[block.source] = build_lookups(
                            pool, alias_map=alias_map,
                        )
                    # §14.29 — same precedence as the regular
                    # weighted dispatch: station rule (passed via
                    # weighted_window_default) overrides the engine
                    # constant. The prev_ref block has no `window`
                    # field of its own (window is reserved for
                    # weighted blocks only — see template parser
                    # coupling) so we skip the per-block level.
                    fallback_window = _resolve_weighted_window(
                        block_window=None,
                        station_default=weighted_window_default,
                    )
                    track, info = apply_weighted(
                        pool,
                        lookups=weighted_lookups[block.source],
                        recent_artist_keys=state.recent_artist_keys,
                        used_track_ids=state.used_track_ids,
                        window=fallback_window,
                        rng=rng,
                        source_name=block.source,
                        # §16.14.t5: same exclusion applies on the
                        # prev_ref fallback path.
                        excluded_track_pids=excluded_track_pids,
                    )
                    # Mutate _GenState same as the regular weighted
                    # dispatch — the prev_ref fallback's pick must
                    # affect subsequent weighted slots in the same
                    # generation.
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
                        reason=(
                            f"prev_ref fallback (position {block.position}: "
                            f"{miss_note}); picked weighted "
                            f"{_weighted_reason(info, fallback_window)}"
                        ),
                        # Combine the failed-mirror diagnostic with
                        # the artist-blocked rejections the weighted
                        # strategy populated.
                        excluded=failure_excluded + info.excluded_candidates,
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

        # §16.11 c1 — progress emission cadence. Yield when this
        # block iteration crossed a multiple of `_PROGRESS_EMIT_EVERY`.
        # `slot_counter` is incremented inside each strategy branch
        # per pick (contract: every dispatch branch produces exactly
        # `block.size` picks). The `max(0, …)` guards against any
        # future short-circuit that picks fewer than block.size —
        # the modulo check would otherwise read negative arithmetic.
        # Fires ONCE per crossing iteration (not once per multiple
        # crossed); see `_PROGRESS_EMIT_EVERY` comment for caveat.
        prev_counter = max(0, slot_counter - block.size)
        if slot_counter // _PROGRESS_EMIT_EVERY > (
            prev_counter // _PROGRESS_EMIT_EVERY
        ):
            yield ProgressEvent(
                phase="picks", current=slot_counter, total=total_slots,
                label=(
                    f"Generating week {week} "
                    f"({slot_counter} / {total_slots})"
                ),
            )

    # §16.11 Decision G: yield the final result. Exactly one Result
    # per stream, last. The wrapper / job-runner asserts no events
    # follow this.
    yield GenerationResult(
        picks=picks,
        trace=trace,
        prev_ref_fallback_mixes=prev_ref_fallback_mixes,
    )


# ---------- internals ----------------------------------------------------


def _check_pools_present(template: Template, pools: Mapping[str, list[Any]]) -> None:
    """Verify every source referenced by any block in the layout has a
    pool entry. Uses `layout.all_blocks()` to cover both layout forms
    (row_template + flat_slots, XOR-enforced).
    """
    required = {b.source for b in template.layout.all_blocks()}
    missing = required - set(pools.keys())
    if missing:
        raise KeyError(
            f"pools missing for content sources: {sorted(missing)}. "
            f"Provided: {sorted(pools.keys())}"
        )
    for name in required:
        if not pools[name]:
            raise ValueError(f"pool '{name}' is empty")


def _iterate_slots(template: Template, base_cursor: int):
    """Yield per-slot dispatch tuples for both layout forms (§14.32).

    Yields ``(row_idx, unit_label, row_start_in_pool, block_idx, block)``
    so the dispatch loop is form-agnostic.

    - **row_template form** (existing): outer loop over rows, inner
      loop over the per-row template. row_idx, unit_label,
      row_start_in_pool reflect the row dimension; block_idx is
      0-indexed within the row.
    - **flat_slots form** (§14.32): single linear loop. row_idx=0
      (→ trace.row = 1), unit_label="" (→ trace.unit = ""),
      row_start_in_pool=base_cursor (unused — parser forbids
      window/pick in flat_slots, which are the only branches that
      read this value). block_idx is the slot index (0-indexed).
    """
    if template.layout.flat_slots:
        for block_idx, block in enumerate(template.layout.flat_slots):
            yield 0, "", base_cursor, block_idx, block
    else:
        for row_idx, row_offset in enumerate(template.layout.row_start_offsets):
            unit_label = template.layout.unit_labels[row_idx]
            row_start_in_pool = base_cursor + row_offset
            for block_idx, block in enumerate(template.layout.row_template):
                yield row_idx, unit_label, row_start_in_pool, block_idx, block


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


def _resolve_weighted_window(
    *,
    block_window: int | None,
    station_default: int | None,
) -> int:
    """§14.29 precedence model for the weighted artist-dedup window.

    1. `block_window` (per-block BlockSpec.window) — set in the template.
    2. `station_default` — passed via generate()'s
       weighted_window_default kwarg, populated by the API from the
       station's `artist_dedup_window` rule.
    3. `DEFAULT_WEIGHTED_WINDOW` — engine constant, last resort.

    The engine constant fallback keeps `generate()` DB-agnostic — a
    caller without DB access (synthetic tests, the bootstrap script)
    can call generate() without first resolving a rule.
    """
    if block_window is not None:
        return block_window
    if station_default is not None:
        return station_default
    return DEFAULT_WEIGHTED_WINDOW


def _prev_ref_failure_excluded(
    failure,
    *,
    position: int,
    source_name: str,
) -> list:
    """Build the single ExcludedCandidate row describing the failed
    prev_ref mirror, for the §14.23 disclosure UI.

    Two cases produce a row:
      - no_previous: no track to record (no previous playlist existed),
        so we emit a *placeholder* entry with track_id=None — the
        user still sees "this slot fell back because no previous
        playlist" in the excluded panel rather than wondering why
        a fallback fired with empty diagnostics.
      - not_in_source: a track WAS resolved from the previous playlist
        but rejected because it's not in this slot's source pool.
        Record it with its id/name/artist so the user can scroll
        back to the originating slot.

    pool_index=0 is the agreed convention for "track not addressable
    in this slot's source pool" — the §14.23 ExcludedCandidate type
    requires an int, and 0 (an invalid 1-indexed position) signals
    "see the reason for context" without inventing a misleading
    pool position.
    """
    if failure is None:
        return []
    if failure.kind == "no_previous":
        return [ExcludedCandidate(
            pool_index=0,
            track_id=None,
            track_name="(no previous playlist)",
            track_artist=None,
            reason=(
                f"prev_ref position {position} could not mirror — "
                f"no previous playlist for this station"
            ),
        )]
    # not_in_source
    rt = failure.resolved_track
    return [ExcludedCandidate(
        pool_index=0,
        track_id=getattr(rt, "id", None),
        track_name=getattr(rt, "name", "?"),
        track_artist=getattr(rt, "artist", None),
        reason=(
            f"resolved from previous playlist position {position} "
            f"but not in source '{source_name}'"
        ),
    )]
