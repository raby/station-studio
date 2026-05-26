"""Tests for engine/editor_metadata.py (§16.2.1).

Covers:
  * Grammar validation: allowlists, required keys, types
  * Each of the 10 drift detector rules (rule-by-rule failure tests)
  * synthesize_trivial_sidecar for stations without one
  * expand_section: atomic, macro, repeat_with_offsets
  * AtomicRef discriminator (XOR validation)
  * Happy-path round-trips for both station shapes (flat_slots + row_template)

Calibrated against the §14.32 / §14.34 / §14.35 / §14.38 strict-mode
parsing pattern. Each drift rule has at least one POSITIVE test (the
correct sidecar passes) AND one NEGATIVE test (the wrong sidecar
fails with a clear message).
"""
from __future__ import annotations

import pytest

from engine.editor_metadata import (
    AtomicRef,
    EditorMetadataError,
    MacroPlaceholder,
    expand_section,
    synthesize_trivial_sidecar,
    validate_sidecar,
)


# ---------- station template fixtures (minimal) -------------------------


def _flat_station(n_slots: int = 5) -> dict:
    """A minimal flat_slots station template with `n_slots` jingle slots."""
    return {
        "station": "test-flat",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "JINGLES", "colour": "#fff", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1, "persist": False},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {},
            "flat_slots": [
                {"source": "JINGLES", "strategy": "jingle", "size": 1, "genre": "X"}
                for _ in range(n_slots)
            ],
        },
    }


def _row_station(n_rows: int = 3, n_blocks: int = 2) -> dict:
    """A minimal row_template station — `n_rows` × `n_blocks` instantiations."""
    return {
        "station": "test-row",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "A", "colour": "#fff", "role": "content"},
            {"name": "B", "colour": "#aaa", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 60, "cycle_weeks": 16},
        "layout": {
            "unit": "day",
            "unit_labels": [f"Day{i+1}" for i in range(n_rows)],
            "row_start_offsets": [i * 10 for i in range(n_rows)],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 2},
                {"source": "B", "strategy": "window", "size": 2},
            ][:n_blocks],
        },
    }


def _trivial_sidecar(station: dict) -> dict:
    """Use the synthesized trivial sidecar as a baseline for tests that
    need a valid-shaped sidecar to mutate."""
    return synthesize_trivial_sidecar(station)


# ---------- AtomicRef discriminator -------------------------------------


def test_atomic_ref_flat_only_ok():
    r = AtomicRef(slot_index=5)
    assert r.slot_index == 5
    assert r.row_idx is None


def test_atomic_ref_row_only_ok():
    r = AtomicRef(row_idx=2, block_idx=1)
    assert r.row_idx == 2 and r.block_idx == 1
    assert r.slot_index is None


def test_atomic_ref_rejects_both_forms():
    with pytest.raises(EditorMetadataError, match="EITHER flat_slots"):
        AtomicRef(slot_index=0, row_idx=0, block_idx=0)


def test_atomic_ref_rejects_neither_form():
    with pytest.raises(EditorMetadataError, match="EITHER flat_slots"):
        AtomicRef()


# ---------- synthesize_trivial_sidecar ----------------------------------


def test_synthesize_trivial_for_flat_station():
    station = _flat_station(3)
    sc = synthesize_trivial_sidecar(station)
    assert sc["schema_version"] == 1
    assert len(sc["sections"]) == 1
    assert sc["sections"][0]["id"] == "all-slots"
    assert len(sc["sections"][0]["blocks"]) == 3
    # All atomic + flat addressing.
    assert all(b["kind"] == "atomic" and "slot_index" in b
               for b in sc["sections"][0]["blocks"])
    # Slot indices cover the full range.
    indices = [b["slot_index"] for b in sc["sections"][0]["blocks"]]
    assert indices == [0, 1, 2]


