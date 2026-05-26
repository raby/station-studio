"""Editor metadata (sidecar) parser + validator (§16.2.1).

A SIDECAR file at `stations/<slug>.editor.json` carries the visual
block editor's semantic structure (sections, macros, layout order)
ALONGSIDE the engine-facing `stations/<slug>.json`. Engine ignores
the sidecar; editor reads both. See DESIGN.md §16.2.0 for the
design discussion + grammar v1 spec; §16.2.1 (this slice) lands
the parser + 10 drift rules.

Why this module exists
----------------------

The visual block editor (§16.2) needs SEMANTIC STRUCTURE on top of
the engine's flat slot list. Weekly's 2,196 slots are unusable as
2,196 chips; the user thinks in segments (day1, day2, mix section,
NIGHT block, …) and patterns (200×commercial, mix-pair-of-8, …).

The sidecar carries that structure. The grammar (Decision 2 in
§16.2.0):

  * `sections[]` — ordered named compositions of atomic-or-macro
    blocks. Optional `repeat_with_offsets` lets a single section
    express row_template's "repeat 7 times with these offsets"
    pattern.
  * `macros[]` — named repeated patterns ("200 × commercial").
    Expanded at editor render time; engine never sees them.
  * `layout_order` — the order sections render in.

This module:

  * Validates the sidecar shape (allowlists, types, cross-refs).
  * Runs the 10 drift detector rules at load time.
  * Provides `synthesize_trivial_sidecar` for stations without a
    sidecar — a single "All slots" section listing every atomic
    block. Progressive disclosure: any station works in the editor;
    authoring a real sidecar later refines the view.
  * Provides `expand_section` to resolve macros + `repeat_with_offsets`
    into the flat list of atomic block addresses the editor renders.

Design lineage: follows the §14.32 / §14.34 / §14.35 / §14.38 strict-
mode parsing pattern (allowlist of keys, per-field type checks,
fail-loud on malformed input).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------- grammar allowlists ------------------------------------------

ALLOWED_TOP_LEVEL = {
    "schema_version", "sections", "macros", "layout_order", "_comment",
}
REQUIRED_TOP_LEVEL = {"schema_version", "sections", "layout_order"}

ALLOWED_SECTION_KEYS = {"id", "name", "blocks", "repeat_with_offsets"}
REQUIRED_SECTION_KEYS = {"id", "blocks"}

ALLOWED_MACRO_KEYS = {"id", "name", "spec", "repeat"}
REQUIRED_MACRO_KEYS = {"id", "spec", "repeat"}

# Atomic block: discriminated by which addressing fields are present.
ATOMIC_FLAT_KEYS = {"kind", "slot_index"}
ATOMIC_ROW_KEYS = {"kind", "row_idx", "block_idx"}
MACRO_BLOCK_KEYS = {"kind", "ref"}


# ---------- error type ---------------------------------------------------


class EditorMetadataError(AssertionError):
    """Raised when sidecar validation or drift detection fails (§16.2.1).

    Subclasses AssertionError so test contexts can use
    `pytest.raises(AssertionError, ...)` patterns; production
    callers should catch the specific class explicitly to
    distinguish editor-side errors from engine-side TemplateError
    or PriorSeedError.
    """


# ---------- public dataclass return shapes ------------------------------


@dataclass(frozen=True)
class AtomicRef:
    """An atomic-block address into the station template.

    Either `slot_index` is set (flat_slots station) OR
    `(row_idx, block_idx)` are both set (row_template station).
    Never both; never neither. Validated at parse time.
    """
    slot_index: int | None = None
    row_idx: int | None = None
    block_idx: int | None = None

    def __post_init__(self):
        # ValueError (not EditorMetadataError) because this is an
        # INTERNAL invariant on a dataclass constructed by
        # `expand_section` — never from user data. Mid-slice review
        # finding 5: separating internal-invariant errors from
        # sidecar-validation errors lets callers catch
        # EditorMetadataError purely for "your sidecar is wrong"
        # without accidentally catching expansion-code bugs.
        has_flat = self.slot_index is not None
        has_row = self.row_idx is not None and self.block_idx is not None
        if has_flat == has_row:
            raise ValueError(
                f"AtomicRef must address EITHER flat_slots ({self.slot_index=}) "
                f"OR row_template ({self.row_idx=}, {self.block_idx=}); "
                f"got both or neither."
            )


@dataclass(frozen=True)
class MacroPlaceholder:
    """A placeholder for one repeat-instance of a macro.

    Macros emit N placeholders (one per repeat); the editor renders
    them as a single grouped chip with a count. v1 placeholders
    don't know which specific slot they correspond to in the engine
    output — the §16.2.4 (macro editing) slice will revisit when
    per-pick addressing is needed for diff/preview.

    Kept as a separate type from `AtomicRef` so the strict XOR
    invariant on `AtomicRef` stays intact + callers explicitly
    handle the sum type.
    """
    macro_id: str
    pick_in_macro: int  # 1-indexed within this macro's repeat span


# A section expansion's output: ordered mix of atomic refs + macro
# placeholders. Callers iterate the union.
SectionExpansion = list  # list[AtomicRef | MacroPlaceholder]


# ---------- public API ---------------------------------------------------


def parse_sidecar(path: Path, station_template: dict) -> dict:
    """Load + validate a sidecar JSON file against its station template.

    Returns the parsed sidecar dict (after validation). Raises
    `EditorMetadataError` on any of the 10 drift rules.

    `station_template` is the parsed-from-disk
    `stations/<slug>.json` content — needed so the drift detector
    can cross-check slot indices, layout form, source roles, etc.
    """
    if not path.is_file():
        raise EditorMetadataError(f"sidecar file not found: {path}")
    # Mid-slice review finding 6: wrap JSONDecodeError so callers get
    # a uniform editor-error surface; raw JSON syntax problems shouldn't
    # leak through a different exception class.
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise EditorMetadataError(
            f"sidecar JSON malformed at {path}: {e}"
        ) from e
    validate_sidecar(raw, station_template)
    return raw


def validate_sidecar(sidecar: dict, station_template: dict) -> None:
    """Run all 10 drift rules + grammar validation against the sidecar.

    Raises `EditorMetadataError` on the first violation found.

    Call order mirrors the §16.2.0 drift-rule numbering:

      structural (grammar)           — type/allowlist/required checks
      rule 6  layout form consistency
      rule 2  macro refs
      rule 3  layout_order refs
      rule 10 macro spec validity
      rule 4+5 repeat_with_offsets cardinality + values
      rule 7  atomic bounds
      rule 8  duplicate atomics
      rule 1  orphan slots

    Validation order is structural-first (grammar) then semantic
    (cross-references with the station template), so a malformed
    sidecar fails on a clean grammar error rather than a confusing
    cross-reference error.

    Mid-slice review finding 3: enforce the engine's XOR invariant
    on `station_template` up-front. The engine's `parse_template`
    enforces this on PARSED templates; here we get raw JSON and
    must guard against a hand-edited station JSON with both
    flat_slots and row_template populated (which would otherwise
    validate the sidecar against the flat-form rules only, masking
    the underlying station bug).
    """
    layout = station_template.get("layout", {})
    has_flat = bool(layout.get("flat_slots"))
    has_row = bool(layout.get("row_template"))
    if has_flat and has_row:
        raise EditorMetadataError(
            "station_template has BOTH flat_slots and row_template "
            "populated; the engine's XOR invariant is violated. Fix "
            "the station JSON before validating its sidecar."
        )

    _validate_grammar(sidecar)
    _validate_layout_form_consistency(sidecar, station_template)
    _validate_macro_refs(sidecar)
    _validate_layout_order_refs(sidecar)
    _validate_macro_specs(sidecar, station_template)
    _validate_repeat_with_offsets(sidecar, station_template)
    _validate_atomic_bounds(sidecar, station_template)
    _validate_no_duplicate_atomics(sidecar, station_template)
    _validate_no_orphan_slots(sidecar, station_template)


def synthesize_trivial_sidecar(station_template: dict) -> dict:
    """Build a trivial 'All slots' sidecar for a station without one.

    Used for the absent-sidecar UX (§16.2.0 Decision: progressive
    disclosure). Every atomic block lives in a single section; no
    macros; layout_order has one entry. The editor renders the
    station with no semantic structure but ALL slots visible and
    editable.

    Authoring a real sidecar later refines the view; the trivial
    one is the floor.
    """
    layout = station_template.get("layout", {})
    flat_slots = layout.get("flat_slots", [])
    row_template = layout.get("row_template", [])
    row_offsets = layout.get("row_start_offsets", [])

    blocks = []
    if flat_slots:
        for i in range(len(flat_slots)):
            blocks.append({"kind": "atomic", "slot_index": i})
    else:
        # row_template — addressing by (row_idx, block_idx). Don't use
        # repeat_with_offsets here; we want a literal flat list so the
        # editor renders every instantiation as its own chip.
        for row_idx in range(len(row_offsets)):
            for block_idx in range(len(row_template)):
                blocks.append({
                    "kind": "atomic",
                    "row_idx": row_idx,
                    "block_idx": block_idx,
                })

    return {
        "_comment": (
            "Synthesized trivial sidecar (§16.2.1). The station has no "
            "stations/<slug>.editor.json; every slot lives in a single "
            "'All slots' section with no semantic structure. Authoring "
            "a real sidecar refines the editor view."
        ),
        "schema_version": 1,
        "sections": [
            {"id": "all-slots", "name": "All slots", "blocks": blocks}
        ],
        "macros": [],
        "layout_order": ["all-slots"],
    }


def lookup_macro(sidecar: dict, macro_id: str) -> dict | None:
    """Find a macro by its id. Returns the macro dict or None.

    Slice-review finding 8: §16.2.2's chip renderer needs to look up
    a macro's spec/name/repeat when rendering a `MacroPlaceholder`
    chip. `expand_section` returns placeholders with `macro_id` only;
    this helper does the dict scan once instead of inlining
    `next(m for m in sidecar["macros"] if m["id"] == macro_id)` at
    every call site.

    Returns None when the macro_id is unknown — `validate_sidecar`
    catches dangling refs at load time (drift rule 2), so callers
    on a validated sidecar can treat None as a programmer error.
    """
    for m in sidecar.get("macros", []):
        if m.get("id") == macro_id:
            return m
    return None


def expand_section(section: dict, sidecar: dict) -> list:
    """Resolve a section into the flat list of refs the editor renders.

    Returns a list whose elements are EITHER `AtomicRef` (single slot
    address) OR `MacroPlaceholder` (one per macro-repeat instance).
    Callers iterate the union; the editor renders them as chips.

    Handles:

      * Atomic blocks → emit one AtomicRef per block.
      * Macro blocks → look up the macro by `ref`; emit
        `macro.repeat` MacroPlaceholders.
      * `repeat_with_offsets` — if set, the section's blocks list
        is the per-row template, instantiated once per offset.
        Each instantiation emits one ref per block, with the
        atomic blocks' `row_idx` overridden to the offset's position
        in `repeat_with_offsets`.

    Returns the flat list in render order.

    NOTE — Macro expansion: today, each repeat of a macro emits a
    `MacroPlaceholder(macro_id, pick_in_macro)`. Placeholders don't
    address specific slot positions in the engine output yet because
    the engine treats macros as a single repeated BlockSpec. The
    §16.2.4 (macro editing) slice will revisit when per-pick
    addressing is needed for diff/preview.
    """
    blocks = section.get("blocks", [])
    repeat_offsets = section.get("repeat_with_offsets")
    macros_by_id = {m["id"]: m for m in sidecar.get("macros", [])}

    def _expand_atomic(b: dict, row_override: int | None = None) -> AtomicRef:
        if "slot_index" in b:
            return AtomicRef(slot_index=b["slot_index"])
        # row_template addressing
        row_idx = row_override if row_override is not None else b["row_idx"]
        return AtomicRef(row_idx=row_idx, block_idx=b["block_idx"])

    def _expand_macro(b: dict) -> list:
        macro = macros_by_id.get(b["ref"])
        if macro is None:
            # Defensive — validate_sidecar should have caught this.
            raise EditorMetadataError(
                f"macro ref {b['ref']!r} not found at expansion time"
            )
        return [
            MacroPlaceholder(macro_id=macro["id"], pick_in_macro=i + 1)
            for i in range(macro["repeat"])
        ]

    def _expand_block(b: dict, row_override: int | None = None) -> list:
        if b["kind"] == "atomic":
            return [_expand_atomic(b, row_override=row_override)]
        return _expand_macro(b)

    if repeat_offsets is None:
        out = []
        for b in blocks:
            out.extend(_expand_block(b))
        return out

    # Repeat-with-offsets: one instantiation per offset.
    out = []
    for row_idx, _offset in enumerate(repeat_offsets):
        for b in blocks:
            out.extend(_expand_block(b, row_override=row_idx))
    return out


# ---------- internal validators (drift rules) ---------------------------


def _validate_grammar(sidecar: dict) -> None:
    """Structural checks: types + allowlist + required keys."""
    if not isinstance(sidecar, dict):
        raise EditorMetadataError(
            f"sidecar must be a JSON object, got {type(sidecar).__name__}"
        )
    missing = REQUIRED_TOP_LEVEL - set(sidecar.keys())
    if missing:
        raise EditorMetadataError(
            f"sidecar missing required keys: {sorted(missing)}. "
            f"Required: {sorted(REQUIRED_TOP_LEVEL)}."
        )
    unknown = set(sidecar.keys()) - ALLOWED_TOP_LEVEL
    if unknown:
        raise EditorMetadataError(
            f"sidecar has unknown top-level keys: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_TOP_LEVEL)}."
        )
    if sidecar.get("schema_version") != 1:
        raise EditorMetadataError(
            f"sidecar schema_version={sidecar.get('schema_version')!r}; "
            f"expected 1 (§16.2.0)."
        )

    sections = sidecar["sections"]
    if not isinstance(sections, list):
        raise EditorMetadataError(
            f"'sections' must be a list, got {type(sections).__name__}"
        )
    # Mid-slice review finding 2: reject empty sections. A sidecar
    # with zero sections renders an empty editor view; if the user
    # wants "no sections" they shouldn't ship a sidecar at all
    # (synthesize_trivial_sidecar handles the no-sidecar case).
    if not sections:
        raise EditorMetadataError(
            "'sections' must contain at least one section. An empty "
            "sidecar has nothing to render; remove the sidecar file "
            "(the editor falls back to a synthesized 'All slots' view) "
            "or add at least one section."
        )
    seen_section_ids: set[str] = set()
    for i, sec in enumerate(sections):
        _validate_section_grammar(sec, i)
        if sec["id"] in seen_section_ids:
            raise EditorMetadataError(
                f"sections[{i}]: duplicate id {sec['id']!r}; "
                f"section ids must be unique within the sidecar."
            )
        seen_section_ids.add(sec["id"])

    macros = sidecar.get("macros", [])
    if not isinstance(macros, list):
        raise EditorMetadataError(
            f"'macros' must be a list, got {type(macros).__name__}"
        )
    seen_macro_ids: set[str] = set()
    for i, m in enumerate(macros):
        _validate_macro_grammar(m, i)
        if m["id"] in seen_macro_ids:
            raise EditorMetadataError(
                f"macros[{i}]: duplicate id {m['id']!r}; "
                f"macro ids must be unique within the sidecar."
            )
        seen_macro_ids.add(m["id"])

    layout_order = sidecar["layout_order"]
    if not isinstance(layout_order, list):
        raise EditorMetadataError(
            f"'layout_order' must be a list, got {type(layout_order).__name__}"
        )
    for i, sid in enumerate(layout_order):
        if not isinstance(sid, str):
            raise EditorMetadataError(
                f"layout_order[{i}] must be a string, got {type(sid).__name__}"
            )


def _validate_section_grammar(sec: dict, i: int) -> None:
    if not isinstance(sec, dict):
        raise EditorMetadataError(
            f"sections[{i}] must be an object, got {type(sec).__name__}"
        )
    missing = REQUIRED_SECTION_KEYS - set(sec.keys())
    if missing:
        raise EditorMetadataError(
            f"sections[{i}] missing required keys: {sorted(missing)}."
        )
    unknown = set(sec.keys()) - ALLOWED_SECTION_KEYS
    if unknown:
        raise EditorMetadataError(
            f"sections[{i}] has unknown keys: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_SECTION_KEYS)}."
        )
    if not isinstance(sec["id"], str) or not sec["id"]:
        raise EditorMetadataError(
            f"sections[{i}].id must be a non-empty string."
        )
    if not isinstance(sec["blocks"], list):
        raise EditorMetadataError(
            f"sections[{i}].blocks must be a list."
        )
    if "name" in sec and not isinstance(sec["name"], str):
        raise EditorMetadataError(
            f"sections[{i}].name must be a string when present."
        )
    if "repeat_with_offsets" in sec:
        rwo = sec["repeat_with_offsets"]
        if not isinstance(rwo, list):
            raise EditorMetadataError(
                f"sections[{i}].repeat_with_offsets must be a list."
            )
        for j, v in enumerate(rwo):
            if not isinstance(v, int):
                raise EditorMetadataError(
                    f"sections[{i}].repeat_with_offsets[{j}] must be int, "
                    f"got {type(v).__name__}"
                )

    # Validate each block in the section.
    has_rwo = "repeat_with_offsets" in sec
    for j, b in enumerate(sec["blocks"]):
        _validate_block_grammar(b, i, j)
        if has_rwo:
            # Mid-slice review finding 4: macros inside a
            # repeat_with_offsets section have ambiguous semantics
            # (does the macro repeat per-row or globally?). v1
            # rejects them; §16.2.4 may relax if a real need emerges.
            if b.get("kind") == "macro":
                raise EditorMetadataError(
                    f"sections[{i}].blocks[{j}]: macro blocks aren't "
                    f"allowed inside a section with repeat_with_offsets. "
                    f"The semantic (does the macro repeat per-row or "
                    f"globally?) isn't settled in v1 — restructure to "
                    f"either drop repeat_with_offsets OR move the macro "
                    f"into a separate section."
                )
            # Mid-slice review finding 1: atomic blocks' `row_idx`
            # field is OVERRIDDEN by the iteration index when the
            # section uses repeat_with_offsets. The block's authored
            # row_idx is template-only; require it to be 0 so the
            # contract is explicit + the user doesn't silently lose
            # an authored value.
            if b.get("kind") == "atomic" and b.get("row_idx") not in (None, 0):
                raise EditorMetadataError(
                    f"sections[{i}].blocks[{j}]: atomic blocks inside a "
                    f"repeat_with_offsets section must use row_idx=0 "
                    f"(the iteration index overrides it). Got row_idx="
                    f"{b['row_idx']}; the value would be silently lost."
                )


def _validate_block_grammar(b: dict, section_idx: int, block_idx: int) -> None:
    if not isinstance(b, dict):
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}] must be an object."
        )
    kind = b.get("kind")
    if kind not in {"atomic", "macro"}:
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}].kind must be "
            f"'atomic' or 'macro'; got {kind!r}."
        )
    if kind == "macro":
        unknown = set(b.keys()) - MACRO_BLOCK_KEYS
        if unknown:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (macro): "
                f"unknown keys {sorted(unknown)}. "
                f"Allowed: {sorted(MACRO_BLOCK_KEYS)}."
            )
        if "ref" not in b:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (macro): "
                f"missing 'ref'."
            )
        if not isinstance(b["ref"], str) or not b["ref"]:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (macro): "
                f"'ref' must be a non-empty string."
            )
        return

    # atomic block — exactly one addressing form
    keys = set(b.keys())
    is_flat = "slot_index" in keys
    is_row = "row_idx" in keys or "block_idx" in keys
    if is_flat and is_row:
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}] (atomic): "
            f"both slot_index AND row_idx/block_idx present; pick one."
        )
    if not is_flat and not is_row:
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}] (atomic): "
            f"missing addressing — need either 'slot_index' OR "
            f"'row_idx'+'block_idx'."
        )
    if is_flat:
        if keys != ATOMIC_FLAT_KEYS:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic flat): "
                f"unexpected keys {sorted(keys - ATOMIC_FLAT_KEYS)}. "
                f"Required: {sorted(ATOMIC_FLAT_KEYS)}."
            )
        if not isinstance(b["slot_index"], int):
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}].slot_index "
                f"must be int, got {type(b['slot_index']).__name__}"
            )
    else:
        if keys != ATOMIC_ROW_KEYS:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic row): "
                f"unexpected keys {sorted(keys - ATOMIC_ROW_KEYS)}. "
                f"Required: {sorted(ATOMIC_ROW_KEYS)}."
            )
        if "row_idx" not in b or "block_idx" not in b:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic row): "
                f"both row_idx AND block_idx required."
            )
        if not isinstance(b["row_idx"], int) or not isinstance(b["block_idx"], int):
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic row): "
                f"row_idx and block_idx must both be int."
            )


def _validate_macro_grammar(m: dict, i: int) -> None:
    if not isinstance(m, dict):
        raise EditorMetadataError(
            f"macros[{i}] must be an object, got {type(m).__name__}"
        )
    missing = REQUIRED_MACRO_KEYS - set(m.keys())
    if missing:
        raise EditorMetadataError(
            f"macros[{i}] missing required keys: {sorted(missing)}."
        )
    unknown = set(m.keys()) - ALLOWED_MACRO_KEYS
    if unknown:
        raise EditorMetadataError(
            f"macros[{i}] has unknown keys: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_MACRO_KEYS)}."
        )
    if not isinstance(m["id"], str) or not m["id"]:
        raise EditorMetadataError(f"macros[{i}].id must be a non-empty string.")
    if not isinstance(m["spec"], dict):
        raise EditorMetadataError(f"macros[{i}].spec must be an object.")
    if not isinstance(m["repeat"], int) or m["repeat"] < 1:
        raise EditorMetadataError(
            f"macros[{i}].repeat must be a positive int (>= 1); "
            f"got {m['repeat']!r}."
        )


def _layout_form(station_template: dict) -> str:
    """Return 'flat_slots' or 'row_template' based on the station's layout."""
    layout = station_template.get("layout", {})
    if layout.get("flat_slots"):
        return "flat_slots"
    return "row_template"


