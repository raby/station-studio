"""Unit tests for scripts/derive_sidecar.py heuristics.

Slice-review finding 2: the auto-derive script ships ~280 LoC of
heuristic code (section split, run-length detection, ID counters)
that was previously only INDIRECTLY tested via the committed
rebirth-radio.editor.json. Direct unit tests cover the boundary
conditions that bad-but-valid output would hide.

Test surface:
  * `_spec_key` — JSON-canonical equality (slice-review finding 9)
  * `_detect_section_ranges` — Daytime-jingle boundary detection +
    the no-leading-jingle fallback
  * `_build_section` — MIN_MACRO_RUN threshold; atomic vs macro;
    macro id counter behaviour
  * `derive_sidecar` end-to-end on minimal templates
  * Rejection of row_template templates
"""
from __future__ import annotations

import pytest

from engine.editor_metadata import EditorMetadataError, validate_sidecar
from scripts.derive_sidecar import (
    MIN_MACRO_RUN,
    _build_section,
    _detect_section_ranges,
    _is_daytime_jingle,
    _spec_key,
    derive_sidecar,
)


def _flat_template(slots: list[dict]) -> dict:
    """Minimal flat_slots station template wrapping the given slots."""
    sources_seen = {(s.get("source"), "content") for s in slots if s.get("source")}
    return {
        "station": "test",
        "name": "Test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": name, "colour": "#abc", "role": role}
            for name, role in sources_seen
        ] or [{"name": "X", "colour": "#abc", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {},
            "flat_slots": slots,
        },
    }


# ---------- _spec_key --------------------------------------------------


def test_spec_key_groups_identical_dicts():
    a = {"source": "X", "strategy": "weighted", "size": 1}
    b = {"source": "X", "strategy": "weighted", "size": 1}
    assert _spec_key(a) == _spec_key(b)


def test_spec_key_distinguishes_on_any_field_difference():
    base = {"source": "X", "strategy": "weighted", "size": 1}
    # Different size.
    assert _spec_key(base) != _spec_key({**base, "size": 2})
    # Different strategy.
    assert _spec_key(base) != _spec_key({**base, "strategy": "window"})
    # Extra field (the finding-9 fragility).
    assert _spec_key(base) != _spec_key({**base, "new_field": "value"})


def test_spec_key_is_key_order_independent():
    """JSON-canonical (sort_keys=True) means dict insertion order
    doesn't matter."""
    a = {"source": "X", "strategy": "weighted"}
    b = {"strategy": "weighted", "source": "X"}
    assert _spec_key(a) == _spec_key(b)


# ---------- _is_daytime_jingle ----------------------------------------


def test_daytime_jingle_detected():
    assert _is_daytime_jingle({"strategy": "jingle", "genre": "Daytime"})


def test_other_jingles_not_daytime():
    assert not _is_daytime_jingle({"strategy": "jingle", "genre": "Mixzone"})
    assert not _is_daytime_jingle({"strategy": "jingle", "genre": "Nighttime"})


def test_non_jingle_strategies_not_daytime():
    assert not _is_daytime_jingle({"strategy": "weighted", "source": "X"})
    assert not _is_daytime_jingle({"strategy": "mix", "genre": "Daytime"})


# ---------- _detect_section_ranges -----------------------------------


def test_section_ranges_empty_slots_returns_empty():
    assert _detect_section_ranges([]) == []


def test_section_ranges_no_daytime_jingles_yields_single_range():
    """Fallback: stations without Daytime jingles get one all-slots
    section starting at index 0."""
    slots = [
        {"strategy": "weighted", "source": "X"},
        {"strategy": "weighted", "source": "X"},
        {"strategy": "weighted", "source": "X"},
    ]
    ranges = _detect_section_ranges(slots)
    assert ranges == [(0, 3)]


def test_section_ranges_leading_daytime_jingle_starts_section_at_0():
    slots = [
        {"strategy": "jingle", "genre": "Daytime"},
        {"strategy": "weighted", "source": "X"},
        {"strategy": "weighted", "source": "X"},
    ]
    ranges = _detect_section_ranges(slots)
    assert ranges == [(0, 3)]


