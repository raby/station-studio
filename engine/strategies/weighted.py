"""weighted strategy: two-tier track + artist dedup with squared-remaining weight.

Ports weekly's `_pick_commercial` (§14.27 / §17 Phase B sub-slice #11).
The most behaviourally complex strategy in the port; the eventual
byte-equality harness depends on identical selection sequences across
the whole pool given the same rng seed.

Two constraints per pick:

1. **Track dedup (shuffle-bag).** A track cannot repeat until
   ``pool_size - 1`` other tracks have been played. Guarantees every
   track plays once before any repeats — produces an even frequency
   distribution.

2. **Artist dedup (sliding window).** The same artist cannot appear
   within the last ``window`` picks (default 10 — overridable per-block
   via BlockSpec.window; sub-slice #13's Rule table will eventually
   provide a station-wide default that per-block can override).

   Uses *resolved artist keys* — `engine.artist_match.resolve_artist_keys`
   splits collaborations and resolves aliases, so "Alex Warren feat.
   Ella Henderson" conflicts with "Alex Warren", and "Plastikman"
   conflicts with "Richie Hawtin" (when aliased).

Three selection tiers:

- **Tier 1** (both constraints satisfied) → weighted random pick where
  ``weight = remaining²``. `remaining` is how many tracks by this
  artist haven't been played this shuffle-bag cycle. Squaring spreads
  multi-track artists (e.g. one artist with 15 tracks) evenly across
  the cycle instead of letting them cluster at the end.

- **Tier 2** (Tier 1 empty — every eligible track is artist-blocked)
  → relax the artist constraint, pick the track whose artist was
  played longest ago. Ties broken by rng.choice. This minimises the
  audible repeat by maximising the gap.

- **Edge case** (pool too small for shuffle-bag) → bypass the track
  window entirely; relies on Tier 2 to relax the artist constraint.

Returns the picked track plus a `WeightedPickInfo` describing which
tier fired and the eligible/excluded counts — sub-slice #11 follow-on
populates the §14.23 excluded-candidates trace from this info.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from engine.artist_match import resolve_artist_keys
from engine.trace import ExcludedCandidate


@dataclass
class WeightedPickInfo:
    """Provenance + diagnostics for one weighted pick.

    Returned alongside the picked track so the caller (generator) can
    surface it in the trace reason and populate §14.23 ExcludedCandidate
    entries for the viewer.

    Tier:
        1 = both constraints satisfied; weighted random selection
        2 = artist constraint relaxed; pick stalest artist
    """
    tier: int
    pool_index: int                  # 1-indexed pool position of the pick
    eligible_count: int              # candidates that survived Tier 1
    artist_blocked_count: int = 0    # tracks blocked by artist dedup only
    track_blocked_count: int = 0     # tracks blocked by shuffle-bag only
    both_blocked_count: int = 0      # tracks blocked by BOTH constraints
    relaxed: bool = False            # Tier 2 fallback fired
    # Per-candidate rejection rationale, scoped to ARTIST-blocked tracks
    # (the audibly meaningful rejections). Track-shuffle-bag-only
    # rejections are skipped intentionally — they're mechanical
    # bookkeeping ("used 50 slots ago, blocked until cycle wraps") that
    # generates noise without authoring value. The picked track itself
    # is filtered out before this list is returned (Tier 2 might pick
    # an artist-blocked track via relaxation; including it as both
    # "rejected" and "selected" would be confusing).
    excluded_candidates: list[ExcludedCandidate] = field(default_factory=list)


@dataclass
class WeightedLookups:
    """Precomputed per-pool lookups. Built once per generation by the
    caller and passed into apply_weighted on every call so we don't
    recompute on each pick.

    For a 210-track pool with 1,001 weighted slots in one generation,
    the savings are real — without this, every pick rebuilds three
    dicts of size ~pool_size.
    """
    track_id_to_artist: dict[int, str]
    artist_counts: dict[str, int]
    # Resolved artist key-set per track id. None means "compute lazily
    # in apply_weighted", but in practice the generator always provides
    # a populated dict so this is the fast path.
    track_keys: dict[int, frozenset[str]] = field(default_factory=dict)


def build_lookups(
    pool: list[Any],
    *,
    alias_map: dict[str, str] | None = None,
) -> WeightedLookups:
    """Pre-build the per-pool lookups for one generation.

    Caller passes the alias_map loaded from `engine.persistence.
    load_alias_map`. Tracks with no artist field are still indexed
    (with empty key-sets) so they don't crash apply_weighted's lookups.
    """
    track_id_to_artist: dict[int, str] = {}
    artist_counts: dict[str, int] = defaultdict(int)
    track_keys: dict[int, frozenset[str]] = {}
    for t in pool:
        tid = getattr(t, "id", None)
        if tid is None:
            continue
        artist = getattr(t, "artist", None) or ""
        track_id_to_artist[tid] = artist
        artist_counts[artist] += 1
        track_keys[tid] = resolve_artist_keys(artist, alias_map)
    return WeightedLookups(
        track_id_to_artist=track_id_to_artist,
        artist_counts=dict(artist_counts),
        track_keys=track_keys,
    )


def apply_weighted(
    pool: list[Any],
    *,
    lookups: WeightedLookups,
    recent_artist_keys: list[frozenset[str]],
    used_track_ids: list[int],
    window: int = 10,
    rng: random.Random | None = None,
    source_name: str,
    excluded_track_pids: frozenset[str] | set[str] | None = None,
    allowed_track_pids: frozenset[str] | set[str] | None = None,
) -> tuple[Any, WeightedPickInfo]:
    """Return (picked_track, info) for one weighted commercial slot.

    `recent_artist_keys` and `used_track_ids` are caller-owned mutable
    lists carrying every weighted pick made earlier in this generation
    (most recent last). The strategy READS but does not mutate them —
    the generator appends after a successful pick so the GenState shape
    stays in the generator's control.

    `rng=None` is supported for determinism in tests, but weekly's path
    always uses a seeded rng. With rng=None the weighted random reduces
    to "pick the first eligible track"; tier 2's rng.choice tie-break
    becomes "first stalest". This is intentionally divergent from
    weekly's behaviour and only useful for synthetic tests — production
    callers pass rng.

    `excluded_track_pids` (§16.14.t5): if non-empty, drops every track
    whose `persistent_id` appears in the set BEFORE the weighted-pick
    machinery runs. This is the cross-pool dedup mechanism — the
    generator resolves the `cross_pool_dedup_sources` station rule
    upstream + passes the union of those pools' track PIDs in here.
    Filter happens at the pool level (not the candidate level) so
    artist-dedup + stalest-tracking work as if the excluded tracks
    were never imported. An empty/None set is a no-op (the rule's
    default).

    `allowed_track_pids` (§16.14.t7): if non-empty, KEEPS only tracks
    whose `persistent_id` appears in the set (positive filter; the
    inverse of `excluded_track_pids`). Generator builds the union
    from the block's `allowlist_sources` upstream. Composes with
    `excluded_track_pids` per D8 — the final candidate set is
    ``(pool ∩ allowlist) - exclusion`` (set-minus, written with a
    plain "-" rather than the conventional backslash to avoid a
    docstring-escape ambiguity). Per-filter empty-pool errors name
    the constraint that emptied the pool so the operator can fix
    the right rule. Filter ORDER (c2-review F2): allowlist runs
    BEFORE exclusion, matching ``apply_pick(rng=on)``'s order —
    set algebra makes the order irrelevant for the final result,
    but the error message depends on which filter empties first.
    "Allowlist first" surfaces the positive-constraint error
    preferentially, matching the operator's mental model.

    **Caveat — `pool_index` semantics under exclusion.** When the
    filter drops tracks, the returned ``WeightedPickInfo.pool_index``
    + every ``ExcludedCandidate.pool_index`` reflect the POST-FILTER
    pool, not the original ``SourcePlaylistEntry.position``. The
    trace's `track_id` is still authoritative for identifying the
    pick; `pool_index` should be read as "position the strategy saw
    after exclusion", not "position in the imported pool". The
    alternative (preserving original indices) would require
    bookkeeping that complicates the hot path with no audible-radio
    benefit. §16.14.t5 c1 review F1 documents this trade-off.
    """
    # §16.14.t5: cross-pool dedup. Filter the pool BEFORE the
    # empty-pool guard so a fully-excluded source raises with a
    # specific diagnostic instead of the bare "pool is empty" error.
    # Post-filter pool_index reflects the filtered subset (see
    # docstring caveat) — operators inspecting the trace use
    # track_id, not pool_index, to locate the pick.
    # §16.14.t7 D4 + c2-review F2: per-block allowlist as a pre-
    # filter (positive filter). Runs BEFORE the exclusion filter
    # so the order matches `apply_pick(rng=on)`'s order. Set
    # algebra makes the final result independent of order; the
    # error message is what depends on order. Operator's mental
    # model: "first restrict to the allowlist, then exclude any
    # in the deny list" — applying allowlist first surfaces the
    # allowlist-too-narrow error preferentially over the deny-
    # list-too-broad one, which matches "fix the positive
    # constraint first" UX.
    if allowed_track_pids:
        pre_allow_size = len(pool)
        pool = [
            t for t in pool
            if getattr(t, "persistent_id", None) in allowed_track_pids
        ]
        if not pool:
            raise IndexError(
                f"source '{source_name}': all {pre_allow_size} tracks "
                f"filtered out by the block's allowlist_sources "
                "(§16.14.t7) — none of the pool's persistent_ids "
                "appear in the allowlist union. Widen the allowlist "
                "pool(s), remove the block's allowlist_sources, or "
                "import more tracks into the content pool."
            )

    if excluded_track_pids:
        pre_filter_size = len(pool)
        pool = [
            t for t in pool
            if getattr(t, "persistent_id", None) not in excluded_track_pids
        ]
        if not pool:
            raise IndexError(
                f"source '{source_name}': all {pre_filter_size} tracks "
                f"excluded by cross_pool_dedup_sources (§16.14.t5). "
                "Either narrow the dedup source list or import more "
                "tracks into the content pool."
            )

    if not pool:
        raise IndexError(f"source '{source_name}': pool is empty")

    pool_size = len(pool)
    track_window = pool_size - 1 if pool_size > 1 else 0
    blocked_track_ids = (
        set(used_track_ids[-track_window:]) if track_window else set()
    )

    blocked_keys: set[str] = set()
    for ks in recent_artist_keys[-window:]:
        blocked_keys.update(ks)

    track_keys = lookups.track_keys
    track_id_to_artist = lookups.track_id_to_artist
    artist_counts = lookups.artist_counts

    def _artist_blocked(t: Any) -> bool:
        return bool(track_keys.get(getattr(t, "id", -1), frozenset()) & blocked_keys)

    # Count how many of this artist's tracks have been used this cycle.
    cycle_used: dict[str, int] = defaultdict(int)
    for tid in used_track_ids[-track_window:] if track_window else []:
        artist = track_id_to_artist.get(tid)
        if artist is not None:
            cycle_used[artist] += 1

    def _remaining(artist: str) -> int:
        return artist_counts.get(artist, 1) - cycle_used.get(artist, 0)

    # --- single pass: classify + collect Tier 1 eligibles + excluded ----
    # We do BOTH in one walk to (a) keep the per-track work in cache and
    # (b) avoid rebuilding the eligible list later. The classification
    # counters feed WeightedPickInfo's summary; the per-track excluded
    # candidates (artist-blocked only — track-shuffle-bag-only rejections
    # are mechanical noise) drive §14.23 ExcludedCandidate population.
    # The pass uses no rng — rng state is identical to weekly's single-
    # pass list-comp construction at this point, so byte-equality holds.
    artist_blocked = 0
    track_blocked = 0
    both_blocked = 0
    eligible: list[tuple[int, Any]] = []  # (pool_idx_1based, track)
    excluded_candidates: list[ExcludedCandidate] = []
    for i, t in enumerate(pool, start=1):
        tid = getattr(t, "id", None)
        is_track_blocked = tid in blocked_track_ids if tid is not None else False
        keys = track_keys.get(tid, frozenset()) if tid is not None else frozenset()
        colliding = keys & blocked_keys
        is_artist_blocked_ = bool(colliding)
        if is_track_blocked and is_artist_blocked_:
            both_blocked += 1
            excluded_candidates.append(_excluded(t, i, colliding, also_in_bag=True))
        elif is_track_blocked:
            track_blocked += 1  # mechanical noise — no ExcludedCandidate
        elif is_artist_blocked_:
            artist_blocked += 1
            excluded_candidates.append(_excluded(t, i, colliding, also_in_bag=False))
        else:
            eligible.append((i, t))

    # --- Tier 1: weighted random over eligible --------------------------
    if eligible:
        weights = [
            max(_remaining(getattr(t, "artist", None) or ""), 1) ** 2
            for (_, t) in eligible
        ]
        picked_idx, picked_track = _weighted_choice(eligible, weights, rng)
        return picked_track, WeightedPickInfo(
            tier=1,
            pool_index=picked_idx,
            eligible_count=len(eligible),
            artist_blocked_count=artist_blocked,
            track_blocked_count=track_blocked,
            both_blocked_count=both_blocked,
            relaxed=False,
            # Tier 1 picks are never artist-blocked, so no need to filter
            # the chosen track out of excluded_candidates.
            excluded_candidates=excluded_candidates,
        )

    # --- Tier 2: relax artist, pick stalest --------------------------------
    # All tracks that aren't track-blocked are now eligible. If even that
    # is empty (pool too small + shuffle-bag drained), fall back to the
    # whole pool — guards weekly's pool_size <= 1 edge case.
    relaxed_eligible: list[tuple[int, Any]] = [
        (i, t) for i, t in enumerate(pool, start=1)
        if (getattr(t, "id", None) not in blocked_track_ids)
    ]
    if not relaxed_eligible:
        relaxed_eligible = [(i, t) for i, t in enumerate(pool, start=1)]

    picked_idx, picked_track = _pick_stalest(
        relaxed_eligible,
        recent_artist_keys=recent_artist_keys,
        track_keys=track_keys,
        rng=rng,
    )
    # Tier 2 relaxation might have picked an artist-blocked track. Strip
    # it from the excluded list so the viewer never shows the picked
    # track as "rejected" alongside its own slot row.
    picked_id = getattr(picked_track, "id", None)
    tier2_excluded = [
        ec for ec in excluded_candidates if ec.track_id != picked_id
    ]
    return picked_track, WeightedPickInfo(
        tier=2,
        pool_index=picked_idx,
        eligible_count=len(relaxed_eligible),
        artist_blocked_count=artist_blocked,
        track_blocked_count=track_blocked,
        both_blocked_count=both_blocked,
        relaxed=True,
        excluded_candidates=tier2_excluded,
    )


# ---------- internals ----------------------------------------------------


def _weighted_choice(
    items: list[tuple[int, Any]],
    weights: list[int],
    rng: random.Random | None,
) -> tuple[int, Any]:
    """Pick (pool_idx, track) by weight. Determinism for rng=None: first item.

    Float-rounding guard returns items[-1] if cumulative comparison
    leaves no winner; mirrors weekly's `return eligible[-1]` fallback
    line that's `# pragma: no cover` in weekly's tests.
    """
    if rng is None:
        return items[0]
    total = sum(weights)
    r = rng.random() * total
    cumulative = 0.0
    for (item, w) in zip(items, weights):
        cumulative += w
        if r < cumulative:
            return item
    return items[-1]


def _pick_stalest(
    items: list[tuple[int, Any]],
    *,
    recent_artist_keys: list[frozenset[str]],
    track_keys: dict[int, frozenset[str]],
    rng: random.Random | None,
) -> tuple[int, Any]:
    """Tier 2: pick the track whose artist keys were played longest ago.

    Build a map of key → most-recent-position, then for each candidate
    compute its gap = current_step - max(position over its keys).
    Ties are broken by rng.choice over (raw_artist, [tracks]) groups,
    then rng.choice over that artist's tracks — matches weekly's exact
    two-step random tiebreak.
    """
    key_last_pos: dict[str, int] = {}
    for i, ks in enumerate(recent_artist_keys):
        for k in ks:
            key_last_pos[k] = i
    current_step = len(recent_artist_keys)

    by_artist: dict[str, list[tuple[int, Any]]] = defaultdict(list)
    for (idx, t) in items:
        by_artist[getattr(t, "artist", None) or ""].append((idx, t))

    def _gap(raw_artist: str) -> int:
        # `first_track` is the leading Track for this artist's group — any
        # track for the artist works since they all share the resolved
        # key-set (artists are grouped by raw artist string). We pull `id`
        # to look up its key-set; mirrors weekly's
        # `next(t.id for t in by_raw_artist[raw_artist])`.
        first_track = by_artist[raw_artist][0][1]
        ks = track_keys.get(getattr(first_track, "id", -1), frozenset())
        if not ks:
            return 99999
        known = [key_last_pos[k] for k in ks if k in key_last_pos]
        if not known:
            return 99999
        return current_step - max(known)

    best_gap = max(_gap(a) for a in by_artist)
    stalest = [(a, ts) for a, ts in by_artist.items() if _gap(a) == best_gap]

    if rng is None:
        _, chosen_tracks = stalest[0]
        return chosen_tracks[0]
    _, chosen_tracks = rng.choice(stalest)
    return rng.choice(chosen_tracks)


def _excluded(
    track: Any,
    pool_index: int,
    colliding_keys: frozenset[str] | set[str],
    *,
    also_in_bag: bool,
) -> ExcludedCandidate:
    """Build the ExcludedCandidate row for one artist-blocked rejection.

    The `reason` quotes the specific colliding artist key(s) so the
    user can scroll back through the trace and find the prior pick
    that put them in the window — answers "why didn't it pick X?"
    without re-running the generator.
    """
    keys_str = ", ".join(f"'{k}'" for k in sorted(colliding_keys))
    plural = "keys" if len(colliding_keys) > 1 else "key"
    suffix = "; also in shuffle-bag" if also_in_bag else ""
    return ExcludedCandidate(
        pool_index=pool_index,
        track_id=getattr(track, "id", None),
        track_name=getattr(track, "name", "?"),
        track_artist=getattr(track, "artist", None),
        reason=f"artist {plural} {keys_str} in dedup window{suffix}",
    )
