"""jingle strategy: pick a track from a genre-filtered subset of a source pool.

Used by Weekly-style templates (§14.24 / §17 Phase B sub-slice #8). The
strategy filters the pool down to entries whose `genre` attribute matches
the block's declared `genre`, then picks one:

- rng=off (deterministic): returns the first matching track. Useful for
  station-studio's repeatable-generation invariant and tests; the byte-
  equality harness against weekly's actual playlists uses rng=on.
- rng=on: returns rng.choice(candidates). Mirrors weekly's `_resolve_jingle`
  so the port reaches byte-equality with the existing generator.

Fails loud (no fallback) when no track in the pool carries the requested
genre — matches weekly's `GenerationError(f"No jingle found with genre …")`
and station-studio's §5.5 "fail loud on missing config" principle.
"""
from __future__ import annotations

import random
from typing import Any


class JingleGenreNotFoundError(IndexError):
    """No track in the source pool carries the requested genre."""


def apply_jingle(
    pool: list[Any],
    *,
    genre: str,
    rng: random.Random | None = None,
    source_name: str,
) -> tuple[int, Any]:
    """Return (pool_index_1based, track) for a genre-filtered pick.

    `genre` is a case-sensitive exact match against `track.genre`. Tracks
    with no genre attribute (None) never match — they're invisible to the
    jingle strategy regardless of slot.
    """
    candidates = [
        (i + 1, t) for i, t in enumerate(pool)
        if getattr(t, "genre", None) == genre
    ]
    if not candidates:
        raise JingleGenreNotFoundError(
            f"source '{source_name}': no track with genre {genre!r} "
            f"(pool size {len(pool)})"
        )
    if rng is None:
        return candidates[0]
    return rng.choice(candidates)
