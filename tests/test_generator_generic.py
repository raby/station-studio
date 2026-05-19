"""Generic generator tests — prove the engine is not Classics-shaped.

These tests exercise templates that look nothing like Rebirth Classics:
minimal 1-row/1-block stations, pick-only layouts, mixed window+pick rows,
>2 content sources, non-day units, flat cursors, single-week cycles, etc.

Each test is a small inline template dict parsed via parse_template, with
synthetic pools constructed on the spot. No external files.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from engine.template import parse_template
from engine.generator import generate


# ---------- helpers ------------------------------------------------------


@dataclass
class FakeTrack:
    id: int
    name: str
    artist: str | None = None
    # Optional — only the §14.24 jingle tests set this. Default None
    # keeps every pre-existing test's pool layout untouched.
    genre: str | None = None


def mk_pool(prefix: str, size: int) -> list[FakeTrack]:
    return [FakeTrack(id=i, name=f"{prefix}{i:04d}") for i in range(1, size + 1)]


def mk_template(**overrides) -> dict[str, Any]:
    """Build a minimal valid template, override specific keys for variety."""
    base = {
        "station": "test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "A", "colour": "#111", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 10, "cycle_weeks": 4},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "window", "size": 3}],
        },
    }
    base.update(overrides)
    return base


# ---------- minimal template --------------------------------------------


def test_minimal_station_1_row_1_block():
    t = parse_template(mk_template())
    pools = {"A": mk_pool("A", 100)}
    r = generate(t, pools, week=1)
    assert len(r.picks) == 3
    assert r.trace.slots[0].unit == "Mon"
    assert r.trace.slots[0].row == 1
    assert r.trace.slots[0].block == 1


# ---------- pick-only ----------------------------------------------------


def test_pick_only_station_sequential():
    t = parse_template(mk_template(
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "pick", "size": 5},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 20)}
    r = generate(t, pools, week=1)
    # Sequential pick from cursor 0 -> pool indices 1..5
    assert [p.name for p in r.picks] == ["A0001", "A0002", "A0003", "A0004", "A0005"]
    assert [s.pool_index for s in r.trace.slots] == [1, 2, 3, 4, 5]
    assert all(s.strategy == "pick" for s in r.trace.slots)


# ---------- mixed window + pick in the same row ------------------------


def test_mixed_window_and_pick_in_same_row():
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
        ],
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 3},
                {"source": "B", "strategy": "pick",   "size": 2},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 50), "B": mk_pool("B", 50)}
    r = generate(t, pools, week=1)
    assert [p.name for p in r.picks] == ["A0001", "A0002", "A0003", "B0001", "B0002"]
    # Strategies per slot match the block spec
    assert [s.strategy for s in r.trace.slots] == ["window", "window", "window", "pick", "pick"]
    # Blocks numbered 1 and 2 within the row
    assert [s.block for s in r.trace.slots] == [1, 1, 1, 2, 2]


# ---------- >2 content sources ------------------------------------------


def test_four_content_sources():
    sources = [{"name": n, "colour": "#000", "role": "content"} for n in ("A", "B", "C", "D")]
    row_template = [{"source": n, "strategy": "window", "size": 2} for n in ("A", "B", "C", "D")]
    t = parse_template(mk_template(sources=sources, layout={
        "unit": "day",
        "unit_labels": ["Mon"],
        "row_start_offsets": [0],
        "row_template": row_template,
    }))
    pools = {n: mk_pool(n, 10) for n in ("A", "B", "C", "D")}
    r = generate(t, pools, week=1)
    assert len(r.picks) == 8
    assert [s.source for s in r.trace.slots] == ["A", "A", "B", "B", "C", "C", "D", "D"]


# ---------- non-day unit (hourly show) -----------------------------------


def test_non_day_unit_labels():
    t = parse_template(mk_template(
        layout={
            "unit": "hour",
            "unit_labels": ["06:00", "07:00", "08:00"],
            "row_start_offsets": [0, 5, 10],
            "row_template": [{"source": "A", "strategy": "window", "size": 2}],
        },
    ))
    pools = {"A": mk_pool("A", 50)}
    r = generate(t, pools, week=1)
    assert [s.unit for s in r.trace.slots] == ["06:00", "06:00", "07:00", "07:00", "08:00", "08:00"]


# ---------- flat cursor (schedule never shifts) --------------------------


def test_flat_cursor_identical_every_week():
    t = parse_template(mk_template(
        cursor={"base_advance_per_week": 0, "cycle_weeks": 1},
    ))
    pools = {"A": mk_pool("A", 50)}
    r1 = generate(t, pools, week=1)
    r5 = generate(t, pools, week=5)
    assert [p.name for p in r1.picks] == [p.name for p in r5.picks]


# ---------- single-week cycle -------------------------------------------


def test_single_week_cycle_wraps_immediately():
    """cycle_weeks=1 means every week has the same base_cursor (0)."""
    t = parse_template(mk_template(
        cursor={"base_advance_per_week": 7, "cycle_weeks": 1},
    ))
    pools = {"A": mk_pool("A", 50)}
    r1 = generate(t, pools, week=1)
    r2 = generate(t, pools, week=2)
    # cycle_weeks=1 -> base is always 0 -> identical
    assert [p.name for p in r1.picks] == [p.name for p in r2.picks]


# ---------- cycle wrap at arbitrary length -------------------------------


def test_cycle_wrap_at_seven_weeks():
    t = parse_template(mk_template(
        cursor={"base_advance_per_week": 3, "cycle_weeks": 7},
    ))
    pools = {"A": mk_pool("A", 100)}
    r1 = generate(t, pools, week=1)
    r8 = generate(t, pools, week=8)
    assert [p.name for p in r1.picks] == [p.name for p in r8.picks]


# ---------- cursor progression across many weeks -------------------------


def test_cursor_advances_correctly_per_week():
    t = parse_template(mk_template(
        cursor={"base_advance_per_week": 5, "cycle_weeks": 10},
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "window", "size": 1}],
        },
    ))
    pools = {"A": mk_pool("A", 100)}
    for week in range(1, 11):
        r = generate(t, pools, week=week)
        expected_pool_index = (week - 1) * 5 + 1
        assert r.trace.slots[0].pool_index == expected_pool_index, \
            f"week {week}: expected {expected_pool_index}, got {r.trace.slots[0].pool_index}"


# ---------- multi-row with varied offsets --------------------------------


def test_row_start_offsets_applied_correctly():
    t = parse_template(mk_template(
        layout={
            "unit": "slot",
            "unit_labels": ["r1", "r2", "r3"],
            "row_start_offsets": [0, 100, 50],
            "row_template": [{"source": "A", "strategy": "window", "size": 1}],
        },
    ))
    pools = {"A": mk_pool("A", 200)}
    r = generate(t, pools, week=1)
    assert [s.pool_index for s in r.trace.slots] == [1, 101, 51]


# ---------- trace integrity ---------------------------------------------


def test_trace_slot_count_always_matches_picks():
    t = parse_template(mk_template(
        layout={
            "unit": "day",
            "unit_labels": ["A", "B", "C"],
            "row_start_offsets": [0, 10, 20],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 4},
                {"source": "A", "strategy": "pick",   "size": 3},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 50)}
    r = generate(t, pools, week=1)
    assert len(r.picks) == 3 * (4 + 3)
    assert len(r.trace.slots) == len(r.picks)


# ---------- §14.22: sequential strategy + named cursors -----------------


def test_sequential_block_starts_at_cursor_init_in_week1():
    t = parse_template(mk_template(
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "cursors_init": {"c1": 20},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 3, "cursor_id": "c1"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 100)}
    r = generate(t, pools, week=1)
    # init=20, advance=0 in week 1 -> pool indices 21..23
    assert [s.pool_index for s in r.trace.slots] == [21, 22, 23]
    assert all(s.strategy == "sequential" for s in r.trace.slots)


def test_sequential_block_advances_with_base_advance():
    t = parse_template(mk_template(
        cursor={"base_advance_per_week": 10, "cycle_weeks": 0},  # no wrap
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "cursors_init": {"c1": 5},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 2, "cursor_id": "c1"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 200)}
    # week 1: start=5, picks 6..7
    # week 2: start=15, picks 16..17
    # week 5: start=45, picks 46..47 (no wrap because cycle_weeks=0)
    assert [s.pool_index for s in generate(t, pools, week=1).trace.slots] == [6, 7]
    assert [s.pool_index for s in generate(t, pools, week=2).trace.slots] == [16, 17]
    assert [s.pool_index for s in generate(t, pools, week=5).trace.slots] == [46, 47]


def test_sequential_block_ignores_row_start_offset():
    """Sequential blocks address by named cursor, not row offset."""
    t = parse_template(mk_template(
        cursor={"base_advance_per_week": 0, "cycle_weeks": 1},
        layout={
            "unit": "day",
            "unit_labels": ["A", "B"],
            "row_start_offsets": [0, 999],  # would blow past pool if applied
            "cursors_init": {"c1": 10},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "c1"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 50)}
    r = generate(t, pools, week=1)
    # Both rows draw from cursor c1 (offset 10) — row 2 does NOT use 999.
    assert [s.pool_index for s in r.trace.slots] == [11, 11]


def test_multi_cursor_template_each_cursor_addresses_independently():
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
        ],
        cursor={"base_advance_per_week": 0, "cycle_weeks": 1},
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "cursors_init": {"a_cur": 5, "b_cur": 30},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 2, "cursor_id": "a_cur"},
                {"source": "B", "strategy": "sequential", "size": 2, "cursor_id": "b_cur"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 50), "B": mk_pool("B", 50)}
    r = generate(t, pools, week=1)
    assert [p.name for p in r.picks] == ["A0006", "A0007", "B0031", "B0032"]


def test_sequential_coexists_with_window_in_same_template():
    """Backward-compat: window+sequential side-by-side both work."""
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
        ],
        cursor={"base_advance_per_week": 0, "cycle_weeks": 1},
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "cursors_init": {"b_cur": 40},
            "row_template": [
                {"source": "A", "strategy": "window", "size": 3},
                {"source": "B", "strategy": "sequential", "size": 2, "cursor_id": "b_cur"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 50), "B": mk_pool("B", 50)}
    r = generate(t, pools, week=1)
    assert [s.pool_index for s in r.trace.slots] == [1, 2, 3, 41, 42]
    assert [s.strategy for s in r.trace.slots] == ["window", "window", "window", "sequential", "sequential"]


def test_trace_ids_are_unique_across_generations():
    t = parse_template(mk_template())
    pools = {"A": mk_pool("A", 50)}
    seen = {generate(t, pools, week=w).trace.trace_id for w in range(1, 6)}
    assert len(seen) == 5


# ---------- §14.24: jingle strategy via generator ----------------------


def _mk_jingle_pool() -> list[FakeTrack]:
    """A small pool with multiple genres + some untagged entries."""
    return [
        FakeTrack(id=1, name="Day-1",   genre="Daytime"),
        FakeTrack(id=2, name="Night-1", genre="Nighttime"),
        FakeTrack(id=3, name="Day-2",   genre="Daytime"),
        FakeTrack(id=4, name="Notag",   genre=None),
        FakeTrack(id=5, name="Day-3",   genre="Daytime"),
    ]


def test_jingle_strategy_picks_first_genre_match_when_rng_off():
    t = parse_template(mk_template(
        rng="off",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
            ],
        },
    ))
    r = generate(t, {"A": _mk_jingle_pool()}, week=1)
    assert len(r.picks) == 1
    assert r.picks[0].name == "Day-1"
    # Trace records 1-indexed pool position of the matched track.
    assert r.trace.slots[0].pool_index == 1
    assert r.trace.slots[0].strategy == "jingle"
    # Reason captures the genre + the mode for debuggability.
    assert "genre='Daytime'" in r.trace.slots[0].reason
    assert "first match" in r.trace.slots[0].reason


def test_jingle_strategy_random_seeded_is_reproducible():
    t = parse_template(mk_template(
        rng="on",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
            ],
        },
    ))
    pools = {"A": _mk_jingle_pool()}
    r1 = generate(t, pools, week=1, seed=99)
    r2 = generate(t, pools, week=1, seed=99)
    assert [p.id for p in r1.picks] == [p.id for p in r2.picks]
    # Whatever was picked must carry the requested genre.
    assert r1.picks[0].genre == "Daytime"


def test_jingle_strategy_unknown_genre_propagates_error():
    t = parse_template(mk_template(
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "jingle", "size": 1, "genre": "NoSuchGenre"},
            ],
        },
    ))
    # Generator surfaces the underlying JingleGenreNotFoundError as an
    # IndexError subclass — same shape as window's out-of-range error
    # so callers handle "missing pool data" uniformly.
    with pytest.raises(IndexError, match="no track with genre 'NoSuchGenre'"):
        generate(t, {"A": _mk_jingle_pool()}, week=1)


def test_jingle_coexists_with_window_in_same_row():
    """Backward-compat check: jingle blocks slot in alongside other strategies."""
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "J", "colour": "#fc0", "role": "content"},
        ],
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 2},
                {"source": "J", "strategy": "jingle", "size": 1, "genre": "Daytime"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 10), "J": _mk_jingle_pool()}
    r = generate(t, pools, week=1)
    # Window pulls A0001/A0002 from source A's row start; jingle pulls
    # first 'Daytime' track from source J. Independent pool addressing.
    assert [p.name for p in r.picks] == ["A0001", "A0002", "Day-1"]
    assert [s.strategy for s in r.trace.slots] == ["window", "window", "jingle"]
    # Slot 3's source is the jingle pool, not the window pool.
    assert r.trace.slots[2].source == "J"


# ---------- §14.27: weighted strategy via generator --------------------


def _weighted_pool_with_artists() -> list[FakeTrack]:
    return [
        FakeTrack(id=201, name="A-1", artist="Artist A"),
        FakeTrack(id=202, name="A-2", artist="Artist A"),
        FakeTrack(id=203, name="B-1", artist="Artist B"),
        FakeTrack(id=204, name="C-1", artist="Artist C"),
    ]


def _weighted_template(size: int = 1, window=None) -> dict:
    """A single-block weighted template; size + window configurable."""
    block: dict = {"source": "A", "strategy": "weighted", "size": size}
    if window is not None:
        block["window"] = window
    return mk_template(
        rng="on",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [block],
        },
    )


def test_weighted_generator_picks_one_track_at_size_1():
    t = parse_template(_weighted_template(size=1))
    r = generate(t, {"A": _weighted_pool_with_artists()}, week=1, seed=42)
    assert len(r.picks) == 1
    assert r.trace.slots[0].strategy == "weighted"
    assert "weighted tier 1" in r.trace.slots[0].reason


def test_weighted_generator_state_persists_across_size_iterations():
    """A single weighted block with size=4 should NOT repeat artists
    within the window (state mutates between iterations of the same block)."""
    t = parse_template(_weighted_template(size=4))
    r = generate(t, {"A": _weighted_pool_with_artists()}, week=1, seed=42)
    artists = [p.artist for p in r.picks]
    # Pool has 4 tracks across 3 artists (A×2, B, C). The shuffle-bag
    # window is pool_size-1 = 3, so the only legal repeat is when the
    # 4th pick falls outside the 3-pick history — which it doesn't.
    # Therefore all 4 tracks are distinct.
    assert len(set(p.id for p in r.picks)) == 4


def test_weighted_generator_seeded_run_reproducible():
    t = parse_template(_weighted_template(size=4))
    pool = _weighted_pool_with_artists()
    r1 = generate(t, {"A": pool}, week=1, seed=99)
    r2 = generate(t, {"A": pool}, week=1, seed=99)
    assert [p.id for p in r1.picks] == [p.id for p in r2.picks]


def test_weighted_window_default_kwarg_overrides_engine_constant():
    """§14.29: weighted_window_default kwarg sits BETWEEN per-block
    BlockSpec.window and the engine constant. When the block doesn't
    set its own window, the kwarg value wins over the engine default."""
    t = parse_template(_weighted_template(size=1))  # no window set
    r = generate(
        t, {"A": _weighted_pool_with_artists()},
        week=1, seed=42, weighted_window_default=25,
    )
    # Trace reason carries the effective window for diagnostics.
    assert "window=25" in r.trace.slots[0].reason


def test_weighted_block_window_overrides_station_default():
    """Precedence: per-block window beats the station default kwarg."""
    t = parse_template(_weighted_template(size=1, window=5))
    r = generate(
        t, {"A": _weighted_pool_with_artists()},
        week=1, seed=42, weighted_window_default=25,
    )
    assert "window=5" in r.trace.slots[0].reason


def test_weighted_falls_back_to_engine_constant_when_neither_set():
    """No per-block window AND no station default → engine constant (10)."""
    t = parse_template(_weighted_template(size=1))
    r = generate(
        t, {"A": _weighted_pool_with_artists()},
        week=1, seed=42,
        # weighted_window_default unset
    )
    assert "window=10" in r.trace.slots[0].reason


def test_weighted_generator_default_window_is_10():
    """No explicit window → default 10 surfaces in trace reason."""
    t = parse_template(_weighted_template(size=1))  # no window field
    r = generate(t, {"A": _weighted_pool_with_artists()}, week=1, seed=1)
    assert "window=10" in r.trace.slots[0].reason


def test_weighted_generator_explicit_window_overrides_default():
    t = parse_template(_weighted_template(size=1, window=5))
    r = generate(t, {"A": _weighted_pool_with_artists()}, week=1, seed=1)
    assert "window=5" in r.trace.slots[0].reason


def test_weighted_generator_alias_map_threads_through():
    """alias_map kwarg on generate() reaches apply_weighted's lookups,
    so an aliased Plastikman pick blocks a subsequent Richie Hawtin pick."""
    pool = [
        FakeTrack(id=301, name="Sheet One", artist="Plastikman"),
        FakeTrack(id=302, name="DE9", artist="Richie Hawtin"),
        FakeTrack(id=303, name="Other", artist="Daft Punk"),
    ]
    t = parse_template(_weighted_template(size=2, window=10))
    r = generate(
        t, {"A": pool}, week=1, seed=42,
        alias_map={"plastikman": "richie hawtin", "richie hawtin": "richie hawtin"},
    )
    artists = [p.artist for p in r.picks]
    # The two picks must not both come from the Plastikman/Richie Hawtin
    # equivalence class; Daft Punk has to appear.
    assert "Daft Punk" in artists


def test_weighted_coexists_with_window_and_jingle_strategies():
    """Smoke test: weighted alongside other strategies in one template."""
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "J", "colour": "#fc0", "role": "content"},
            {"name": "W", "colour": "#0cf", "role": "content"},
        ],
        rng="on",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 1},
                {"source": "J", "strategy": "jingle", "size": 1, "genre": "Daytime"},
                {"source": "W", "strategy": "weighted", "size": 1, "window": 5},
            ],
        },
    ))
    pools = {
        "A": mk_pool("A", 10),
        "J": _mk_jingle_pool(),
        "W": _weighted_pool_with_artists(),
    }
    r = generate(t, pools, week=1, seed=42)
    assert [s.strategy for s in r.trace.slots] == ["window", "jingle", "weighted"]
    assert len(r.picks) == 3


def test_weighted_generator_threads_excluded_into_trace_slot():
    """§14.23 + §14.27 integration: artist-blocked candidates from
    apply_weighted reach TraceSlot.excluded so the viewer can render
    them."""
    pool = [
        FakeTrack(id=401, name="X", artist="Artist X"),
        FakeTrack(id=402, name="X2", artist="Artist X"),  # same artist as 401
        FakeTrack(id=403, name="Y", artist="Artist Y"),
    ]
    # size=2 ensures the second pick has prior state to dedup against.
    t = parse_template(_weighted_template(size=2, window=10))
    r = generate(t, {"A": pool}, week=1, seed=42)

    # Slot 1: no prior state, no excluded.
    assert r.trace.slots[0].excluded == []
    # Slot 2: pick #1 placed Artist X's key in the window. Whichever
    # track wasn't picked by slot 1 (id=401 or 402, whichever wasn't
    # chosen by the weighted rng + the picked-track-stays out of
    # excluded rule) should appear in slot 2's excluded list.
    slot2 = r.trace.slots[1]
    excluded_ids = {ec.track_id for ec in slot2.excluded}
    # At least one Artist X track is recorded as artist-blocked.
    assert excluded_ids & {401, 402}
    # The picked track itself is not in its own excluded list.
    assert r.picks[1].id not in excluded_ids
    # The reason cites the artist key (resolved to lowercase).
    sample = next(iter(slot2.excluded))
    assert "artist key" in sample.reason or "artist keys" in sample.reason
    assert "in dedup window" in sample.reason


def test_weighted_dedup_does_not_pollute_across_generations():
    """Two separate generate() calls should not share state — each gets
    a fresh _GenState. Tested by running the same seeded generation
    twice; both must produce identical pick sequences."""
    t = parse_template(_weighted_template(size=4))
    pool = _weighted_pool_with_artists()
    r1 = generate(t, {"A": pool}, week=1, seed=7)
    r2 = generate(t, {"A": pool}, week=2, seed=7)
    # Same template + seed + pool → same picks across week boundary
    # (no shared state).
    assert [p.id for p in r1.picks] == [p.id for p in r2.picks]


# ---------- §14.28: prev_ref strategy via generator --------------------


def _prev_ref_pool_with_genres() -> list[FakeTrack]:
    """A small source pool with two genre-tagged mix-style tracks +
    one that won't appear in any previous-playlist scenario."""
    return [
        FakeTrack(id=500, name="House-1", genre="House"),
        FakeTrack(id=501, name="House-2", genre="House"),
        FakeTrack(id=502, name="Trance-1", genre="Trance"),
    ]


