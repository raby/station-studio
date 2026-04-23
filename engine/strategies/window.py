"""window strategy: take N consecutive items starting at a pool index.

Used by Rebirth Classics for every slot. Deterministic. Fails loud if the
window runs off the end of the pool — no silent wrap.
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
) -> list[tuple[int, Any]]:
    """Return [(pool_index_1based, track), ...] for `size` items starting at `start` (0-indexed).

    Raises WindowOutOfRangeError if start+size exceeds len(pool).
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
    return [(start + i + 1, pool[start + i]) for i in range(size)]
