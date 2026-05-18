"""Per-station rule CRUD endpoints (§14.29).

Endpoints:

- GET    /api/stations/{slug}/rules         — list every known rule with
                                               its effective value,
                                               default, and is_override flag
- PUT    /api/stations/{slug}/rules/{key}   — upsert a per-station override
- DELETE /api/stations/{slug}/rules/{key}   — clear the override (revert
                                               to default)

The list endpoint includes rules that aren't currently consumed by the
engine (cursor_advance_step, cursor_reset_cycle) — see engine.rules
for the full registry. They round-trip correctly; reading/writing
them today is a no-op on generation, future slices wire them.

No body parsing of the rule key — keys are URL path segments,
validated against engine.rules.RULE_DEFAULTS.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import RuleListResponse, RuleOut, RuleValueIn
from engine.models import Station
from engine.rules import (
    InvalidRuleValueError, UnknownRuleError,
    clear_rule, get_all_rules, set_rule_value,
)


router = APIRouter(tags=["rules"])


def _resolve_station(db: Session, slug: str) -> Station:
    """Look up a station by slug, 404 if absent."""
    st = db.query(Station).filter(Station.slug == slug).first()
    if st is None:
        raise HTTPException(404, f"station {slug!r} not found")
    return st


@router.get(
    "/api/stations/{slug}/rules",
    response_model=RuleListResponse,
)
def list_rules(slug: str, db: Session = Depends(get_db)) -> RuleListResponse:
    """List every known rule with effective value, default, and override flag.

    Returns rules in stable sorted-by-key order so a settings UI can
    render deterministically.
    """
    station = _resolve_station(db, slug)
    rows = get_all_rules(db, station_id=station.id)
    return RuleListResponse(rules=[RuleOut(**r) for r in rows])


@router.put(
    "/api/stations/{slug}/rules/{key}",
    response_model=RuleOut,
)
def set_rule(
    slug: str,
    key: str,
    payload: RuleValueIn,
    db: Session = Depends(get_db),
) -> RuleOut:
    """Upsert a per-station override for `key`.

    400 on:
      - unknown key (not in engine.rules.RULE_DEFAULTS)
      - value out of bounds (negative or above max_value)
    """
    station = _resolve_station(db, slug)
    try:
        set_rule_value(
            db, station_id=station.id, key=key, value=payload.value,
        )
    except UnknownRuleError as e:
        raise HTTPException(400, str(e))
    except InvalidRuleValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    # Re-read so the response reflects the canonical post-write state.
    rows = get_all_rules(db, station_id=station.id)
    by_key = {r["key"]: r for r in rows}
    return RuleOut(**by_key[key])


@router.delete(
    "/api/stations/{slug}/rules/{key}",
    status_code=204,
)
def delete_rule(slug: str, key: str, db: Session = Depends(get_db)):
    """Clear the override for `key`, reverting to the default.

    Idempotent: 204 even if no override existed. 400 on unknown key.
    """
    station = _resolve_station(db, slug)
    try:
        clear_rule(db, station_id=station.id, key=key)
    except UnknownRuleError as e:
        raise HTTPException(400, str(e))
    db.commit()
