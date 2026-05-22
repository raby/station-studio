"""Tests for engine.template_importer (§14.34, sub-slice #16).

Verifies the weekly-shaped → station-studio-shaped translator:
- Per-slot-type translation (every weekly type → station-studio strategy)
- Field-mapping fidelity (genre / exclusion_window / position /
  fallback_genre / cursor_id preserved)
- Source-pool name hardcoding (matches weekly verbatim)
- Cursor.persist=true wired in template (§14.30 stateful mode)
- cursors_init copied through
- parse_template round-trip succeeds (translator-correctness guard)
- Error paths: malformed input, unknown slot type, missing required
  fields, non-dict slot, missing top-level keys

Plus a fixture-driven test exercising the synthetic
`sample_weekly_template.json` end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.template import parse_template
from engine.template_importer import (
    WeeklyImportError,
    import_weekly_template,
)


# ---------- per-slot translation ----------------------------------------


def _minimal_weekly(slots: list[dict]) -> dict:
    """Helper: build a weekly_data dict with the given slot list + a
    minimal cursors_init covering whichever cursors the slots reference."""
    return {"slots": slots, "initial_cursors": {"NIGHT_1": 0, "WEEKEND_1": 0}}


def test_jingle_translates_to_jingle_strategy_with_JINGLES_source():
    out = import_weekly_template(
        _minimal_weekly([{"type": "jingle", "genre": "Daytime"}]),
        slug="x", name="X",
    )
    block = out["layout"]["flat_slots"][0]
    assert block == {
        "source": "JINGLES", "strategy": "jingle", "size": 1, "genre": "Daytime",
    }


def test_commercial_translates_to_weighted_strategy_with_COMMERCIAL_source():
    out = import_weekly_template(
        _minimal_weekly([{"type": "commercial"}]),
        slug="x", name="X",
    )
    block = out["layout"]["flat_slots"][0]
    # No per-block window — rule default applies at generation time.
    assert block == {"source": "COMMERCIAL", "strategy": "weighted", "size": 1}


def test_mix_translates_to_mix_strategy_with_MIXES_source():
    out = import_weekly_template(
        _minimal_weekly([{"type": "mix", "genre": "01Alphastate", "exclusion_window": 20}]),
        slug="x", name="X",
    )
    block = out["layout"]["flat_slots"][0]
    assert block == {
        "source": "MIXES", "strategy": "mix", "size": 1,
        "genre": "01Alphastate", "exclusion_window": 20,
    }


def test_prev_ref_translates_to_prev_ref_strategy_with_MIXES_source():
    out = import_weekly_template(
        _minimal_weekly([
            {"type": "prev_ref", "position": 1742, "fallback_genre": "22Universal"},
        ]),
        slug="x", name="X",
    )
    block = out["layout"]["flat_slots"][0]
    assert block == {
        "source": "MIXES", "strategy": "prev_ref", "size": 1,
        "position": 1742, "fallback_genre": "22Universal",
    }


def test_night_translates_to_sequential_with_NIGHT_source():
    out = import_weekly_template(
        _minimal_weekly([{"type": "night", "cursor_id": "NIGHT_1"}]),
        slug="x", name="X",
    )
    block = out["layout"]["flat_slots"][0]
    assert block == {
        "source": "NIGHT", "strategy": "sequential", "size": 1,
        "cursor_id": "NIGHT_1",
    }


def test_weekend_translates_to_sequential_with_WEEKEND_source():
    out = import_weekly_template(
        _minimal_weekly([{"type": "weekend", "cursor_id": "WEEKEND_1"}]),
        slug="x", name="X",
    )
    block = out["layout"]["flat_slots"][0]
    assert block == {
        "source": "WEEKEND", "strategy": "sequential", "size": 1,
        "cursor_id": "WEEKEND_1",
    }


# ---------- template-level shape ----------------------------------------


def test_template_includes_station_and_name_from_kwargs():
    out = import_weekly_template(
        _minimal_weekly([{"type": "commercial"}]),
        slug="my-station", name="My Station",
    )
    assert out["station"] == "my-station"
    assert out["name"] == "My Station"


def test_template_has_cadence_weekly_and_rng_on():
    out = import_weekly_template(
        _minimal_weekly([{"type": "commercial"}]),
        slug="x", name="X",
    )
    assert out["cadence"] == "weekly"
    assert out["rng"] == "on"  # weekly is rng=on with per-gen seed


def test_template_has_six_hardcoded_sources_with_master_as_reference():
    out = import_weekly_template(
        _minimal_weekly([{"type": "commercial"}]),
        slug="x", name="X",
    )
    sources = out["sources"]
    names = [s["name"] for s in sources]
    assert names == ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND", "MASTER"]
    # MASTER is reference-role; the rest are content-role.
    roles = {s["name"]: s["role"] for s in sources}
    assert roles["MASTER"] == "reference"
    for content_source in ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND"]:
        assert roles[content_source] == "content"


def test_template_sets_cursor_persist_true():
    """§14.30 stateful cursors are required for Rebirth Radio's
    9-cursor model — the importer hardcodes persist=true."""
    out = import_weekly_template(
        _minimal_weekly([{"type": "night", "cursor_id": "NIGHT_1"}]),
        slug="x", name="X",
    )
    assert out["cursor"]["persist"] is True


def test_template_uses_flat_slots_form_with_empty_row_machinery():
    """§14.32 flat_slots is the natural form for weekly-shaped templates
    — no row dimension. row_template + row_start_offsets + unit_labels
    must all be absent or empty."""
    out = import_weekly_template(
        _minimal_weekly([{"type": "commercial"}, {"type": "commercial"}]),
        slug="x", name="X",
    )
    assert "flat_slots" in out["layout"]
    assert out["layout"]["flat_slots"]
    assert out["layout"]["unit_labels"] == []
    assert out["layout"]["row_start_offsets"] == []
    assert "row_template" not in out["layout"]


def test_initial_cursors_copied_to_cursors_init():
    weekly = {
        "slots": [{"type": "night", "cursor_id": "NIGHT_1"}],
        "initial_cursors": {
            "NIGHT_1": 0, "NIGHT_2": 10, "WEEKEND_1": 0, "WEEKEND_2": 120,
        },
    }
    out = import_weekly_template(weekly, slug="x", name="X")
    assert out["layout"]["cursors_init"] == weekly["initial_cursors"]


# ---------- parse_template round-trip ----------------------------------


def test_translator_output_round_trips_through_parse_template():
    """Translator output is always parse_template-valid (the translator
    runs parse_template internally before returning). This test
    documents that property + would catch a regression that bypassed
    the internal validation."""
    out = import_weekly_template(
        _minimal_weekly([
            {"type": "jingle", "genre": "Daytime"},
            {"type": "commercial"},
            {"type": "mix", "genre": "House", "exclusion_window": 20},
            {"type": "night", "cursor_id": "NIGHT_1"},
        ]),
        slug="x", name="X",
    )
    # If parse_template rejected the output, the translator would have
    # already raised. This call is a belt-and-braces verification.
    tmpl = parse_template(out)
    assert len(tmpl.layout.flat_slots) == 4
    assert tmpl.cursor.persist is True


# ---------- error paths -------------------------------------------------


def test_non_dict_input_rejected():
    with pytest.raises(WeeklyImportError, match="must be a dict"):
        import_weekly_template("not a dict", slug="x", name="X")  # type: ignore[arg-type]


def test_missing_top_level_keys_rejected():
    with pytest.raises(WeeklyImportError, match="missing required keys"):
        import_weekly_template({"slots": []}, slug="x", name="X")
    with pytest.raises(WeeklyImportError, match="missing required keys"):
        import_weekly_template({"initial_cursors": {}}, slug="x", name="X")


def test_non_list_slots_rejected():
    with pytest.raises(WeeklyImportError, match="must be a list"):
        import_weekly_template(
            {"slots": "oops", "initial_cursors": {}}, slug="x", name="X",
        )


def test_non_dict_initial_cursors_rejected():
    with pytest.raises(WeeklyImportError, match="initial_cursors.*must be a dict"):
        import_weekly_template(
            {"slots": [], "initial_cursors": []}, slug="x", name="X",
        )


def test_unknown_slot_type_rejected_with_helpful_message():
    with pytest.raises(WeeklyImportError, match="slot 1.*unknown weekly slot type 'banana'"):
        import_weekly_template(
            _minimal_weekly([{"type": "banana"}]),
            slug="x", name="X",
        )


def test_slot_missing_type_field_rejected():
    with pytest.raises(WeeklyImportError, match="slot 1.*missing 'type' field"):
        import_weekly_template(
            _minimal_weekly([{"genre": "Daytime"}]),
            slug="x", name="X",
        )


def test_non_dict_slot_rejected():
    with pytest.raises(WeeklyImportError, match="slot 2.*must be a dict"):
        import_weekly_template(
            _minimal_weekly([{"type": "commercial"}, "oops"]),  # type: ignore[list-item]
            slug="x", name="X",
        )


@pytest.mark.parametrize("slot, missing_name", [
    ({"type": "jingle"}, "genre"),
    ({"type": "mix", "genre": "House"}, "exclusion_window"),
    ({"type": "mix", "exclusion_window": 20}, "genre"),
    ({"type": "prev_ref", "position": 5}, "fallback_genre"),
    ({"type": "prev_ref", "fallback_genre": "x"}, "position"),
    ({"type": "night"}, "cursor_id"),
    ({"type": "weekend"}, "cursor_id"),
])
def test_slot_missing_required_field_rejected_per_type(slot, missing_name):
    """Each weekly slot type has its own required-field set. The
    translator surfaces the specific missing fields per type."""
    # Match on a simple substring rather than the full list repr —
    # avoids regex character-class trap (`[`/`]` are metachars).
    with pytest.raises(WeeklyImportError) as exc_info:
        import_weekly_template(_minimal_weekly([slot]), slug="x", name="X")
    assert "missing required fields" in str(exc_info.value)
    assert f"'{missing_name}'" in str(exc_info.value)


# ---------- fixture-driven end-to-end ----------------------------------


@pytest.fixture
def sample_weekly_data() -> dict:
    """Load the synthetic weekly-shaped fixture."""
    path = Path(__file__).parent / "fixtures" / "sample_weekly_template.json"
    return json.loads(path.read_text())


def test_sample_fixture_translates_to_valid_station_studio_template(sample_weekly_data):
    """End-to-end: the committed fixture parses through the importer +
    the resulting template parses through parse_template. Catches
    fixture drift if either the importer or the fixture's shape
    diverges.

    Fixture has 13 weekly-shaped slots. §14.39 made the translator
    strictly 1:1 (no more coalescing patch) — so the output has 13
    flat_slots, each size=1. The engine's per-cursor offset
    accumulator handles the consecutive same-cursor case correctly
    at generation time.
    """
    out = import_weekly_template(
        sample_weekly_data, slug="sample-rb", name="Sample Rebirth",
    )
    tmpl = parse_template(out)
    assert len(tmpl.layout.flat_slots) == 13
    strategies = [b.strategy for b in tmpl.layout.flat_slots]
    assert strategies == [
        "jingle", "weighted", "weighted", "weighted",
        "jingle", "mix", "prev_ref", "mix",
        "sequential", "sequential",  # 2 × NIGHT_1 (was size=2 pre-§14.39)
        "sequential",                  # NIGHT_2
        "sequential",                  # WEEKEND_1
        "sequential",                  # WEEKEND_2
    ]
    # All sequential blocks now size=1 (1:1 translation).
    sequential_blocks = [b for b in tmpl.layout.flat_slots if b.strategy == "sequential"]
    assert all(b.size == 1 for b in sequential_blocks)
    # cursors_init comes through verbatim.
    assert tmpl.layout.cursors_init == {
        "NIGHT_1": 0, "NIGHT_2": 10,
        "WEEKEND_1": 0, "WEEKEND_2": 120,
    }
    # Persistent cursors (§14.30) for the Rebirth Radio pattern.
    assert tmpl.cursor.persist is True


# ---------- §14.39 — translator is strict 1:1 (no coalescing) ----------


def test_translator_emits_individual_slots_for_consecutive_same_cursor():
    """§14.39 removed `_coalesce_sequential_runs`. Each weekly slot
    now produces exactly one station-studio block (1:1 mapping).
    Consecutive same-cursor sequential slots stay as individual
    size=1 blocks — the engine's per-cursor within-generation offset
    accumulator (`engine.generator.generate`'s `seq_offsets` dict)
    is what makes them read disjoint pool slices.
    """
    weekly = {
        "initial_cursors": {"NIGHT_1": 0, "NIGHT_2": 5, "WEEKEND_1": 0},
        "slots": (
            [{"type": "night", "cursor_id": "NIGHT_1"}] * 3
            + [{"type": "night", "cursor_id": "NIGHT_2"}] * 2
            + [{"type": "jingle", "genre": "Daytime"}]
            + [{"type": "night", "cursor_id": "NIGHT_2"}] * 4
            + [{"type": "weekend", "cursor_id": "WEEKEND_1"}] * 5
        ),
    }
    out = import_weekly_template(weekly, slug="x", name="X")
    flat = out["layout"]["flat_slots"]

    # 15 weekly slots → 15 station-studio blocks (1:1, no coalesce).
    assert len(flat) == 15
    # All sequential blocks have size=1.
    sequential_blocks = [b for b in flat if b["strategy"] == "sequential"]
    assert len(sequential_blocks) == 14  # 3 + 2 + 4 + 5
    assert all(b["size"] == 1 for b in sequential_blocks)
    # Cursor IDs preserved in order.
    cursor_ids = [b["cursor_id"] for b in sequential_blocks]
    assert cursor_ids == (
        ["NIGHT_1"] * 3 + ["NIGHT_2"] * 2 + ["NIGHT_2"] * 4 + ["WEEKEND_1"] * 5
    )


# ---------- review fix F5: undeclared cursor surfaces user-side --------


def test_slot_references_undeclared_cursor_rejected_with_clear_message():
    """A weekly export whose night/weekend slot points at a cursor not
    listed in initial_cursors is a user-data mismatch, NOT a translator
    bug. The importer catches this pre-parse so the error reads as
    user-facing input validation rather than the misleading
    'translator produced an invalid template — file an issue' wrapper.
    """
    weekly = {
        "slots": [
            {"type": "night", "cursor_id": "NIGHT_1"},
            {"type": "weekend", "cursor_id": "WEEKEND_99"},  # not declared
        ],
        "initial_cursors": {"NIGHT_1": 0, "WEEKEND_1": 0},
    }
    with pytest.raises(WeeklyImportError) as exc_info:
        import_weekly_template(weekly, slug="x", name="X")
    msg = str(exc_info.value)
    assert "WEEKEND_99" in msg
    assert "not declared in initial_cursors" in msg
    # And critically: NOT phrased as a translator/station-studio bug.
    assert "station-studio bug" not in msg


# ---------- review fix F6: 9-cursor variant (Rebirth Radio shape) -----


def test_nine_cursor_fixture_round_trips():
    """Rebirth Radio's production weekly template carries 9 named
    cursors (5 NIGHT_*, 4 WEEKEND_*). The sample fixture is the
    minimal 4-cursor variant; this test exercises the wider shape
    inline so the parser's cursor-set bookkeeping is verified against
    the real-world cardinality.
    """
    weekly = {
        "initial_cursors": {
            "NIGHT_1": 0, "NIGHT_2": 10, "NIGHT_3": 20, "NIGHT_4": 30, "NIGHT_5": 40,
            "WEEKEND_1": 0, "WEEKEND_2": 120, "WEEKEND_3": 240, "WEEKEND_4": 360,
        },
        "slots": (
            [{"type": "night", "cursor_id": f"NIGHT_{i}"} for i in range(1, 6)]
            + [{"type": "weekend", "cursor_id": f"WEEKEND_{i}"} for i in range(1, 5)]
        ),
    }
    out = import_weekly_template(weekly, slug="rb9", name="Rebirth9")
    tmpl = parse_template(out)
    assert set(tmpl.layout.cursors_init.keys()) == set(weekly["initial_cursors"].keys())
    assert len(tmpl.layout.flat_slots) == 9
    # All 9 are sequential — every cursor gets exactly one referencing slot.
    assert all(b.strategy == "sequential" for b in tmpl.layout.flat_slots)
    referenced = {b.cursor_id for b in tmpl.layout.flat_slots}
    assert referenced == set(weekly["initial_cursors"].keys())


# ---------- review fix F4: generator integration smoke test ------------


def test_imported_template_generates_against_synthetic_pools():
    """End-to-end: import_weekly_template → generate() against synthetic
    pools. Catches the class of bug where the translator output is
    parse_template-valid but breaks at generation time (e.g. a strategy
    field mapped to the wrong key, a source name typo that parses but
    can't be sampled). Without this, sub-slice #16's first integration
    point is the production import script — surfacing failures there
    forces a much harder bisect than a 30-line synthetic-pool test."""
    from dataclasses import dataclass

    from engine.generator import generate

    @dataclass
    class FakeTrack:
        id: int
        name: str
        artist: str | None = None
        genre: str | None = None

    def mk_pool(prefix: str, size: int, genre: str | None = None) -> list:
        return [
            FakeTrack(id=i, name=f"{prefix}{i:04d}", artist=f"{prefix}-art{i}", genre=genre)
            for i in range(1, size + 1)
        ]

    weekly = {
        "initial_cursors": {"NIGHT_1": 0, "WEEKEND_1": 0},
        "slots": [
            {"type": "jingle", "genre": "Daytime"},
            {"type": "commercial"},
            {"type": "mix", "genre": "House", "exclusion_window": 20},
            {"type": "night", "cursor_id": "NIGHT_1"},
            {"type": "weekend", "cursor_id": "WEEKEND_1"},
        ],
    }
    out = import_weekly_template(weekly, slug="smoke", name="Smoke")
    tmpl = parse_template(out)

    pools = {
        "JINGLES": mk_pool("J", 10, genre="Daytime"),
        "COMMERCIAL": mk_pool("C", 50),
        "MIXES": mk_pool("M", 100, genre="House"),
        "NIGHT": mk_pool("N", 50),
        "WEEKEND": mk_pool("W", 50),
        # MASTER is reference-role; generator never samples it but the
        # pool argument must be present (or absent for reference) per
        # _check_pools_present. The check exempts reference sources.
    }
    r = generate(
        tmpl, pools, week=1, seed=1234,
        named_cursor_positions={"NIGHT_1": 0, "WEEKEND_1": 0},
    )
    assert len(r.picks) == 5
    # Strategies recorded on the trace match the imported template.
    assert [s.strategy for s in r.trace.slots] == [
        "jingle", "weighted", "mix", "sequential", "sequential",
    ]
    # Each pick comes from the correct pool (name prefix is the tell).
    assert r.picks[0].name.startswith("J")  # jingle ← JINGLES
    assert r.picks[1].name.startswith("C")  # commercial ← COMMERCIAL
    assert r.picks[2].name.startswith("M")  # mix ← MIXES
    assert r.picks[3].name.startswith("N")  # night ← NIGHT
    assert r.picks[4].name.startswith("W")  # weekend ← WEEKEND
