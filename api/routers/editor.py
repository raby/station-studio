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
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_db, get_stations_dir
from api.schemas import (
    BlockPatchRequest,
    BlockPatchResponse,
    EditorSidecarResponse,
    TemplateExportResponse,
)
from engine.editor_metadata import (
    EditorMetadataError,
    parse_sidecar,
    synthesize_trivial_sidecar,
)
from engine.models import Station
from engine.template import parse_template, TemplateError


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
    "pick": set(),
    "window": set(),
    "sequential": {"cursor_id"},
    "jingle": {"genre"},
    "mix": {"genre", "exclusion_window"},
    "weighted": {"window"},
    "prev_ref": {"position", "fallback_genre"},
}


def _iso(dt: datetime | None) -> str | None:
    """ISO-8601 string for a datetime column, or None. Used for the
    optimistic-concurrency token + export response."""
    return dt.isoformat() if dt is not None else None


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
