"""§16.2.2 c1 — visual block editor backend endpoints.

Today: one endpoint, GET /api/stations/{slug}/sidecar — serves the
station's template + the editor sidecar (the visual-layer metadata
that turns a flat template into named sections + macros).

Decision E from DESIGN.md §16.2.2:

  * GET /api/stations/{slug}/sidecar
      → 200 {template, sidecar, sidecar_error}
      → 404 only when the station itself doesn't exist
      → NEVER 404 / 5xx for "sidecar missing or broken" — the
        frontend ALWAYS gets a valid sidecar dict to render against
        (server synthesizes a trivial one as the floor).

Sidecar-resolution algorithm:

  1. Look up the station by slug. 404 if missing.
  2. Look for `stations/{slug}.editor.json`:
     a. Absent → synthesize_trivial_sidecar(template). sidecar_error
        is null (this is the design-intended path for un-authored
        stations, not an error).
     b. Present + parse_sidecar succeeds → return that sidecar.
        sidecar_error is null.
     c. Present + parse_sidecar raises EditorMetadataError → fall
        back to synthesize_trivial_sidecar(template) + populate
        sidecar_error with the error message. The frontend renders
        a warning banner; the user can keep working.

Separate router (api/routers/editor.py) so sidecar concerns don't
crowd stations.py (which handles the Station ORM CRUD). §16.2.3+
will grow this module with PATCH / atomic-edit endpoints; the
single router scopes cleanly.
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_db, get_stations_dir
from api.schemas import (
    BlockAddress,
    BlockPatchRequest,
    BlockPatchResponse,
    BlockRetypeRequest,
    BlockRetypeResponse,
    EditorSidecarResponse,
    MacroPatchRequest,
    MacroPatchResponse,
    TemplateExportResponse,
)
from engine.editor_metadata import (
    EditorMetadataError,
    parse_sidecar,
    synthesize_trivial_sidecar,
)
from engine.models import Station
from engine.template import SUPPORTED_STRATEGIES, parse_template, TemplateError
from api.timefmt import iso as _iso


logger = logging.getLogger(__name__)

router = APIRouter(tags=["editor"])


# §16.2.3 Decision C — strategy → editable PARAMETER fields. Mirrors
# the TS `EDITABLE_FIELDS` in web/src/lib/editor.ts AND the engine's
# `_require_strategy_field` coupling in engine/template.py. A block
# PATCH may only touch fields in its strategy's set; strategy / source
# / size (structural) are never in any set → always rejected (Decision
# C: those are §16.2.4/§16.2.5). Kept in sync by
# tests/test_editable_fields_parity.py.
EDITABLE_FIELDS_BY_STRATEGY: dict[str, set[str]] = {
    # §16.2.4 D3: `allowlist_sources` joins the editable set for
    # the four content-sampling strategies (the
    # CONTENT_SAMPLING_STRATEGIES opt-in set from §16.14.t7).
    # Forbidden for the others (jingle, mix, prev_ref, archive_ref
    # — they don't sample from a candidate set). Closes the
    # §16.14.t7 c3 docs promise that "the block visualiser does
    # not yet expose per-block fields for editing (see
    # §16.2.4/§16.2.5 in the roadmap)."
    "pick": {"allowlist_sources"},
    "window": {"allowlist_sources"},
    "sequential": {"cursor_id", "allowlist_sources"},
    "jingle": {"genre"},
    "mix": {"genre", "exclusion_window"},
    "weighted": {"window", "allowlist_sources"},
    "prev_ref": {"position", "fallback_genre"},
    # §16.14.t6: archive_ref shares prev_ref's `position` field
    # (1-indexed). No `fallback_genre` — pure deterministic, no
    # fallback. Caught by the editable-fields parity test on the
    # backend ↔ frontend EDITABLE_FIELDS_BY_STRATEGY ↔
    # EDITABLE_FIELDS pair (t8 c1 picked up this miss from the
    # t6 sweep that only added the TS side).
    "archive_ref": {"position"},
}




@router.get(
    "/api/stations/{slug}/sidecar",
    response_model=EditorSidecarResponse,
)
def get_station_sidecar(
    slug: str,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Return the station's template + the editor sidecar.

    See module docstring for the resolution algorithm. The frontend
    treats this response as everything it needs to render the
    visual canvas — no second request required.
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")

    template: dict = station.template  # JSON column → dict

    sidecar_path = stations_dir / f"{slug}.editor.json"
    if not sidecar_path.is_file():
        # Case (a): no committed sidecar — synthesize the trivial
        # one. This is the DESIGN-intended path for un-authored
        # stations; sidecar_error stays null.
        return EditorSidecarResponse(
            template=template,
            sidecar=synthesize_trivial_sidecar(template),
            sidecar_error=None,
            updated_at=_iso(station.updated_at),
        )

    try:
        sidecar = parse_sidecar(sidecar_path, template)
    except EditorMetadataError as e:
        # Case (c): committed sidecar exists but is broken (one of the
        # 10 drift rules + grammar checks failed, OR JSON syntax was
        # bad — parse_sidecar wraps JSONDecodeError into the same
        # exception class per §16.2.1 c1 review finding 6).
        # Frontend renders a warning + the trivial fallback.
        logger.warning(
            "sidecar at %s failed validation; falling back to trivial: %s",
            sidecar_path, e,
        )
        error_message = str(e)
    except Exception as e:  # noqa: BLE001 — see c1 review 🟡-1 below
        # Case (c'): non-EditorMetadataError raised during the sidecar
        # load. Two known sources:
        #
        #   1. OSError from `path.read_text()` racing the is_file()
        #      check (file deleted / perm flipped / disk full).
        #   2. KeyError / TypeError from validate_sidecar() if the
        #      stored TEMPLATE is corrupted (DB hand-edit, partial
        #      restore). synthesize_trivial_sidecar already defends
        #      against missing-layout-keys; parse_sidecar doesn't.
        #
        # Decision E's contract: "frontend NEVER handles 'no sidecar'
        # — always renders the canvas against a valid sidecar." A 500
        # here breaks that contract. So we widen the catch + treat
        # any failure as a sidecar problem → trivial fallback +
        # populated sidecar_error.
        #
        # `logger.exception` preserves the traceback in case the
        # broader catch is hiding a real engine bug; the operator
        # sees it in the log without it blowing up the UI.
        logger.exception(
            "unexpected sidecar load error at %s; falling back to trivial",
            sidecar_path,
        )
        error_message = f"sidecar load failed: {type(e).__name__}: {e}"
    else:
        # Case (b): committed + valid sidecar.
        return EditorSidecarResponse(
            template=template,
            sidecar=sidecar,
            sidecar_error=None,
            updated_at=_iso(station.updated_at),
        )

    # Shared fallback path for both except branches.
    return EditorSidecarResponse(
        template=template,
        sidecar=synthesize_trivial_sidecar(template),
        sidecar_error=error_message,
        updated_at=_iso(station.updated_at),
    )


@router.patch(
    "/api/stations/{slug}/blocks",
    response_model=BlockPatchResponse,
)
def patch_block(
    slug: str,
    payload: BlockPatchRequest,
    db: Session = Depends(get_db),
):
    """Edit a single block's PARAMETER fields (§16.2.3 Decision B2).

    Targeted PATCH (vs whole-template PUT) so the SERVER enforces the
    parameter-only constraint (Decision C) rather than trusting the
    client. Algorithm:

      1. Resolve station (404).
      2. Optimistic concurrency (Decision D2): 409 if
         `expected_updated_at` was sent and no longer matches the
         row's `updated_at` (another tab / the wizard-edit route
         changed it).
      3. Resolve the address → block in flat_slots[slot_index] OR
         row_template[block_idx]. Editing a row_template block edits
         the SHARED block → all rows (Decision B). 400 wrong layout
         form / 404 out of range.
      4. Reject any field outside the strategy's editable set
         (Decision C — keeps strategy/source/size + the sidecar's
         atomic bounds invariant). 400.
      5. Apply (null clears an optional key), re-`parse_template` the
         whole doc (catches cross-block breakage), 400 on failure.
      6. Reassign `station.template` (marks the row dirty → the
         `updated_at` onupdate fires) + commit. Return the updated
         block + the new token.
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")

    current_token = _iso(station.updated_at)
    if (
        payload.expected_updated_at is not None
        and payload.expected_updated_at != current_token
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "station changed since you loaded it (another tab or the "
                    "edit route). Reload to see the latest, then re-apply."
                ),
                "current_updated_at": current_token,
            },
        )

    # Deep-copy so a mid-flight validation failure can't leave the
    # ORM object holding a partially-mutated template.
    template = copy.deepcopy(station.template)
    layout = template.get("layout", {})
    addr = payload.address

    if addr.slot_index is not None and addr.block_idx is not None:
        raise HTTPException(
            400, "address must set exactly one of slot_index / block_idx"
        )
    if addr.slot_index is not None:
        blocks = layout.get("flat_slots")
        if not isinstance(blocks, list):
            raise HTTPException(400, "station does not use a flat_slots layout")
        if not (0 <= addr.slot_index < len(blocks)):
            raise HTTPException(404, f"slot_index {addr.slot_index} out of range")
        block = blocks[addr.slot_index]
    elif addr.block_idx is not None:
        blocks = layout.get("row_template")
        if not isinstance(blocks, list):
            raise HTTPException(400, "station does not use a row_template layout")
        if not (0 <= addr.block_idx < len(blocks)):
            raise HTTPException(404, f"block_idx {addr.block_idx} out of range")
        block = blocks[addr.block_idx]
    else:
        raise HTTPException(400, "address must set slot_index or block_idx")

    # Decision C: parameter-only. Any field outside the strategy's
    # editable set (including strategy/source/size) is rejected.
    strategy = block.get("strategy")
    allowed = EDITABLE_FIELDS_BY_STRATEGY.get(strategy, set())
    rejected = sorted(set(payload.fields) - allowed)
    if rejected:
        raise HTTPException(
            400,
            f"fields {rejected} are not editable for strategy '{strategy}'. "
            f"Editable: {sorted(allowed) or '(none)'}. "
            f"(strategy/source/size + structural edits are §16.2.4/§16.2.5.)",
        )

    # Apply: null clears an optional key; otherwise set.
    for key, value in payload.fields.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value

    # Re-validate the WHOLE template (catches cross-block breakage +
    # the engine's per-field validators, e.g. cursor_id ∈ cursors_init).
    try:
        parse_template(template)
    except TemplateError as e:
        raise HTTPException(400, f"edit produces an invalid template: {e}")

    # Reassign (not in-place mutate) so SQLAlchemy marks the JSON
    # column dirty → the `updated_at` onupdate fires.
    station.template = template
    db.commit()
    db.refresh(station)

    return BlockPatchResponse(block=block, updated_at=_iso(station.updated_at))