def _prev_ref_template(*, position=1, fallback_genre="House", size=1) -> dict:
    block = {"source": "A", "strategy": "prev_ref", "size": size, "position": position}
    if fallback_genre is not None:
        block["fallback_genre"] = fallback_genre
    return mk_template(
        rng="off",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [block],
        },
    )


def test_prev_ref_mirror_uses_track_from_previous_playlist():
    """Happy path: previous playlist's slot at position is in our
    source pool → mirror succeeds, no fallback fires."""
    pool = _prev_ref_pool_with_genres()
    # Previous playlist: position 1 happens to be one of our pool tracks.
    previous = [pool[1]]  # id 501
    t = parse_template(_prev_ref_template(position=1))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous)
    assert r.picks[0].id == 501
    assert "mirror from previous playlist position 1" in r.trace.slots[0].reason
    # Mirrors do NOT record into prev_ref_fallback_mixes.
    assert r.prev_ref_fallback_mixes == []


def test_prev_ref_falls_back_to_mix_when_no_previous_playlist():
    """No previous → fallback path picks a mix from fallback_genre."""
    pool = _prev_ref_pool_with_genres()
    t = parse_template(_prev_ref_template(position=1, fallback_genre="House"))
    r = generate(t, {"A": pool}, week=1, previous_playlist=None)
    # First eligible House track (rng=off → deterministic first match).
    assert r.picks[0].id == 500
    assert "prev_ref fallback" in r.trace.slots[0].reason
    assert "no previous playlist" in r.trace.slots[0].reason
    assert "genre 'House'" in r.trace.slots[0].reason
    # Fallback to a mix MUST record into prev_ref_fallback_mixes.
    assert r.prev_ref_fallback_mixes == [(1, "House", pool[0])]


