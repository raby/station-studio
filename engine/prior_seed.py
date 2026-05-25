"""Prior-seed fixture validator + helpers (§14.38).

A "prior seed" is a playlist station-studio didn't generate itself
but treats as if it had — for purposes of:

  * `prev_ref` strategy's "previous-week lookup".
  * Cross-week mix exclusion (the prior's MixUsage rows, if any).
  * Cursor-state initialisation post-import.

The prior-seed fixture format ships in JSON (today as part of
byte-equality fixtures; future as a production CLI/API payload).
See DESIGN.md §14.38 for the full contract.

Why this module lives in `engine/` (not `tests/`):

  * `scripts/generate_user_playlists.py` already consumes
    `trace_id_for_prior`; a user-facing OSS script cannot import
    from `tests/` (§14.41 post-review sweep 4).
  * The eventual production CLI (`scripts/import_prior_seed.py`)
    + API endpoint will need the same validator. Test code
    shouldn't be the only home.

History — this module was extracted from `tests/byte_equality.py`
during the §14.41 post-review sweep 4. Contents unchanged from the
§14.38 R1 sweep-up; only the file location moved.
"""
from __future__ import annotations


# Allowlists mirror §14.32 / §14.34 / §14.35's strict-parsing pattern.
ALLOWED_FIXTURE_TOP_LEVEL = {
    "schema_version", "seed_base", "weeks_generated", "weeks_asserted",
    "priors", "weeks", "_comment",
}
REQUIRED_PRIOR_KEYS = {"week", "pids", "cursors"}
ALLOWED_PRIOR_KEYS = {"week", "pids", "cursors"}


class PriorSeedError(AssertionError):
    """Raised when fixture validation fails (§14.38).

    Subclasses AssertionError so test contexts can use the standard
    `pytest.raises(AssertionError, ...)` pattern; production callers
    can catch the more specific class explicitly.
    """


def validate_prior_seed_fixture(fixture: dict) -> None:
    """Strict-mode prior-seed fixture validation (§14.38 R1 sweep-up).

    Calibrated against the §14.32 / §14.34 / §14.35 strict-parser
    pattern: top-level allowlist + per-prior allowlist + per-field
    type checks. Catches typos that would otherwise silently drop
    data downstream.

    Allows `len(priors) in {0, 1}` so any byte-equality test can use
    the same validator regardless of whether the station has a prior-
    seed playlist (weekly: 1 prior, WEEKLY 001; Classics: 0 priors,
    stateless). Multi-prior (`len > 1`) is queued as §14.38b.
    """
    if not isinstance(fixture, dict):
        raise PriorSeedError(
            f"fixture must be a JSON object, got {type(fixture).__name__}"
        )
    if fixture.get("schema_version") != 2:
        raise PriorSeedError(
            f"fixture schema_version={fixture.get('schema_version')!r}; "
            f"expected 2 (§14.38). Regenerate via the appropriate "
            f"`make refresh-…-fixture` target."
        )
    unknown_top = set(fixture.keys()) - ALLOWED_FIXTURE_TOP_LEVEL
    if unknown_top:
        raise PriorSeedError(
            f"fixture has unknown top-level keys: {sorted(unknown_top)}. "
            f"Allowed: {sorted(ALLOWED_FIXTURE_TOP_LEVEL)}."
        )
    priors = fixture.get("priors")
    if not isinstance(priors, list):
        raise PriorSeedError(
            f"fixture 'priors' must be a list, got {type(priors).__name__}"
        )
    if len(priors) > 1:
        raise PriorSeedError(
            f"fixture has {len(priors)} priors; loader supports 0 or 1 "
            f"(multi-prior is §14.38b — queued)."
        )
    for i, prior in enumerate(priors):
        if not isinstance(prior, dict):
            raise PriorSeedError(
                f"priors[{i}] must be an object, got {type(prior).__name__}"
            )
        missing = REQUIRED_PRIOR_KEYS - set(prior.keys())
        if missing:
            raise PriorSeedError(
                f"priors[{i}] missing required keys: {sorted(missing)}. "
                f"Required: {sorted(REQUIRED_PRIOR_KEYS)}."
            )
        unknown = set(prior.keys()) - ALLOWED_PRIOR_KEYS
        if unknown:
            raise PriorSeedError(
                f"priors[{i}] has unknown keys: {sorted(unknown)}. "
                f"Allowed: {sorted(ALLOWED_PRIOR_KEYS)}."
            )
        if not isinstance(prior["week"], int):
            raise PriorSeedError(
                f"priors[{i}].week must be int, got "
                f"{type(prior['week']).__name__}"
            )
        if not isinstance(prior["pids"], list):
            raise PriorSeedError(
                f"priors[{i}].pids must be a list, got "
                f"{type(prior['pids']).__name__}"
            )
        for j, pid in enumerate(prior["pids"]):
            if not isinstance(pid, str):
                raise PriorSeedError(
                    f"priors[{i}].pids[{j}] must be a string, got "
                    f"{type(pid).__name__}"
                )
        if not isinstance(prior["cursors"], dict):
            raise PriorSeedError(
                f"priors[{i}].cursors must be an object, got "
                f"{type(prior['cursors']).__name__}"
            )
        for name, pos in prior["cursors"].items():
            if not isinstance(name, str) or not name:
                raise PriorSeedError(
                    f"priors[{i}].cursors key must be a non-empty string, "
                    f"got {name!r}"
                )
            if not isinstance(pos, int):
                raise PriorSeedError(
                    f"priors[{i}].cursors[{name!r}] must be int, got "
                    f"{type(pos).__name__}"
                )


def validate_prior_cursor_names(prior: dict, template_cursors: set[str]) -> None:
    """Cross-check prior's cursor names against the station template's
    `cursors_init`. A typo would otherwise create a dead
    `cursor_state` row that runtime silently ignores — exactly the
    silent-field-drop class of bug this slice fights against. R1
    sweep-up.
    """
    declared = set(prior["cursors"].keys())
    unknown = declared - template_cursors
    if unknown:
        raise PriorSeedError(
            f"prior declares cursors {sorted(unknown)} that are NOT in "
            f"the station template's cursors_init (declared: "
            f"{sorted(template_cursors)}). A typo here would create a "
            f"dead cursor_state row that runtime ignores — fail loud "
            f"instead."
        )


def trace_id_for_prior(week: int) -> str:
    """Build a clearly-non-UUID trace_id for an imported prior playlist.

    Format: `imported-prior:week{N}`. The `imported-prior:` prefix
    documents intent + distinguishes from generation traces (full
    UUIDs). Any future code that filters `Playlist.trace_id` on UUID
    shape will skip these correctly; explicit prior-aware filters can
    pattern-match the prefix. R3 sweep-up.
    """
    return f"imported-prior:week{week}"
