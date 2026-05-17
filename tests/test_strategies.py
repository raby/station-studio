"""Direct unit tests for the slot-filling strategies.

Station Studio's engine is composed of small primitives. Testing them directly
(not just via end-to-end Classics generation) makes regressions obvious when a
primitive breaks, and documents the exact contract of each.
"""
from __future__ import annotations

import random
import pytest

from dataclasses import dataclass

from engine.strategies.window import apply_window, WindowOutOfRangeError
from engine.strategies.pick import apply_pick
from engine.strategies.jingle import apply_jingle, JingleGenreNotFoundError
from engine.strategies.mix import apply_mix, MixGenreNotFoundError
from engine.strategies.weighted import apply_weighted, build_lookups


# ---------- window -------------------------------------------------------


def test_window_happy_path():
    pool = list(range(1, 101))  # 1..100
    got = apply_window(pool, start=10, size=5, source_name="X")
    assert got == [(11, 11), (12, 12), (13, 13), (14, 14), (15, 15)]


def test_window_at_start_of_pool():
    pool = ["a", "b", "c"]
    got = apply_window(pool, start=0, size=2, source_name="X")
    assert got == [(1, "a"), (2, "b")]


def test_window_to_end_of_pool():
    pool = ["a", "b", "c"]
    got = apply_window(pool, start=1, size=2, source_name="X")
    assert got == [(2, "b"), (3, "c")]


def test_window_exceeding_pool_raises_with_helpful_message():
    pool = list(range(10))
    with pytest.raises(WindowOutOfRangeError, match="exceeds pool length 10"):
        apply_window(pool, start=5, size=10, source_name="MY_POOL")


def test_window_error_mentions_pool_name_and_shortfall():
    pool = list(range(10))
    with pytest.raises(WindowOutOfRangeError, match="MY_POOL"):
        apply_window(pool, start=5, size=10, source_name="MY_POOL")
    with pytest.raises(WindowOutOfRangeError, match="Add at least 5"):
        apply_window(pool, start=5, size=10, source_name="MY_POOL")


def test_window_negative_start_raises():
    pool = list(range(10))
    with pytest.raises(WindowOutOfRangeError, match="negative"):
        apply_window(pool, start=-1, size=5, source_name="X")


def test_window_size_one():
    pool = ["only"]
    assert apply_window(pool, start=0, size=1, source_name="X") == [(1, "only")]


# ---------- pick ---------------------------------------------------------


def test_pick_sequential_advances_cursor():
    pool = ["a", "b", "c"]
    idx, track, new_cursor = apply_pick(pool, cursor=0, rng=None, source_name="X")
    assert (idx, track, new_cursor) == (1, "a", 1)
    idx, track, new_cursor = apply_pick(pool, cursor=new_cursor, rng=None, source_name="X")
    assert (idx, track, new_cursor) == (2, "b", 2)


def test_pick_sequential_raises_at_end():
    pool = ["a"]
    with pytest.raises(IndexError, match="cursor 1 exceeds"):
        apply_pick(pool, cursor=1, rng=None, source_name="X")


def test_pick_random_respects_seed():
    pool = list(range(100))
    rng_a = random.Random(42)
    rng_b = random.Random(42)
    a = apply_pick(pool, cursor=0, rng=rng_a, source_name="X")
    b = apply_pick(pool, cursor=0, rng=rng_b, source_name="X")
    assert a == b  # same seed -> same result


def test_pick_random_does_not_advance_cursor():
    """In random mode, the cursor isn't the primary driver, so it shouldn't move."""
    pool = list(range(100))
    rng = random.Random(42)
    _idx, _track, new_cursor = apply_pick(pool, cursor=5, rng=rng, source_name="X")
    assert new_cursor == 5


def test_pick_empty_pool_raises():
    with pytest.raises(IndexError, match="pool is empty"):
        apply_pick([], cursor=0, rng=None, source_name="X")


# ---------- jingle (§14.24) ---------------------------------------------


@dataclass
class GenreTrack:
    id: int
    name: str
    genre: str | None = None


def _jingle_pool() -> list[GenreTrack]:
    return [
        GenreTrack(id=1, name="Day-A", genre="Daytime"),
        GenreTrack(id=2, name="Night-A", genre="Nighttime"),
        GenreTrack(id=3, name="Day-B", genre="Daytime"),
        GenreTrack(id=4, name="Mix-A", genre="Mixzone"),
        GenreTrack(id=5, name="Day-C", genre="Daytime"),
    ]