def test_prev_ref_falls_back_to_mix_when_resolved_track_not_in_source():
    """Previous playlist exists but the slot's track isn't in the
    source pool → fallback path fires with 'not in source' note."""
    pool = _prev_ref_pool_with_genres()
    foreign = FakeTrack(id=999, name="Unrelated", genre="Pop")
    previous = [foreign]
    t = parse_template(_prev_ref_template(position=1, fallback_genre="Trance"))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous)
    assert r.picks[0].id == 502
    assert "not in source 'A'" in r.trace.slots[0].reason
    assert r.prev_ref_fallback_mixes == [(1, "Trance", pool[2])]


def test_prev_ref_position_out_of_range_raises():
    """Template error — should surface as a generation failure, not
    silently skip the slot."""
    pool = _prev_ref_pool_with_genres()
    previous = [pool[0]]  # only one slot
    t = parse_template(_prev_ref_template(position=99, fallback_genre="House"))
    with pytest.raises(ValueError, match="prev_ref position 99 out of range"):
        generate(t, {"A": pool}, week=2, previous_playlist=previous)


def test_prev_ref_without_fallback_genre_uses_weighted_fallback():
    """§14.28 commit 2: when fallback_genre is absent AND the mirror
    misses, prev_ref defers to the weighted strategy. Uses the same
    state machinery as a regular weighted dispatch (lookups +
    _GenState mutation)."""
    pool = _weighted_pool_with_artists()  # 4 tracks across 3 artists
    t = parse_template(_prev_ref_template(position=1, fallback_genre=None))
    r = generate(t, {"A": pool}, week=1, previous_playlist=None, seed=42)
    assert len(r.picks) == 1
    # Track must come from the pool — weighted picks from `source`.
    assert r.picks[0].id in {201, 202, 203, 204}
    # Trace reason carries the weighted dispatch info, not a mix reason.
    assert "weighted" in r.trace.slots[0].reason
    assert "tier" in r.trace.slots[0].reason


