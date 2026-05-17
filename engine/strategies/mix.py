"""mix strategy: genre-filtered random pick with cross-week exclusion.

Ports weekly's `_pick_mix` (DESIGN.md §14.25, §17 Phase B sub-slice #9):
filter the pool to tracks matching the requested genre, drop any whose
id is in the caller-provided exclusion set, then pick one. If the
exclusion set drains the candidates entirely, the strategy *relaxes*
the exclusion and picks from the full genre-filtered set — same
behaviour as weekly, which prefers a repeat over a generation failure.

The exclusion set is built by the persistence layer from MixUsage rows
of the station's last N playlists (N = block.exclusion_window). The
strategy itself is pure: no DB access, no global state.

Modes:
  * rng=None (template's `rng: "off"`) — deterministic: first eligible
    track in pool order.
  * rng set — `rng.choice(eligible)`. Matches weekly so the byte-
    equality harness can pass during the port.

Genre matching is the same case-sensitive exact match as `jingle`
(§14.24). Tracks with `genre=None` never match. Failure to find any
candidate at all (before exclusion) raises loud — protects against
typo'd genres silently producing an empty playlist row.
"""
from __future__ import annotations

import random
from typing import Any


class MixGenreNotFoundError(IndexError):
    """No track in the source pool carries the requested genre."""


def apply_mix(
    pool: list[Any],
    *,
    genre: str,
    excluded_ids: set[int] | frozenset[int] = frozenset(),
    rng: random.Random | None = None,
    source_name: str,
) -> tuple[int, Any, bool, int, int]:
    """Return (pool_index_1based, track, relaxed, n_candidates, n_eligible).

    - `n_candidates`: how many tracks in the pool match the genre. This is
      the upper bound on what the exclusion could filter — useful to spot
      "the source pool has only 3 House tracks, no wonder we keep
      repeating".
    - `n_eligible`: how many of those candidates survived the exclusion.
      Equal to n_candidates when `relaxed=True` (the fallback branch).
    - `relaxed`: True when the exclusion set was bypassed because it
      would have drained the candidate set to empty. Weekly behaviour:
      a repeat is better than a generation failure.

    The two counts are surfaced so the trace reason can report
    genre-specific filtering pressure (`eligible 2 of 12`) rather than
    just the opaque total exclusion-set size — review fix for §14.25 r2.
    """
    candidates = [
        (i + 1, t) for i, t in enumerate(pool)
        if getattr(t, "genre", None) == genre
    ]
    if not candidates:
        raise MixGenreNotFoundError(
            f"source '{source_name}': no track with genre {genre!r} "
            f"(pool size {len(pool)})"
        )

    eligible = [c for c in candidates if getattr(c[1], "id", None) not in excluded_ids]
    relaxed = False
    if not eligible:
        eligible = candidates
        relaxed = True

    if rng is None:
        idx, track = eligible[0]
    else:
        idx, track = rng.choice(eligible)
    return idx, track, relaxed, len(candidates), len(eligible)