def test_synthesize_trivial_for_row_station():
    station = _row_station(2, 2)
    sc = synthesize_trivial_sidecar(station)
    assert len(sc["sections"][0]["blocks"]) == 4  # 2 rows × 2 blocks
    # All atomic + row addressing.
    for b in sc["sections"][0]["blocks"]:
        assert b["kind"] == "atomic"
        assert "row_idx" in b and "block_idx" in b
        assert "slot_index" not in b


def test_synthesize_trivial_validates_clean():
    """The synthesized sidecar must itself pass validate_sidecar against
    its station — closes the loop on 'any station works in the editor.'"""
    for station in [_flat_station(5), _row_station(3, 2)]:
        sc = synthesize_trivial_sidecar(station)
        validate_sidecar(sc, station)  # no raise


# ---------- happy-path validation ---------------------------------------


def test_valid_flat_sidecar_passes():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "name": "Section 1",
            "blocks": [
                {"kind": "atomic", "slot_index": 0},
                {"kind": "atomic", "slot_index": 1},
                {"kind": "atomic", "slot_index": 2},
            ],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    validate_sidecar(sc, station)  # no raise


def test_valid_row_sidecar_with_repeat_with_offsets_passes():
    """Classics-style sidecar: one section, repeat_with_offsets per row,
    one atomic per block-per-row.

    Note: with `repeat_with_offsets`, atomics' `row_idx` is overridden
    per offset iteration. So the blocks list addresses
    `(row_idx=0, block_idx=N)` as a TEMPLATE; iterations use the
    offset index as the effective row.
    """
    station = _row_station(3, 2)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "week",
            "name": "Week",
            "blocks": [
                {"kind": "atomic", "row_idx": 0, "block_idx": 0},
                {"kind": "atomic", "row_idx": 0, "block_idx": 1},
            ],
            "repeat_with_offsets": [0, 10, 20],
        }],
        "macros": [],
        "layout_order": ["week"],
    }
    validate_sidecar(sc, station)


# ---------- drift rule 1 — orphan slots ---------------------------------


def test_drift_rule_orphan_slots_in_flat_station():
    station = _flat_station(5)
    sc = synthesize_trivial_sidecar(station)
    # Remove an atomic → orphan.
    sc["sections"][0]["blocks"].pop()
    with pytest.raises(EditorMetadataError, match="orphan slots"):
        validate_sidecar(sc, station)


def test_drift_rule_orphan_slots_skipped_when_sidecar_has_macros():
    """With macros present, the orphan check is skipped (see v1 docstring)."""
    station = _flat_station(5)
    sc = {
        "schema_version": 1,
        "sections": [{"id": "s1", "blocks": [{"kind": "macro", "ref": "m1"}]}],
        "macros": [{
            "id": "m1",
            "spec": {"source": "JINGLES", "strategy": "jingle", "size": 1},
            "repeat": 5,
        }],
        "layout_order": ["s1"],
    }
    validate_sidecar(sc, station)  # no raise — macros present, skip orphan check


# ---------- drift rule 2 — dangling macro refs --------------------------


def test_drift_rule_dangling_macro_ref():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [
                {"kind": "atomic", "slot_index": 0},
                {"kind": "macro", "ref": "non_existent"},
                {"kind": "atomic", "slot_index": 2},
            ],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="macro ref 'non_existent'"):
        validate_sidecar(sc, station)


# ---------- drift rule 3 — section in layout_order not in sections[] ----


def test_drift_rule_layout_order_unknown_section():
    station = _flat_station(3)
    sc = synthesize_trivial_sidecar(station)
    sc["layout_order"].append("ghost-section")
    with pytest.raises(EditorMetadataError, match="layout_order.*ghost-section"):
        validate_sidecar(sc, station)


# ---------- drift rule 4 — repeat_with_offsets cardinality --------------


