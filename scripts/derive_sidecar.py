"""Auto-derive an editor sidecar from a flat_slots station template.

§16.2.1 commit 2 deliverable. Heuristic walker that proposes
sections + macros from a station's `flat_slots` list, producing a
sidecar JSON that validates against `engine.editor_metadata`.

The output is a STARTING POINT — humans refine section names,
macro names, group boundaries. The derived sidecar passes
`validate_sidecar`'s drift rules; what it doesn't get right is
*semantic naming* (the heuristic can't know "this segment is
'Tuesday morning'", only "this is the 3rd Daytime-jingle-bounded
segment").

Heuristic (flat_slots only; row_template stations are hand-authored
because their structure is already explicit — see
`stations/rebirth-classics.editor.json` for the canonical row_template
sidecar shape):

  1. Section boundaries: each `(strategy=jingle, genre=Daytime)` slot
     starts a new section. For Rebirth Radio's 2,196-slot template
     this yields 5 sections (the Daytime jingles fall at slot indices
     0, 312, 626, 940, 1253). The final section spans ~943 slots
     because no Daytime jingle marks the end of programming —
     operators should split that section by hand if a finer grouping
     matches their concept. Stations without Daytime jingles get a
     single "all-slots" section.

  2. Within a section: detect runs of ≥3 consecutive slots with
     IDENTICAL specs (same source + strategy + per-strategy fields
     + size). Each run becomes a macro; one-offs stay atomic.

  3. Macro IDs: `{strategy}-{counter}` — e.g. `commercial-1`,
     `night1-1`, `mix-1`. Counter resets per slice-of-spec; names
     are stable across re-runs because the iteration order is
     deterministic.

  4. Section IDs: `segment-{n}` where n is 1-indexed.
     Section names default to `f"Segment {n}"` — humans rename.

Usage
-----

    python -m scripts.derive_sidecar \\
        --station stations/rebirth-radio.json \\
        --out stations/rebirth-radio.editor.json

The output validates against the station template (the script runs
`validate_sidecar` before writing). Refresh via re-running; for
hand-edited sidecars, the derive step is one-shot during initial
authoring.

Limitations
-----------

* row_template stations: not supported. Their sections are
  naturally one-per-row via `repeat_with_offsets`; hand-author.
* Macro grouping is by EXACT spec match. Two `weighted COMMERCIAL`
  blocks with different sizes won't group. (In practice, weekly's
  templates use uniform sizes within commercial runs.)
* Section names default to "Segment N" — operators should rename
  to match their actual programming concept ("Morning Drive",
  "Midnight Hour", etc.).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine.editor_metadata import EditorMetadataError, validate_sidecar


# Minimum run length to propose a macro. Shorter runs stay atomic
# (the macro overhead of {id, repeat, spec} isn't worth it for ~2
# slots).
MIN_MACRO_RUN = 3


def _spec_key(slot: dict) -> str:
    """Build a hashable key for spec equality.

    Two slots with the same key can be grouped into one macro. The
    key includes EVERY field in the slot dict — slice review finding
    9: hand-enumerated field lists are a regression hazard when a
    future strategy adds a new spec parameter (slots differing only
    in that field would silently coalesce). `json.dumps(sort_keys=True)`
    handles all primitive + nested values; new fields automatically
    distinguish.
    """
    return json.dumps(slot, sort_keys=True)


def _is_daytime_jingle(slot: dict) -> bool:
    """Section-boundary detector. A Daytime jingle starts a new section."""
    return (
        slot.get("strategy") == "jingle"
        and slot.get("genre") == "Daytime"
    )


def _detect_section_ranges(slots: list[dict]) -> list[tuple[int, int]]:
    """Walk slots; return [(start, end_exclusive), ...] section ranges.

    Each section starts at a Daytime jingle (or at slot 0 if the
    template doesn't lead with one) and runs until the next Daytime
    jingle or end-of-template.
    """
    if not slots:
        return []

    # Find all Daytime-jingle indices.
    boundaries = [i for i, s in enumerate(slots) if _is_daytime_jingle(s)]
    if not boundaries or boundaries[0] != 0:
        # Template doesn't lead with a Daytime jingle — prepend slot 0
        # as the first section's start so we don't lose the leading
        # slots.
        boundaries = [0] + boundaries

    ranges = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(slots)
        ranges.append((start, end))
    return ranges


def _build_section(
    section_idx: int,
    slot_range: tuple[int, int],
    slots: list[dict],
    macros: list[dict],
    macro_counters: dict[str, int],
) -> dict:
    """Build a single section dict + append any macros it generates.

    `macros` is mutated to collect new macro definitions; the section's
    blocks list either references macros (kind=macro) or atomic slots
    (kind=atomic). `macro_counters` tracks per-strategy counters so
    macro IDs are stable + unique across the whole sidecar.
    """
    start, end = slot_range
    section_slots = slots[start:end]
    blocks: list[dict] = []

    # Run-length walk: detect ≥MIN_MACRO_RUN consecutive same-spec
    # slots → emit a macro. Otherwise emit atomic.
    i = 0
    while i < len(section_slots):
        # Look ahead to see how long the same-spec run is.
        run_key = _spec_key(section_slots[i])
        run_len = 1
        while (
            i + run_len < len(section_slots)
            and _spec_key(section_slots[i + run_len]) == run_key
        ):
            run_len += 1

        if run_len >= MIN_MACRO_RUN:
            # Emit a macro for this run.
            strategy = section_slots[i].get("strategy", "block")
            macro_counters[strategy] = macro_counters.get(strategy, 0) + 1
            macro_id = f"{strategy}-{macro_counters[strategy]}"
            macro_spec = dict(section_slots[i])  # full spec; engine ignores
            macros.append({
                "id": macro_id,
                "name": f"{run_len} × {strategy}",
                "repeat": run_len,
                "spec": macro_spec,
            })
            blocks.append({"kind": "macro", "ref": macro_id})
            i += run_len
        else:
            # Emit atomic blocks for this run.
            for j in range(run_len):
                slot_index = start + i + j
                blocks.append({"kind": "atomic", "slot_index": slot_index})
            i += run_len

    return {
        "id": f"segment-{section_idx + 1}",
        "name": f"Segment {section_idx + 1}",
        "blocks": blocks,
    }


def derive_sidecar(station_template: dict) -> dict:
    """Derive a sidecar dict from a station template.

    Runs the heuristic + validates the output against the station
    template's drift rules before returning. If validation fails,
    raises `EditorMetadataError` — the heuristic produced something
    invalid, which is a derive-script bug, not a station bug.
    """
    layout = station_template.get("layout", {})
    flat_slots = layout.get("flat_slots") or []
    if not flat_slots:
        # row_template stations: nothing to auto-derive; the operator
        # should hand-author (single section + repeat_with_offsets).
        raise EditorMetadataError(
            "derive_sidecar supports flat_slots stations only. "
            "row_template stations should be hand-authored (see "
            "stations/rebirth-classics.editor.json for the canonical "
            "shape)."
        )

    section_ranges = _detect_section_ranges(flat_slots)
    macros: list[dict] = []
    macro_counters: dict[str, int] = {}
    sections: list[dict] = []

    for idx, slot_range in enumerate(section_ranges):
        sections.append(_build_section(
            idx, slot_range, flat_slots, macros, macro_counters,
        ))

    layout_order = [s["id"] for s in sections]

    sidecar = {
        "_comment": (
            "Auto-derived from the station's flat_slots via "
            "scripts/derive_sidecar.py. Heuristic: sections split at "
            "Daytime jingles; macros for runs of ≥3 identical specs. "
            "Refine section names + macro names to match your "
            "programming concept (the heuristic can't infer 'Morning "
            "Drive' from a block of 200 commercials)."
        ),
        "schema_version": 1,
        "sections": sections,
        "macros": macros,
        "layout_order": layout_order,
    }

    # Drift-rule check — the heuristic should never produce invalid
    # output; if it does, that's a derive-script bug.
    validate_sidecar(sidecar, station_template)

    return sidecar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Auto-derive an editor sidecar from a flat_slots station template.",
    )
    parser.add_argument(
        "--station",
        type=Path,
        required=True,
        help="Path to the station JSON (must use flat_slots layout form).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Path to write the derived sidecar JSON.",
    )
    args = parser.parse_args(argv)

    if not args.station.is_file():
        print(f"error: station file not found: {args.station}", file=sys.stderr)
        return 1

    station = json.loads(args.station.read_text())
    try:
        sidecar = derive_sidecar(station)
    except EditorMetadataError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.write_text(json.dumps(sidecar, indent=2) + "\n")
    n_sections = len(sidecar["sections"])
    n_macros = len(sidecar["macros"])
    print(
        f"  wrote     {args.out}  "
        f"{n_sections:>3} sections, "
        f"{n_macros:>3} macros"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
