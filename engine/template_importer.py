"""Translate a weekly-format template export into a station-studio template.

Sub-slice #16 of the §17 Phase B port roadmap. Pure-function translator;
no I/O, no DB. Given a dict shaped like weekly's `build_template()` output
+ `INITIAL_CURSORS`, produce a station-studio template dict that
round-trips through `engine.template.parse_template`.

**Expected input shape** (produced by `scripts/dump_weekly_template.py`
run inside a weekly/ checkout):

    {
      "slots": [
        {"type": "jingle", "genre": "Daytime"},
        {"type": "commercial"},
        {"type": "mix", "genre": "01Alphastate", "exclusion_window": 20},
        {"type": "prev_ref", "position": 1742, "fallback_genre": "22Universal"},
        {"type": "night", "cursor_id": "NIGHT_1"},
        {"type": "weekend", "cursor_id": "WEEKEND_1"},
        ...
      ],
      "initial_cursors": {
        "NIGHT_1": 0, "NIGHT_2": 10, ..., "WEEKEND_2": 120
      }
    }

**Translation table** (also documented in DESIGN.md §14.32 + §14.34):

| Weekly `type` | Strategy   | Source pool | Field map                          |
|---------------|------------|-------------|------------------------------------|
| jingle        | jingle     | JINGLES     | genre                              |
| commercial    | weighted   | COMMERCIAL  | (no per-block fields; rule default)|
| mix           | mix        | MIXES       | genre, exclusion_window            |
| prev_ref      | prev_ref   | MIXES       | position, fallback_genre           |
| night         | sequential | NIGHT       | cursor_id                          |
| weekend       | sequential | WEEKEND     | cursor_id                          |

**Source-pool names are hardcoded** to match weekly's pool names verbatim
(JINGLES / COMMERCIAL / MIXES / NIGHT / WEEKEND / MASTER), so the iTunes
XML import in station-studio uses the same names and byte-equality with
weekly's playlist generation is straightforward. The user picked
"hardcode verbatim" during §14.34's design discussion.

**Cursor persistence**: the resulting template sets `cursor.persist=true`
(§14.30 stateful-cursor mode). Weekly's named cursors live in a
DB table that advances after each generation; station-studio's
equivalent (`cursor_state`) drives the same semantics when
`cursor.persist=true`.

**Validation**: the translator round-trips its output through
`parse_template` before returning. A translator bug surfaces at
import time (clear error message + which slot triggered it), not
at generation time (mysterious KeyError on a missing field).
"""
from __future__ import annotations

from typing import Any

from engine.template import parse_template


class WeeklyImportError(ValueError):
    """Raised when a weekly-format template can't be translated.

    Distinct from TemplateError so the import script can distinguish
    "input data is malformed" (this) from "translator produced an
    invalid station-studio template" (TemplateError surfacing from
    the post-translation parse_template call — which would be a
    translator bug, not user error).
    """


# Hardcoded source pool names + colours matching weekly's vocabulary.
# Colours are arbitrary (weekly doesn't carry them); chosen to be
# visually distinguishable in the trace viewer's chip rendering.
# MASTER is the reference-role source — weekly's third XML file
# containing canonical iTunes Persistent IDs across all tracks; not
# sampled into playlists but declared so the iTunes XML import has a
# place to land.
_PORT_SOURCES: list[dict[str, str]] = [
    {"name": "JINGLES",    "colour": "#fb8500", "role": "content"},
    {"name": "COMMERCIAL", "colour": "#219ebc", "role": "content"},
    {"name": "MIXES",      "colour": "#8338ec", "role": "content"},
    {"name": "NIGHT",      "colour": "#3a86ff", "role": "content"},
    {"name": "WEEKEND",    "colour": "#ff006e", "role": "content"},
    {"name": "MASTER",     "colour": "#888888", "role": "reference"},
]