def test_drift_rule_repeat_with_offsets_cardinality():
    station = _row_station(3, 2)  # 3 rows
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "w",
            "blocks": [
                {"kind": "atomic", "row_idx": 0, "block_idx": 0},
                {"kind": "atomic", "row_idx": 0, "block_idx": 1},
            ],
            "repeat_with_offsets": [0, 10],  # only 2; station has 3 rows
        }],
        "macros": [],
        "layout_order": ["w"],
    }
    with pytest.raises(EditorMetadataError, match="repeat_with_offsets has 2"):
        validate_sidecar(sc, station)


# ---------- drift rule 5 — repeat_with_offsets value mismatch -----------


def test_drift_rule_repeat_with_offsets_value_mismatch():
    station = _row_station(3, 2)  # offsets [0, 10, 20]
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "w",
            "blocks": [
                {"kind": "atomic", "row_idx": 0, "block_idx": 0},
                {"kind": "atomic", "row_idx": 0, "block_idx": 1},
            ],
            "repeat_with_offsets": [0, 10, 99],  # 99 ≠ station's 20
        }],
        "macros": [],
        "layout_order": ["w"],
    }
    with pytest.raises(EditorMetadataError, match="repeat_with_offsets.*99.*20"):
        validate_sidecar(sc, station)


# ---------- drift rule 6 — atomic addressing form mismatch --------------


def test_drift_rule_atomic_form_mismatch_flat_using_row():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [{"kind": "atomic", "row_idx": 0, "block_idx": 0}],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="flat_slots layout but"):
        validate_sidecar(sc, station)


def test_drift_rule_atomic_form_mismatch_row_using_flat():
    station = _row_station(2, 2)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [{"kind": "atomic", "slot_index": 0}],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="row_template layout but"):
        validate_sidecar(sc, station)


# ---------- drift rule 7 — atomic index out of bounds -------------------


def test_drift_rule_slot_index_out_of_bounds():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [
                {"kind": "atomic", "slot_index": 0},
                {"kind": "atomic", "slot_index": 1},
                {"kind": "atomic", "slot_index": 2},
                {"kind": "atomic", "slot_index": 99},  # OOB
            ],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="slot_index=99 out of bounds"):
        validate_sidecar(sc, station)


def test_drift_rule_row_idx_out_of_bounds():
    station = _row_station(2, 2)  # rows 0..1
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [
                {"kind": "atomic", "row_idx": 5, "block_idx": 0},
            ],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="row_idx=5 out of bounds"):
        validate_sidecar(sc, station)


def test_drift_rule_block_idx_out_of_bounds():
    station = _row_station(2, 2)  # block_idx 0..1
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [
                {"kind": "atomic", "row_idx": 0, "block_idx": 99},
            ],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="block_idx=99 out of bounds"):
        validate_sidecar(sc, station)


# ---------- drift rule 8 — duplicate atomic refs ------------------------


def test_drift_rule_duplicate_atomic():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [
            {"id": "s1", "blocks": [{"kind": "atomic", "slot_index": 0}]},
            {"id": "s2", "blocks": [{"kind": "atomic", "slot_index": 0}]},  # dup
            {"id": "s3", "blocks": [
                {"kind": "atomic", "slot_index": 1},
                {"kind": "atomic", "slot_index": 2},
            ]},
        ],
        "macros": [],
        "layout_order": ["s1", "s2", "s3"],
    }
    with pytest.raises(EditorMetadataError, match="duplicate atomic reference"):
        validate_sidecar(sc, station)


# ---------- drift rule 9 — missing station JSON (parse_sidecar) ---------


def test_drift_rule_missing_station_json_via_parse_sidecar(tmp_path):
    """parse_sidecar reports a clean error when the sidecar file is missing."""
    from engine.editor_metadata import parse_sidecar
    missing = tmp_path / "ghost.editor.json"
    with pytest.raises(EditorMetadataError, match="sidecar file not found"):
        parse_sidecar(missing, _flat_station(3))


# ---------- drift rule 10 — macro spec invalid --------------------------