def _validate_layout_form_consistency(sidecar: dict, station_template: dict) -> None:
    """Drift rule 6: atomic addressing form must match the station's layout."""
    form = _layout_form(station_template)
    for s_idx, sec in enumerate(sidecar["sections"]):
        for b_idx, b in enumerate(sec["blocks"]):
            if b.get("kind") != "atomic":
                continue
            uses_flat = "slot_index" in b
            if form == "flat_slots" and not uses_flat:
                raise EditorMetadataError(
                    f"sections[{s_idx}].blocks[{b_idx}]: station uses "
                    f"flat_slots layout but block uses row_idx/block_idx "
                    f"addressing. Use slot_index instead."
                )
            if form == "row_template" and uses_flat:
                raise EditorMetadataError(
                    f"sections[{s_idx}].blocks[{b_idx}]: station uses "
                    f"row_template layout but block uses slot_index. "
                    f"Use row_idx + block_idx instead."
                )


def _validate_atomic_bounds(sidecar: dict, station_template: dict) -> None:
    """Drift rule 7: atomic indices must fit the station's dimensions."""
    layout = station_template.get("layout", {})
    flat_n = len(layout.get("flat_slots", []))
    n_rows = len(layout.get("row_start_offsets", []))
    n_blocks_per_row = len(layout.get("row_template", []))
    for s_idx, sec in enumerate(sidecar["sections"]):
        for b_idx, b in enumerate(sec["blocks"]):
            if b.get("kind") != "atomic":
                continue
            if "slot_index" in b:
                if not (0 <= b["slot_index"] < flat_n):
                    raise EditorMetadataError(
                        f"sections[{s_idx}].blocks[{b_idx}]: "
                        f"slot_index={b['slot_index']} out of bounds "
                        f"(station has {flat_n} flat_slots)."
                    )
            else:
                if not (0 <= b["row_idx"] < n_rows):
                    raise EditorMetadataError(
                        f"sections[{s_idx}].blocks[{b_idx}]: "
                        f"row_idx={b['row_idx']} out of bounds "
                        f"(station has {n_rows} rows)."
                    )
                if not (0 <= b["block_idx"] < n_blocks_per_row):
                    raise EditorMetadataError(
                        f"sections[{s_idx}].blocks[{b_idx}]: "
                        f"block_idx={b['block_idx']} out of bounds "
                        f"(station's row_template has {n_blocks_per_row} blocks)."
                    )