def test_prev_ref_weighted_fallback_mutates_gen_state():
    """The weighted-fallback path must mutate _GenState so a
    subsequent regular weighted slot in the same generation respects
    the prev_ref pick's artist + track dedup."""
    pool = _weighted_pool_with_artists()
    t = parse_template(mk_template(
        sources=[{"name": "A", "colour": "#aaa", "role": "content"}],
        rng="on",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                # prev_ref with no fallback_genre → weighted-fallback path.
                {"source": "A", "strategy": "prev_ref", "size": 1, "position": 1},
                # Then a regular weighted slot: must NOT repeat artist or
                # track from the prev_ref's fallback pick.
                {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
            ],
        },
    ))
    r = generate(t, {"A": pool}, week=1, previous_playlist=None, seed=42)
    # Both slots picked from the same pool but should not repeat the
    # same track or share an artist key within the dedup window.
    assert r.picks[0].id != r.picks[1].id
    assert r.picks[0].artist != r.picks[1].artist


def test_prev_ref_fallback_mix_emits_excluded_for_failed_mirror():
    """§14.28 commit 2: when prev_ref falls back, the slot's TraceSlot
    .excluded carries one ExcludedCandidate describing the failed
    mirror (for the §14.23 viewer disclosure)."""
    pool = _prev_ref_pool_with_genres()
    foreign = FakeTrack(id=999, name="Unrelated", genre="Pop", artist="Pop Star")
    previous = [foreign]
    t = parse_template(_prev_ref_template(position=1, fallback_genre="House"))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous)
    # Fallback fired. The slot's excluded list has exactly one entry —
    # the failed-mirror diagnostic.
    slot = r.trace.slots[0]
    assert len(slot.excluded) == 1
    ec = slot.excluded[0]
    assert ec.track_id == 999
    assert ec.track_name == "Unrelated"
    assert ec.track_artist == "Pop Star"
    assert "not in source 'A'" in ec.reason
    assert "position 1" in ec.reason


