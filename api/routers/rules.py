"""Per-station rule CRUD endpoints (§14.29 + §16.14.t10).

Endpoints:

- GET    /api/stations/{slug}/rules         — list every known rule with
                                               its effective value,
                                               kind discriminator,
                                               default, and is_override flag
- PUT    /api/stations/{slug}/rules/{key}   — upsert a per-station override
- DELETE /api/stations/{slug}/rules/{key}   — clear the override (revert
                                               to default)

The list endpoint covers BOTH int rules (RULE_DEFAULTS) and list
rules (RULE_LIST_DEFAULTS) post-§16.14.t10. The kind discriminator
on each row lets the frontend dispatch to the right control without
inspecting types. Rules that aren't consumed by the engine yet
(cursor_advance_step, cursor_reset_cycle) still round-trip
correctly; reading/writing them today is a no-op on generation.

PUT/DELETE dispatch by which registry the URL `{key}` belongs to:
RULE_DEFAULTS → int path (set_rule_value / clear_rule);
RULE_LIST_DEFAULTS → list path (set_rule_list_value / clear_rule_list).
Body validation reuses the parse-time validators from engine.rules
(§16.14.t10 D6) so the contract is byte-identical to the template
`rules:` block's parse-time path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_db
from api.schemas import RuleListResponse, RuleOut, RuleValueIn
from engine.models import Station
from engine.rules import (
    InvalidRuleValueError, RULE_DEFAULTS, RULE_LIST_DEFAULTS,
    UnknownRuleError,
    clear_rule, clear_rule_list, get_all_rules,
    set_rule_value, set_rule_list_value,
    validate_int_rule_value, validate_list_rule_value,
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

    §16.14.t10 — dispatches by kind:

      * RULE_DEFAULTS key → int path. `payload.value` must be int.
      * RULE_LIST_DEFAULTS key → list path. `payload.value` must
        be list[str]; entries stripped + validated (D6 strict).

    400 on:
      - unknown key (not in either registry)
      - kind/shape mismatch (e.g. list for an int key)
      - value out of bounds (int path) or bad entry (list path)
    """
    station = _resolve_station(db, slug)
    value = payload.value
    if key in RULE_DEFAULTS:
        # Int path.
        try:
            validate_int_rule_value(key, value)
            set_rule_value(
                db, station_id=station.id, key=key, value=value,
            )
        except UnknownRuleError as e:
            raise HTTPException(400, str(e))
        except InvalidRuleValueError as e:
            raise HTTPException(400, str(e))
    elif key in RULE_LIST_DEFAULTS:
        # List path.
        try:
            cleaned = validate_list_rule_value(key, value)
            set_rule_list_value(
                db, station_id=station.id, key=key, value=cleaned,
            )
        except UnknownRuleError as e:
            raise HTTPException(400, str(e))
        except InvalidRuleValueError as e:
            raise HTTPException(400, str(e))
    else:
        # Unknown key — surface the same 400 + message the int
        # accessor would have raised, with the union of registries
        # in the message so an operator with a typo gets the full
        # list of valid keys.
        known = sorted(set(RULE_DEFAULTS) | set(RULE_LIST_DEFAULTS))
        raise HTTPException(
            400, f"unknown rule key: {key!r}. Known keys: {known}"
        )
    db.commit()
    # Re-read so the response reflects the canonical post-write
    # state (and dispatches via get_all_rules' kind-aware emit).
    rows = get_all_rules(db, station_id=station.id)
    by_key = {r["key"]: r for r in rows}
    return RuleOut(**by_key[key])


@router.delete(
    "/api/stations/{slug}/rules/{key}",
    status_code=204,
)
def delete_rule(slug: str, key: str, db: Session = Depends(get_db)):
    """Clear the override for `key`, reverting to the default.

    §16.14.t10 — dispatches by kind:

      * RULE_DEFAULTS key → clear_rule (int accessor).
      * RULE_LIST_DEFAULTS key → clear_rule_list (list accessor).

    Idempotent: 204 even if no override existed. 400 on unknown key.
    """
    station = _resolve_station(db, slug)
    if key in RULE_DEFAULTS:
        try:
            clear_rule(db, station_id=station.id, key=key)
        except UnknownRuleError as e:
            raise HTTPException(400, str(e))
    elif key in RULE_LIST_DEFAULTS:
        try:
            clear_rule_list(db, station_id=station.id, key=key)
        except UnknownRuleError as e:
            raise HTTPException(400, str(e))
    else:
        known = sorted(set(RULE_DEFAULTS) | set(RULE_LIST_DEFAULTS))
        raise HTTPException(
            400, f"unknown rule key: {key!r}. Known keys: {known}"
        )
    db.commit()
