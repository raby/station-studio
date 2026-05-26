"""Integration tests for the committed editor sidecars (§16.2.1 c2).

Verifies the two `stations/*.editor.json` files validate against their
respective station templates. Catches:

  * Drift between the station template and its sidecar (e.g. someone
    edits the station JSON without re-running derive_sidecar).
  * Regressions in the validator (a strict-mode parser change that
    accidentally invalidates a previously-valid sidecar).
  * Regressions in the derive script (running it on rebirth-radio.json
    should produce a sidecar that validates).

These tests run in CI; the committed sidecars are part of the
project's contract going forward.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.editor_metadata import (
    EditorMetadataError,
    expand_section,
    parse_sidecar,
    validate_sidecar,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIONS_DIR = REPO_ROOT / "stations"


def _load_pair(slug: str) -> tuple[dict, dict]:
    """Load (station_template, sidecar) for the given slug."""
    template = json.loads((STATIONS_DIR / f"{slug}.json").read_text())
    sidecar_path = STATIONS_DIR / f"{slug}.editor.json"
    sidecar = parse_sidecar(sidecar_path, template)
    return template, sidecar


# ---------- Rebirth Classics (row_template, hand-authored) -------------


def test_classics_sidecar_validates_against_template():
    """The committed Classics sidecar parses + validates clean."""
    template, sidecar = _load_pair("rebirth-classics")
    # parse_sidecar already ran validate; this re-runs for explicitness.
    validate_sidecar(sidecar, template)


def test_classics_sidecar_structure_matches_design():
    """Classics's sidecar is the canonical row_template shape: ONE
    section with `repeat_with_offsets` matching the station's
    `row_start_offsets`. Lock the structure so future edits don't
    accidentally diverge from the design example."""
    _, sidecar = _load_pair("rebirth-classics")
    assert len(sidecar["sections"]) == 1
    sec = sidecar["sections"][0]
    assert sec["id"] == "weekly-cycle"
    # repeat_with_offsets matches the station's row_start_offsets.
    assert sec["repeat_with_offsets"] == [0, 10, 20, 30, 40, 50, 50]
    # Two atomic blocks per row (one per row_template block).
    assert len(sec["blocks"]) == 2
    assert all(b["kind"] == "atomic" for b in sec["blocks"])
    # row_idx=0 (overridden per iteration); block_idx selects the
    # specific row_template block.
    assert sec["blocks"][0]["row_idx"] == 0
    assert sec["blocks"][0]["block_idx"] == 0
    assert sec["blocks"][1]["row_idx"] == 0
    assert sec["blocks"][1]["block_idx"] == 1


def test_classics_sidecar_expansion_yields_14_blocks():
    """Section expansion produces 7 rows × 2 blocks = 14 AtomicRefs.
    Matches the station's total content slot count (3,010 picks
    after the engine multiplies by `block.size=215`)."""
    _, sidecar = _load_pair("rebirth-classics")
    section = sidecar["sections"][0]
    refs = expand_section(section, sidecar)
    assert len(refs) == 14
    # 7 rows × 2 blocks.
    row_indices = sorted({r.row_idx for r in refs})
    block_indices = sorted({r.block_idx for r in refs})
    assert row_indices == [0, 1, 2, 3, 4, 5, 6]
    assert block_indices == [0, 1]


# ---------- Rebirth Radio (flat_slots, auto-derived) -------------------


def test_radio_sidecar_validates_against_template():
    """The committed Rebirth Radio sidecar parses + validates clean.
    Auto-derived via scripts/derive_sidecar.py from the station's
    2,196 flat_slots. Re-run derive_sidecar.py whenever the station
    template changes."""
    template, sidecar = _load_pair("rebirth-radio")
    validate_sidecar(sidecar, template)


def test_radio_sidecar_has_expected_structural_shape():
    """Lock the derived sidecar's shape so the heuristic's output
    stays stable across refresh runs. If a contributor changes
    derive_sidecar's heuristic, this test surfaces the impact."""
    _, sidecar = _load_pair("rebirth-radio")
    # 5 sections (Daytime-jingle bounded). Real weekly template has
    # 5 Daytime jingles in the 2,196 slots.
    assert len(sidecar["sections"]) == 5
    # 14 macros — large runs of identical specs (200 × weighted, ~95
    # × sequential per NIGHT/WEEKEND block).
    assert len(sidecar["macros"]) == 14
    # All sections in layout_order.
    assert sidecar["layout_order"] == [s["id"] for s in sidecar["sections"]]


def test_radio_sidecar_covers_all_2196_slots():
    """Every slot in the station's flat_slots must be addressed
    EXACTLY ONCE by the sidecar (drift rule 1 + 8). This is
    enforced by validate_sidecar, but assert the count here for
    clarity in the diagnostic if it ever fires."""
    template, sidecar = _load_pair("rebirth-radio")
    total = sum(
        1 for s in template["layout"]["flat_slots"]
    )
    # The expanded coverage from validate_sidecar's orphan check
    # passing means the sidecar covers all 2,196 slots. Confirm
    # the station has the expected count.
    assert total == 2196


def test_radio_sidecar_macros_have_strategy_named_ids():
    """Macro IDs follow the `{strategy}-{counter}` pattern from
    derive_sidecar's heuristic. Useful as a contract for the editor's
    chip-rendering: future code can pattern-match the ID prefix."""
    _, sidecar = _load_pair("rebirth-radio")
    for m in sidecar["macros"]:
        mid = m["id"]
        # ID = "{strategy}-{n}" where strategy comes from the macro's
        # spec.
        strategy = m["spec"]["strategy"]
        assert mid.startswith(f"{strategy}-"), (
            f"macro id {mid!r} doesn't match {strategy}-N pattern"
        )


# ---------- Cross-cutting checks ---------------------------------------


@pytest.mark.parametrize("slug", ["rebirth-classics", "rebirth-radio"])
def test_committed_sidecar_round_trips_through_parse(slug):
    """parse_sidecar (which calls validate_sidecar internally) yields
    a sidecar that re-validates cleanly. Catches mutation bugs in
    validate_sidecar (the §16.2.1 mid-slice review confirmed
    validate doesn't mutate; this test locks that property)."""
    template, sidecar = _load_pair(slug)
    # Re-validating the parsed sidecar must succeed (no mutation
    # produced an invalid intermediate state).
    validate_sidecar(sidecar, template)


@pytest.mark.parametrize("slug", ["rebirth-classics", "rebirth-radio"])
def test_committed_sidecar_has_no_empty_sections(slug):
    """Mid-slice review finding 2: empty sections are rejected by
    validate. Each committed sidecar's sections must have ≥1 block."""
    _, sidecar = _load_pair(slug)
    for sec in sidecar["sections"]:
        assert sec["blocks"], f"section {sec['id']!r} has no blocks"
