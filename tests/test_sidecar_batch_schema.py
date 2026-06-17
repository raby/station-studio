"""§16.2.6.t2 c1a review N3 — schema parse tests for SidecarBatchRequest.

The c1a smoke test was a one-off `python -c "..."` snippet; pinning
it here guards against alias / discriminator drift before c1b's
full endpoint tests land. Each test fixture uses the EXACT camelCase
payload the frontend sends (matching `web/src/lib/edit-ops.ts`'s
`Edit` union shape), not the snake_case Python field names — so an
alias regression like the N1 `macroId` miss surfaces immediately.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import SidecarBatchErrorResponse, SidecarBatchRequest


def test_all_nine_kinds_parse_from_camelcase_wire_shape() -> None:
    """Pin every Edit kind's wire shape end-to-end."""
    payload = {
        "edits": [
            {
                "kind": "patchBlock",
                "address": {"slot_index": 0},
                "fields": {"size": 5},
            },
            {
                "kind": "patchMacro",
                "macroId": "m1",  # N1: camelCase, NOT snake_case.
                "fields": {"strategy": "window"},
            },
            {
                "kind": "renameSection",
                "sectionId": "morning",
                "name": "Morning",
            },
            {
                "kind": "sectionCreate",
                "sectionId": "evening",
                "name": "Evening",
            },
            {
                "kind": "sectionDelete",
                "sectionId": "morning",
            },
            {
                "kind": "reorderSections",
                "layoutOrder": ["morning", "evening"],
            },
            {
                "kind": "moveBlock",
                "address": {"slot_index": 0},
                "targetSectionId": "evening",
                "targetIndex": 1,
            },
            {
                "kind": "retypeBlock",
                "address": {"slot_index": 0},
                "newStrategy": "window",
                "newSource": "A",
                "newFields": {"window": 4},
            },
            {
                "kind": "structuralBlockEdit",
                "edit": {"op": "size", "slot_index": 0, "new_size": 5},
            },
        ],
        "expected_updated_at": "2026-06-16T20:00:00Z",
    }
    req = SidecarBatchRequest(**payload)
    assert len(req.edits) == 9
    assert [e.kind for e in req.edits] == [
        "patchBlock",
        "patchMacro",
        "renameSection",
        "sectionCreate",
        "sectionDelete",
        "reorderSections",
        "moveBlock",
        "retypeBlock",
        "structuralBlockEdit",
    ]


def test_patch_macro_camelcase_alias_works() -> None:
    """N1 regression pin: `macroId` (frontend) must parse without
    422. Pre-fix, `_BatchPatchMacroEnv.macro_id` had no alias and
    Pydantic required snake_case; this test would have failed."""
    req = SidecarBatchRequest(
        edits=[{"kind": "patchMacro", "macroId": "m1", "fields": {"strategy": "window"}}],
    )
    # Verify the discriminated union parsed it correctly.
    assert req.edits[0].kind == "patchMacro"
    assert req.edits[0].macro_id == "m1"


def test_patch_macro_snake_case_also_works_via_populate_by_name() -> None:
    """Belt-and-braces: snake_case `macro_id` still parses (tests,
    direct Python callers). `populate_by_name=True` enables both."""
    req = SidecarBatchRequest(
        edits=[{"kind": "patchMacro", "macro_id": "m1", "fields": {"strategy": "window"}}],
    )
    assert req.edits[0].macro_id == "m1"


def test_unknown_kind_is_rejected() -> None:
    """Discriminator catches typos / future kinds the server doesn't
    know yet (e.g. a future `replaceMacro` kind sent by a newer
    frontend against an older backend)."""
    with pytest.raises(ValidationError):
        SidecarBatchRequest(edits=[{"kind": "futureKind", "foo": "bar"}])


def test_missing_required_field_per_kind_is_rejected() -> None:
    """Each envelope's required fields are pinned by the discriminator.
    A patchBlock without `fields` is invalid; a renameSection without
    `name` is invalid."""
    with pytest.raises(ValidationError):
        SidecarBatchRequest(
            edits=[{"kind": "patchBlock", "address": {"slot_index": 0}}],
        )
    with pytest.raises(ValidationError):
        SidecarBatchRequest(
            edits=[{"kind": "renameSection", "sectionId": "x"}],
        )


def test_sidecar_create_name_is_optional() -> None:
    """sectionCreate's `name` is `str | None` (auto-named on create
    per existing /sections route semantic). Other envelopes whose
    `name` is required (renameSection) should still 422 on missing."""
    req = SidecarBatchRequest(
        edits=[{"kind": "sectionCreate", "sectionId": "evening"}],
    )
    assert req.edits[0].name is None


def test_retype_block_new_fields_defaults_empty() -> None:
    """`newFields` defaults to an empty dict — the frontend always
    sends it, but the default protects against a future caller that
    omits the key (vs the patchBlock `fields` which is required —
    a patch with no fields is meaningless)."""
    req = SidecarBatchRequest(
        edits=[
            {
                "kind": "retypeBlock",
                "address": {"slot_index": 0},
                "newStrategy": "window",
            },
        ],
    )
    assert req.edits[0].new_fields == {}


def test_empty_edits_list_parses() -> None:
    """An empty batch is allowed at the schema layer. The endpoint
    (c1b) decides whether to treat it as a 200 no-op or a 400."""
    req = SidecarBatchRequest(edits=[])
    assert req.edits == []


def test_error_response_shape() -> None:
    """`failed_at_idx` is 1-indexed so UI can say 'edit 3 of 5 failed.'"""
    err = SidecarBatchErrorResponse(
        failed_at_idx=3,
        kind="patchBlock",
        error="edit produces an invalid template: ...",
    )
    body = err.model_dump()
    assert body["failed_at_idx"] == 3
    assert body["kind"] == "patchBlock"
    assert body["error"].startswith("edit produces")