def import_weekly_template(
    weekly_data: dict[str, Any],
    *,
    slug: str,
    name: str,
) -> dict[str, Any]:
    """Translate a weekly-shaped dict to a station-studio template dict.

    `slug` and `name` are caller-provided (weekly's template doesn't
    carry station identity — weekly is single-station, so these are
    new fields when the template becomes one of many in station-studio).

    Raises WeeklyImportError on malformed input. Round-trips through
    parse_template before returning so translator output is guaranteed
    valid.
    """
    if not isinstance(weekly_data, dict):
        raise WeeklyImportError(
            f"weekly_data must be a dict, got {type(weekly_data).__name__}"
        )
    # §14.34 review fix F1: strict-mode top-level keys. Mirrors the §14.32
    # parser's "unknown keys rejected" policy — silent acceptance of
    # typos (`slot` for `slots`) would mask the empty-slots error case
    # behind misleading downstream errors.
    required = {"slots", "initial_cursors"}
    allowed_extras = {
        "_comment",       # JSON-friendly inline doc, conventional
        "schema_version", # forward-compat for a future dump-script versioning
    }
    missing = required - set(weekly_data.keys())
    if missing:
        raise WeeklyImportError(
            f"weekly_data missing required keys: {sorted(missing)}. "
            f"Got: {sorted(weekly_data.keys())}"
        )
    unknown = set(weekly_data.keys()) - required - allowed_extras
    if unknown:
        raise WeeklyImportError(
            f"weekly_data has unknown top-level keys: {sorted(unknown)}. "
            f"Allowed: {sorted(required | allowed_extras)}. "
            f"Strict mode prevents silent typos (e.g. 'slot' instead of 'slots')."
        )
    if not isinstance(weekly_data["slots"], list):
        raise WeeklyImportError(
            f"weekly_data['slots'] must be a list, got "
            f"{type(weekly_data['slots']).__name__}"
        )
    if not isinstance(weekly_data["initial_cursors"], dict):
        raise WeeklyImportError(
            f"weekly_data['initial_cursors'] must be a dict, got "
            f"{type(weekly_data['initial_cursors']).__name__}"
        )
    # §14.34 review fix F3: explicit error for empty slots. Otherwise the
    # post-translation parse_template raises "layout must specify a
    # non-empty row_template or flat_slots" wrapped as "translator
    # produced an invalid station-studio template" — phrased as a bug
    # when it's actually user input.
    if not weekly_data["slots"]:
        raise WeeklyImportError(
            "weekly_data['slots'] must contain at least one slot. "
            "An empty template can't be generated against."
        )

    flat_slots = [_translate_slot(s, i) for i, s in enumerate(weekly_data["slots"])]

    # §14.34 review fix F5: surface a clear user-facing error when a slot
    # references a cursor not declared in initial_cursors. Without this
    # check the post-translation parse_template raises (correctly) but
    # the message gets wrapped as "translator produced an invalid station-
    # studio template — file an issue", phrasing it as a station-studio
    # bug when it's actually a mismatch in the input JSON.
    declared = set(weekly_data["initial_cursors"].keys())
    referenced = {s["cursor_id"] for s in flat_slots if "cursor_id" in s}
    undeclared = referenced - declared
    if undeclared:
        raise WeeklyImportError(
            f"slots reference cursor(s) {sorted(undeclared)} not declared in "
            f"initial_cursors {sorted(declared)}. Every night/weekend slot's "
            f"cursor_id must have a matching key in initial_cursors."
        )

    template = {
        "station": slug,
        "name": name,
        "cadence": "weekly",
        # Weekly is rng=on with seeded determinism per generation.
        # Station-studio's seed flows via the API's generate payload.
        "rng": "on",
        "sources": [dict(s) for s in _PORT_SOURCES],
        # cursor.persist=true → §14.30 stateful named cursors driven
        # by the cursor_advance_step / cursor_reset_cycle rules. The
        # base_advance_per_week + cycle_weeks fields are placeholders
        # required by the schema; they don't influence persistent
        # cursors.
        "cursor": {
            "base_advance_per_week": 0,
            "cycle_weeks": 1,
            "persist": True,
        },
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": dict(weekly_data["initial_cursors"]),
            "flat_slots": flat_slots,
        },
    }

    # Translator-correctness guard: any bug in the translation table or
    # field-shape decision surfaces here, BEFORE the template is saved
    # to a Station row. A failure here is a station-studio bug (not a
    # user input bug); the error message includes the parser's
    # explanation which usually narrows the source field.
    try:
        parse_template(template)
    except Exception as e:
        raise WeeklyImportError(
            f"translator produced an invalid station-studio template: {e}. "
            f"This is a station-studio bug — file an issue with the input "
            f"weekly_data attached."
        ) from e

    return template