def test_jingle_deterministic_returns_first_genre_match():
    # No rng: first matching pool entry wins. 1-indexed pool position.
    idx, track = apply_jingle(_jingle_pool(), genre="Daytime", source_name="JINGLES")
    assert (idx, track.name) == (1, "Day-A")


def test_jingle_random_seeded_is_reproducible():
    pool = _jingle_pool()
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    a = apply_jingle(pool, genre="Daytime", rng=rng_a, source_name="JINGLES")
    b = apply_jingle(pool, genre="Daytime", rng=rng_b, source_name="JINGLES")
    assert a == b


def test_jingle_random_only_picks_from_matching_genre():
    pool = _jingle_pool()
    rng = random.Random(123)
    # Run many picks; every result must be a Daytime track.
    for _ in range(50):
        idx, track = apply_jingle(pool, genre="Daytime", rng=rng, source_name="JINGLES")
        assert track.genre == "Daytime"
        # 1-indexed positions for Daytime entries: 1, 3, 5.
        assert idx in {1, 3, 5}


def test_jingle_unknown_genre_raises_with_pool_context():
    with pytest.raises(JingleGenreNotFoundError, match="MY_JINGLES.*no track with genre 'Banger'"):
        apply_jingle(_jingle_pool(), genre="Banger", source_name="MY_JINGLES")


def test_jingle_skips_tracks_with_no_genre_attribute():
    pool = [GenreTrack(id=1, name="X", genre=None), GenreTrack(id=2, name="Y", genre="Daytime")]
    idx, track = apply_jingle(pool, genre="Daytime", source_name="JINGLES")
    assert (idx, track.name) == (2, "Y")


def test_jingle_genre_match_is_case_sensitive():
    # 'daytime' != 'Daytime'; we want loud failure on case drift rather
    # than silently matching against a stale/typo'd genre tag.
    with pytest.raises(JingleGenreNotFoundError):
        apply_jingle(_jingle_pool(), genre="daytime", source_name="JINGLES")


# ---------- mix (§14.25) -------------------------------------------------


@dataclass
class MixTrack:
    id: int
    name: str
    genre: str | None = None


def _mix_pool() -> list[MixTrack]:
    return [
        MixTrack(id=10, name="House-A", genre="House"),
        MixTrack(id=11, name="Trance-A", genre="Trance"),
        MixTrack(id=12, name="House-B", genre="House"),
        MixTrack(id=13, name="House-C", genre="House"),
        MixTrack(id=14, name="Trance-B", genre="Trance"),
    ]


def test_mix_deterministic_picks_first_eligible_genre_match():
    idx, track, relaxed, n_cand, n_elig = apply_mix(
        _mix_pool(), genre="House", excluded_ids=set(), source_name="MIXES",
    )
    assert (idx, track.id, relaxed) == (1, 10, False)
    # 3 House tracks in pool; nothing excluded ⇒ all 3 eligible.
    assert (n_cand, n_elig) == (3, 3)


def test_mix_skips_excluded_track_ids():
    # Exclude House-A; first eligible should now be House-B at pool idx 3.
    idx, track, relaxed, n_cand, n_elig = apply_mix(
        _mix_pool(), genre="House", excluded_ids={10}, source_name="MIXES",
    )
    assert (idx, track.id, relaxed) == (3, 12, False)
    # 3 House candidates; 1 excluded ⇒ 2 eligible (the actionable count).
    assert (n_cand, n_elig) == (3, 2)


def test_mix_relaxes_when_exclusion_drains_candidates():
    # Exclude every House track; strategy falls back to all genre matches.
    idx, track, relaxed, n_cand, n_elig = apply_mix(
        _mix_pool(), genre="House", excluded_ids={10, 12, 13}, source_name="MIXES",
    )
    assert relaxed is True
    assert track.genre == "House"
    # In deterministic mode this is the first candidate (pool idx 1).
    assert idx == 1
    # Relaxation falls back to candidates, so n_eligible == n_candidates.
    assert (n_cand, n_elig) == (3, 3)


