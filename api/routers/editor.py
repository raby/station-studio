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

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_db, get_stations_dir
from api.schemas import EditorSidecarResponse
from engine.editor_metadata import (
    EditorMetadataError,
    parse_sidecar,
    synthesize_trivial_sidecar,
)
from engine.models import Station


logger = logging.getLogger(__name__)

router = APIRouter(tags=["editor"])


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
        return EditorSidecarResponse(
            template=template,
            sidecar=synthesize_trivial_sidecar(template),
            sidecar_error=str(e),
        )

    # Case (b): committed + valid sidecar.
    return EditorSidecarResponse(
        template=template,
        sidecar=sidecar,
        sidecar_error=None,
    )