# §14.34 review fix F2: per-type allowlist of weekly slot-dict keys. The
# value sets are the EXACT keys weekly's build_template() emits for
# each type — verified against weekly/app/services/template.py. Any
# slot field outside the allowlist for its type triggers a strict-
# mode rejection so a typo (`exclusin_window`, `cursorid`) surfaces at
# import time instead of as a silent field-drop. Matches §14.32
# parser's "unknown keys rejected" policy.
_SLOT_FIELDS_BY_TYPE: dict[str, set[str]] = {
    "jingle":     {"type", "genre"},
    "commercial": {"type"},
    "mix":        {"type", "genre", "exclusion_window"},
    "prev_ref":   {"type", "position", "fallback_genre"},
    "night":      {"type", "cursor_id"},
    "weekend":    {"type", "cursor_id"},
}


def _translate_slot(slot: dict[str, Any], idx: int) -> dict[str, Any]:
    """Translate one weekly slot dict to a station-studio BlockSpec dict.

    `idx` is 0-indexed; error messages add +1 for human-readable
    1-indexed slot numbering.
    """
    if not isinstance(slot, dict):
        raise WeeklyImportError(
            f"slot {idx + 1}: must be a dict, got {type(slot).__name__}"
        )
    type_ = slot.get("type")
    if type_ is None:
        raise WeeklyImportError(
            f"slot {idx + 1}: missing 'type' field. "
            f"Weekly slots must declare their type."
        )
    if type_ not in _SLOT_FIELDS_BY_TYPE:
        raise WeeklyImportError(
            f"slot {idx + 1}: unknown weekly slot type {type_!r}. "
            f"Supported: jingle, commercial, mix, prev_ref, night, weekend."
        )
    # F2: reject unknown fields per type.
    unknown = set(slot.keys()) - _SLOT_FIELDS_BY_TYPE[type_]
    if unknown:
        raise WeeklyImportError(
            f"slot {idx + 1} (type={type_!r}): unknown fields "
            f"{sorted(unknown)}. Allowed for this type: "
            f"{sorted(_SLOT_FIELDS_BY_TYPE[type_])}. "
            f"Strict mode catches typos like 'cursorid' or 'exclusin_window'."
        )

    if type_ == "jingle":
        _require_fields(slot, idx, "jingle", required={"genre"})
        return {
            "source": "JINGLES",
            "strategy": "jingle",
            "size": 1,
            "genre": slot["genre"],
        }
    if type_ == "commercial":
        # No per-block fields; the artist_dedup_window rule supplies the
        # weighted strategy's dedup window default.
        return {
            "source": "COMMERCIAL",
            "strategy": "weighted",
            "size": 1,
        }
    if type_ == "mix":
        _require_fields(slot, idx, "mix", required={"genre", "exclusion_window"})
        return {
            "source": "MIXES",
            "strategy": "mix",
            "size": 1,
            "genre": slot["genre"],
            "exclusion_window": slot["exclusion_window"],
        }
    if type_ == "prev_ref":
        _require_fields(slot, idx, "prev_ref", required={"position", "fallback_genre"})
        return {
            "source": "MIXES",
            "strategy": "prev_ref",
            "size": 1,
            "position": slot["position"],
            "fallback_genre": slot["fallback_genre"],
        }
    if type_ == "night":
        _require_fields(slot, idx, "night", required={"cursor_id"})
        return {
            "source": "NIGHT",
            "strategy": "sequential",
            "size": 1,
            "cursor_id": slot["cursor_id"],
        }
    if type_ == "weekend":
        _require_fields(slot, idx, "weekend", required={"cursor_id"})
        return {
            "source": "WEEKEND",
            "strategy": "sequential",
            "size": 1,
            "cursor_id": slot["cursor_id"],
        }

    # Unreachable — the type-check above this block already rejects
    # unknown types. Defensive `assert False` would also work but
    # raising mirrors the helper's idiom.
    raise WeeklyImportError(  # pragma: no cover
        f"slot {idx + 1}: unhandled weekly slot type {type_!r} "
        f"(in _SLOT_FIELDS_BY_TYPE but no translation branch)."
    )


def _require_fields(slot: dict[str, Any], idx: int, type_: str, *, required: set[str]) -> None:
    """Raise WeeklyImportError if any required field is missing from `slot`."""
    missing = required - set(slot.keys())
    if missing:
        raise WeeklyImportError(
            f"slot {idx + 1} (type={type_!r}): missing required fields "
            f"{sorted(missing)}. Got: {sorted(slot.keys())}"
        )