def test_drift_rule_macro_spec_unknown_strategy():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{"id": "s1", "blocks": [{"kind": "macro", "ref": "m1"}]}],
        "macros": [{
            "id": "m1",
            "spec": {"source": "JINGLES", "strategy": "made_up", "size": 1},
            "repeat": 3,
        }],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="strategy='made_up' not in"):
        validate_sidecar(sc, station)


def test_drift_rule_macro_spec_unknown_source():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{"id": "s1", "blocks": [{"kind": "macro", "ref": "m1"}]}],
        "macros": [{
            "id": "m1",
            "spec": {"source": "NONEXISTENT", "strategy": "jingle", "size": 1},
            "repeat": 3,
        }],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="source='NONEXISTENT'"):
        validate_sidecar(sc, station)


def test_drift_rule_macro_spec_reference_source_rejected():
    """Macros can only sample from CONTENT sources; reference sources are
    declared but never sampled."""
    station = _flat_station(3)
    station["sources"].append(
        {"name": "MASTER", "colour": "#888", "role": "reference"}
    )
    sc = {
        "schema_version": 1,
        "sections": [{"id": "s1", "blocks": [{"kind": "macro", "ref": "m1"}]}],
        "macros": [{
            "id": "m1",
            "spec": {"source": "MASTER", "strategy": "jingle", "size": 1},
            "repeat": 3,
        }],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="role='reference'"):
        validate_sidecar(sc, station)


# ---------- grammar tests (top-level + per-section + per-macro) ---------


def test_grammar_rejects_non_dict_sidecar():
    with pytest.raises(EditorMetadataError, match="must be a JSON object"):
        validate_sidecar(["not", "a", "dict"], _flat_station(3))


def test_grammar_rejects_missing_required_top_level_keys():
    with pytest.raises(EditorMetadataError, match="missing required keys"):
        validate_sidecar({"schema_version": 1}, _flat_station(3))


def test_grammar_rejects_unknown_top_level_keys():
    sc = {
        "schema_version": 1, "sections": [], "layout_order": [],
        "bogus": 42,
    }
    with pytest.raises(EditorMetadataError, match="unknown top-level keys.*bogus"):
        validate_sidecar(sc, _flat_station(3))


def test_grammar_rejects_wrong_schema_version():
    sc = {"schema_version": 99, "sections": [], "layout_order": []}
    with pytest.raises(EditorMetadataError, match="schema_version=99"):
        validate_sidecar(sc, _flat_station(3))


def test_grammar_rejects_duplicate_section_ids():
    station = _flat_station(2)
    sc = {
        "schema_version": 1,
        "sections": [
            {"id": "dup", "blocks": [{"kind": "atomic", "slot_index": 0}]},
            {"id": "dup", "blocks": [{"kind": "atomic", "slot_index": 1}]},
        ],
        "macros": [],
        "layout_order": ["dup"],
    }
    with pytest.raises(EditorMetadataError, match="duplicate id 'dup'"):
        validate_sidecar(sc, station)


def test_grammar_rejects_duplicate_macro_ids():
    station = _flat_station(2)
    sc = {
        "schema_version": 1,
        "sections": [
            {"id": "s1", "blocks": [{"kind": "macro", "ref": "dup"}]},
        ],
        "macros": [
            {"id": "dup", "spec": {"source": "JINGLES", "strategy": "jingle", "size": 1}, "repeat": 1},
            {"id": "dup", "spec": {"source": "JINGLES", "strategy": "jingle", "size": 1}, "repeat": 1},
        ],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="duplicate id 'dup'"):
        validate_sidecar(sc, station)


def test_grammar_rejects_atomic_block_with_both_addressing_forms():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [{
                "kind": "atomic", "slot_index": 0,
                "row_idx": 0, "block_idx": 0,  # both — invalid
            }],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="pick one"):
        validate_sidecar(sc, station)


def test_grammar_rejects_atomic_block_with_no_addressing():
    station = _flat_station(3)
    sc = {
        "schema_version": 1,
        "sections": [{
            "id": "s1",
            "blocks": [{"kind": "atomic"}],
        }],
        "macros": [],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="missing addressing"):
        validate_sidecar(sc, station)