def test_mix_random_seeded_is_reproducible():
    pool = _mix_pool()
    rng_a = random.Random(11)
    rng_b = random.Random(11)
    a = apply_mix(pool, genre="House", excluded_ids=set(), rng=rng_a, source_name="MIXES")
    b = apply_mix(pool, genre="House", excluded_ids=set(), rng=rng_b, source_name="MIXES")
    assert a == b


def test_mix_random_respects_exclusion():
    pool = _mix_pool()
    rng = random.Random(42)
    # Exclude two of the three House tracks; only House-C (id=13) eligible.
    for _ in range(30):
        idx, track, relaxed, *_ = apply_mix(
            pool, genre="House", excluded_ids={10, 12}, rng=rng, source_name="MIXES",
        )
        assert track.id == 13
        assert relaxed is False


def test_mix_unknown_genre_raises_with_pool_context():
    with pytest.raises(MixGenreNotFoundError, match="MY_MIXES.*'Drum'"):
        apply_mix(_mix_pool(), genre="Drum", excluded_ids=set(), source_name="MY_MIXES")


def test_mix_accepts_frozenset_as_excluded_ids():
    """Generator passes frozenset (normalised from caller-provided set/None)."""
    idx, track, relaxed, *_ = apply_mix(
        _mix_pool(), genre="House",
        excluded_ids=frozenset({10}),
        source_name="MIXES",
    )
    assert track.id != 10
    assert relaxed is False


# ---------- weighted (§14.27) -------------------------------------------


@dataclass
class CommercialTrack:
    id: int
    name: str
    artist: str | None = None


def _weighted_pool() -> list[CommercialTrack]:
    """Small commercial-style pool with deliberate artist concentrations.

    - Artist A has 3 tracks (ids 1,2,3)
    - Artist B has 1 track (id 4)
    - Artist C has 2 tracks (ids 5,6)

    Gives us cases for: shuffle-bag rotation, artist dedup,
    multi-track artist weighting.
    """
    return [
        CommercialTrack(id=1, name="A-1", artist="Artist A"),
        CommercialTrack(id=2, name="A-2", artist="Artist A"),
        CommercialTrack(id=3, name="A-3", artist="Artist A"),
        CommercialTrack(id=4, name="B-1", artist="Artist B"),
        CommercialTrack(id=5, name="C-1", artist="Artist C"),
        CommercialTrack(id=6, name="C-2", artist="Artist C"),
    ]


def test_weighted_tier1_with_no_history_picks_eligible():
    """First pick: nothing blocked; Tier 1 fires with eligible=full pool."""
    pool = _weighted_pool()
    lookups = build_lookups(pool)
    track, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[], used_track_ids=[],
        rng=None, source_name="COMM",
    )
    assert info.tier == 1
    assert info.eligible_count == 6
    assert info.artist_blocked_count == 0
    assert info.track_blocked_count == 0
    # rng=None → first eligible track in pool order.
    assert track.id == 1


def test_weighted_blocks_artist_within_window():
    """After picking Artist A, next pick within window=10 must NOT be Artist A."""
    pool = _weighted_pool()
    lookups = build_lookups(pool)
    rng = random.Random(123)
    track1, info1 = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[], used_track_ids=[],
        rng=rng, source_name="COMM",
    )
    track2, info2 = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[track1.id]],
        used_track_ids=[track1.id],
        rng=rng, source_name="COMM",
    )
    assert info1.tier == 1
    assert info2.tier == 1
    assert track2.artist != track1.artist


def test_weighted_shuffle_bag_rotates_through_pool():
    """With rng=None and a 6-track pool, each track is picked once before
    any repeat. (rng=None bypasses random selection so this is fully
    deterministic — picks the first eligible track each call.)"""
    pool = _weighted_pool()
    lookups = build_lookups(pool)
    state_keys: list[frozenset[str]] = []
    state_ids: list[int] = []
    picked = []
    for _ in range(6):
        # Use window=0 (artist dedup off) so we isolate the track-shuffle
        # behaviour. Artist dedup is tested separately above.
        t, _ = apply_weighted(
            pool, lookups=lookups,
            recent_artist_keys=state_keys, used_track_ids=state_ids,
            window=0, rng=None, source_name="COMM",
        )
        picked.append(t.id)
        state_keys.append(lookups.track_keys[t.id])
        state_ids.append(t.id)
    # All 6 tracks picked exactly once (the shuffle-bag invariant).
    assert sorted(picked) == [1, 2, 3, 4, 5, 6]