# §16.2.4 c1: address-resolution helpers shared between PATCH /blocks,
# PATCH /blocks/retype, and PATCH /macros/{macro_id}. Mirrors the
# inline logic in patch_block above; extracting it keeps the three
# endpoints' address-handling consistent + avoids drift if the
# XOR-validation message phrasing needs to change.


def _resolve_block_address(template: dict, addr) -> dict:
    """Return the block dict in `template.layout` named by `addr`.

    Raises HTTPException (400 / 404) with operator-readable messages
    matching the patch_block phrasing — three endpoints share these
    so changes stay in lockstep.
    """
    layout = template.get("layout", {})
    if addr.slot_index is not None and addr.block_idx is not None:
        raise HTTPException(
            400, "address must set exactly one of slot_index / block_idx"
        )
    if addr.slot_index is not None:
        blocks = layout.get("flat_slots")
        if not isinstance(blocks, list):
            raise HTTPException(400, "station does not use a flat_slots layout")
        if not (0 <= addr.slot_index < len(blocks)):
            raise HTTPException(404, f"slot_index {addr.slot_index} out of range")
        return blocks[addr.slot_index]
    if addr.block_idx is not None:
        blocks = layout.get("row_template")
        if not isinstance(blocks, list):
            raise HTTPException(400, "station does not use a row_template layout")
        if not (0 <= addr.block_idx < len(blocks)):
            raise HTTPException(404, f"block_idx {addr.block_idx} out of range")
        return blocks[addr.block_idx]
    raise HTTPException(400, "address must set slot_index or block_idx")