def _validate_macro_refs(sidecar: dict) -> None:
    """Drift rule 2: macro `ref` must point at an existing macros[].id."""
    macro_ids = {m["id"] for m in sidecar.get("macros", [])}
    for s_idx, sec in enumerate(sidecar["sections"]):
        for b_idx, b in enumerate(sec["blocks"]):
            if b.get("kind") != "macro":
                continue
            if b["ref"] not in macro_ids:
                raise EditorMetadataError(
                    f"sections[{s_idx}].blocks[{b_idx}]: macro ref "
                    f"{b['ref']!r} doesn't exist. Available: "
                    f"{sorted(macro_ids)}."
                )


def _validate_layout_order_refs(sidecar: dict) -> None:
    """Drift rule 3: layout_order references each defined section
    EXACTLY ONCE.

    Slice-review finding 3: tightened from "every layout_order entry
    must match SOME section" to "set(layout_order) ==
    set(section_ids)" — i.e. cardinality + completeness enforced.

    Rationale: under the loose check, a section could exist in
    `sections[]` and never appear in `layout_order`, silently hiding
    it from the editor. A user renaming a section but forgetting to
    update `layout_order` would lose a section without a clean
    error.

    If hiding a section is later needed as a feature, add a separate
    `section.hidden: bool` field (in a follow-on slice). v1: explicit
    is better than implicit; missing-from-layout_order is an error.
    """
    section_ids = {s["id"] for s in sidecar["sections"]}
    layout_ids = set(sidecar["layout_order"])

    for i, sid in enumerate(sidecar["layout_order"]):
        if sid not in section_ids:
            raise EditorMetadataError(
                f"layout_order[{i}]={sid!r} doesn't match any section. "
                f"Available: {sorted(section_ids)}."
            )

    missing = section_ids - layout_ids
    if missing:
        raise EditorMetadataError(
            f"sections {sorted(missing)} are defined but not listed "
            f"in layout_order. Every section must appear in "
            f"layout_order exactly once (drift rule 3). To hide a "
            f"section, remove it from sections[] entirely; the editor "
            f"has no 'hidden but defined' state in v1."
        )

    # Drift rule 3 also forbids duplicates: each section appears once
    # in layout_order. Since the set-difference check above passes,
    # this is just len-vs-set-len.
    if len(sidecar["layout_order"]) != len(layout_ids):
        # Find the duplicate(s) for a clean error message.
        seen: set[str] = set()
        dups: set[str] = set()
        for sid in sidecar["layout_order"]:
            if sid in seen:
                dups.add(sid)
            seen.add(sid)
        raise EditorMetadataError(
            f"layout_order contains duplicate section id(s): "
            f"{sorted(dups)}. Each section appears once."
        )


