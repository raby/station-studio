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

# §16.2.5.t13 — grammar constants + error type + grammar walk +
# `validate_sidecar_grammar` public alias + `SidecarJSON`
# TypeDecorator moved to `engine.sidecar_types` (leaf module so
# `engine/models.py` doesn't pull in this 1.5K-LOC file). Re-exported
# here so existing call sites (api routers, tests, scripts) keep
# importing them from `engine.editor_metadata` without churn.
from engine.sidecar_types import (
    ALLOWED_MACRO_KEYS,
    ALLOWED_SECTION_KEYS,
    ALLOWED_TOP_LEVEL,
    ATOMIC_FLAT_KEYS,
    ATOMIC_ROW_KEYS,
    EditorMetadataError,
    MACRO_BLOCK_KEYS,
    REQUIRED_MACRO_KEYS,
    REQUIRED_SECTION_KEYS,
    REQUIRED_TOP_LEVEL,
    SidecarJSON,
    validate_sidecar_grammar,
)

__all__ = (
    # Re-exported from engine.sidecar_types
    "ALLOWED_MACRO_KEYS", "ALLOWED_SECTION_KEYS", "ALLOWED_TOP_LEVEL",
    "ATOMIC_FLAT_KEYS", "ATOMIC_ROW_KEYS", "EditorMetadataError",
    "MACRO_BLOCK_KEYS", "REQUIRED_MACRO_KEYS", "REQUIRED_SECTION_KEYS",
    "REQUIRED_TOP_LEVEL", "SidecarJSON", "validate_sidecar_grammar",
    # Native to this module
    "AtomicRef", "MacroPlaceholder", "MacroResolutionError",
    "SYSTEM_SECTION_IDS", "derive_sidecar", "ensure_unsorted_section",
    "expand_section", "is_reserved_id_prefix", "is_system_section",
    "lookup_macro", "lookup_section", "parse_sidecar",
    "resolve_macro_addresses", "shift_atomic_slot_indices",
    "synthesize_trivial_sidecar", "validate_sidecar",
    "validate_sidecar_semantics",
)


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
    output. §16.2.4 SHIPPED `resolve_macro_addresses` (below) which
    derives the addresses externally — patch_macro uses it for the
    edit-once-applies-to-all fanout without stamping them onto the
    placeholder itself. Future per-pick diff/preview surfaces (e.g.
    §16.2.6 staged-edit) can stamp positions onto MacroPlaceholder
    if the cost of re-walking the sidecar becomes meaningful.

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
    #
    # c1 review #3 finding #2: explicit `encoding='utf-8'` + catch
    # UnicodeDecodeError. Without these, a sidecar file saved by a
    # Windows editor as cp1252 or UTF-16 (BOM mismatch) raises
    # UnicodeDecodeError (subclass of ValueError) from read_text()
    # using the locale default. Pre-fix, that escaped parse_sidecar
    # entirely + bubbled to populate_db_sidecars's `(EditorMetadataError,
    # OSError, json.JSONDecodeError)` catch (no ValueError) → lifespan
    # 'auto-seed failed' and every other un-authored station stays
    # NULL too. Wrapping here keeps the "all disk-format failures
    # become EditorMetadataError" contract intact for every caller.
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise EditorMetadataError(
            f"sidecar JSON malformed at {path}: {e}"
        ) from e
    validate_sidecar(raw, station_template)
    return raw


