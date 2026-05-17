"""prev_ref strategy: mirror a slot from the previous playlist (§14.28).

Ports the mirror-resolution portion of weekly's PREV_REF branch
(`_resolve_prev_ref`-style logic; weekly does it inline in the
generation loop rather than a dedicated function). The pure mirror
attempt is the only piece this module owns — the fallback dispatch
(mix or weighted) happens in the generator because it needs access
to live state machinery (`_GenState`, lookup caches, excluded_mix_ids)
that strategies shouldn't reach into.

Behaviour mirrors weekly exactly:

- Position is 1-indexed against the previous playlist's slot order.
- The resolved track must be in this slot's `source` pool to count
  as a successful mirror. Weekly's hardcoded "is this a MIXES track?"
  generalises to "is this a track from `source`?"; for the eventual
  Rebirth Radio port that source is "MIXES" and the behaviour is
  byte-identical.
- Position out of range is a TEMPLATE ERROR — raised, not silently
  recovered. The template author chose this position knowing the
  previous playlist's length.
- Failed mirror (no previous, or resolved track not in source) does
  NOT raise — the caller (generator) is responsible for invoking
  the appropriate fallback path (mix or weighted).

Pure function; no DB, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PrevRefPositionOutOfRangeError(IndexError):
    """The template's position field references a slot beyond the
    previous playlist's length. Template authoring error."""


@dataclass
class PrevRefMirrorFailure:
    """Why the mirror attempt failed; drives the generator's fallback
    dispatch decision.

    kind:
        "no_previous"     — no previous playlist exists (first generation
                            for this station, or `previous_playlist=None`
                            was passed in).
        "not_in_source"   — previous playlist DID have a track at the
                            position, but that track is not present in
                            this slot's declared `source` pool. The
                            resolved track is captured for diagnostic
                            context (the §14.23 ExcludedCandidate
                            populating commit 2 will surface it).
    """
    kind: str  # "no_previous" | "not_in_source"
    resolved_track: Any | None = None  # populated only for "not_in_source"


def try_prev_ref_mirror(
    previous_playlist: list[Any] | None,
    *,
    position: int,
    source_pool: list[Any],
) -> tuple[Any | None, PrevRefMirrorFailure | None]:
    """Attempt to mirror a slot from the previous playlist.

    Returns:
        (mirrored_track, None)   on success — caller uses the track as-is
        (None, failure_reason)   on a recoverable miss — caller falls back

    Raises:
        PrevRefPositionOutOfRangeError when position is < 1 or > len(prev).
        Surfaced upstream (generator) as a generation failure with a
        template-authoring-error message.
    """
    if previous_playlist is None:
        return None, PrevRefMirrorFailure(kind="no_previous")

    idx = position - 1
    if idx < 0 or idx >= len(previous_playlist):
        raise PrevRefPositionOutOfRangeError(
            f"prev_ref position {position} out of range "
            f"(previous playlist has {len(previous_playlist)} slots)"
        )

    resolved = previous_playlist[idx]
    # "In source" check: build the id-set once per call. Pool sizes are
    # in the hundreds for real templates — cheap. Caching across calls
    # is a future optimisation if a profile shows it (see §14.28 punts).
    source_ids = {getattr(t, "id", None) for t in source_pool}
    if getattr(resolved, "id", None) in source_ids:
        return resolved, None

    return None, PrevRefMirrorFailure(kind="not_in_source", resolved_track=resolved)