def _validate_repeat_with_offsets(sidecar: dict, station_template: dict) -> None:
    """Drift rules 4 + 5: repeat_with_offsets cardinality + values match
    the station's row_start_offsets."""
    layout = station_template.get("layout", {})
    station_offsets = layout.get("row_start_offsets", [])
    for s_idx, sec in enumerate(sidecar["sections"]):
        rwo = sec.get("repeat_with_offsets")
        if rwo is None:
            continue
        if len(rwo) != len(station_offsets):
            raise EditorMetadataError(
                f"sections[{s_idx}].repeat_with_offsets has "
                f"{len(rwo)} entries but station's row_start_offsets "
                f"has {len(station_offsets)}. Align them."
            )
        for j, (sidecar_val, station_val) in enumerate(zip(rwo, station_offsets)):
            if sidecar_val != station_val:
                raise EditorMetadataError(
                    f"sections[{s_idx}].repeat_with_offsets[{j}]="
                    f"{sidecar_val} doesn't match station's "
                    f"row_start_offsets[{j}]={station_val}. One file or "
                    f"the other was hand-edited; align them."
                )


def _flatten_atomic_addresses(sidecar: dict) -> list[tuple]:
    """Return the list of unique addressing tuples emitted by all
    atomic blocks (after section ordering but BEFORE macro expansion).

    For repeat_with_offsets sections, the `row_idx` is overridden per
    iteration — this function emits the per-iteration tuples.

    Used by drift rules 1 + 8 to detect orphan and duplicate
    references.
    """
    out: list[tuple] = []
    for sec in sidecar["sections"]:
        rwo = sec.get("repeat_with_offsets")
        if rwo is None:
            for b in sec["blocks"]:
                if b.get("kind") != "atomic":
                    continue
                if "slot_index" in b:
                    out.append(("flat", b["slot_index"]))
                else:
                    out.append(("row", b["row_idx"], b["block_idx"]))
        else:
            for row_idx in range(len(rwo)):
                for b in sec["blocks"]:
                    if b.get("kind") != "atomic":
                        continue
                    # In repeat_with_offsets sections, atomics ignore their
                    # own row_idx (the offset iteration drives it).
                    out.append(("row", row_idx, b["block_idx"]))
    return out


