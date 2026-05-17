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
