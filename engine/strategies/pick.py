"""pick strategy: take the next item from a source pool.

Not used by Classics. Here so the engine has a second primitive from day one,
and so stations that need a simple sequential "one-at-a-time" block can express
it without introducing a new strategy.

In rng='off' mode this is identical to window of size 1 advanced per call.
In rng='on' mode (future) this draws randomly.

§16.14.t8: optionally checks against an exclusion set built from the
`cross_pool_dedup_sources` rule (§16.14.t5). The two modes get
DIFFERENT semantics, honest about their determinism stories:

  * rng=None (deterministic): fail-loud at the cursor position. If
    `pool[cursor].persistent_id` is in the exclusion set, raise. The
    cursor doesn't advance past excluded tracks — explicit failure
    is the §16.14.t8 D5 contract. The operator's positions clash
    with the rule; they fix one or the other.
  * rng=on (random): pre-filter the pool to non-excluded tracks
    BEFORE the random pick. Fail-loud only if the filtered pool is
    empty (the §16.14.t8 D6 weighted-pattern). Random picks have
    no "addressed position" to collide; the addressed range IS the
    whole pool, so the failure model is "no valid choice exists."
"""
from __future__ import annotations

import random
from typing import Any


def apply_pick(
    pool: list[Any],
    *,
    cursor: int,
    rng: random.Random | None = None,
    source_name: str,
    excluded_track_pids: frozenset[str] | set[str] | None = None,
) -> tuple[int, Any, int]:
    """Return (pool_index_1based, track, new_cursor).

    If rng is None, picks pool[cursor] and returns cursor+1 as new_cursor.
    If rng is set, picks a random item and returns cursor unchanged.

    `excluded_track_pids` (§16.14.t8): see module docstring for the
    rng=None vs rng=on semantic split.
    """
    if not pool:
        raise IndexError(f"source '{source_name}': pool is empty")

    if rng is None:
        if cursor >= len(pool):
            raise IndexError(
                f"source '{source_name}': cursor {cursor} exceeds pool length {len(pool)}"
            )
        # §16.14.t8 D5 — deterministic mode: fail-loud at the
        # addressed cursor position. No skip-and-advance; the
        # operator chose this cursor.
        if excluded_track_pids:
            track = pool[cursor]
            pid = getattr(track, "persistent_id", None)
            if pid in excluded_track_pids:
                raise IndexError(
                    f"source '{source_name}': pick (rng=None) collided "
                    f"with a track excluded by cross_pool_dedup_sources "
                    f"(§16.14.t8) at cursor {cursor} (pool_index "
                    f"{cursor + 1}, persistent_id={pid!r}). The cursor "
                    "doesn't advance past excluded tracks — either "
                    "widen the pool, narrow cross_pool_dedup_sources, "
                    "or move this block's source / row offset so the "
                    "cursor lands past the excluded track."
                )
        return (cursor + 1, pool[cursor], cursor + 1)

    # §16.14.t8 D6 — random mode: pre-filter to non-excluded tracks.
    # Fail-loud only if the filtered pool is empty (the weighted-
    # strategy pattern). Random picks have no "addressed position"
    # to collide with — the addressed range is the whole pool.
    if excluded_track_pids:
        filtered = [
            (i, t) for i, t in enumerate(pool)
            if getattr(t, "persistent_id", None) not in excluded_track_pids
        ]
        if not filtered:
            raise IndexError(
                f"source '{source_name}': pick (rng=on) found no "
                f"non-excluded tracks — all {len(pool)} pool tracks "
                "are in cross_pool_dedup_sources (§16.14.t8). Narrow "
                "the rule or widen the pool."
            )
        chosen_idx_zero_based, track = filtered[rng.randrange(len(filtered))]
        return (chosen_idx_zero_based + 1, track, cursor)

    idx = rng.randrange(len(pool))
    return (idx + 1, pool[idx], cursor)