def test_section_ranges_multiple_daytime_jingles_split_sections():
    slots = [
        {"strategy": "jingle", "genre": "Daytime"},  # 0
        {"strategy": "weighted", "source": "X"},     # 1
        {"strategy": "jingle", "genre": "Daytime"},  # 2 — boundary
        {"strategy": "weighted", "source": "X"},     # 3
        {"strategy": "weighted", "source": "X"},     # 4
    ]
    ranges = _detect_section_ranges(slots)
    assert ranges == [(0, 2), (2, 5)]


def test_section_ranges_non_leading_jingle_uses_0_fallback():
    """Slice-review note: when the template doesn't lead with a
    Daytime jingle, prepend (0, first_jingle) as the first section
    so leading slots aren't lost."""
    slots = [
        {"strategy": "weighted", "source": "X"},     # 0
        {"strategy": "weighted", "source": "X"},     # 1
        {"strategy": "jingle", "genre": "Daytime"},  # 2 — boundary
        {"strategy": "weighted", "source": "X"},     # 3
    ]
    ranges = _detect_section_ranges(slots)
    assert ranges == [(0, 2), (2, 4)]


# ---------- _build_section macro threshold ----------------------------


def test_build_section_below_macro_threshold_all_atomic():
    """Runs shorter than MIN_MACRO_RUN stay atomic. With
    MIN_MACRO_RUN=3, a run of 2 identical specs stays atomic."""
    assert MIN_MACRO_RUN == 3  # contract check; update test if changed
    slots = [
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
    ]
    macros: list[dict] = []
    counters: dict[str, int] = {}
    section = _build_section(0, (0, 2), slots, macros, counters)
    assert len(macros) == 0
    assert all(b["kind"] == "atomic" for b in section["blocks"])
    assert len(section["blocks"]) == 2


def test_build_section_at_macro_threshold_emits_macro():
    """Runs of exactly MIN_MACRO_RUN coalesce into one macro."""
    slots = [
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
    ]
    macros: list[dict] = []
    counters: dict[str, int] = {}
    section = _build_section(0, (0, 3), slots, macros, counters)
    assert len(macros) == 1
    assert macros[0]["repeat"] == 3
    assert macros[0]["id"] == "weighted-1"
    assert len(section["blocks"]) == 1
    assert section["blocks"][0] == {"kind": "macro", "ref": "weighted-1"}


def test_build_section_macro_counter_distinguishes_runs():
    """Two different-spec runs of the same strategy get distinct
    macro IDs (`weighted-1` + `weighted-2`)."""
    slots = (
        [{"source": "A", "strategy": "weighted", "size": 1}] * 3
        + [{"source": "B", "strategy": "weighted", "size": 1}] * 3
    )
    macros: list[dict] = []
    counters: dict[str, int] = {}
    section = _build_section(0, (0, 6), slots, macros, counters)
    assert len(macros) == 2
    assert macros[0]["id"] == "weighted-1"
    assert macros[1]["id"] == "weighted-2"
    assert section["blocks"] == [
        {"kind": "macro", "ref": "weighted-1"},
        {"kind": "macro", "ref": "weighted-2"},
    ]


def test_build_section_per_strategy_counter_independent():
    """Macro counter is PER strategy — weighted-1, jingle-1,
    sequential-1 can all coexist."""
    slots = (
        [{"source": "C", "strategy": "weighted", "size": 1}] * 3
        + [{"source": "J", "strategy": "jingle", "size": 1, "genre": "Day"}] * 3
    )
    macros: list[dict] = []
    counters: dict[str, int] = {}
    _build_section(0, (0, 6), slots, macros, counters)
    assert {m["id"] for m in macros} == {"weighted-1", "jingle-1"}