def _validate_no_duplicate_atomics(sidecar: dict, station_template: dict) -> None:
    """Drift rule 8: each slot may appear in at most one atomic block
    across the sidecar (after expansion)."""
    addresses = _flatten_atomic_addresses(sidecar)
    seen: set[tuple] = set()
    for addr in addresses:
        if addr in seen:
            raise EditorMetadataError(
                f"duplicate atomic reference: {addr} appears in more than "
                f"one section. Each slot belongs to exactly one section."
            )
        seen.add(addr)


def _validate_no_orphan_slots(sidecar: dict, station_template: dict) -> None:
    """Drift rule 1: every slot in the station template must be referenced
    by some atomic block in the sidecar.

    Limitation: macro blocks don't have known addresses yet (see
    `expand_section` docstring note). This means stations using
    macros today get a more lenient orphan check — any slot not
    explicitly atomic-referenced is treated as "covered by a macro
    somewhere" and skipped. Sidecars where every slot has a macro
    cover technically pass this rule even if the coverage is wrong.

    The §16.2.4 (macro editing) slice will revisit this when macros
    gain per-pick addressing.
    """
    form = _layout_form(station_template)
    addresses = set(_flatten_atomic_addresses(sidecar))

    # If the sidecar contains any macros, we can't fully verify coverage
    # yet (see docstring). Skip the orphan check entirely.
    if sidecar.get("macros"):
        return

    layout = station_template.get("layout", {})
    if form == "flat_slots":
        n = len(layout.get("flat_slots", []))
        expected = {("flat", i) for i in range(n)}
    else:
        n_rows = len(layout.get("row_start_offsets", []))
        n_blocks = len(layout.get("row_template", []))
        expected = {
            ("row", r, b)
            for r in range(n_rows)
            for b in range(n_blocks)
        }
    orphans = expected - addresses
    if orphans:
        raise EditorMetadataError(
            f"orphan slots: {sorted(orphans)[:5]}"
            f"{'…' if len(orphans) > 5 else ''} "
            f"({len(orphans)} total) — these station slots aren't "
            f"referenced by any section. Add them to a section or "
            f"remove them from the station template."
        )


