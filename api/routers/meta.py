"""Meta endpoint — exposes the constants the frontend dropdowns need.

Single source of truth for `STRATEGIES` / `RNG` / `ROLES` / `CADENCES`. The
frontend used to duplicate these in `<StationForm>` and `<LayoutFieldset>`
with "keep in sync with engine/template.py" comments — this endpoint
removes the duplication and the trigger named in §14.7's punt list ("third
caller appears") is met by the visual block editor (§17 Phase B).

See DESIGN.md §14.19 for the rationale + the cadence-vs-rest distinction
(strategies / rng / roles are server-side validated; cadence is a
frontend-only convention until §11's scheduler v1.1 lands).
"""
from __future__ import annotations

from fastapi import APIRouter

from engine.template import (
    SUPPORTED_ROLES,
    SUPPORTED_RNG,
    SUPPORTED_STRATEGIES,
    SUGGESTED_CADENCES,
)
from api.schemas import MetaResponse


router = APIRouter(tags=["meta"])


@router.get("/api/meta", response_model=MetaResponse)
def get_meta() -> MetaResponse:
    """Return the canonical option lists for template-shaped UI dropdowns.

    Lists are sorted so frontend `<option>` order is deterministic across
    deploys (a Python set's iteration order isn't stable).
    """
    return MetaResponse(
        strategies=sorted(SUPPORTED_STRATEGIES),
        rng=sorted(SUPPORTED_RNG),
        roles=sorted(SUPPORTED_ROLES),
        cadences=sorted(SUGGESTED_CADENCES),
    )