def test_prev_ref_no_previous_emits_placeholder_excluded():
    """When no previous playlist exists, the excluded entry is a
    placeholder (track_id=None, descriptive reason) so the user sees
    the fallback rationale rather than wondering why excluded is empty."""
    pool = _prev_ref_pool_with_genres()
    t = parse_template(_prev_ref_template(position=1, fallback_genre="House"))
    r = generate(t, {"A": pool}, week=1, previous_playlist=None)
    slot = r.trace.slots[0]
    assert len(slot.excluded) == 1
    ec = slot.excluded[0]
    assert ec.track_id is None
    assert ec.track_name == "(no previous playlist)"
    assert "no previous playlist" in ec.reason


def test_prev_ref_weighted_fallback_combines_excluded_lists():
    """Weighted-fallback's slot.excluded = [failed-mirror entry]
    PLUS weighted's own artist-blocked rejections."""
    # Pool where the weighted dispatch will produce SOME artist-blocked
    # rejections: two tracks share an artist, so picking one blocks
    # the other from subsequent considerations.
    pool = [
        FakeTrack(id=601, name="X1", artist="Artist X"),
        FakeTrack(id=602, name="X2", artist="Artist X"),
        FakeTrack(id=603, name="Y1", artist="Artist Y"),
    ]
    foreign = FakeTrack(id=999, name="Foreign", genre="Pop", artist="None")
    previous = [foreign]
    t = parse_template(mk_template(
        sources=[{"name": "A", "colour": "#aaa", "role": "content"}],
        rng="on",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                # First a regular weighted to seed the dedup window with
                # Artist X — so the prev_ref-weighted-fallback will see
                # X-tracks as artist-blocked rejections.
                {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
                {"source": "A", "strategy": "prev_ref", "size": 1, "position": 1},
            ],
        },
    ))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous, seed=42)
    # Slot 2 is the prev_ref weighted-fallback. Its excluded list must
    # include at least the failed-mirror entry; if Artist X was already
    # in the dedup window, additional artist-blocked entries appear too.
    slot2 = r.trace.slots[1]
    # First entry is always the failed-mirror diagnostic.
    assert slot2.excluded[0].track_id == 999
    assert "not in source 'A'" in slot2.excluded[0].reason
    # Remaining entries (if any) are weighted's artist-blocked rejections.
    for ec in slot2.excluded[1:]:
        assert "dedup window" in ec.reason


def test_prev_ref_all_three_outcomes_interleaved_in_one_generation():
    """End-to-end smoke test: a single template with three prev_ref
    slots — one mirrors, one falls back to mix, one falls back to
    weighted — all in the same generation pass. Real Rebirth Radio
    templates have many prev_ref slots; this proves the dispatch
    correctly handles a mixture of outcomes interleaved (including
    state plumbing flowing through all three paths)."""
    # Pool that supports all three paths:
    # - Has a House-genre track (for fallback_genre='House' path)
    # - Has multiple Artists (for weighted-fallback path's dedup)
    # - At pool[0] sits a track that ALSO appears in the previous
    #   playlist at position 1 (for the mirror path)
    pool = [
        FakeTrack(id=701, name="Mirror-Target", artist="Artist M", genre="House"),
        FakeTrack(id=702, name="House-B", artist="Artist B", genre="House"),
        FakeTrack(id=703, name="House-C", artist="Artist C", genre="House"),
        FakeTrack(id=704, name="Filler", artist="Artist D", genre="Filler"),
    ]
    foreign = FakeTrack(id=999, name="Foreign", artist="Stranger", genre="Pop")
    # Previous playlist:
    #   position 1 → pool[0] (mirror hits — Artist M House)
    #   position 2 → foreign (not in source → fallback fires)
    #   position 3 → foreign (not in source → fallback fires)
    previous = [pool[0], foreign, foreign]

    t = parse_template(mk_template(
        sources=[{"name": "A", "colour": "#aaa", "role": "content"}],
        rng="on",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                # Slot 1: prev_ref position=1, with fallback_genre.
                # Mirror SUCCEEDS (pool[0] is in source).
                {"source": "A", "strategy": "prev_ref", "size": 1,
                 "position": 1, "fallback_genre": "House"},
                # Slot 2: prev_ref position=2, with fallback_genre.
                # Mirror MISSES (foreign not in source); FALLBACK_MIX fires.
                {"source": "A", "strategy": "prev_ref", "size": 1,
                 "position": 2, "fallback_genre": "House"},
                # Slot 3: prev_ref position=3, NO fallback_genre.
                # Mirror MISSES; FALLBACK_WEIGHTED fires.
                {"source": "A", "strategy": "prev_ref", "size": 1, "position": 3},
            ],
        },
    ))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous, seed=42)
    assert len(r.picks) == 3

    # Slot 1 — MIRROR: picked exactly what previous[0] held.
    assert r.picks[0].id == 701
    assert "mirror from previous playlist position 1" in r.trace.slots[0].reason
    assert r.trace.slots[0].excluded == []  # Mirrors emit no diagnostics.

    # Slot 2 — FALLBACK_MIX: picked SOME House track from the source.
    # With rng='on' the choice is rng.choice over the eligible set
    # (matching weekly), so the exact id depends on the seed, but
    # it must be a House track. Mirrors don't add to excluded_mix_ids
    # (no apply_mix call), so all 3 House tracks remain eligible.
    assert r.picks[1].genre == "House"
    assert r.picks[1].id in {701, 702, 703}
    assert "fallback" in r.trace.slots[1].reason
    assert "genre 'House'" in r.trace.slots[1].reason
    assert "not in source 'A'" in r.trace.slots[1].reason
    # Fallback-mix slot has the failed-mirror ExcludedCandidate.
    assert len(r.trace.slots[1].excluded) == 1
    assert r.trace.slots[1].excluded[0].track_id == 999  # the foreign track
    # The fallback-mix recorded in prev_ref_fallback_mixes for MixUsage.
    assert (2, "House", r.picks[1]) in r.prev_ref_fallback_mixes

    # Slot 3 — FALLBACK_WEIGHTED: no fallback_genre, weighted fallback
    # fires. The pick comes from the whole pool (genre-agnostic).
    assert r.picks[2].id in {701, 702, 703, 704}
    assert "weighted" in r.trace.slots[2].reason
    assert "tier" in r.trace.slots[2].reason
    # Slot 3's excluded list starts with the failed-mirror entry.
    assert r.trace.slots[2].excluded[0].track_id == 999

    # No regression: prev_ref_fallback_mixes has exactly one entry
    # (the FALLBACK_MIX from slot 2). FALLBACK_WEIGHTED doesn't record.
    assert len(r.prev_ref_fallback_mixes) == 1


