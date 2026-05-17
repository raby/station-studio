"""Artist matching: collaboration splitting and alias resolution.

Ported from weekly/app/services/artist_match.py (§14.26 / §17 Phase B
sub-slice #10). Behaviour is intentionally byte-equivalent to weekly's
implementation — the upcoming `weighted` commercial strategy (sub-slice
#11) needs the same artist-conflict semantics so the eventual byte-
equality harness against weekly's real playlists passes.

Handles two cases for the artist dedup window:

1. **Collaborations.** ``"Alex Warren feat. Ella Henderson"`` should
   conflict with ``"Alex Warren"`` because the same person appears in
   both. The raw artist string is split on common separators
   (``feat.``, ``ft.``, ``with``, ``vs``, ``&``, ``,``, ``;``,
   standalone ``x``, etc.) and each fragment is compared individually.

2. **Aliases.** ``"Plastikman"`` and ``"Richie Hawtin"`` are the same
   person. A configurable alias table maps multiple names to a single
   canonical key. The alias table is loaded from
   ``engine.models.ArtistAlias`` via
   ``engine.persistence.load_alias_map``.

Public API:

* ``extract_artist_keys(artist)`` — split a raw artist string into a
  *frozenset* of lowercase individual names.
* ``resolve_artist_keys(artist, alias_map)`` — same as above, but also
  resolves aliases via a ``{lowercase_alias: canonical}`` dict.
* ``artists_conflict(keys_a, keys_b)`` — True if the two key-sets
  overlap.
* ``build_alias_map(aliases)`` — build the lookup dict from a list of
  ``(alias_name, canonical_name)`` pairs (typically from ArtistAlias
  rows; the persistence layer adapts).

Pure functions; no DB, no I/O, no globals.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Collaboration splitter
# ---------------------------------------------------------------------------

# Matches common collaboration separators.
#
# Key design decisions (preserved verbatim from weekly):
# - "x" only matches as a STANDALONE word (\\bx\\b) to avoid splitting
#   "Alex" or "Charli xcx". We also exclude matches that are preceded or
#   followed by a letter-with-no-space (handled by \\b).
# - "&" is only treated as a separator when surrounded by whitespace, so
#   "Chase & Status" (a single act) is NOT split. Users should add an
#   alias if "Chase & Status" needs to match something else.
# - "," is treated as a separator (e.g. "Calvin Harris, Ellie Goulding").
# - ";" is treated as a separator (e.g. "Lady Gaga;Bruno Mars").
# - "feat.", "ft.", "featuring", "with", "vs", "vs." are always separators.
#
# The regex uses a lookahead/lookbehind-free approach: we require whitespace
# (or start/end) around the word-based separators, and allow no-space around
# punctuation separators (, ; ).

_COLLAB_RE = re.compile(
    r"""
    \s+ (?: feat\.? | ft\.? | featuring | with | vs\.? | and ) \s+  # word seps
    | \s* [;] \s*                                               # semicolon
    | \s* , \s*                                                 # comma
    | \s+ & \s+                                                 # ampersand (space-padded only)
    | \s+ x \s+                                                 # "x" (space-padded only)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Parenthetical content like "(Years & Years)" — extract as a separate key
# and strip from the main name.
_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*")

# Detect feat/ft/featuring/with prefix inside parenthetical content.
# When present, the parenthetical is a collaboration credit and its
# contents should be split on _COLLAB_RE rather than kept intact.
_FEAT_PREFIX_RE = re.compile(
    r"^(?:feat\.?|ft\.?|featuring|with)\s+", re.IGNORECASE
)


def extract_artist_keys(artist: str | None) -> frozenset[str]:
    """Split a raw artist string into individual artist name keys.

    Returns a frozenset of **lowercase, stripped** names. Returns an empty
    frozenset for None or blank strings.

    Processing order:
    1. Extract parenthetical content — if it starts with feat./ft./
       featuring/with, strip the prefix and split on collaboration
       separators; otherwise keep intact.
    2. Split the remaining string on collaboration separators.

    Examples::

        >>> extract_artist_keys("Alex Warren feat. Ella Henderson")
        frozenset({'alex warren', 'ella henderson'})

        >>> extract_artist_keys("Chase & Status")
        frozenset({'chase', 'status'})
        # Space-padded "&" is a separator, so this splits. Add an alias
        # if "chase" or "status" collides with unrelated artists.

        >>> extract_artist_keys("Charli xcx")
        frozenset({'charli xcx'})
        # "xcx" is not standalone "x", so it stays intact.

        >>> extract_artist_keys("Olly Alexander (Years & Years)")
        frozenset({'olly alexander', 'years & years'})

        >>> extract_artist_keys("David Guetta and Sia")
        frozenset({'david guetta', 'sia'})

        >>> extract_artist_keys(None)
        frozenset()
    """
    if not artist or not artist.strip():
        return frozenset()

    # Step 1: extract parenthetical groups as extra keys (kept intact,
    # not split — the parenthetical IS the act name, e.g. "Years & Years").
    extra_keys: list[str] = []
    for m in _PAREN_RE.finditer(artist):
        inner = m.group(1).strip()
        pfx = _FEAT_PREFIX_RE.match(inner)
        if pfx:
            # Collaboration credit — strip prefix, split on separators
            remainder = inner[pfx.end():]
            for part in _COLLAB_RE.split(remainder):
                if part and part.strip():
                    extra_keys.append(part.strip().lower())
        else:
            # Act name — keep intact
            extra_keys.append(inner.lower())
    # Remove parenthetical content from the main string
    main = _PAREN_RE.sub(" ", artist).strip()

    # Step 2: split main on collaboration separators
    parts = _COLLAB_RE.split(main) if main else []

    keys = frozenset(
        [p.strip().lower() for p in parts if p and p.strip()] + extra_keys
    )
    return keys


def resolve_artist_keys(
    artist: str | None,
    alias_map: dict[str, str] | None = None,
) -> frozenset[str]:
    """Split and resolve aliases, returning canonical artist keys.

    ``alias_map`` should be ``{lowercase_alias: canonical_lowercase}``.
    If a split name appears in the alias map, it is replaced by its
    canonical name.

    Examples::

        >>> alias_map = {"plastikman": "richie hawtin"}
        >>> resolve_artist_keys("Plastikman", alias_map)
        frozenset({'richie hawtin'})

        >>> resolve_artist_keys("Plastikman & Someone", alias_map)
        frozenset({'richie hawtin', 'someone'})
    """
    raw_keys = extract_artist_keys(artist)
    if not alias_map or not raw_keys:
        return raw_keys
    return frozenset(alias_map.get(k, k) for k in raw_keys)


def artists_conflict(
    keys_a: frozenset[str],
    keys_b: frozenset[str],
) -> bool:
    """Return True if two artist key-sets share any common key."""
    return bool(keys_a & keys_b)


def build_alias_map(
    aliases: list[tuple[str, str]],
) -> dict[str, str]:
    """Build a lookup dict from (alias_name, canonical_name) pairs.

    Both names are lowercased. The canonical name also maps to itself
    so that ``resolve_artist_keys("Richie Hawtin", alias_map)`` resolves
    the same way as ``resolve_artist_keys("Plastikman", alias_map)``.

    Example::

        >>> build_alias_map([("Plastikman", "Richie Hawtin")])
        {'plastikman': 'richie hawtin', 'richie hawtin': 'richie hawtin'}
    """
    result: dict[str, str] = {}
    for alias, canonical in aliases:
        canon_lower = canonical.strip().lower()
        result[alias.strip().lower()] = canon_lower
        result[canon_lower] = canon_lower  # self-mapping for canonical
    return result
