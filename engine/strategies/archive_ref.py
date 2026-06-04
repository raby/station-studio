"""archive_ref strategy: position-based lookup into a reference pool (§16.14.t6).

Sibling to ``prev_ref`` (§14.28). Where ``prev_ref`` reads from
``previous_playlist`` and filters against this slot's source pool with a
soft fallback, ``archive_ref`` is a pure deterministic lookup:

- Source pool is a ``role: reference`` pool (parser cross-check
  extension in §16.14.t6 D3 enforces this).
- Position is 1-indexed against the pool's ``SourcePlaylistEntry`` order
  (matches ``prev_ref``'s indexing convention).
- Position out of range → ``ArchiveRefPositionOutOfRangeError`` (template
  authoring error — the operator chose this position knowing the pool's
  length).
- No fallback. The reference pool is static + pre-imported; there's no
  "transient miss" equivalent to ``prev_ref``'s "no previous playlist
  yet on first generation."

Pure function; no DB, no I/O. The generator owns slot bookkeeping +
trace emission.
"""
from __future__ import annotations

from typing import Any


class ArchiveRefPositionOutOfRangeError(IndexError):
    """The template's position references a slot beyond the reference
    pool's length. Template authoring error — raise loudly so the
    operator sees exactly which slot + which pool + what length."""


def apply_archive_ref(
    source_pool: list[Any],
    *,
    position: int,
    source_name: str,
) -> Any:
    """Return the track at 1-indexed ``position`` from ``source_pool``.

    Args:
        source_pool: the reference pool's tracks in their persisted
            ``SourcePlaylistEntry`` order. ``load_pools`` includes the
            pool when the template's ``archive_ref`` block references
            it (§16.14.t6 D9).
        position: 1-indexed slot in the pool. The 1-indexing matches
            ``prev_ref``'s convention so operators using both
            strategies don't have to context-switch between conventions.
        source_name: the pool's name. Used only in error messages so
            the operator sees which pool failed.

    Raises:
        ArchiveRefPositionOutOfRangeError when ``position`` is < 1 OR
        > ``len(source_pool)``. The error message names the pool +
        the requested position + the actual pool length.
    """
    idx = position - 1
    if idx < 0 or idx >= len(source_pool):
        raise ArchiveRefPositionOutOfRangeError(
            f"archive_ref position {position} out of range for source "
            f"'{source_name}' (pool has {len(source_pool)} tracks; "
            f"valid 1-indexed positions are 1..{len(source_pool)})"
        )
    return source_pool[idx]