def test_prev_ref_mirror_emits_no_excluded():
    """Successful mirrors don't emit ExcludedCandidate entries — there's
    no rejection to surface (the resolved track was used as-is)."""
    pool = _prev_ref_pool_with_genres()
    previous = [pool[1]]
    t = parse_template(_prev_ref_template(position=1))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous)
    assert r.trace.slots[0].excluded == []


def test_prev_ref_falls_back_respects_mix_exclusion():
    """The fallback path threads through excluded_mix_ids (same as a
    regular mix slot), so a recently-used track is excluded."""
    pool = _prev_ref_pool_with_genres()
    t = parse_template(_prev_ref_template(position=1, fallback_genre="House"))
    r = generate(
        t, {"A": pool}, week=1, previous_playlist=None,
        excluded_mix_ids={500},  # exclude House-1; should pick House-2
    )
    assert r.picks[0].id == 501
    assert r.prev_ref_fallback_mixes == [(1, "House", pool[1])]


def test_prev_ref_coexists_with_mix_and_window():
    """Smoke test: prev_ref alongside mix + window in the same template."""
    pool_a = mk_pool("A", 10)
    pool_b = _prev_ref_pool_with_genres()
    previous = [pool_b[0]]  # House-1 at position 1 of previous

    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#0cf", "role": "content"},
        ],
        rng="off",
        # Flat cursor (advance=0, cycle=1) so the window block stays at
        # pool index 0 across weeks. Otherwise mk_template's default
        # advance=10 + week=2 would push the window beyond pool A.
        cursor={"base_advance_per_week": 0, "cycle_weeks": 1},
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 1},
                {"source": "B", "strategy": "prev_ref", "size": 1,
                 "position": 1, "fallback_genre": "House"},
                {"source": "B", "strategy": "mix", "size": 1,
                 "genre": "Trance", "exclusion_window": 5},
            ],
        },
    ))
    r = generate(t, {"A": pool_a, "B": pool_b}, week=2, previous_playlist=previous)
    assert [s.strategy for s in r.trace.slots] == ["window", "prev_ref", "mix"]
    # Slot 2: mirror succeeded because the previous's position 1 IS in B.
    assert r.picks[1].id == 500
    # Slot 3: regular mix pick from B, Trance.
    assert r.picks[2].id == 502
    # No fallback fired → empty list.
    assert r.prev_ref_fallback_mixes == []


# ---------- §14.25: mix strategy via generator --------------------------


def _mk_mix_pool() -> list[FakeTrack]:
    return [
        FakeTrack(id=100, name="H-1", genre="House"),
        FakeTrack(id=101, name="T-1", genre="Trance"),
        FakeTrack(id=102, name="H-2", genre="House"),
        FakeTrack(id=103, name="H-3", genre="House"),
    ]


def _mix_template_one_block(rng: str = "off") -> dict:
    return mk_template(
        rng=rng,
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "mix", "size": 1, "genre": "House", "exclusion_window": 5},
            ],
        },
    )


def test_mix_strategy_no_exclusion_picks_first_genre_match():
    t = parse_template(_mix_template_one_block())
    r = generate(t, {"A": _mk_mix_pool()}, week=1)
    assert r.picks[0].id == 100
    assert r.trace.slots[0].strategy == "mix"
    # Pool has 3 House tracks; nothing excluded ⇒ eligible 3 of 3.
    assert "eligible 3 of 3" in r.trace.slots[0].reason
    assert "relaxed" not in r.trace.slots[0].reason


def test_mix_strategy_with_excluded_ids_skips_them():
    t = parse_template(_mix_template_one_block())
    r = generate(
        t, {"A": _mk_mix_pool()},
        week=1,
        excluded_mix_ids={100},  # exclude H-1; should pick H-2 next
    )
    assert r.picks[0].id == 102
    # 3 House candidates, 1 excluded ⇒ eligible 2 of 3.
    assert "eligible 2 of 3" in r.trace.slots[0].reason
    assert "relaxed" not in r.trace.slots[0].reason


def test_mix_within_generation_picks_dedupe_across_slots():
    """Byte-equality with weekly: two mix slots same genre in one
    generation pick DIFFERENT tracks because excluded_mix_ids
    accumulates as picks happen. (Weekly's generator.py line 555
    mutates excluded_mix_ids in-place after each mix pick.) The prior
    §14.25 design wrongly claimed mix dedup was 'purely cross-week' —
    that divergence surfaced during §14.28 mid-slice review and is
    fixed here."""
    t = parse_template(mk_template(
        rng="off",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "mix", "size": 1,
                 "genre": "House", "exclusion_window": 5},
                {"source": "A", "strategy": "mix", "size": 1,
                 "genre": "House", "exclusion_window": 5},
            ],
        },
    ))
    pool = _mk_mix_pool()  # H-1, T-1, H-2, H-3 (3 House tracks)
    r = generate(t, {"A": pool}, week=1)
    # Two House slots; without within-gen accumulation both would
    # pick H-1. With it, slot 2 sees H-1 excluded and picks H-2.
    assert r.picks[0].id != r.picks[1].id


def test_mix_within_generation_relaxes_when_genre_exhausted():
    """When within-gen accumulation drains the genre, the relaxation
    path fires for the last slot (matches apply_mix's relaxed=True
    semantics — better to repeat than fail generation)."""
    t = parse_template(mk_template(
        rng="off",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                # 4 House slots; pool has only 3 House tracks.
                {"source": "A", "strategy": "mix", "size": 4,
                 "genre": "House", "exclusion_window": 5},
            ],
        },
    ))
    pool = _mk_mix_pool()  # 3 House tracks
    r = generate(t, {"A": pool}, week=1)
    # First 3 picks distinct; 4th relaxes and repeats one.
    first_three_ids = {p.id for p in r.picks[:3]}
    assert len(first_three_ids) == 3
    assert r.picks[3].id in first_three_ids
    # The relaxation surfaces in the 4th slot's trace reason.
    assert "relaxed" in r.trace.slots[3].reason