def _check_template_xor_invariant(station_template: dict) -> None:
    """Engine's flat_slots-XOR-row_template guard for raw-JSON templates.

    `parse_template` enforces this on PARSED templates; callers that
    receive raw JSON (sidecar validation, derive_sidecar, disk-load)
    must invoke this explicitly so a hand-edited station with BOTH
    fields populated produces a clean template-shape error rather
    than masquerading as a sidecar bug.

    Extracted in §16.2.5.t15 review #1 fix #4 so the `validate_sidecar`
    wrapper can run it BEFORE grammar (the original "fail fast on the
    real problem" order). `validate_sidecar_semantics` also calls it
    for direct callers that bypass the wrapper.
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


def validate_sidecar_semantics(sidecar: dict, station_template: dict) -> None:
    """Cross-document drift rules — sidecar against its station_template.

    §16.2.5.t15 split: this is what `_persist_sidecar` calls. The
    grammar walk is now the TypeDecorator's responsibility at bind
    time (one walk per mutation, not two).

    **REQUIRES** `sidecar` has already passed
    `validate_sidecar_grammar` OR is structurally well-formed by
    construction. Safe sources:
      * `_persist_sidecar` after the TypeDecorator's bind-time
        grammar gate (§16.2.5.t4) — the normal route path.
      * A deep-copy of an already-stored `Station.sidecar` value
        (the column type re-validated on its last bind).
    Calling with a structurally-invalid sidecar may raise
    `KeyError` on `sidecar["sections"]` / `["layout_order"]`,
    `TypeError` if `sections` isn't a list, or surface a confusing
    drift-rule error against malformed inputs the grammar would
    have caught cleanly. If you got the sidecar from an untrusted
    source — disk JSON, third-party tool, ad-hoc dict — call
    `validate_sidecar` instead (composes XOR + grammar + semantics
    in the order that produces the cleanest error message).

    Call order (drift-rule numbering per §16.2.0):

      rule 6   layout form consistency
      rule 2   macro refs
      rule 3   layout_order refs
      rule 10  macro spec validity
      rule 4+5 repeat_with_offsets cardinality + values
      rule 7   atomic bounds
      rule 8   duplicate atomics
      rule 9   non-overlapping macros
      rule 1   orphan slots

    Mid-slice review finding 3 (preserved): enforce the engine's
    XOR invariant on `station_template` up-front via the shared
    `_check_template_xor_invariant` helper. `parse_template` enforces
    it on PARSED templates; here we get raw JSON and must guard
    against a hand-edited station JSON with both flat_slots AND
    row_template populated (which would otherwise validate the
    sidecar against the flat-form rules only, masking the underlying
    station bug). The wrapper `validate_sidecar` runs the XOR check
    BEFORE grammar so the cleanest error surfaces first; this direct-
    call entry point re-runs the helper so `_persist_sidecar` (which
    calls only semantics) still gets defense-in-depth.
    """
    _check_template_xor_invariant(station_template)

    _validate_layout_form_consistency(sidecar, station_template)
    _validate_macro_refs(sidecar)
    _validate_layout_order_refs(sidecar)
    _validate_macro_specs(sidecar, station_template)
    _validate_repeat_with_offsets(sidecar, station_template)
    _validate_atomic_bounds(sidecar, station_template)
    _validate_no_duplicate_atomics(sidecar, station_template)
    _validate_no_overlapping_macros(sidecar, station_template)
    _validate_no_orphan_slots(sidecar, station_template)


def validate_sidecar(sidecar: dict, station_template: dict) -> None:
    """Run template-XOR → grammar → semantics in the cleanest-error order.

    Use for any caller that wants to fail-fast on the cleanest error
    message: a station with a malformed template (both flat_slots
    AND row_template populated) surfaces the TEMPLATE error before
    any sidecar-shape complaint; malformed sidecar structure surfaces
    a grammar error before semantic cross-reference confusion.

    Order matches the pre-§16.2.5.t15 monolithic `validate_sidecar`:

      1. `_check_template_xor_invariant(template)` — template shape
      2. `validate_sidecar_grammar(sidecar)` — sidecar structure
      3. `validate_sidecar_semantics(sidecar, template)` — cross-doc
         drift rules (which re-runs the XOR helper for defense-in-
         depth in case it's called directly — cheap)

    Callers:
      - `_load_sidecar_disk` — disk JSON, never pre-validated
      - `_resolve_sidecar` route-layer DB read — possibly grandfathered
        broken DB-column content (lenient mode falls back; strict
        mode 400s)
      - `derive_sidecar` — re-validates the derived output
      - tests + fixtures

    NOT used by `_persist_sidecar` since §16.2.5.t15 — that path
    relies on the TypeDecorator for grammar at bind time + calls
    `validate_sidecar_semantics` directly to avoid the double walk
    (2× O(sections+blocks+macros) → 1× per mutation; matters at
    Rebirth Radio's 2,196-block scale and at §16.2.6 live-preview's
    per-keystroke validate cadence).
    """
    _check_template_xor_invariant(station_template)
    validate_sidecar_grammar(sidecar)
    validate_sidecar_semantics(sidecar, station_template)


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


class MacroResolutionError(Exception):
    """A sidecar's macro-position math can't be computed.

    Raised by `resolve_macro_addresses` when a section's anchor
    yields a negative `section_start` (leading macros wider than
    the first atomic's slot_index) or when atomic blocks within a
    section are non-contiguous (gaps not absorbed by macro
    widths). These slip through `validate_sidecar` because drift
    rule 1 (no-orphan-slots) is skipped whenever the sidecar has
    any macros — see `_validate_no_orphan_slots`'s docstring.
    Callers (`api/routers/editor.py:patch_macro`) translate the
    exception into a 400 with the operator-readable detail.
    """


def resolve_macro_addresses(sidecar: dict, macro_id: str) -> list[dict]:
    """Return the list of unique block-address dicts that expand from `macro_id`.

    Sidecar grammar (§16.2.1): sections have a `blocks` list; each
    block is either ``{kind: atomic, slot_index | (row_idx,
    block_idx)}`` or ``{kind: macro, ref: <id>}``. Macros don't
    carry slot positions — they reference an entry in
    ``sidecar["macros"]`` with a `repeat` count. So to compute
    the engine-template addresses a macro instance covers we walk
    the SECTION it lives in:

      1. Find the section's first atomic block — its slot_index /
         block_idx anchors the section to a real layout position.
      2. Compute `section_start` by subtracting the cumulative
         width of blocks BEFORE the anchor (each atomic = 1, each
         macro = its `repeat`).
      3. Walk the section again, advancing a running `pos`. For
         every macro block whose `ref == macro_id`, emit
         `macro.repeat` consecutive addresses starting at `pos`.

    Cross-checks every subsequent atomic's declared position
    against the running `pos`; raises `MacroResolutionError` on
    mismatch (drift rule 1's orphan check is skipped when
    macros are present — see `_validate_no_orphan_slots`).

    De-dupes — for row_template addressing, the same shared
    `block_idx` may be referenced by multiple sections (one per
    row); applying the patch N times is redundant and wrong if
    any field has side effects.

    Sections that are 100% macros (no atomic anchor) are skipped:
    we cannot compute slot positions without an anchor. If the
    target macro lived only in such sections, the caller sees an
    empty list. (In practice derive_sidecar + hand-authored
    sidecars always intersperse atomic blocks; an all-macro
    section would have to be deliberately authored.)

    Lifted here from `api/routers/editor.py` in §16.2.4 c1 review
    fix #10: the algorithm is a fundamental sidecar-grammar
    primitive (it derives the position information that
    `expand_section`'s MacroPlaceholder doesn't carry today — see
    §16.2.4's design note for the planned `expand_section`
    extension). Lives next to `expand_section` + `lookup_macro`
    so future macro-addressing work has one home.
    """
    macros_by_id = {m["id"]: m for m in sidecar.get("macros", [])}
    addresses: list[dict] = []
    seen: set[tuple] = set()

    def _block_width(b: dict) -> int:
        if b.get("kind") == "atomic":
            return 1
        m = macros_by_id.get(b.get("ref"))
        return m.get("repeat", 1) if m else 1

    for section in sidecar.get("sections", []):
        blocks_list = section.get("blocks", [])
        anchor_idx: int | None = None
        anchor_pos: int | None = None
        anchor_form: str | None = None  # "flat" | "row"
        for j, b in enumerate(blocks_list):
            if b.get("kind") != "atomic":
                continue
            if "slot_index" in b:
                anchor_idx, anchor_pos, anchor_form = j, b["slot_index"], "flat"
            else:
                anchor_idx, anchor_pos, anchor_form = j, b["block_idx"], "row"
            break
        if anchor_idx is None:
            continue  # all-macro section — un-anchorable, skip.

        cumulative = sum(_block_width(blocks_list[k]) for k in range(anchor_idx))
        section_start = anchor_pos - cumulative

        if section_start < 0:
            raise MacroResolutionError(
                f"sidecar section '{section.get('id', '?')}': computed "
                f"section_start={section_start} is negative — the "
                f"leading macros' total width ({cumulative}) exceeds "
                f"the first atomic anchor's position ({anchor_pos})."
            )

        pos = section_start
        for b in blocks_list:
            if b.get("kind") == "atomic":
                declared = b.get("slot_index") if anchor_form == "flat" \
                    else b.get("block_idx")
                if declared != pos:
                    field = "slot_index" if anchor_form == "flat" \
                        else "block_idx"
                    raise MacroResolutionError(
                        f"sidecar section '{section.get('id', '?')}': "
                        f"atomic block's {field}={declared} doesn't "
                        f"match the running position ({pos}) implied "
                        f"by preceding blocks. The section's coverage "
                        f"is non-contiguous."
                    )
            w = _block_width(b)
            if b.get("kind") == "macro" and b.get("ref") == macro_id:
                for i in range(w):
                    if anchor_form == "flat":
                        key = ("slot_index", pos + i)
                        addr = {"slot_index": pos + i}
                    else:
                        key = ("block_idx", pos + i)
                        addr = {"block_idx": pos + i}
                    if key not in seen:
                        seen.add(key)
                        addresses.append(addr)
            pos += w

    return addresses


# §16.2.5: structural-edit support
#
# `_unsorted` is the reserved section id for orphaned blocks (Decision
# D2 — when an operator deletes a non-empty section, its atomic blocks
# move here instead of being lost). The leading underscore is the UI
# signal that this is a system bin (the editor hides the rename
# affordance on it). The handler creates it on first need and appends
# to layout_order so it stays visible.
UNSORTED_SECTION_ID = "_unsorted"
UNSORTED_SECTION_NAME = "Unsorted (moved from a deleted section)"


# §16.2.5.t2 — system-section abstraction. The "reserved id" convention
# is a leading underscore (today only `_unsorted`); centralising the
# predicate makes adding a second bin (`_archived` on retype,
# `_macro_orphans` on cascade) a single-site change. Before this, the
# `id.startswith('_')` literal was duplicated across 5 sites in 3
# layers (canDropOn / SectionHeader / EditorCanvas / create_section /
# rename+delete guards).
SYSTEM_SECTION_IDS: frozenset[str] = frozenset({UNSORTED_SECTION_ID})


def is_reserved_id_prefix(section_id: str) -> bool:
    """True if `section_id` uses the reserved leading-underscore
    convention. Used by `create_section` to BLOCK new operator-named
    ids that look system-managed (forward-compat for adding more
    SYSTEM_SECTION_IDS later — operators can't claim `_archived`
    today, then have a future system bin steal the name).

    Distinct from `is_system_section` (which checks
    SYSTEM_SECTION_IDS membership). Pre-c1-review #3: the two were
    conflated, so operators with legacy hand-authored sidecars
    containing `_archive` etc. could not rename / delete / move
    those sections through the UI even though the id wasn't a
    current system bin.
    """
    return section_id.startswith("_")


def is_system_section(section_id: str) -> bool:
    """True if `section_id` is a CURRENT reserved system bin (member
    of `SYSTEM_SECTION_IDS`). Used by rename / delete / move guards
    to refuse runtime mutation of system-managed sections.

    c1 review #3 finding (Reuse angle): membership-only check.
    Previously this also matched any leading-underscore id, which
    blocked legacy hand-authored sidecars using `_archive` /
    `_specials` etc. from being managed by the operator. The
    leading-underscore convention is now enforced ONLY at
    `create_section` (via `is_reserved_id_prefix`) — forward-compat
    against operators claiming a future-system-bin name. Existing
    sidecars whose ids escaped that guard (legacy + hand-authored)
    are still operator-managed.
    """
    return section_id in SYSTEM_SECTION_IDS


# §16.2.5.t13: `SidecarJSON` TypeDecorator + grammar walk moved to
# `engine.sidecar_types` (leaf module). Re-exported at the top of this
# file for backward-compat with existing call sites.


def lookup_section(sidecar: dict, section_id: str) -> dict | None:
    """Return the section dict in `sidecar` whose `id` matches, or None.

    Sibling to `lookup_macro` — both are fundamental dict-scans over
    sidecar's id-keyed lists. Lives here so the 7 router call sites
    that need section lookup share one home with the engine's other
    sidecar-grammar primitives (and so future macro/section drift
    detectors can reach them without an api→engine reverse import).
    """
    for s in sidecar.get("sections", []):
        if s.get("id") == section_id:
            return s
    return None


def shift_atomic_slot_indices(sidecar: dict, threshold: int, delta: int) -> dict:
    """Return a deep-copied sidecar with every atomic block's
    `slot_index >= threshold` shifted by `delta`.

    Used by structural block edits (add/remove a flat_slots block):
    a NEW block at slot_index=N bumps every existing reference at
    slot_index >= N by +1; a REMOVED block at slot_index=N decrements
    every reference at slot_index > N by -1. Macros don't carry slot
    indices — their positions derive at read time via
    `resolve_macro_addresses` (§16.2.4 c1 review #10), so they shift
    implicitly when atomic anchors shift.

    This helper does NOT validate the resulting sidecar; the caller
    re-runs `validate_sidecar` + `_validate_no_overlapping_macros`
    after the template's structural change lands. Splitting the
    shift from the validation keeps the helper composable (add +
    remove paths share it).

    `repeat_with_offsets` sections store atomic blocks by
    `(row_idx, block_idx)`, NOT slot_index — those are unaffected by
    this shift (row_template structural edits are §16.2.5's
    block_idx-domain analogue, not yet implemented; the structural
    endpoint rejects with 400 until they are).
    """
    import copy as _copy
    out = _copy.deepcopy(sidecar)
    for section in out.get("sections", []):
        for block in section.get("blocks", []):
            if block.get("kind") != "atomic":
                continue
            si = block.get("slot_index")
            if isinstance(si, int) and si >= threshold:
                block["slot_index"] = si + delta
    return out


def ensure_unsorted_section(sidecar: dict) -> dict:
    """Return a deep-copied sidecar with an `_unsorted` section
    present in BOTH `sections[]` AND `layout_order`.

    Used by DELETE /sections/{id} (Decision D2): when the deleted
    section has blocks, they move into the Unsorted bin. The bin's
    `id` starts with an underscore (UI hides the rename affordance);
    the layout_order entry keeps it visible at the end of the canvas.

    c1 review #5: the prior implementation early-returned when the
    section existed in `sections[]` without checking layout_order.
    A hand-edited sidecar (or one partially mutated by a prior
    buggy write) could have the section but NOT the layout_order
    entry — blocks landed there but rendered nowhere. Heal both
    invariants unconditionally.
    """
    import copy as _copy
    out = _copy.deepcopy(sidecar)
    sections = out.setdefault("sections", [])
    if not any(s.get("id") == UNSORTED_SECTION_ID for s in sections):
        sections.append({
            "id": UNSORTED_SECTION_ID,
            "name": UNSORTED_SECTION_NAME,
            "blocks": [],
        })
    layout_order = out.setdefault("layout_order", [])
    if UNSORTED_SECTION_ID not in layout_order:
        layout_order.append(UNSORTED_SECTION_ID)
    return out


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
    `resolve_macro_addresses` helper above DOES compute slot
    positions for a given macro_id (added in §16.2.4 c1 review
    fix #10) — a future extension can stamp those positions onto
    MacroPlaceholder so the editor's frontend (chip rendering,
    diff/preview) gets per-pick addressing without re-walking the
    sidecar. See `resolve_macro_addresses`'s docstring for the
    anchoring + cross-check algorithm.
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


# ---------- internal drift-rule validators ----------------------------

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


def _validate_no_overlapping_macros(
    sidecar: dict, station_template: dict,
) -> None:
    """Drift rule 11 (§16.2.4 c1 review #12): macros must not be
    referenced by more than one section such that their expanded
    slot ranges would overlap.

    Drift rule 8 catches duplicate ATOMICS only; nothing catches
    "section A and section B both reference macro M, both anchor
    to non-overlapping section ranges, but M's expanded slots
    overlap with the OTHER section's atomics or another macro
    expansion." resolve_macro_addresses dedups silently rather
    than raising, masking the broken sidecar from patch_macro.

    Re-use resolve_macro_addresses for the heavy lifting: for each
    macro_id, accumulate its emitted addresses across all sections.
    If the same key appears via _flatten_atomic_addresses' atomic
    set, OR if any single section's emit would land on an address
    already produced by an EARLIER section, that's an overlap.

    Sections that can't be anchored (all-macro sections — skipped
    by resolve_macro_addresses) are also skipped here; their macro
    references are effectively orphaned but not OVERLAPPING with
    anything resolvable.
    """
    # Build the atomic-address set once (drift rule 8 already
    # verified no internal duplicates).
    atomic_addrs: set[tuple] = set()
    for sec in sidecar["sections"]:
        rwo = sec.get("repeat_with_offsets")
        for b in sec["blocks"]:
            if b.get("kind") != "atomic":
                continue
            if "slot_index" in b:
                atomic_addrs.add(("flat", b["slot_index"]))
            elif rwo is None:
                atomic_addrs.add(("row", b["row_idx"], b["block_idx"]))
            else:
                for row_idx in range(len(rwo)):
                    atomic_addrs.add(("row", row_idx, b["block_idx"]))

    # For each macro_id, collect emitted addresses; raise if any
    # collide with atomics OR with another macro's emissions.
    macro_addrs: dict[str, set[tuple]] = {}
    for macro in sidecar.get("macros", []):
        mid = macro.get("id")
        try:
            addrs = resolve_macro_addresses(sidecar, mid)
        except MacroResolutionError:
            # Negative section_start / non-contiguous: a separate
            # error class — surface as a validator failure too.
            raise EditorMetadataError(
                f"macro '{mid}' has unresolvable section positions. "
                f"Fix the sidecar's section layout (run "
                f"resolve_macro_addresses for the underlying error)."
            )
        keyed: set[tuple] = set()
        for a in addrs:
            if "slot_index" in a:
                k = ("flat", a["slot_index"])
            else:
                k = ("row", -1, a["block_idx"])  # -1 = shared row_template
            if k[0] == "flat" and k in atomic_addrs:
                raise EditorMetadataError(
                    f"macro '{mid}' expands onto slot_index={k[1]} "
                    f"which is also referenced by an atomic block. "
                    f"Each slot belongs to exactly one chip."
                )
            keyed.add(k)
        for other_id, other in macro_addrs.items():
            overlap = keyed & other
            if overlap:
                raise EditorMetadataError(
                    f"macros '{mid}' and '{other_id}' expand onto "
                    f"overlapping addresses {sorted(overlap)}. "
                    f"Restructure sections so each slot is covered "
                    f"by exactly one macro or atomic."
                )
        macro_addrs[mid] = keyed


def _validate_no_orphan_slots(sidecar: dict, station_template: dict) -> None:
    """Drift rule 1: every slot in the station template must be referenced
    by some atomic block in the sidecar.

    Limitation: macro blocks' addresses are derived at lookup time
    (resolve_macro_addresses above), not stored on the placeholder.
    This rule treats any slot not explicitly atomic-referenced as
    "covered by a macro somewhere" and skips it; sidecars where
    every slot has a macro cover technically pass this rule even
    if the coverage is wrong.

    Partial mitigation (§16.2.4 c1 review #12):
    `_validate_no_overlapping_macros` catches the cases this rule
    can't — sections with negative section_start, non-contiguous
    coverage, or macro/atomic overlap. Together they bound the
    drift surface, though a "macro covers slots N..M but should
    cover N..M-1" mis-grouping is still invisible (the macro is
    the source of truth for its own coverage).
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


# ---------------------------------------------------------------------------
# §16.14.t2 — `derive_sidecar` core helper (lifted from
# scripts/derive_sidecar.py). Previously the helper lived in scripts/,
# but `scripts/` no longer ships in the wheel (closed §16.13.t9), and
# `engine.seeding.auto_derive_missing_sidecars` imported from
# scripts.derive_sidecar at function-body level — which crashed at
# runtime with `ModuleNotFoundError` on any wheel install where
# bootstrap fired the helper.
#
# Caught by the §16.14.t1 slice-review sweep manually running
# `make wheel-smoke`. The function is pure (no I/O) and depends only
# on stdlib + the EditorMetadataError + validate_sidecar already
# defined above, so it lives here cleanly. The
# `scripts/derive_sidecar.py` script remains as a thin CLI wrapper
# importing this function — preserves the `python -m
# scripts.derive_sidecar --station X --out Y` invocation that's used
# from Makefile + docs + the CONTRIBUTING.md recipe.
# ---------------------------------------------------------------------------


# Minimum run length to propose a macro. Shorter runs stay atomic
# (the macro overhead of {id, repeat, spec} isn't worth it for ~2
# slots).
_DERIVE_MIN_MACRO_RUN = 3


def _derive_spec_key(slot: dict) -> str:
    """Build a hashable key for spec equality. Two slots with the
    same key can be grouped into one macro. JSON-with-sort_keys
    handles arbitrary primitive + nested values, so a future strategy
    adding a new spec field can't silently coalesce slots that differ
    only in that field."""
    import json as _json
    return _json.dumps(slot, sort_keys=True)


def _derive_is_daytime_jingle(slot: dict) -> bool:
    """Section-boundary detector. A Daytime jingle starts a new
    section."""
    return (
        slot.get("strategy") == "jingle"
        and slot.get("genre") == "Daytime"
    )


def _derive_section_ranges(slots: list[dict]) -> list[tuple[int, int]]:
    """Walk slots; return [(start, end_exclusive), ...] section ranges.

    Each section starts at a Daytime jingle (or at slot 0 if the
    template doesn't lead with one) and runs until the next Daytime
    jingle or end-of-template."""
    if not slots:
        return []
    boundaries = [
        i for i, s in enumerate(slots) if _derive_is_daytime_jingle(s)
    ]
    if not boundaries or boundaries[0] != 0:
        boundaries = [0] + boundaries
    ranges = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(slots)
        ranges.append((start, end))
    return ranges


def _derive_build_section(
    section_idx: int,
    slot_range: tuple[int, int],
    slots: list[dict],
    macros: list[dict],
    macro_counters: dict[str, int],
) -> dict:
    """Build a single section dict + append any macros it generates.

    `macros` is mutated to collect new macro definitions; the
    section's blocks list either references macros (kind=macro) or
    atomic slots (kind=atomic). `macro_counters` tracks per-strategy
    counters so macro IDs are stable + unique across the whole sidecar.
    """
    start, end = slot_range
    section_slots = slots[start:end]
    blocks: list[dict] = []

    i = 0
    while i < len(section_slots):
        run_key = _derive_spec_key(section_slots[i])
        run_len = 1
        while (
            i + run_len < len(section_slots)
            and _derive_spec_key(section_slots[i + run_len]) == run_key
        ):
            run_len += 1

        if run_len >= _DERIVE_MIN_MACRO_RUN:
            strategy = section_slots[i].get("strategy", "block")
            macro_counters[strategy] = macro_counters.get(strategy, 0) + 1
            macro_id = f"{strategy}-{macro_counters[strategy]}"
            macro_spec = dict(section_slots[i])
            macros.append({
                "id": macro_id,
                "name": f"{run_len} × {strategy}",
                "repeat": run_len,
                "spec": macro_spec,
            })
            blocks.append({"kind": "macro", "ref": macro_id})
            i += run_len
        else:
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

    Lifted from `scripts/derive_sidecar.py` in §16.14.t1's slice-
    review sweep (closes the deferred §16.14.t2). The script of the
    same name remains as the CLI wrapper for the `python -m
    scripts.derive_sidecar` invocation.
    """
    layout = station_template.get("layout", {})
    flat_slots = layout.get("flat_slots") or []
    if not flat_slots:
        raise EditorMetadataError(
            "derive_sidecar supports flat_slots stations only. "
            "row_template stations should be hand-authored (see "
            "stations/rebirth-classics.editor.json for the canonical "
            "shape)."
        )

    section_ranges = _derive_section_ranges(flat_slots)
    macros: list[dict] = []
    macro_counters: dict[str, int] = {}
    sections: list[dict] = []

    for idx, slot_range in enumerate(section_ranges):
        sections.append(_derive_build_section(
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
    # output; if it does, that's a derive-helper bug.
    validate_sidecar(sidecar, station_template)
    return sidecar