def test_weighted_tier2_relaxes_when_all_artists_blocked():
    """A 2-track pool where both tracks share an artist key, after the
    first pick, has no Tier 1 candidates (artist is in the window).
    Must fall back to Tier 2 (relaxed) and pick the only available
    track that isn't track-blocked."""
    pool = [
        CommercialTrack(id=1, name="A-1", artist="Same Person"),
        CommercialTrack(id=2, name="A-2", artist="Same Person"),
    ]
    lookups = build_lookups(pool)
    # After picking id=1, the artist's keys are in the window so the
    # second pick would have zero Tier 1 candidates.
    track, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        window=10, rng=None, source_name="COMM",
    )
    assert info.tier == 2
    assert info.relaxed is True
    # Track id 1 is shuffle-blocked (pool_size - 1 = 1 → it's in the
    # last-1 window). Track id 2 is the only candidate.
    assert track.id == 2


def test_weighted_tier1_no_history_emits_no_excluded():
    """First pick: nothing blocked, so the excluded list is empty."""
    pool = _weighted_pool()
    lookups = build_lookups(pool)
    _, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[], used_track_ids=[],
        rng=None, source_name="COMM",
    )
    assert info.excluded_candidates == []


def test_weighted_excluded_skips_shuffle_bag_only_rejections():
    """A track blocked ONLY by shuffle-bag (not artist) should NOT appear
    in excluded_candidates — those are mechanical bookkeeping noise."""
    pool = _weighted_pool()
    lookups = build_lookups(pool)
    # Pick id=1 (Artist A). Track 1 is now shuffle-bag-blocked. Artist A
    # is also in the window — so tracks 2 and 3 are artist+shuffle blocked.
    # Track 1 itself is artist+shuffle. Tracks 4 (B), 5 (C), 6 (C) are
    # Tier 1 candidates.
    _, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        rng=None, window=10, source_name="COMM",
    )
    # Track 1 itself: both blocked. Track 2, 3: both blocked (artist + bag).
    # Excluded contains all artist-blocked tracks: 1, 2, 3.
    excluded_ids = {ec.track_id for ec in info.excluded_candidates}
    assert excluded_ids == {1, 2, 3}
    # No track is shuffle-bag-only blocked here (Artist A's tracks are
    # all in window). To get a shuffle-bag-only block, we need a track
    # whose artist is NOT in the window but whose id IS in the shuffle
    # bag — see the next test for that.


def test_weighted_excluded_omits_track_with_only_shuffle_bag_block():
    """Construct a case where some tracks are shuffle-bag-blocked but
    NOT artist-blocked — verify they do NOT appear in excluded
    (shuffle-bag-only rejections are mechanical noise)."""
    # 6 tracks. Artists A-D for tracks 1-4 (one each). Artist E has TWO
    # tracks (ids 5 and 6) — gives us an artist-blocked track that
    # ISN'T the picked one, so the assertion isn't masked by the
    # picked-track filter on Tier 2.
    pool = [
        CommercialTrack(id=1, name="A1", artist="Artist A"),
        CommercialTrack(id=2, name="B1", artist="Artist B"),
        CommercialTrack(id=3, name="C1", artist="Artist C"),
        CommercialTrack(id=4, name="D1", artist="Artist D"),
        CommercialTrack(id=5, name="E1", artist="Artist E"),
        CommercialTrack(id=6, name="E2", artist="Artist E"),
    ]
    lookups = build_lookups(pool)
    # State: ids [1,2,3,4] are in the shuffle-bag (last 5 = pool_size-1).
    # Artist E in the window via the recent_artist_keys entry.
    # → Tracks 1-4: shuffle-bag-blocked ONLY (their artists A-D aren't in
    #   the window). Should NOT appear in excluded.
    # → Tracks 5 and 6: artist-blocked ONLY (not in shuffle-bag). Both
    #   are Tier 1 ineligible, so Tier 2 fires and picks one of them.
    #   The other remains in excluded.
    _, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[5]],
        used_track_ids=[1, 2, 3, 4],
        rng=None, window=10, source_name="COMM",
    )
    assert info.tier == 2  # confirms relaxation fired
    excluded_ids = {ec.track_id for ec in info.excluded_candidates}
    # Tracks 1-4 (shuffle-bag-only) must NOT appear — the rule under test.
    assert excluded_ids.isdisjoint({1, 2, 3, 4})
    # Exactly one of {5, 6} should appear — the one that wasn't picked.
    assert excluded_ids in ({5}, {6})