def test_prev_ref_fallback_mix_accumulates_with_regular_mix_slots():
    """Within-gen accumulation crosses strategy boundaries: a regular
    mix slot's pick excludes the same track from a prev_ref-fallback-
    mix slot that runs later, and vice versa."""
    pool = _mk_mix_pool()  # H-1, T-1, H-2, H-3
    t = parse_template(mk_template(
        rng="off",
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                # Regular mix slot first picks H-1.
                {"source": "A", "strategy": "mix", "size": 1,
                 "genre": "House", "exclusion_window": 5},
                # prev_ref with no previous → fallback path. Must pick
                # something OTHER than H-1 (since H-1 was just used).
                {"source": "A", "strategy": "prev_ref", "size": 1,
                 "position": 1, "fallback_genre": "House"},
            ],
        },
    ))
    r = generate(t, {"A": pool}, week=1, previous_playlist=None)
    assert r.picks[0].id == 100  # H-1 (first eligible)
    assert r.picks[1].id != 100   # fallback skips H-1


def test_mix_strategy_relaxation_surfaces_in_trace_reason():
    t = parse_template(_mix_template_one_block())
    # Exclude every House track — strategy relaxes back to candidates[0].
    r = generate(
        t, {"A": _mk_mix_pool()},
        week=1,
        excluded_mix_ids={100, 102, 103},
    )
    assert r.picks[0].genre == "House"
    assert "relaxed" in r.trace.slots[0].reason
    assert "exclusion saturated genre" in r.trace.slots[0].reason
    # Relaxation falls back to all candidates ⇒ eligible 3 of 3 again.
    assert "eligible 3 of 3" in r.trace.slots[0].reason


def test_mix_strategy_rng_seeded_is_reproducible():
    t = parse_template(_mix_template_one_block(rng="on"))
    pools = {"A": _mk_mix_pool()}
    r1 = generate(t, pools, week=1, seed=7)
    r2 = generate(t, pools, week=1, seed=7)
    assert [p.id for p in r1.picks] == [p.id for p in r2.picks]


def test_mix_coexists_with_jingle_and_window_in_same_template():
    """Smoke test: three different non-trivial strategies in one row."""
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "J", "colour": "#fc0", "role": "content"},
            {"name": "M", "colour": "#0cf", "role": "content"},
        ],
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 1},
                {"source": "J", "strategy": "jingle", "size": 1, "genre": "Daytime"},
                {"source": "M", "strategy": "mix", "size": 1, "genre": "House", "exclusion_window": 5},
            ],
        },
    ))
    pools = {
        "A": mk_pool("A", 10),
        "J": _mk_jingle_pool(),
        "M": _mk_mix_pool(),
    }
    r = generate(t, pools, week=1, excluded_mix_ids={100})
    assert [s.strategy for s in r.trace.slots] == ["window", "jingle", "mix"]
    # Mix picked H-2 (id=102) because H-1 (id=100) was excluded.
    assert r.picks[2].id == 102


# ---------- §14.23: excluded-candidates trace field ---------------------


def test_deterministic_strategies_emit_empty_excluded_lists():
    """window/pick/sequential don't reject candidates, so excluded stays []."""
    t = parse_template(mk_template(
        sources=[
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
        ],
        layout={
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "cursors_init": {"c1": 0},
            "row_template": [
                {"source": "A", "strategy": "window", "size": 2},
                {"source": "A", "strategy": "pick",   "size": 1},
                {"source": "B", "strategy": "sequential", "size": 2, "cursor_id": "c1"},
            ],
        },
    ))
    pools = {"A": mk_pool("A", 50), "B": mk_pool("B", 50)}
    r = generate(t, pools, week=1)
    assert all(s.excluded == [] for s in r.trace.slots)
    # And it serialises through to_dict.
    for s_dict in r.trace.to_dict()["slots"]:
        assert s_dict["excluded"] == []


# ---------- §14.32: flat_slots dispatch ---------------------------------


def _flat_slots_template(slots: list[dict], **layout_overrides) -> dict:
    """Build a template using the flat_slots layout form. The caller
    supplies the slot list; the function fills in the (empty) row
    machinery the §14.32 parser requires for the form."""
    layout = {
        "unit": "slot",
        "unit_labels": [],
        "row_start_offsets": [],
        "flat_slots": slots,
    }
    layout.update(layout_overrides)
    return mk_template(
        rng="off",
        cursor={"base_advance_per_week": 0, "cycle_weeks": 1},
        layout=layout,
    )