def _resolve_macro_addresses(sidecar: dict, macro_id: str) -> list[dict]:
    """Return the list of unique block-address dicts that expand from `macro_id`.

    Sidecar grammar (§16.2.1): sections have a `blocks` list; each block
    is either `{kind: atomic, slot_index | (row_idx, block_idx)}` or
    `{kind: macro, ref: <id>}`. Macros don't carry slot positions —
    they reference an entry in `sidecar["macros"]` with a `repeat`
    count. So to compute the engine-template addresses a macro
    instance covers we walk the SECTION it lives in:

      1. Find the section's first atomic block — its slot_index /
         block_idx anchors the section to a real layout position.
      2. Compute `section_start` by subtracting the cumulative
         width of blocks BEFORE the anchor (each atomic = 1, each
         macro = its `repeat`).
      3. Walk the section again, advancing a running `pos`. For
         every macro block whose `ref == macro_id`, emit
         `macro.repeat` consecutive addresses starting at `pos`.

    De-dupes — for row_template addressing, the same shared
    `block_idx` may be referenced by multiple sections (one per
    row); applying the patch N times is redundant and wrong if any
    field has side effects.

    Sections that are 100% macros (no atomic anchor) are skipped:
    we cannot compute slot positions without an anchor. If the
    target macro lived only in such sections, the caller sees an
    empty list → 404 with a meaningful message. (In practice the
    derive_sidecar heuristic + hand-authored sidecars always
    intersperse atomic blocks; an all-macro section would have to
    be deliberately authored.)
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
        # 1. Find anchor (first atomic block) + its addressing form.
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

        # 2. Compute section_start.
        cumulative = sum(_block_width(blocks_list[k]) for k in range(anchor_idx))
        section_start = anchor_pos - cumulative

        # c1 review #3: a hand-authored sidecar with leading macros
        # wider than the first atomic's slot_index gives a NEGATIVE
        # section_start. Walking forward then hands Python negative
        # slot_indices → `flat_slots[-1]` silently mutates blocks
        # from the END of the layout. Fail loud instead.
        if section_start < 0:
            raise HTTPException(
                400,
                f"sidecar section '{section.get('id', '?')}': computed "
                f"section_start={section_start} is negative — the "
                f"leading macros' total width ({cumulative}) exceeds "
                f"the first atomic anchor's position ({anchor_pos}). "
                f"Fix the sidecar's section layout or regenerate via "
                f"`python -m scripts.derive_sidecar`.",
            )

        # 3. Walk + collect addresses for matching macros.
        # c1 review #2: when a subsequent atomic block is encountered,
        # cross-check that its declared slot_index/block_idx matches
        # the running `pos`. If they disagree, the section has a gap
        # (non-contiguous coverage) — the §16.2.1 orphan-slots check
        # is SKIPPED whenever the sidecar has any macros (deliberate
        # limitation), so gaps can silently slip through and produce
        # wrong macro→slot mappings. Fail loud here instead.
        pos = section_start
        for b in blocks_list:
            if b.get("kind") == "atomic":
                declared = b.get("slot_index") if anchor_form == "flat" \
                    else b.get("block_idx")
                if declared != pos:
                    field = "slot_index" if anchor_form == "flat" \
                        else "block_idx"
                    raise HTTPException(
                        400,
                        f"sidecar section '{section.get('id', '?')}': "
                        f"atomic block's {field}={declared} doesn't "
                        f"match the running position ({pos}) implied "
                        f"by preceding blocks. The section's coverage "
                        f"is non-contiguous — fix the sidecar or "
                        f"regenerate it.",
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


def _check_optimistic_token(station: Station, expected: str | None) -> None:
    """409 if `expected` is set + doesn't match the station's current
    `updated_at`. Shared by all three PATCH endpoints (Decision D2)."""
    current_token = _iso(station.updated_at)
    if expected is not None and expected != current_token:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "station changed since you loaded it (another tab or the "
                    "edit route). Reload to see the latest, then re-apply."
                ),
                "current_updated_at": current_token,
            },
        )


@router.patch(
    "/api/stations/{slug}/blocks/retype",
    response_model=BlockRetypeResponse,
)
def patch_block_retype(
    slug: str,
    payload: BlockRetypeRequest,
    db: Session = Depends(get_db),
):
    """Retype a block: change its `strategy` and/or `source` (§16.2.4 D2).

    Distinct from PATCH /blocks (parameter-only). Retyping shifts the
    valid-field-set + may require new fields (e.g. `position` becomes
    required when retyping to archive_ref/prev_ref). The endpoint
    sequences three writes against a deep-copy of the template:

      1. Apply `new_strategy` (if set) — validated against
         ``SUPPORTED_STRATEGIES``.
      2. Apply `new_source` (if set) — checked to exist in
         ``template.sources[]``. The role compatibility check
         (content vs reference per strategy) is left to
         ``parse_template`` (the §16.14.t6 strategy-aware role
         cross-check enforces it; we don't duplicate).
      3. Apply `new_fields` (null clears a key). All strategy-
         parameter fields are valid here (no parameter-only guard);
         old fields incompatible with the new strategy are dropped
         server-side AFTER the new_fields apply (so the operator
         can both clear AND set in one round-trip).
      4. Drop any keys NOT in the new strategy's editable set
         (rather than 400ing — the operator explicitly chose a new
         strategy that obviates them).

    Re-`parse_template` revalidates everything (role, valid-field-
    set, pool existence, cross-block consistency). 400 on any
    failure with the engine's error text inline.
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")
    _check_optimistic_token(station, payload.expected_updated_at)

    # c1 review #5: structural keys (strategy/source/size) must come
    # via new_strategy/new_source/(structural-edits-§16.2.5), NOT
    # smuggled through new_fields. Without this guard, an operator
    # could clear `strategy` via `new_fields: {"strategy": null}` —
    # step 3 would pop it, step 4 would drop everything except
    # {strategy, source, size}, and parse_template would 400 with a
    # confusing 'missing strategy' message. Reject up front instead.
    STRUCTURAL_KEYS = {"strategy", "source", "size"}
    smuggled = sorted(STRUCTURAL_KEYS & set(payload.new_fields))
    if smuggled:
        raise HTTPException(
            400,
            f"new_fields cannot carry structural keys {smuggled}. "
            "Use new_strategy / new_source for strategy + source "
            "changes; `size` changes are §16.2.5 (structural edits).",
        )

    template = copy.deepcopy(station.template)
    block = _resolve_block_address(template, payload.address)

    # 1. Apply new_strategy + 2. new_source on the in-place block
    # (template was deep-copied so this doesn't poison the ORM
    # state if validation later fails).
    if payload.new_strategy is not None:
        if payload.new_strategy not in SUPPORTED_STRATEGIES:
            raise HTTPException(
                400,
                f"new_strategy '{payload.new_strategy}' not supported. "
                f"Supported: {sorted(SUPPORTED_STRATEGIES)}.",
            )
        block["strategy"] = payload.new_strategy
    if payload.new_source is not None:
        known_sources = {s.get("name") for s in template.get("sources", [])}
        if payload.new_source not in known_sources:
            raise HTTPException(
                400,
                f"new_source '{payload.new_source}' not declared in "
                "template.sources[]. Add it there first (with the "
                "right role for the strategy) or pick an existing source.",
            )
        block["source"] = payload.new_source

    # 3. Apply new_fields (null clears).
    for key, value in payload.new_fields.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value

    # 4. Drop keys not in the new strategy's editable set. parse_template
    # would 400 on stale ones anyway (e.g. `genre` on a non-jingle/mix
    # strategy); pre-dropping makes the retype's intent clear.
    strategy = block.get("strategy")
    allowed = EDITABLE_FIELDS_BY_STRATEGY.get(strategy, set())
    structural = {"strategy", "source", "size"}
    for key in list(block.keys()):
        if key in structural or key in allowed:
            continue
        block.pop(key, None)

    try:
        parse_template(template)
    except TemplateError as e:
        raise HTTPException(400, f"retype produces an invalid template: {e}")

    station.template = template
    db.commit()
    db.refresh(station)

    return BlockRetypeResponse(block=block, updated_at=_iso(station.updated_at))


@router.patch(
    "/api/stations/{slug}/macros/{macro_id}",
    response_model=MacroPatchResponse,
)
def patch_macro(
    slug: str,
    macro_id: str,
    payload: MacroPatchRequest,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Edit-once-applies-to-all (§16.2.4 D1).

    Resolves `macro_id` against the station's editor sidecar (parsed +
    drift-validated) → gets the set of block addresses expanded from
    the macro → applies `payload.fields` to each via the same
    parameter-only logic as PATCH /blocks (per-block re-validation
    via parse_template at the end). All-or-nothing transaction: any
    block's parameter-only-guard rejection rolls back the whole
    write.

    The macro is resolved against the SIDECAR, not the template —
    sidecars define the "this run of consecutive identical blocks
    is a macro" grouping. If the sidecar is missing, broken, or
    doesn't contain the named macro, 404.
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")
    _check_optimistic_token(station, payload.expected_updated_at)

    template = copy.deepcopy(station.template)

    # Resolve the macro via the sidecar. Mirror the sidecar-resolution
    # algorithm from GET /sidecar (look for `stations/{slug}.editor.
    # json`; fall back to synthesize_trivial_sidecar on absence or
    # parse failure). For PATCH we tolerate the trivial-sidecar
    # fallback — un-authored stations have no MACROS by definition
    # (every chip is its own atomic block), so a macro_id lookup
    # against a trivial sidecar always 404s with a meaningful
    # "macro_id not found" message.
    sidecar_path = stations_dir / f"{slug}.editor.json"
    sidecar: dict
    if sidecar_path.is_file():
        try:
            # parse_sidecar takes a Path (mirrors GET /sidecar); it
            # also wraps JSONDecodeError as EditorMetadataError per
            # §16.2.1 c1 review #6, so EditorMetadataError covers the
            # validation + JSON-syntax paths.
            sidecar = parse_sidecar(sidecar_path, template)
        except EditorMetadataError as e:
            raise HTTPException(
                400,
                f"sidecar at {sidecar_path} is broken — fix it or "
                f"delete to regenerate from the template ({e}).",
            )
        except Exception as e:  # noqa: BLE001
            # c1 review #4: mirror GET /sidecar's wider catch. Two
            # known non-EditorMetadataError sources:
            #   1. OSError racing `is_file()` (file deleted / perms /
            #      disk error).
            #   2. KeyError/TypeError from a hand-edited station
            #      template that drops layout keys parse_sidecar
            #      assumes are present.
            # Unlike GET (which trivially falls back), mutation paths
            # can't safely synthesize-trivial — that'd silently lose
            # the operator's macro grouping. So we 400 with the same
            # phrasing as the validation path, but log the traceback
            # for engine-bug forensics.
            logger.exception(
                "unexpected sidecar load error at %s during macro patch",
                sidecar_path,
            )
            raise HTTPException(
                400,
                f"sidecar at {sidecar_path} failed to load "
                f"({type(e).__name__}: {e}) — fix or delete it before "
                f"editing macros.",
            )
    else:
        # Trivial sidecar has no macros by definition (one section,
        # all atomic) → the macro_id lookup below will 404 with a
        # meaningful message.
        sidecar = synthesize_trivial_sidecar(template)

    # Resolve macro_id → unique engine-template addresses by walking
    # the sidecar. See _resolve_macro_addresses' docstring for the
    # anchoring algorithm + de-dup rationale.
    deduped = _resolve_macro_addresses(sidecar, macro_id)

    if not deduped:
        raise HTTPException(
            404,
            f"macro_id '{macro_id}' not found in the station's "
            "sidecar. The macro either doesn't exist or the sidecar "
            "is stale (regenerate via `python -m scripts.derive_sidecar` "
            "if you've edited the template since).",
        )

    # Apply payload.fields to each addressed block. Parameter-only
    # guard fires per block — if any block rejects, the whole
    # transaction aborts (deep-copy isolation makes this clean).
    # c1 review #1: route through _resolve_block_address so any
    # sidecar→template drift (stale slot_index, layout-form mismatch)
    # surfaces as the same 400/404 contract patch_block uses, not
    # an uncaught IndexError/KeyError → 500.
    blocks_touched = 0
    for addr_dict in deduped:
        addr = BlockAddress(**addr_dict)
        block = _resolve_block_address(template, addr)

        strategy = block.get("strategy")
        allowed = EDITABLE_FIELDS_BY_STRATEGY.get(strategy, set())
        rejected = sorted(set(payload.fields) - allowed)
        if rejected:
            raise HTTPException(
                400,
                f"macro '{macro_id}': fields {rejected} are not "
                f"editable for strategy '{strategy}' (block at "
                f"{addr_dict}). Editable: {sorted(allowed) or '(none)'}. "
                "(Macro retyping is PATCH /blocks/retype per block; "
                "structural edits are §16.2.5.)",
            )
        for key, value in payload.fields.items():
            if value is None:
                block.pop(key, None)
            else:
                block[key] = value
        blocks_touched += 1

    try:
        parse_template(template)
    except TemplateError as e:
        raise HTTPException(400, f"macro edit produces an invalid template: {e}")

    station.template = template
    db.commit()
    db.refresh(station)

    return MacroPatchResponse(
        blocks_updated=blocks_touched,
        updated_at=_iso(station.updated_at),
    )


@router.post(
    "/api/stations/{slug}/export",
    response_model=TemplateExportResponse,
)
def export_template(
    slug: str,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Write the DB template to `stations/{slug}.json` — the A3
    'commit to disk' affordance (§16.2.3 Decision A3). DB is the live
    runtime truth; this produces the git-committable artifact on
    demand. Overwrites silently; the operator commits via git.

    `name` is forced to the canonical `Station.name` so an export
    never drifts from the row's display name (the wizard-edit PUT
    keeps them synced, but belt-and-suspenders).
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")

    doc = dict(station.template)
    doc["name"] = station.name
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    path = stations_dir / f"{slug}.json"
    path.write_text(text)
    return TemplateExportResponse(path=str(path), bytes=len(text.encode("utf-8")))