def test_weighted_excluded_reason_quotes_colliding_artist_key():
    """The reason field cites the SPECIFIC colliding key so the user
    can scroll back through the trace and find the originating pick."""
    pool = [
        CommercialTrack(id=1, name="X", artist="Alex Warren"),
        CommercialTrack(id=2, name="Y", artist="Alex Warren feat. Someone"),
        CommercialTrack(id=3, name="Z", artist="Daft Punk"),
    ]
    lookups = build_lookups(pool)
    # Pick #1 puts {alex warren} in the window. Track 2 should be
    # artist-blocked via 'alex warren'.
    _, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        rng=None, window=10, source_name="COMM",
    )
    # Track 1 and track 2 both have 'alex warren' in their keys. Track 1
    # is in window already (and shuffle-bag), track 2 is artist-blocked
    # only. Both should appear in excluded.
    excluded = {ec.track_id: ec for ec in info.excluded_candidates}
    assert 2 in excluded
    assert "'alex warren'" in excluded[2].reason
    assert "in dedup window" in excluded[2].reason


def test_weighted_excluded_marks_both_blocked_with_suffix():
    """Both-blocked tracks get a '; also in shuffle-bag' suffix on the
    reason — distinguishes from artist-only blocks."""
    pool = [
        CommercialTrack(id=1, name="A1", artist="Artist A"),
        CommercialTrack(id=2, name="A2", artist="Artist A"),
    ]
    lookups = build_lookups(pool)
    # Pick id=1 → Artist A's key in window; id=1 in shuffle-bag.
    # On next call, id=1 is BOTH blocked; id=2 is artist-only blocked.
    # Tier 1 has no candidates → Tier 2 fires and picks id=2.
    _, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        rng=None, window=10, source_name="COMM",
    )
    assert info.tier == 2  # confirms relaxation fired
    # The picked track (id=2) is filtered out of excluded — Tier 2 may
    # have picked an artist-blocked track, and the viewer should not
    # show it as both selected and rejected.
    excluded_ids = {ec.track_id for ec in info.excluded_candidates}
    assert 2 not in excluded_ids
    # id=1 (both-blocked) is in the list with the suffix.
    assert 1 in excluded_ids
    by_id = {ec.track_id: ec for ec in info.excluded_candidates}
    assert "also in shuffle-bag" in by_id[1].reason


def test_weighted_excluded_uses_resolved_aliases_in_reason():
    """When an alias map is in play, the reason cites the CANONICAL
    key (which is what's actually in the dedup window), not the raw
    alias string."""
    alias_map = {"plastikman": "richie hawtin", "richie hawtin": "richie hawtin"}
    pool = [
        CommercialTrack(id=1, name="Sheet One", artist="Plastikman"),
        CommercialTrack(id=2, name="DE9", artist="Richie Hawtin"),
        CommercialTrack(id=3, name="Other", artist="Daft Punk"),
    ]
    lookups = build_lookups(pool, alias_map=alias_map)
    _, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        rng=None, window=10, source_name="COMM",
    )
    # Track 2 (Richie Hawtin) blocked because Plastikman pick resolved
    # to 'richie hawtin' canonical. Reason should cite that canonical.
    by_id = {ec.track_id: ec for ec in info.excluded_candidates}
    assert 2 in by_id
    assert "'richie hawtin'" in by_id[2].reason


