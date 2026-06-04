"""window strategy: take N consecutive items starting at a pool index.

Used by Rebirth Classics for every slot. Deterministic. Fails loud if the
window runs off the end of the pool — no silent wrap.

§16.14.t8: optionally checks each addressed track against an exclusion
set built from the `cross_pool_dedup_sources` rule (§16.14.t5). Fail-
loud on collision per D3 — the operator's deterministic positions
clash with the rule's exclusion contract; the engine surfaces the
conflict explicitly rather than silently skipping.
"""
from __future__ import annotations

from typing import Any


class WindowOutOfRangeError(IndexError):
    pass


def apply_window(
    pool: list[Any],
    *,
    start: int,
    size: int,
    source_name: str,
    excluded_track_pids: frozenset[str] | set[str] | None = None,
) -> list[tuple[int, Any]]:
    """Return [(pool_index_1based, track), ...] for `size` items starting at `start` (0-indexed).

    Raises WindowOutOfRangeError if start+size exceeds len(pool).

    `excluded_track_pids` (§16.14.t8): if non-empty, checks each track
    in the addressed range `[start, start+size)` against the set. The
    FIRST excluded track raises WindowOutOfRangeError with a t8-
    specific diagnostic naming the pool, the offending pool index
    (1-indexed in the message to match operator-facing convention),
    and the excluded track's persistent_id. Per §16.14.t8 D3 the
    operator either accepts the layout-vs-rule conflict OR redesigns;
    the engine honors the "never play these tracks" contract
    absolutely rather than silently walking past.
    """
    if start < 0:
        raise WindowOutOfRangeError(
            f"source '{source_name}': window start {start} is negative"
        )
    if start + size > len(pool):
        raise WindowOutOfRangeError(
            f"source '{source_name}': window [{start}..{start + size - 1}] "
            f"exceeds pool length {len(pool)}. "
            f"Add at least {start + size - len(pool)} more tracks to '{source_name}'."
        )
    if excluded_track_pids:
        for i in range(size):
            track = pool[start + i]
            pid = getattr(track, "persistent_id", None)
            if pid in excluded_track_pids:
                # §16.14.t8 D3: surface the exact collision so the
                # operator can fix by adding pool tracks, narrowing
                # the rule, or redesigning the layout.
                raise WindowOutOfRangeError(
                    f"source '{source_name}': window collided with a "
                    f"track excluded by cross_pool_dedup_sources "
                    f"(§16.14.t8) at pool_index {start + i + 1} "
                    f"(persistent_id={pid!r}). The rule's exclusion "
                    "contract is absolute; either widen the pool, "
                    "narrow cross_pool_dedup_sources, or move this "
                    "window's start position past the excluded track."
                )
    return [(start + i + 1, pool[start + i]) for i in range(size)]