def test_build_section_mixed_runs_alternate_atomic_and_macro():
    """A long run + a short interruption + another long run yields
    macro + atomic + macro."""
    slots = (
        [{"source": "X", "strategy": "weighted", "size": 1}] * 3
        + [{"source": "X", "strategy": "weighted", "size": 2}]  # different size → atomic
        + [{"source": "X", "strategy": "weighted", "size": 1}] * 3
    )
    macros: list[dict] = []
    counters: dict[str, int] = {}
    section = _build_section(0, (0, 7), slots, macros, counters)
    assert len(macros) == 2
    kinds = [b["kind"] for b in section["blocks"]]
    assert kinds == ["macro", "atomic", "macro"]


# ---------- derive_sidecar end-to-end ---------------------------------


def test_derive_sidecar_minimal_flat_template():
    """A trivial 3-slot template auto-derives to a single section
    + one macro covering all 3 (above MIN_MACRO_RUN)."""
    template = _flat_template([
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
    ])
    sidecar = derive_sidecar(template)
    assert sidecar["schema_version"] == 1
    assert len(sidecar["sections"]) == 1
    assert sidecar["sections"][0]["id"] == "segment-1"
    assert len(sidecar["macros"]) == 1
    assert sidecar["macros"][0]["repeat"] == 3
    # And the output validates.
    validate_sidecar(sidecar, template)


def test_derive_sidecar_idempotent_on_re_run():
    """Running derive twice on the same template produces equal
    sidecars. Locks the "deterministic heuristic" contract — if a
    contributor adds state-leaking code (e.g. shared counters), this
    catches it."""
    template = _flat_template([
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
    ])
    first = derive_sidecar(template)
    second = derive_sidecar(template)
    assert first == second


def test_derive_sidecar_rejects_row_template_stations():
    """row_template stations should be hand-authored (their structure
    is naturally expressed via repeat_with_offsets). derive_sidecar
    raises clean."""
    template = {
        "station": "x",
        "name": "X",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "X", "colour": "#abc", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "X", "strategy": "window", "size": 3}],
        },
    }
    with pytest.raises(EditorMetadataError, match="flat_slots"):
        derive_sidecar(template)


def test_derive_sidecar_no_slots_raises_clean():
    """An empty flat_slots template can't have a useful sidecar
    derived. derive_sidecar raises clean."""
    template = _flat_template([])
    with pytest.raises(EditorMetadataError, match="flat_slots"):
        derive_sidecar(template)


def test_derive_sidecar_multiple_segments_via_daytime_jingles():
    """A template with two Daytime jingles yields two sections."""
    template = _flat_template([
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
    ])
    sidecar = derive_sidecar(template)
    assert len(sidecar["sections"]) == 2
    assert [s["id"] for s in sidecar["sections"]] == ["segment-1", "segment-2"]
    assert sidecar["layout_order"] == ["segment-1", "segment-2"]
    validate_sidecar(sidecar, template)


def test_derive_sidecar_layout_order_matches_sections():
    """Drift rule 3 (now enforced strictly per slice-review #3):
    layout_order is exactly the section ids, in declaration order."""
    template = _flat_template([
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
    ])
    sidecar = derive_sidecar(template)
    section_ids = [s["id"] for s in sidecar["sections"]]
    assert sidecar["layout_order"] == section_ids


# ---------- §16.14.t9 — D7 pass-through regression --------------------


def test_derive_sidecar_passes_through_rules_block_opaquely():
    """§16.14.t9 D7 + c1 review F4: derive_sidecar reads only
    `layout` + `sources` from the template. A template with a
    `rules:` block must derive a sidecar identical to the same
    template WITHOUT the block.

    Pins the contract so a future change to derive_sidecar (or
    validate_sidecar) that starts touching top-level keys can't
    silently regress on rules-bearing templates (i.e. the c2
    starter migration).
    """
    base = _flat_template([
        {"strategy": "jingle", "genre": "Daytime"},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
        {"source": "X", "strategy": "weighted", "size": 1},
    ])
    with_rules = {
        **base,
        "rules": {
            "artist_dedup_window": 12,
            "cross_pool_dedup_sources": ["ARCHIVE"],
        },
    }
    assert derive_sidecar(with_rules) == derive_sidecar(base)