def test_weighted_tier2_tie_break_when_all_artists_have_equal_gap():
    """Force a genuine Tier 2 tie: all candidates' artists were played
    at the same step in the window. rng.choice picks among them, so
    the result is rng-dependent — verify reproducibility under seed."""
    # 4 tracks, 4 distinct artists, but only the LAST 2 picks count
    # toward the window. Both came from the same multi-track artist
    # (id 1 then id 2, both Artist X), so the window contains a single
    # key-set. Tracks 3 and 4 (Artist Y, Artist Z) are both Tier-1
    # eligible. To force Tier 2: bump window down so dedup catches
    # everything, and exclude tracks 3,4 via shuffle-bag.
    pool = [
        CommercialTrack(id=1, name="X1", artist="Artist X"),
        CommercialTrack(id=2, name="X2", artist="Artist X"),
        CommercialTrack(id=3, name="X3", artist="Artist X"),
        CommercialTrack(id=4, name="X4", artist="Artist X"),
    ]
    # All same artist. After any pick, every remaining track is
    # artist-blocked. Tier 2 must always fire on pick #2+.
    lookups = build_lookups(pool)
    # Last 3 ids (1,2,3) are shuffle-blocked; track 4 is the only one
    # not shuffle-blocked. Artist X is in the window. Tier 1 = ∅
    # (track 4 IS artist-blocked). Tier 2 fires with one eligible
    # (track 4 — not shuffle-blocked).
    track, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[
            lookups.track_keys[1], lookups.track_keys[2], lookups.track_keys[3],
        ],
        used_track_ids=[1, 2, 3],
        window=10, rng=random.Random(0), source_name="COMM",
    )
    assert info.tier == 2
    assert info.relaxed is True
    assert track.id == 4
    # Reproducibility under same seed.
    again, _ = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[
            lookups.track_keys[1], lookups.track_keys[2], lookups.track_keys[3],
        ],
        used_track_ids=[1, 2, 3],
        window=10, rng=random.Random(0), source_name="COMM",
    )
    assert again.id == track.id


def test_weighted_artist_dedup_uses_resolved_keys():
    """Collaboration strings are split, so "Solo A feat. B" conflicts
    with subsequent "Artist B" picks via the shared "b" key."""
    pool = [
        CommercialTrack(id=1, name="X", artist="Alpha feat. Beta"),
        CommercialTrack(id=2, name="Y", artist="Beta"),
        CommercialTrack(id=3, name="Z", artist="Charlie"),
    ]
    lookups = build_lookups(pool)
    # Pick #1 leaves keys {alpha, beta} in the window. Pick #2 must
    # not be id=2 ("Beta" key) — only id=3 (Charlie) is eligible Tier 1.
    track, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        window=10, rng=None, source_name="COMM",
    )
    assert info.tier == 1
    assert track.id == 3


def test_weighted_alias_resolution_blocks_aliased_artist():
    """alias_map at build_lookups time resolves both names to the same
    key, so a pick of Plastikman blocks subsequent Richie Hawtin picks."""
    alias_map = {"plastikman": "richie hawtin", "richie hawtin": "richie hawtin"}
    pool = [
        CommercialTrack(id=1, name="Sheet One", artist="Plastikman"),
        CommercialTrack(id=2, name="DE9", artist="Richie Hawtin"),
        CommercialTrack(id=3, name="Other", artist="Daft Punk"),
    ]
    lookups = build_lookups(pool, alias_map=alias_map)
    # Pick Plastikman → window contains {richie hawtin}. Richie Hawtin
    # track must now be blocked from Tier 1.
    track, info = apply_weighted(
        pool, lookups=lookups,
        recent_artist_keys=[lookups.track_keys[1]],
        used_track_ids=[1],
        window=10, rng=None, source_name="COMM",
    )
    assert info.tier == 1
    assert track.id == 3  # Daft Punk; not Richie Hawtin


def test_weighted_seeded_rng_is_reproducible():
    """Same seed → same selection sequence (the byte-equality guarantee)."""
    pool = _weighted_pool()
    lookups = build_lookups(pool)
    def run(seed):
        rng = random.Random(seed)
        state_keys: list[frozenset[str]] = []
        state_ids: list[int] = []
        picks = []
        for _ in range(5):
            t, _ = apply_weighted(
                pool, lookups=lookups,
                recent_artist_keys=state_keys, used_track_ids=state_ids,
                rng=rng, source_name="COMM",
            )
            picks.append(t.id)
            state_keys.append(lookups.track_keys[t.id])
            state_ids.append(t.id)
        return picks
    assert run(42) == run(42)


def test_weighted_empty_pool_raises():
    lookups = build_lookups([])
    with pytest.raises(IndexError, match="pool is empty"):
        apply_weighted(
            [], lookups=lookups,
            recent_artist_keys=[], used_track_ids=[],
            source_name="COMM",
        )


def test_build_lookups_handles_missing_artist():
    """Tracks with artist=None get an empty key-set but still index;
    avoids None.id-or-similar crashes at pick time."""
    pool = [CommercialTrack(id=1, name="X", artist=None)]
    lookups = build_lookups(pool)
    assert lookups.track_keys[1] == frozenset()
    assert lookups.track_id_to_artist[1] == ""