def _validate_macro_specs(sidecar: dict, station_template: dict) -> None:
    """Drift rule 10: each macro's `spec` must validate as a BlockSpec
    against the station's declared sources + supported strategies.

    Reuses the station template's source declarations + the engine's
    SUPPORTED_STRATEGIES list. Doesn't run the full `parse_template`
    (the macro's spec is a SLOT-level fragment, not a full template),
    just the equivalent per-block checks.
    """
    from engine.template import SUPPORTED_STRATEGIES

    sources_by_name = {
        s["name"]: s for s in station_template.get("sources", [])
    }
    for i, m in enumerate(sidecar.get("macros", [])):
        spec = m["spec"]
        if not isinstance(spec, dict):
            raise EditorMetadataError(
                f"macros[{i}].spec must be an object."
            )
        strat = spec.get("strategy")
        if strat not in SUPPORTED_STRATEGIES:
            raise EditorMetadataError(
                f"macros[{i}].spec.strategy={strat!r} not in supported "
                f"strategies {sorted(SUPPORTED_STRATEGIES)}."
            )
        src_name = spec.get("source")
        if src_name not in sources_by_name:
            raise EditorMetadataError(
                f"macros[{i}].spec.source={src_name!r} not declared in "
                f"station's sources. Available: {sorted(sources_by_name)}."
            )
        if sources_by_name[src_name]["role"] != "content":
            raise EditorMetadataError(
                f"macros[{i}].spec.source={src_name!r} is role="
                f"{sources_by_name[src_name]['role']!r}; macros can only "
                f"sample from content sources."
            )
        size = spec.get("size")
        if not isinstance(size, int) or size < 1:
            raise EditorMetadataError(
                f"macros[{i}].spec.size must be a positive int; "
                f"got {size!r}."
            )