def test_grammar_rejects_macro_repeat_zero():
    station = _flat_station(2)
    sc = {
        "schema_version": 1,
        "sections": [{"id": "s1", "blocks": [{"kind": "macro", "ref": "m"}]}],
        "macros": [{
            "id": "m", "repeat": 0,
            "spec": {"source": "JINGLES", "strategy": "jingle", "size": 1},
        }],
        "layout_order": ["s1"],
    }
    with pytest.raises(EditorMetadataError, match="repeat.*positive int"):
        validate_sidecar(sc, station)


# ---------- expand_section ---------------------------------------------


def test_expand_section_atomic_flat():
    sec = {
        "id": "s1",
        "blocks": [
            {"kind": "atomic", "slot_index": 0},
            {"kind": "atomic", "slot_index": 2},
            {"kind": "atomic", "slot_index": 1},
        ],
    }
    refs = expand_section(sec, {"macros": []})
    assert [r.slot_index for r in refs] == [0, 2, 1]


def test_expand_section_atomic_row():
    sec = {
        "id": "s1",
        "blocks": [
            {"kind": "atomic", "row_idx": 0, "block_idx": 0},
            {"kind": "atomic", "row_idx": 1, "block_idx": 1},
        ],
    }
    refs = expand_section(sec, {"macros": []})
    assert refs[0].row_idx == 0 and refs[0].block_idx == 0
    assert refs[1].row_idx == 1 and refs[1].block_idx == 1


def test_expand_section_with_macro():
    sec = {
        "id": "s1",
        "blocks": [
            {"kind": "atomic", "slot_index": 0},
            {"kind": "macro", "ref": "rep3"},
            {"kind": "atomic", "slot_index": 4},
        ],
    }
    sidecar = {
        "macros": [{
            "id": "rep3",
            "spec": {"source": "JINGLES", "strategy": "jingle", "size": 1},
            "repeat": 3,
        }],
    }
    refs = expand_section(sec, sidecar)
    # 1 atomic + 3 macro placeholders + 1 atomic = 5 refs.
    assert len(refs) == 5
    assert isinstance(refs[0], AtomicRef) and refs[0].slot_index == 0
    assert isinstance(refs[4], AtomicRef) and refs[4].slot_index == 4
    # Middle 3 are MacroPlaceholders carrying the macro id + 1-indexed
    # pick_in_macro (so the editor can render "Rep3 (1 of 3)" etc.).
    for i, expected_pick in enumerate([1, 2, 3], start=1):
        assert isinstance(refs[i], MacroPlaceholder)
        assert refs[i].macro_id == "rep3"
        assert refs[i].pick_in_macro == expected_pick


def test_expand_section_repeat_with_offsets():
    """Section's blocks list is the per-row template; iterations
    override row_idx with the offset's INDEX (not the offset value)."""
    sec = {
        "id": "week",
        "blocks": [
            {"kind": "atomic", "row_idx": 0, "block_idx": 0},
            {"kind": "atomic", "row_idx": 0, "block_idx": 1},
        ],
        "repeat_with_offsets": [0, 10, 20],
    }
    refs = expand_section(sec, {"macros": []})
    # 3 rows × 2 blocks = 6 refs.
    assert len(refs) == 6
    # row_idx is overridden by the iteration index (0..2), block_idx
    # comes from the template block.
    assert (refs[0].row_idx, refs[0].block_idx) == (0, 0)
    assert (refs[1].row_idx, refs[1].block_idx) == (0, 1)
    assert (refs[2].row_idx, refs[2].block_idx) == (1, 0)
    assert (refs[3].row_idx, refs[3].block_idx) == (1, 1)
    assert (refs[4].row_idx, refs[4].block_idx) == (2, 0)
    assert (refs[5].row_idx, refs[5].block_idx) == (2, 1)
