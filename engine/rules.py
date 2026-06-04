"""Per-station configurable generation rules (§14.29).

Ports weekly's `app/services/rules.py` with three structural differences:

1. **Station-scoped**: rule values are stored per-station via
   ``Rule.station_id``. Weekly is single-station; station-studio is
   multi-station and stations don't share configuration.

2. **Engine-defaults dict drives validation**: ``RULE_DEFAULTS`` is the
   canonical list of valid keys + their defaults + value bounds.
   Adding a future rule is a single entry here — no schema migration,
   no accessor changes.

3. **Value column is TEXT**: rules are parsed to int by the current
   accessors, but the column type stays string so a future rule with
   a different shape (e.g. string enum, list-of-int duration vector)
   doesn't require a schema change.

The engine's `generate()` function stays DB-agnostic: it receives the
resolved rule values via kwargs from the API layer. Engine-level
fallback constants (``DEFAULT_WEIGHTED_WINDOW`` etc. in
``engine/generator.py``) provide the bottom-of-precedence value when
no rule row exists.

**Precedence (resolved at the API layer, applied by the generator):**

1. **Per-block BlockSpec field** (e.g. ``BlockSpec.window``,
   ``BlockSpec.exclusion_window``) — when set in the template.
2. **Station rule** — from this table, via ``get_rule_value``.
3. **Engine default constant** — last-resort, ensures the engine
   doesn't need a DB to function.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from engine.models import Rule


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleDefault:
    """Canonical definition of a rule key.

    `value` is the engine-default that fires when no override exists.
    `max_value` caps the override (None = no cap). `description` is the
    UI-facing explainer; future settings panels read it directly.
    """
    value: int
    description: str
    max_value: int | None = None


# Adding a new rule = one entry here. Validation, the API listing, and
# the per-station accessor all drive off this dict. Keys must be
# snake_case for consistency with weekly's vocabulary.
RULE_DEFAULTS: dict[str, RuleDefault] = {
    "artist_dedup_window": RuleDefault(
        value=10,
        description=(
            "How many recent weighted (commercial) picks the dedup "
            "window considers when blocking same-artist conflicts. "
            "Per-block BlockSpec.window on a weighted slot overrides "
            "this default for that slot."
        ),
        max_value=1000,
    ),
    "mix_exclusion_weeks": RuleDefault(
        value=20,
        description=(
            "How many recent playlists to exclude mixes from when "
            "preventing the same mix from repeating too soon. "
            "Per-block BlockSpec.exclusion_window on a mix slot "
            "overrides this default for that slot."
        ),
        max_value=1000,
    ),
    # §14.30 wired both cursor rules: when `template.cursor.persist=true`
    # the engine reads these rules at generation time and uses them to
    # drive the post-save cursor advance (cursor_advance_step) + reset
    # cadence (cursor_reset_cycle). For STATELESS templates (the §14.22
    # default), the engine's cursor math reads from the template's
    # cursor block (base_advance_per_week, cycle_weeks) and these rule
    # values are stored-but-ignored. The full precedence chain is:
    #   - cursor.persist=true template → station rule drives advance/reset
    #   - cursor.persist=false template → template fields drive advance/reset
    # (Earlier docstrings on these two rules wrongly claimed they were
    # "not yet wired"; §14.30 closed that gap. See engine.persistence.
    # advance_cursors + api/routers/generate.py:60-65.)
    "cursor_advance_step": RuleDefault(
        value=120,
        description=(
            "How far sequential-strategy cursors advance per generation "
            "(weekly's NIGHT/WEEKEND cursor step). Active for templates "
            "with cursor.persist=true (§14.30). Stateless templates "
            "ignore this — they control advance via "
            "cursor.base_advance_per_week in the template's cursor block."
        ),
        max_value=10000,
    ),
    "cursor_reset_cycle": RuleDefault(
        value=0,
        description=(
            "How often persistent cursors reset to their initial "
            "positions (0 = never reset; wrap-only). Active for "
            "templates with cursor.persist=true (§14.30). Stateless "
            "templates ignore this."
        ),
        max_value=None,
    ),
}


class UnknownRuleError(KeyError):
    """Raised when a CRUD operation references a key not in RULE_DEFAULTS."""


class InvalidRuleValueError(ValueError):
    """Raised when an override value is out of bounds (negative or above max)."""


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def get_rule_value(db: Session, *, station_id: int, key: str) -> int:
    """Return the effective value for `key` on `station_id`.

    Reads the per-station override if present; falls back to
    RULE_DEFAULTS[key].value otherwise. Raises UnknownRuleError for
    keys not in the registry — callers must validate their inputs.
    All current rule values parse as int; future shape diversification
    will need a per-rule parser.
    """
    if key not in RULE_DEFAULTS:
        raise UnknownRuleError(f"unknown rule key: {key!r}")
    row = (
        db.query(Rule)
        .filter(Rule.station_id == station_id, Rule.key == key)
        .first()
    )
    if row is not None:
        return int(row.value)
    return RULE_DEFAULTS[key].value


def set_rule_value(db: Session, *, station_id: int, key: str, value: int) -> None:
    """Upsert a rule override for `(station_id, key)`. Caller commits.

    Raises:
        UnknownRuleError if `key` isn't in RULE_DEFAULTS.
        InvalidRuleValueError if `value` is negative or exceeds max_value.
    """
    if key not in RULE_DEFAULTS:
        raise UnknownRuleError(f"unknown rule key: {key!r}")
    defn = RULE_DEFAULTS[key]
    if value < 0:
        raise InvalidRuleValueError(
            f"rule {key!r} value must be non-negative, got {value}"
        )
    if defn.max_value is not None and value > defn.max_value:
        raise InvalidRuleValueError(
            f"rule {key!r} value must be at most {defn.max_value}, got {value}"
        )
    row = (
        db.query(Rule)
        .filter(Rule.station_id == station_id, Rule.key == key)
        .first()
    )
    if row is not None:
        row.value = str(value)
    else:
        db.add(Rule(station_id=station_id, key=key, value=str(value)))
    db.flush()


def clear_rule(db: Session, *, station_id: int, key: str) -> bool:
    """Delete an override for `(station_id, key)`, reverting to default.

    Returns True if a row was deleted, False if no override existed.
    Raises UnknownRuleError for invalid keys.
    """
    if key not in RULE_DEFAULTS:
        raise UnknownRuleError(f"unknown rule key: {key!r}")
    row = (
        db.query(Rule)
        .filter(Rule.station_id == station_id, Rule.key == key)
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def get_all_rules(db: Session, *, station_id: int) -> list[dict[str, Any]]:
    """Return every INT rule with its effective value, default, and override.

    Returns a list of dicts: ``{key, value, default_value, description,
    max_value, is_override}``. Sorted by key for stable UI ordering.
    Drives the CRUD list endpoint at ``/api/stations/{slug}/rules``.

    Scope note (§16.14.t5 c1 review F4): only iterates ``RULE_DEFAULTS``
    (int rules). List-typed rules (``RULE_LIST_DEFAULTS``, e.g.
    ``cross_pool_dedup_sources``) are NOT surfaced here — the CRUD
    endpoint's value schema is integer-shaped. List rules ship with
    imperative seeding via scripts/seed_stations.py (D8) until the
    Rules UI slice (§16.5) adds polymorphic value types.
    """
    overrides = {
        r.key: r
        for r in db.query(Rule).filter(Rule.station_id == station_id).all()
    }
    result = []
    for key in sorted(RULE_DEFAULTS):
        defn = RULE_DEFAULTS[key]
        override = overrides.get(key)
        result.append({
            "key": key,
            "value": int(override.value) if override is not None else defn.value,
            "default_value": defn.value,
            "description": defn.description,
            "max_value": defn.max_value,
            "is_override": override is not None,
        })
    return result


# ---------------------------------------------------------------------------
# §16.14.t5 — list-of-str rule registry
# ---------------------------------------------------------------------------
#
# Parallel registry for rules whose value is a LIST of strings (not
# int). cross_pool_dedup_sources (D1+D3) is the first entry — templates
# name reference pools whose tracks the weighted strategy excludes
# from its pick set.
#
# Storage: `Rule.value` column is TEXT; for list values we store
# comma-separated strings. Empty list is "" (the empty string);
# absent rule is the registry default.
#
# Why parallel vs extending RULE_DEFAULTS: keeping int rules strictly
# int-typed at the accessor level preserves the existing API + UI
# shape (numeric inputs). A future polymorphic Rules UI (§16.5) might
# merge these — for now, keep the typing explicit.
#
# Surface note (§16.14.t5 c1 review F4): list rules are NOT exposed
# by ``get_all_rules`` / the CRUD endpoint. Seeding for this slice
# is imperative (D8 in the §16.14.t5 design pass). When the Rules UI
# slice (§16.5) lands, this is the registry to plumb into it.


LIST_SEPARATOR = ","


@dataclass(frozen=True)
class ListRuleDefault:
    """Canonical definition of a list-typed rule key.

    `value` is the engine-default list (typically `[]` — no
    exclusion). `description` is UI-facing copy. The list elements
    are bare strings (no per-element validation here; the engine
    validates them at use time, e.g. load_excluded_track_pids checks
    each named source exists in the DB with the expected role).
    """
    value: list[str]
    description: str


RULE_LIST_DEFAULTS: dict[str, ListRuleDefault] = {
    "cross_pool_dedup_sources": ListRuleDefault(
        value=[],
        description=(
            "§16.14.t5: list of source pool names whose tracks the "
            "weighted strategy excludes from its pick set. Each named "
            "pool must be imported with role=reference (cross-checked "
            "at generation time via engine.persistence."
            "load_excluded_track_pids). Empty list = no exclusion. "
            "Used today by `weighted` only; `window` extension is "
            "queued as §16.14.t8."
        ),
    ),
}


def get_rule_list_value(
    db: Session, *, station_id: int, key: str,
) -> list[str]:
    """Return the effective list value for `key` on `station_id`.

    Per-station override stored as comma-separated string in
    Rule.value; falls back to RULE_LIST_DEFAULTS[key].value otherwise.
    Empty string in the override row → empty list (explicit "no
    sources" override).
    """
    if key not in RULE_LIST_DEFAULTS:
        raise UnknownRuleError(f"unknown list-rule key: {key!r}")
    row = (
        db.query(Rule)
        .filter(Rule.station_id == station_id, Rule.key == key)
        .first()
    )
    if row is not None:
        raw = row.value or ""
        if not raw.strip():
            return []
        return [s.strip() for s in raw.split(LIST_SEPARATOR) if s.strip()]
    # Return a copy so callers can't mutate the registry default.
    return list(RULE_LIST_DEFAULTS[key].value)


def set_rule_list_value(
    db: Session, *, station_id: int, key: str, value: list[str],
) -> None:
    """Upsert a list-rule override for `(station_id, key)`. Caller
    commits.

    Strips whitespace per element, drops empty entries. An empty
    incoming list IS persisted as the empty string (explicit "no
    exclusion" override; different from the absent default, which is
    also `[]`). Rejects commas inside source names since the storage
    format can't escape them.
    """
    if key not in RULE_LIST_DEFAULTS:
        raise UnknownRuleError(f"unknown list-rule key: {key!r}")
    cleaned = [s.strip() for s in value if s and s.strip()]
    for entry in cleaned:
        if LIST_SEPARATOR in entry:
            raise InvalidRuleValueError(
                f"list-rule {key!r} entry {entry!r} contains the "
                f"separator character {LIST_SEPARATOR!r}; source pool "
                "names with commas aren't supported by the on-disk "
                "format. Rename the pool."
            )
    encoded = LIST_SEPARATOR.join(cleaned)
    row = (
        db.query(Rule)
        .filter(Rule.station_id == station_id, Rule.key == key)
        .first()
    )
    if row is not None:
        row.value = encoded
    else:
        db.add(Rule(station_id=station_id, key=key, value=encoded))
    db.flush()


def clear_rule_list(db: Session, *, station_id: int, key: str) -> bool:
    """Delete a list-rule override, reverting to RULE_LIST_DEFAULTS.

    Returns True if a row was deleted, False if no override existed.
    Mirrors `clear_rule` for int rules.
    """
    if key not in RULE_LIST_DEFAULTS:
        raise UnknownRuleError(f"unknown list-rule key: {key!r}")
    row = (
        db.query(Rule)
        .filter(Rule.station_id == station_id, Rule.key == key)
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
