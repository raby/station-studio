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
