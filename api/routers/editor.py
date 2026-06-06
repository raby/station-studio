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
    BlockMoveRequest,
    BlockPatchRequest,
    BlockPatchResponse,
    BlockRetypeRequest,
    BlockRetypeResponse,
    BlockStructureRequest,
    EditorSidecarResponse,
    MacroPatchRequest,
    MacroPatchResponse,
    SectionCreateRequest,
    SectionRenameRequest,
    SectionsOrderRequest,
    SidecarMutationResponse,
    TemplateExportResponse,
)
from engine.editor_metadata import (
    EditorMetadataError,
    MacroResolutionError,
    UNSORTED_SECTION_ID,
    UNSORTED_SECTION_NAME,
    ensure_unsorted_section,
    parse_sidecar,
    resolve_macro_addresses,
    shift_atomic_slot_indices,
    synthesize_trivial_sidecar,
    validate_sidecar,
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
# / size are never in any set → always rejected. Strategy + source
# edits go through PATCH /blocks/retype (§16.2.4 SHIPPED); size + add/
# remove are §16.2.5 (structural). Kept in sync by
# tests/test_editable_fields_parity.py.
# c1 review fix #8: frozenset values — mutable `set` at module level
# is a foot-gun: any future caller could do
# `EDITABLE_FIELDS_BY_STRATEGY["pick"].add(...)` and silently pollute
# every endpoint's allowlist forever. frozenset makes mutation a
# TypeError; the `set(payload.fields) - allowed` math works
# identically against frozensets.
EDITABLE_FIELDS_BY_STRATEGY: dict[str, frozenset[str]] = {
    # §16.2.4 D3: `allowlist_sources` joins the editable set for
    # the four content-sampling strategies (the
    # CONTENT_SAMPLING_STRATEGIES opt-in set from §16.14.t7).
    # Forbidden for the others (jingle, mix, prev_ref, archive_ref
    # — they don't sample from a candidate set). Closed the
    # §16.14.t7 c3 docs promise that "the block visualiser does
    # not yet expose per-block fields for editing" — operators
    # now edit allowlist_sources per chip via the §16.2.4 c2
    # DetailPanel `<ChipMultiInput>`.
    "pick": frozenset({"allowlist_sources"}),
    "window": frozenset({"allowlist_sources"}),
    "sequential": frozenset({"cursor_id", "allowlist_sources"}),
    "jingle": frozenset({"genre"}),
    "mix": frozenset({"genre", "exclusion_window"}),
    "weighted": frozenset({"window", "allowlist_sources"}),
    "prev_ref": frozenset({"position", "fallback_genre"}),
    # §16.14.t6: archive_ref shares prev_ref's `position` field
    # (1-indexed). No `fallback_genre` — pure deterministic, no
    # fallback. Caught by the editable-fields parity test on the
    # backend ↔ frontend EDITABLE_FIELDS_BY_STRATEGY ↔
    # EDITABLE_FIELDS pair (t8 c1 picked up this miss from the
    # t6 sweep that only added the TS side).
    "archive_ref": frozenset({"position"}),
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
    sidecar, error_message = _load_sidecar(station, template, stations_dir)
    return EditorSidecarResponse(
        template=template,
        sidecar=sidecar,
        sidecar_error=error_message,
        updated_at=_iso(station.updated_at),
    )


def _resolve_sidecar(
    station: Station,
    template: dict,
    stations_dir: Path,
    *,
    strict: bool,
) -> tuple[dict, str | None]:
    """Layered DB→file→trivial sidecar resolution (§16.2.5 D1).

    c1 review #7: unifies the two prior helpers (`_load_sidecar` for
    GET, `_load_sidecar_for_mutation` for writes). They diverged in
    one dimension only — what to do on a stored-but-broken sidecar:

    * `strict=False` (GET) — degrade to trivial-with-sidecar_error.
      Decision E from §16.2.2 says the frontend never handles "no
      sidecar"; it always renders against a valid sidecar dict,
      with `sidecar_error` populated for the warning banner.
    * `strict=True`  (mutation) — RAISE 400. Applying a write
      against the trivial fallback would silently lose the
      operator's macro/section grouping for this transaction; the
      operator has to fix the source before mutating.

    Order (regardless of strictness):
      1. `Station.sidecar` JSON column — the new source-of-truth.
      2. On-disk file `stations/{slug}.editor.json` — legacy + the
         "drop a file in stations/ and reload" authoring workflow.
      3. Trivial synthesised sidecar — the floor for un-authored
         stations (NULL column + no disk file).

    Returns `(sidecar_dict, error_message_or_None)`. The mutation
    callers ignore the second tuple element since they never see
    an error (strict=True raises instead).
    """
    slug = station.slug

    def _broken(source: str, err: Exception) -> tuple[dict, str | None]:
        if strict:
            # Already EditorMetadataError or wider — preserve the
            # exception class info in the message.
            cls = (
                type(err).__name__
                if not isinstance(err, EditorMetadataError) else ""
            )
            cls_prefix = f"{cls}: " if cls else ""
            raise HTTPException(
                400,
                f"{source} for '{slug}' failed validation — fix or "
                f"clear it to fall back to disk/trivial "
                f"({cls_prefix}{err}).",
            )
        logger.warning(
            "%s for slug=%s failed validation; falling back to trivial: %s",
            source, slug, err,
        )
        return synthesize_trivial_sidecar(template), str(err)

    def _unexpected(source: str, err: Exception) -> tuple[dict, str | None]:
        logger.exception(
            "unexpected sidecar load error: %s for slug=%s", source, slug,
        )
        if strict:
            raise HTTPException(
                400,
                f"{source} for '{slug}' failed to load "
                f"({type(err).__name__}: {err}).",
            )
        return (
            synthesize_trivial_sidecar(template),
            f"sidecar load failed: {type(err).__name__}: {err}",
        )

    # Case (1): DB column.
    if station.sidecar is not None:
        try:
            validate_sidecar(station.sidecar, template)
            return station.sidecar, None
        except EditorMetadataError as e:
            return _broken("Station.sidecar (DB)", e)
        except Exception as e:  # noqa: BLE001
            return _unexpected("Station.sidecar (DB)", e)

    # Case (2): on-disk file.
    sidecar_path = stations_dir / f"{slug}.editor.json"
    if sidecar_path.is_file():
        try:
            return parse_sidecar(sidecar_path, template), None
        except EditorMetadataError as e:
            return _broken(f"sidecar file at {sidecar_path}", e)
        except Exception as e:  # noqa: BLE001
            return _unexpected(f"sidecar file at {sidecar_path}", e)

    # Case (3): trivial floor.
    return synthesize_trivial_sidecar(template), None


def _load_sidecar(
    station: Station, template: dict, stations_dir: Path,
) -> tuple[dict, str | None]:
    """Lenient resolution for GET /sidecar. See `_resolve_sidecar`."""
    return _resolve_sidecar(station, template, stations_dir, strict=False)


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
    # c1 review #9: route through the shared helpers (extracted in c1)
    # so this endpoint, /blocks/retype, and /macros/{macro_id} stay in
    # lockstep on optimistic-concurrency + address-resolution phrasing.
    _check_optimistic_token(station, payload.expected_updated_at)

    # Deep-copy so a mid-flight validation failure can't leave the
    # ORM object holding a partially-mutated template.
    template = copy.deepcopy(station.template)
    block = _resolve_block_address(template, payload.address)

    # Decision C: parameter-only. Any field outside the strategy's
    # editable set (including strategy/source/size) is rejected.
    strategy = block.get("strategy")
    allowed = EDITABLE_FIELDS_BY_STRATEGY.get(strategy, frozenset())
    rejected = sorted(set(payload.fields) - allowed)
    if rejected:
        raise HTTPException(
            400,
            f"fields {rejected} are not editable for strategy '{strategy}'. "
            f"Editable: {sorted(allowed) or '(none)'}. "
            f"(strategy/source go through PATCH /blocks/retype; "
            f"size + structural edits are §16.2.5.)",
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


def _resolve_block_address(template: dict, addr: BlockAddress) -> dict:
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


def _load_sidecar_for_mutation(
    station: Station, template: dict, stations_dir: Path, slug: str,
) -> dict:
    """Strict resolution for mutation endpoints. See `_resolve_sidecar`.

    The slug arg is kept for API stability + clearer error messages
    even though `station.slug` would do; existing call sites pass it
    explicitly so changing the contract would cascade.
    """
    sidecar, _ = _resolve_sidecar(station, template, stations_dir, strict=True)
    return sidecar


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

    Re-`parse_template` revalidates structure, role, valid-field-
    set, and cross-block consistency. 400 on any failure with the
    engine's error text inline.

    NOTE — pool-existence is NOT checked here (c1 review #7).
    parse_template is a pure structural validator; the actual pool
    file (`pools/<source>.json` or whatever the manifest points at)
    is loaded by `load_pools` at generation time. A retype that
    points at a declared-but-pool-less source returns 200 here but
    the next playlist generation will fail with a load_pools error.
    Adding a load_pools check here would couple the editor to the
    pool-manifest layer; the proper home is a dedicated
    `/api/stations/{slug}/validate` endpoint (queued for a later
    §16.x slice). Frontend mitigates by filtering source dropdowns
    to declared sources only (D4 symmetric filtering).
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
    allowed = EDITABLE_FIELDS_BY_STRATEGY.get(strategy, frozenset())
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

    # §16.2.5 D1: resolve the sidecar via the layered helper —
    # Station.sidecar (DB column) → on-disk file → trivial fallback.
    # Mutation paths can't safely accept the trivial fallback unless
    # the sidecar is genuinely absent (no DB column AND no file);
    # a broken DB column or file is a 400 here (operator must fix
    # before mutating). Unlike GET, we can't silently degrade —
    # that'd lose the operator's macro grouping for this write.
    sidecar = _load_sidecar_for_mutation(station, template, stations_dir, slug)

    # Resolve macro_id → unique engine-template addresses by walking
    # the sidecar. See engine.editor_metadata.resolve_macro_addresses
    # for the anchoring algorithm + cross-checks. c1 review #10 moved
    # this primitive next to expand_section / lookup_macro so the
    # planned §16.2.4-design `expand_section` slot-position extension
    # has one home, not two.
    try:
        deduped = resolve_macro_addresses(sidecar, macro_id)
    except MacroResolutionError as e:
        # c1 review #2 + #3: negative-section_start and non-
        # contiguous-section errors come from the engine helper as
        # MacroResolutionError; translate to a 400 with the
        # operator-readable message + a regeneration hint.
        raise HTTPException(
            400,
            f"{e} Fix the sidecar's section layout or regenerate via "
            "`python -m scripts.derive_sidecar`.",
        )

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
    # c1 review #11: hoist `set(payload.fields)` — it doesn't change
    # across the loop, so recomputing N times for a wide macro is
    # wasted work.
    payload_keys = set(payload.fields)
    blocks_touched = 0
    for addr_dict in deduped:
        addr = BlockAddress(**addr_dict)
        block = _resolve_block_address(template, addr)

        strategy = block.get("strategy")
        allowed = EDITABLE_FIELDS_BY_STRATEGY.get(strategy, frozenset())
        rejected = sorted(payload_keys - allowed)
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

    # §16.2.5 D1: update the sidecar's macros[].spec + write to DB
    # (not disk — that's now the export artifact). Without this, the
    # committed sidecar's spec drifts from the template after every
    # macro edit — GET /sidecar would return a macro chip whose
    # DetailPanel shows the OLD spec value while the underlying
    # engine blocks have the new one.
    #
    # When the sidecar came from the trivial fallback (Station.sidecar
    # IS NULL AND no on-disk file), there's nothing to write back —
    # un-authored stations have no macros[] entries by definition, so
    # the `for macro in ...` loop is a no-op + we skip the column
    # update to avoid materializing the trivial sidecar in the DB
    # (preserves the "NULL = un-authored" semantics).
    spec_changed = False
    for macro in sidecar.get("macros", []):
        if macro.get("id") != macro_id:
            continue
        spec = macro.setdefault("spec", {})
        for key, value in payload.fields.items():
            if value is None:
                spec.pop(key, None)
            else:
                spec[key] = value
        spec_changed = True

    # c1 review #3: route through _persist_sidecar so drift rules
    # re-run on the post-edit sidecar (rule 10 — macro spec validity
    # — could otherwise drift if a spec mutation introduced a now-
    # invalid field). Mirrors the 6 §16.2.5 endpoints' shape;
    # consistent revalidation across all write paths.
    if spec_changed:
        _persist_sidecar(station, copy.deepcopy(sidecar), template=template)
    else:
        # Trivial-fallback path (no macros[] entry to update) → just
        # the template. We DON'T persist the synthesised sidecar:
        # writing it would falsely flip Station.sidecar from NULL
        # to a synthesised value + GET would prefer that over the
        # legitimate template-driven trivial.
        station.template = template
    db.commit()
    db.refresh(station)

    return MacroPatchResponse(
        blocks_updated=blocks_touched,
        updated_at=_iso(station.updated_at),
    )


# ============================================================
# §16.2.5 c1: section composition + structural block edits
# ============================================================
#
# Six new endpoints share the same lifecycle (§16.2.4 c2 review #5
# `withSaveLifecycle` would generalise these, but the route-level
# helper is frontend-only). All six:
#
#   1. Resolve station (404)
#   2. _check_optimistic_token (409)
#   3. Load sidecar via _load_sidecar_for_mutation (400 on broken)
#   4. Apply mutation to a deep-copy of the sidecar (and template
#      for structural edits)
#   5. validate_sidecar against the (possibly new) template
#   6. Commit + return the new sidecar in the response so the
#      frontend can re-render without a separate GET
#
# `_persist_sidecar` is the shared step-5 + step-6 helper.


def _persist_sidecar(
    station: Station,
    sidecar: dict,
    template: dict | None = None,
) -> None:
    """Validate + commit a sidecar mutation.

    `template` defaults to the station's current template; pass
    a NEW template (deep-copy with structural edits applied)
    when the mutation changes slot counts. The function:

      1. validate_sidecar(sidecar, template) — drift rules +
         grammar
      2. Reassign station.sidecar (+ template if supplied) so the
         SQLAlchemy onupdate fires for `updated_at`
      3. Caller commits + refreshes; this helper deliberately
         does NOT commit so transactional all-or-nothing semantics
         stay with the endpoint
    """
    eff_template = template if template is not None else station.template
    try:
        validate_sidecar(sidecar, eff_template)
    except EditorMetadataError as e:
        raise HTTPException(400, f"mutation produces an invalid sidecar: {e}")
    if template is not None:
        station.template = template
    station.sidecar = sidecar


def _find_section(sidecar: dict, section_id: str) -> dict | None:
    for s in sidecar.get("sections", []):
        if s.get("id") == section_id:
            return s
    return None


# c1 review #6: prepare/finish helpers extracted from the 6 sidecar-
# mutation endpoints' scaffolding. Each endpoint shed ~10 lines of
# duplicated boilerplate; future endpoints (§16.2.6 staged-edit,
# §16.2.7 fork) reuse the same scaffold.


def _prepare_sidecar_mutation(
    slug: str,
    db: Session,
    stations_dir: Path,
    expected_updated_at: str | None,
) -> tuple[Station, dict, dict]:
    """Run the shared pre-mutation steps (404 / 409 / load / deep-copy).

    Returns `(station, template_deepcopy, sidecar_deepcopy)`. The
    endpoint body receives ALREADY-DEEP-COPIED template + sidecar so
    a mid-flight validation failure can't leave the ORM holding a
    partially mutated dict.
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")
    _check_optimistic_token(station, expected_updated_at)
    template = copy.deepcopy(station.template)
    sidecar = copy.deepcopy(
        _load_sidecar_for_mutation(station, template, stations_dir, slug),
    )
    return station, template, sidecar


def _finish_sidecar_mutation(
    station: Station,
    sidecar: dict,
    db: Session,
    template: dict | None = None,
) -> SidecarMutationResponse:
    """Run the shared post-mutation steps (validate, commit, refresh,
    response build). Pass a `template` ONLY when the mutation changed
    slot counts (structural edits); section-CRUD + block-move leave
    the template untouched."""
    _persist_sidecar(station, sidecar, template=template)
    db.commit()
    db.refresh(station)
    return SidecarMutationResponse(
        sidecar=sidecar, updated_at=_iso(station.updated_at),
    )


@router.post(
    "/api/stations/{slug}/sections",
    response_model=SidecarMutationResponse,
    status_code=201,
)
def create_section(
    slug: str,
    payload: SectionCreateRequest,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Create an empty section + append to layout_order.

    `id` is operator-supplied (the §16.2.5 brief picks
    operator-control over server-generated ids — the visual editor
    treats ids as labels, not opaque handles). Rejected: ids
    starting with `_` (reserved for system bins) or duplicates of
    an existing section's id.
    """
    station, _template, sidecar = _prepare_sidecar_mutation(
        slug, db, stations_dir, payload.expected_updated_at,
    )

    if payload.id.startswith("_"):
        raise HTTPException(
            400,
            "section id can't start with `_` (reserved for system bins "
            f"like `{UNSORTED_SECTION_ID}`).",
        )
    if not payload.id:
        raise HTTPException(400, "section id must be a non-empty string.")
    if _find_section(sidecar, payload.id) is not None:
        raise HTTPException(
            409,
            f"section id '{payload.id}' already exists.",
        )

    sidecar.setdefault("sections", []).append({
        "id": payload.id,
        "name": payload.name or payload.id,
        "blocks": [],
    })
    sidecar.setdefault("layout_order", []).append(payload.id)

    return _finish_sidecar_mutation(station, sidecar, db)


@router.patch(
    "/api/stations/{slug}/sections/{section_id}",
    response_model=SidecarMutationResponse,
)
def rename_section(
    slug: str,
    section_id: str,
    payload: SectionRenameRequest,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Rename a section's `name` (the user-visible label). Id stays
    immutable here. Rejected for the `_unsorted` bin (system-managed)."""
    station, _template, sidecar = _prepare_sidecar_mutation(
        slug, db, stations_dir, payload.expected_updated_at,
    )

    if section_id == UNSORTED_SECTION_ID:
        raise HTTPException(
            400,
            f"can't rename the system `{UNSORTED_SECTION_ID}` section.",
        )
    section = _find_section(sidecar, section_id)
    if section is None:
        raise HTTPException(404, f"section '{section_id}' not found.")
    if not payload.name:
        raise HTTPException(400, "section name must be a non-empty string.")
    section["name"] = payload.name

    return _finish_sidecar_mutation(station, sidecar, db)


@router.delete(
    "/api/stations/{slug}/sections/{section_id}",
    response_model=SidecarMutationResponse,
)
def delete_section(
    slug: str,
    section_id: str,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
    expected_updated_at: str | None = None,
):
    """Delete a section (§16.2.5 D2: auto-move blocks to `_unsorted`).

    Empty sections delete cleanly. Non-empty sections re-home their
    blocks into the `_unsorted` bin (server creates it on first
    need) then remove the original. The bin's blocks list APPENDS
    — never inserts at the head — so prior bin contents stay in
    place + the operator's most-recent orphans land at the end.

    Rejected: deleting the `_unsorted` bin itself (it's system-
    managed; users empty it by moving blocks elsewhere).
    """
    station, _template, sidecar = _prepare_sidecar_mutation(
        slug, db, stations_dir, expected_updated_at,
    )

    if section_id == UNSORTED_SECTION_ID:
        raise HTTPException(
            400,
            f"can't delete the system `{UNSORTED_SECTION_ID}` section "
            "(it's auto-managed; move its blocks into other sections "
            "to empty it).",
        )
    section = _find_section(sidecar, section_id)
    if section is None:
        raise HTTPException(404, f"section '{section_id}' not found.")

    # c1 review #10: capture the blocks AFTER ensure_unsorted_section's
    # deep-copy + re-find, so the bin's blocks list holds the
    # post-deepcopy block dicts (no aliasing with the pre-deepcopy
    # section dicts). The prior implementation captured `blocks` from
    # the pre-deepcopy section, then extended the bin with those
    # ORIGINAL references — the bin worked by accident but mixed
    # pre/post-deepcopy dicts in the same sidecar (a latent aliasing
    # hazard for any code that mutates the bin's blocks expecting them
    # to be fresh).
    has_blocks = bool(section.get("blocks"))
    if has_blocks:
        sidecar = ensure_unsorted_section(sidecar)
        # Re-find both sections in the deep-copied sidecar.
        moved_section = _find_section(sidecar, section_id)
        bin_section = _find_section(sidecar, UNSORTED_SECTION_ID)
        # Move the post-deepcopy blocks (these dicts are NEW; no
        # aliasing with the pre-deepcopy section's blocks).
        bin_section["blocks"].extend(moved_section.get("blocks", []))

    sidecar["sections"] = [
        s for s in sidecar["sections"] if s.get("id") != section_id
    ]
    sidecar["layout_order"] = [
        sid for sid in sidecar.get("layout_order", []) if sid != section_id
    ]

    return _finish_sidecar_mutation(station, sidecar, db)


@router.put(
    "/api/stations/{slug}/sections/order",
    response_model=SidecarMutationResponse,
)
def reorder_sections(
    slug: str,
    payload: SectionsOrderRequest,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Replace `layout_order` with a permutation of the current
    section ids. add / drop ids → 400 (those are POST + DELETE)."""
    station, _template, sidecar = _prepare_sidecar_mutation(
        slug, db, stations_dir, payload.expected_updated_at,
    )

    current = {s.get("id") for s in sidecar.get("sections", [])}
    proposed = set(payload.layout_order)
    if proposed != current:
        added = sorted(proposed - current)
        dropped = sorted(current - proposed)
        raise HTTPException(
            400,
            f"layout_order must be a PERMUTATION of existing section ids. "
            f"Added (unknown): {added}; dropped: {dropped}.",
        )
    if len(payload.layout_order) != len(set(payload.layout_order)):
        raise HTTPException(
            400, "layout_order must not contain duplicate ids.",
        )
    sidecar["layout_order"] = list(payload.layout_order)

    return _finish_sidecar_mutation(station, sidecar, db)


@router.patch(
    "/api/stations/{slug}/blocks/move",
    response_model=SidecarMutationResponse,
)
def move_block(
    slug: str,
    payload: BlockMoveRequest,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Move a block between sections (sidecar-only — template unchanged).

    Resolves the source via `payload.address` (XOR slot_index /
    block_idx), removes it from its current section's blocks list,
    inserts into `target_section_id` at `target_index` (default
    append). The block dict is moved by VALUE (an atomic ref carries
    just `{kind: atomic, slot_index | (row_idx, block_idx)}`; a
    macro ref carries `{kind: macro, ref}`); no template touch.

    Source section is the one currently containing a block with
    matching atomic addressing — if multiple sections reference the
    same address (drift rule 8 catches that at load), 400.
    """
    station, _template, sidecar = _prepare_sidecar_mutation(
        slug, db, stations_dir, payload.expected_updated_at,
    )

    target = _find_section(sidecar, payload.target_section_id)
    if target is None:
        raise HTTPException(
            404, f"target section '{payload.target_section_id}' not found.",
        )

    # c1 review #2: ATOMIC ONLY. Moving a macro between sections
    # mis-aligns its slot range (resolve_macro_addresses derives
    # positions from section context); the schema's docstring spells
    # out the limitation. The matcher rejects macros explicitly so
    # operators see a clear 400 rather than a silent 404 if the
    # frontend forgets to send `address` (BlockAddress is required
    # by the schema, but a future BlockMoveRequest extension might
    # XOR with macro_id again — this guard catches that drift).
    addr = payload.address
    if addr.slot_index is None and addr.block_idx is None:
        raise HTTPException(
            400, "address must set slot_index or block_idx.",
        )
    if addr.slot_index is not None and addr.block_idx is not None:
        raise HTTPException(
            400,
            "address must set exactly one of slot_index / block_idx.",
        )

    def _matches(b: dict) -> bool:
        if b.get("kind") != "atomic":
            return False
        if addr.slot_index is not None:
            return b.get("slot_index") == addr.slot_index
        return b.get("block_idx") == addr.block_idx

    source: dict | None = None
    moving_block: dict | None = None
    for s in sidecar["sections"]:
        for b in s.get("blocks", []):
            if _matches(b):
                if source is not None:
                    raise HTTPException(
                        400,
                        "address matches blocks in multiple sections "
                        "(sidecar drift — re-derive).",
                    )
                source = s
                moving_block = b
    if source is None or moving_block is None:
        raise HTTPException(
            404,
            f"no atomic block matching address "
            f"{addr.model_dump(exclude_none=True)} found in any section. "
            "(Macros aren't movable in §16.2.5 — see BlockMoveRequest "
            "docstring; queued for §16.2.6 + a sidecar grammar update.)",
        )

    # Remove from source, insert into target.
    source["blocks"] = [b for b in source["blocks"] if b is not moving_block]
    target_blocks = target.setdefault("blocks", [])
    idx = payload.target_index
    if idx is None or idx >= len(target_blocks):
        target_blocks.append(moving_block)
    else:
        if idx < 0:
            raise HTTPException(400, "target_index must be >= 0.")
        target_blocks.insert(idx, moving_block)

    return _finish_sidecar_mutation(station, sidecar, db)


@router.patch(
    "/api/stations/{slug}/blocks/structure",
    response_model=SidecarMutationResponse,
)
def structural_block_edit(
    slug: str,
    payload: BlockStructureRequest,
    db: Session = Depends(get_db),
    stations_dir: Path = Depends(get_stations_dir),
):
    """Apply one structural edit (size / add / remove) to the
    template's flat_slots, shifting sidecar atomic refs in lockstep
    (D5). row_template is rejected for now — its block_idx-domain
    structural edits land in a follow-up slice (D8 punts table).
    """
    station, template, sidecar = _prepare_sidecar_mutation(
        slug, db, stations_dir, payload.expected_updated_at,
    )
    layout = template.get("layout", {})

    if "flat_slots" not in layout or not isinstance(layout["flat_slots"], list):
        raise HTTPException(
            400,
            "structural edits to row_template stations aren't supported in "
            "§16.2.5 (D8 punt — block_idx-domain edits land in a future "
            "slice). Convert the layout via the wizard-edit PUT first.",
        )

    flat_slots = layout["flat_slots"]
    edit = payload.edit
    idx = edit.slot_index

    if edit.op == "size":
        if not (0 <= idx < len(flat_slots)):
            raise HTTPException(404, f"slot_index {idx} out of range.")
        if edit.new_size is None or edit.new_size < 1:
            raise HTTPException(
                400, "size edits require new_size >= 1.",
            )
        flat_slots[idx]["size"] = edit.new_size
        # No slot-count change → no sidecar shift required.

    elif edit.op == "add":
        if not (0 <= idx <= len(flat_slots)):
            raise HTTPException(
                404,
                f"slot_index {idx} out of range for insert "
                f"(valid 0..{len(flat_slots)} inclusive).",
            )
        if edit.new_block is None:
            raise HTTPException(
                400, "add edits require new_block.",
            )
        flat_slots.insert(idx, edit.new_block)
        # Sidecar atomic refs at slot_index >= idx (BEFORE the insert)
        # become slot_index + 1 (AFTER). Use the helper which deep-
        # copies the sidecar; re-bind to the result.
        sidecar = shift_atomic_slot_indices(sidecar, threshold=idx, delta=1)
        # Auto-add the new block's atomic ref to `_unsorted` (D2
        # philosophy: never leave a slot orphaned + give the operator
        # a recoverable starting position). They move it to a real
        # section via a follow-up PATCH /blocks/move call.
        sidecar = ensure_unsorted_section(sidecar)
        bin_section = _find_section(sidecar, UNSORTED_SECTION_ID)
        bin_section["blocks"].append(
            {"kind": "atomic", "slot_index": idx},
        )

    elif edit.op == "remove":
        if not (0 <= idx < len(flat_slots)):
            raise HTTPException(404, f"slot_index {idx} out of range.")
        # c1 review #1: cascade-drop matching sidecar atomic refs
        # before popping flat_slots. The prior implementation 400'd
        # with a "move to _unsorted first" hint, but moving doesn't
        # REMOVE the reference — it just relocates it. The operator
        # would be stuck (move-then-remove still 400s; there's no
        # in-UI "delete chip" path until §16.2.6's chip-deletion
        # affordance lands).
        #
        # The cascade is safe: a slot can have AT MOST one atomic
        # reference (drift rule 8 catches duplicates at load time).
        # So at most one block disappears from one section per
        # call — observable + recoverable (operator can re-add via
        # op="add" if it was a mistake; the new block lands in
        # _unsorted).
        #
        # c1 review #9: single-pass cascade rather than per-section
        # list-comprehension rebuild (which would re-allocate every
        # section's blocks list even when no match). For Rebirth
        # Radio's 2,196-chip sidecar the inner predicate evaluates
        # exactly once per block — same asymptotics, ~3x fewer
        # allocations in the common no-match-in-section case.
        for s in sidecar.get("sections", []):
            blocks = s.get("blocks")
            if not blocks:
                continue
            for i, b in enumerate(blocks):
                if (
                    b.get("kind") == "atomic"
                    and b.get("slot_index") == idx
                ):
                    blocks.pop(i)
                    break  # drift rule 8: at most one match across all sections
        flat_slots.pop(idx)
        # Sidecar atomic refs at slot_index > idx decrement by 1.
        sidecar = shift_atomic_slot_indices(
            sidecar, threshold=idx + 1, delta=-1,
        )

    # parse_template revalidates the WHOLE template (structural edits
    # could leave cursor refs dangling, role mismatches, etc.).
    try:
        parse_template(template)
    except TemplateError as e:
        raise HTTPException(
            400, f"structural edit produces an invalid template: {e}",
        )

    return _finish_sidecar_mutation(station, sidecar, db, template=template)


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
