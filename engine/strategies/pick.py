"""pick strategy: take the next item from a source pool.

Not used by Classics. Here so the engine has a second primitive from day one,
and so stations that need a simple sequential "one-at-a-time" block can express
it without introducing a new strategy.

In rng='off' mode this is identical to window of size 1 advanced per call.
In rng='on' mode (future) this draws randomly.
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
) -> tuple[int, Any, int]:
    """Return (pool_index_1based, track, new_cursor).

    If rng is None, picks pool[cursor] and returns cursor+1 as new_cursor.
    If rng is set, picks a random item and returns cursor unchanged.
    """
    if not pool:
        raise IndexError(f"source '{source_name}': pool is empty")

    if rng is None:
        if cursor >= len(pool):
            raise IndexError(
                f"source '{source_name}': cursor {cursor} exceeds pool length {len(pool)}"
            )
        return (cursor + 1, pool[cursor], cursor + 1)

    idx = rng.randrange(len(pool))
    return (idx + 1, pool[idx], cursor)
