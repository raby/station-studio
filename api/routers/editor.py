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
    MacroResolutionError,
    parse_sidecar,
    resolve_macro_addresses,
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


def _load_sidecar(
    station: Station, template: dict, stations_dir: Path,
) -> tuple[dict, str | None]:
    """Resolve a station's sidecar with the §16.2.5 layered precedence.

    Order (§16.2.5 D1):
      1. `Station.sidecar` JSON column — the new source-of-truth.
         If non-NULL, validate it against the template (catches
         drift after a template-only edit landed without re-syncing
         the sidecar) and return.
      2. On-disk file `stations/{slug}.editor.json` — legacy path
         for stations imported before the migration AND the
         operator-friendly "edit a sidecar from disk and reload"
         authoring workflow. Same drift validation.
      3. Trivial synthesized sidecar — the floor for un-authored
         stations (Decision E from §16.2.2).

    Returns `(sidecar_dict, error_message_or_None)`. error_message
    is non-None only when a stored sidecar (DB or file) FAILED
    validation; the caller renders a banner pointing the operator
    at the bad source. Empty / missing sidecars surface as
    `(synthesized_trivial, None)` — that's the design-intended
    path, not an error.
    """
    # Case (1): DB column.
    if station.sidecar is not None:
        try:
            from engine.editor_metadata import validate_sidecar
            validate_sidecar(station.sidecar, template)
            return station.sidecar, None
        except EditorMetadataError as e:
            logger.warning(
                "Station.sidecar (DB) for slug=%s failed validation; "
                "falling back to trivial: %s", station.slug, e,
            )
            return synthesize_trivial_sidecar(template), str(e)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "unexpected DB-sidecar load error for slug=%s; "
                "falling back to trivial", station.slug,
            )
            return (
                synthesize_trivial_sidecar(template),
                f"sidecar load failed: {type(e).__name__}: {e}",
            )

    # Case (2): on-disk file.
    sidecar_path = stations_dir / f"{station.slug}.editor.json"
    if not sidecar_path.is_file():
        # Case (3): no committed sidecar anywhere — synthesize. This
        # is the DESIGN-intended path for un-authored stations;
        # sidecar_error stays None.
        return synthesize_trivial_sidecar(template), None

    try:
        sidecar = parse_sidecar(sidecar_path, template)
        return sidecar, None
    except EditorMetadataError as e:
        # File present but broken (one of the 10 drift rules OR JSON
        # syntax — parse_sidecar wraps JSONDecodeError per §16.2.1
        # c1 review finding 6). Frontend renders a warning + the
        # trivial fallback.
        logger.warning(
            "sidecar file %s failed validation; falling back to trivial: %s",
            sidecar_path, e,
        )
        return synthesize_trivial_sidecar(template), str(e)
    except Exception as e:  # noqa: BLE001 — see §16.2.2 c1 review 🟡-1
        # Two known sources for non-EditorMetadataError:
        #   1. OSError from `path.read_text()` racing the is_file()
        #      check.
        #   2. KeyError / TypeError from validate_sidecar() if the
        #      stored TEMPLATE is corrupted.
        # Decision E: frontend NEVER handles "no sidecar" — widen
        # the catch + trivial-fallback. logger.exception preserves
        # the traceback for the operator's logs.
        logger.exception(
            "unexpected sidecar load error at %s; falling back to trivial",
            sidecar_path,
        )
        return (
            synthesize_trivial_sidecar(template),
            f"sidecar load failed: {type(e).__name__}: {e}",
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
    """Strict sidecar resolution for mutation endpoints.

    Unlike `_load_sidecar` (GET path, which degrades to trivial-with-
    sidecar_error on bad data), this helper RAISES HTTPException 400
    when a stored sidecar can't be parsed. Mutation paths can't
    safely tolerate a silent fallback — applying a write against the
    trivial fallback would lose the operator's macro/section
    grouping for this transaction.

    Order (§16.2.5 D1):
      1. Station.sidecar (DB) — preferred; validate via
         `validate_sidecar` (the same drift rules GET applies).
      2. On-disk file — legacy + authoring path.
      3. Trivial — only when NEITHER source has data. The macro
         endpoint's caller surfaces a meaningful 404 ("macro_id
         not found in the station's sidecar") since the trivial
         sidecar has no macros by definition.
    """
    from engine.editor_metadata import validate_sidecar

    if station.sidecar is not None:
        try:
            validate_sidecar(station.sidecar, template)
            return station.sidecar
        except EditorMetadataError as e:
            raise HTTPException(
                400,
                f"Station.sidecar for '{slug}' failed validation "
                f"— fix it or set it to NULL to fall back to disk/"
                f"trivial ({e}).",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "unexpected DB-sidecar load error for slug=%s during mutation",
                slug,
            )
            raise HTTPException(
                400,
                f"Station.sidecar for '{slug}' failed to load "
                f"({type(e).__name__}: {e}).",
            )

    sidecar_path = stations_dir / f"{slug}.editor.json"
    if sidecar_path.is_file():
        try:
            return parse_sidecar(sidecar_path, template)
        except EditorMetadataError as e:
            raise HTTPException(
                400,
                f"sidecar at {sidecar_path} is broken — fix it or "
                f"delete to regenerate from the template ({e}).",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "unexpected sidecar load error at %s during mutation",
                sidecar_path,
            )
            raise HTTPException(
                400,
                f"sidecar at {sidecar_path} failed to load "
                f"({type(e).__name__}: {e}) — fix or delete it before "
                f"editing.",
            )

    return synthesize_trivial_sidecar(template)


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

    station.template = template
    # Only materialise the sidecar in the DB column when we made a
    # real change to a stored sidecar (DB or file). A trivial-
    # fallback sidecar wouldn't have hit the spec-change branch
    # (no macros[]); writing it would falsely flip the column from
    # NULL to a synthesised value + GET would prefer that over the
    # legitimate template-driven trivial.
    if spec_changed:
        station.sidecar = copy.deepcopy(sidecar)
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