def test_flat_slots_minimal_dispatch():
    """Smallest flat_slots template: one jingle slot. Verifies the
    flat dispatch loop runs at all + the trace.row/unit values match
    the §14.32 design (row=1, unit="")."""
    pool = [FakeTrack(id=1, name="D-1", genre="Daytime")]
    t = parse_template(_flat_slots_template([
        {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
    ]))
    r = generate(t, {"A": pool}, week=1)
    assert len(r.picks) == 1
    assert r.picks[0].id == 1
    assert r.trace.slots[0].row == 1
    assert r.trace.slots[0].unit == ""
    assert r.trace.slots[0].block == 1
    assert r.trace.slots[0].strategy == "jingle"


def test_flat_slots_multi_strategy_interleaved():
    """Real-world-shaped flat template: jingle / mix / weighted /
    sequential / prev_ref interleaved. Each slot's trace fields
    reflect its position in the flat list."""
    pool = [
        FakeTrack(id=10, name="D1", genre="Daytime"),
        FakeTrack(id=20, name="H1", artist="Artist X", genre="House"),
        FakeTrack(id=30, name="X1", artist="Artist Y"),
        FakeTrack(id=40, name="N1", artist="Artist Z"),
    ]
    t = parse_template(_flat_slots_template(
        [
            {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
            {"source": "A", "strategy": "mix", "size": 1, "genre": "House", "exclusion_window": 5},
            {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
            {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "NIGHT_1"},
        ],
        cursors_init={"NIGHT_1": 3},  # → pool[3] = N1
    ))
    r = generate(t, {"A": pool}, week=1, seed=42)
    assert len(r.picks) == 4
    # All slots report row=1 / unit="" (no row dim in flat form).
    assert all(s.row == 1 for s in r.trace.slots)
    assert all(s.unit == "" for s in r.trace.slots)
    # block is 1-indexed slot position.
    assert [s.block for s in r.trace.slots] == [1, 2, 3, 4]
    # Strategies threaded through correctly.
    assert [s.strategy for s in r.trace.slots] == [
        "jingle", "mix", "weighted", "sequential",
    ]
    # Sequential reads from cursors_init["NIGHT_1"]=3 → pool[3] = N1.
    assert r.picks[3].id == 40


def test_flat_slots_size_greater_than_one():
    """A flat_slots entry with size>1 emits multiple slots, each with
    its own incrementing block index."""
    pool = [FakeTrack(id=i, name=f"D{i}", genre="Daytime") for i in range(1, 6)]
    t = parse_template(_flat_slots_template([
        {"source": "A", "strategy": "jingle", "size": 3, "genre": "Daytime"},
    ]))
    r = generate(t, {"A": pool}, week=1)
    # size=3 → 3 slots from this single block entry. With rng="off"
    # the jingle strategy picks first match every time, so all three
    # are the same track. (size>1 + jingle is unusual but parser-legal.)
    assert len(r.picks) == 3
    # All three slots share the SAME block_idx (1) because they came
    # from a single flat_slots entry. trace.block stays = 1.
    assert [s.block for s in r.trace.slots] == [1, 1, 1]
    # But slot counter increments.
    assert [s.slot for s in r.trace.slots] == [1, 2, 3]


def test_flat_slots_prev_ref_mirror():
    """prev_ref in flat_slots: mirror reads from previous playlist."""
    pool = [
        FakeTrack(id=100, name="X", genre="House"),
        FakeTrack(id=101, name="Y", genre="House"),
    ]
    previous = [pool[1]]  # position 1 → pool[1] = Y, in source ✓
    t = parse_template(_flat_slots_template([
        {"source": "A", "strategy": "prev_ref", "size": 1,
         "position": 1, "fallback_genre": "House"},
    ]))
    r = generate(t, {"A": pool}, week=2, previous_playlist=previous)
    assert r.picks[0].id == 101  # mirror succeeded
    assert "mirror from previous playlist position 1" in r.trace.slots[0].reason


def test_flat_slots_weighted_dedup_works_across_flat_list():
    """Weighted's cross-slot state (_GenState) accumulates between
    flat_slots picks just as it does in row_template form."""
    pool = [
        FakeTrack(id=1, name="A1", artist="Artist A"),
        FakeTrack(id=2, name="A2", artist="Artist A"),
        FakeTrack(id=3, name="B1", artist="Artist B"),
        FakeTrack(id=4, name="C1", artist="Artist C"),
    ]
    # 3 consecutive weighted slots. The artist dedup window must
    # prevent repeats.
    t = parse_template(_flat_slots_template(
        [
            {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
            {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
            {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
        ],
    ))
    r = generate(t, {"A": pool}, week=1, seed=42)
    # 3 unique artists across the 3 slots.
    artists = [p.artist for p in r.picks]
    assert len(set(artists)) == 3


def test_flat_slots_with_cursor_persist_true():
    """§14.30 stateful cursor mode + §14.32 flat_slots coexist
    correctly. The sequential dispatch reads from named_cursor_positions
    regardless of layout form."""
    pool = mk_pool("X", 10)  # X0001 .. X0010 (zero-padded per mk_pool)
    t = parse_template({
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {
            "base_advance_per_week": 0, "cycle_weeks": 1, "persist": True,
        },
        "layout": {
            "unit": "slot", "unit_labels": [], "row_start_offsets": [],
            "cursors_init": {"C1": 0},
            "flat_slots": [
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C1"},
            ],
        },
    })
    r = generate(
        t, {"A": pool}, week=1,
        named_cursor_positions={"C1": 5},  # → pool[5] = X0006
    )
    assert r.picks[0].name == "X0006"


def test_row_template_still_works_unchanged():
    """The §14.32 generator refactor must not regress the existing
    row_template path. This test mirrors the original
    test_minimal_station_1_row_1_block but as an explicit
    regression-after-refactor guard."""
    t = parse_template(mk_template())
    pools = {"A": mk_pool("A", 100)}
    r = generate(t, pools, week=1)
    # 3 slots from window size=3. unit/row/block values come from the
    # row_template form's iteration.
    assert len(r.picks) == 3
    assert all(s.unit == "Mon" for s in r.trace.slots)
    assert all(s.row == 1 for s in r.trace.slots)
    assert [s.block for s in r.trace.slots] == [1, 1, 1]


def test_slots_per_week_for_flat_slots_template():
    """slots_per_week() sums block sizes in flat_slots (no row
    multiplier)."""
    t = parse_template(_flat_slots_template([
        {"source": "A", "strategy": "jingle", "size": 1, "genre": "D"},
        {"source": "A", "strategy": "jingle", "size": 5, "genre": "D"},
        {"source": "A", "strategy": "mix", "size": 2,
         "genre": "H", "exclusion_window": 5},
    ]))
    assert t.slots_per_week() == 8  # 1 + 5 + 2


def test_flat_slots_weighted_populates_excluded_candidates():
    """§14.32 × §14.23 integration: a weighted slot in flat_slots form
    must populate trace.slots[].excluded with the artist-blocked
    candidates, identically to a weighted slot in row_template form.

    Forward-compat: ensures the §14.23 ExcludedCandidate machinery
    threads correctly through the flat dispatch path. Surfaces
    regressions if a future refactor accidentally drops the excluded
    list for one form but not the other.
    """
    # Three tracks, two by the same artist. After picking the first,
    # the second should land in excluded for the second weighted slot.
    pool = [
        FakeTrack(id=1, name="A1", artist="Artist A"),
        FakeTrack(id=2, name="A2", artist="Artist A"),
        FakeTrack(id=3, name="B1", artist="Artist B"),
    ]
    t = parse_template(_flat_slots_template([
        {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
        {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
    ]))
    r = generate(t, {"A": pool}, week=1, seed=42)
    # Slot 1: no prior state → no excluded.
    assert r.trace.slots[0].excluded == []
    # Slot 2: whichever Artist A track wasn't picked must appear in
    # excluded (artist-blocked by slot 1's pick).
    slot2_excluded_ids = {ec.track_id for ec in r.trace.slots[1].excluded}
    assert slot2_excluded_ids & {1, 2}  # at least one Artist A track blocked
    # Picked track itself is filtered out of its own excluded list.
    assert r.picks[1].id not in slot2_excluded_ids
    # Reason cites the artist key.
    sample = next(iter(r.trace.slots[1].excluded))
    assert "artist key" in sample.reason or "artist keys" in sample.reason
    assert "in dedup window" in sample.reason
