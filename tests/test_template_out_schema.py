"""§16.2.5.t8 — strict-mode boundary tests for `TemplateOut`.

The wire envelope for `Station.template` was promoted from
`dict[str, Any]` to `TemplateOut` with `ConfigDict(strict=True,
extra="allow")`. These tests pin both halves of that contract:

  * Strict: required fields can't be missing; declared types can't be
    coerced (a `cadence: 1` int won't silently become `"1"`).
  * extra="allow": operator-authored fields beyond the model
    declaration (e.g. `rules`, `_comment`) round-trip through the
    boundary unchanged — a future schema bump doesn't need to mirror
    the new field in `TemplateOut` first.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import TemplateOut


def _minimal_valid() -> dict:
    """Smallest template dict that passes strict validation. Matches
    the shape used by `tests/_helpers.minimal_template` minus the
    optional `rules` block."""
    return {
        "station": "ts",
        "name": "Test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "A", "colour": "#abc", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "flat_slots": [],
        },
    }


# ---------- happy path ---------------------------------------------------


def test_minimal_template_parses() -> None:
    parsed = TemplateOut.model_validate(_minimal_valid())
    assert parsed.station == "ts"
    assert parsed.name == "Test"
    assert parsed.sources[0].name == "A"
    assert parsed.sources[0].role == "content"


# ---------- strict mode rejects missing required fields ------------------


@pytest.mark.parametrize(
    "missing_field",
    ["station", "name", "cadence", "rng", "sources", "cursor", "layout"],
)
def test_missing_required_field_rejected(missing_field: str) -> None:
    """A partial template (a refactor returns the wrong shape) must
    surface as a ValidationError at the boundary — not silently render
    as `undefined` on the canvas."""
    payload = _minimal_valid()
    del payload[missing_field]
    with pytest.raises(ValidationError) as exc_info:
        TemplateOut.model_validate(payload)
    # The error message names the missing field so the operator's log
    # points at the wire-shape bug, not a generic 500.
    assert missing_field in str(exc_info.value)


# ---------- strict mode rejects type coercion ----------------------------


def test_strict_blocks_int_for_string_cadence() -> None:
    """Without strict mode Pydantic would coerce `cadence: 1` into
    `"1"`. Strict mode rejects — a backend serialization bug that
    encodes a number where a string is expected surfaces loudly."""
    payload = _minimal_valid()
    payload["cadence"] = 1
    with pytest.raises(ValidationError):
        TemplateOut.model_validate(payload)


def test_strict_blocks_null_for_required_name() -> None:
    payload = _minimal_valid()
    payload["name"] = None
    with pytest.raises(ValidationError):
        TemplateOut.model_validate(payload)


def test_invalid_source_role_rejected() -> None:
    """`role` is a Literal["content", "reference"]. A typo like
    `"COntent"` or a new unstated role surfaces immediately rather
    than silently rendering as an unknown pool."""
    payload = _minimal_valid()
    payload["sources"][0]["role"] = "Content"
    with pytest.raises(ValidationError):
        TemplateOut.model_validate(payload)


def test_source_missing_name_rejected() -> None:
    payload = _minimal_valid()
    payload["sources"][0] = {"role": "content"}
    with pytest.raises(ValidationError):
        TemplateOut.model_validate(payload)


# ---------- extra="allow" preserves operator-authored fields --------------


def test_unknown_top_level_field_passes_through() -> None:
    """A future schema bump (e.g. adding `pool_manifest` at the top
    level) flows through the boundary without a schema patch on
    every deploy."""
    payload = _minimal_valid()
    payload["pool_manifest"] = {"v": 1, "pools": []}
    parsed = TemplateOut.model_validate(payload)
    # Dumps include the extra field — Pydantic's default for
    # extra="allow" is to surface them in model_dump.
    dumped = parsed.model_dump()
    assert dumped["pool_manifest"] == {"v": 1, "pools": []}


def test_optional_rules_block_round_trips() -> None:
    """Several shipped templates carry a `rules:` block parsed by
    engine.template.parse_template into rule_int/list overrides. Since
    `rules` isn't declared on TemplateOut, it rides through via
    extra="allow"."""
    payload = _minimal_valid()
    payload["rules"] = {"cross_pool_dedup_sources": ["REFERENCE_ARCHIVE"]}
    parsed = TemplateOut.model_validate(payload)
    dumped = parsed.model_dump()
    assert dumped["rules"] == {
        "cross_pool_dedup_sources": ["REFERENCE_ARCHIVE"],
    }


def test_undeclared_rules_omitted_from_dump_when_absent() -> None:
    """No `rules: null` should leak into the wire for a template that
    didn't author one (a regression here would break byte-for-byte
    round-trip tests against the source dict, e.g.
    test_absent_sidecar_returns_synthesized_trivial)."""
    payload = _minimal_valid()
    parsed = TemplateOut.model_validate(payload)
    dumped = parsed.model_dump()
    assert "rules" not in dumped
