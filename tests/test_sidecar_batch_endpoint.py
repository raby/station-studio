"""§16.2.6.t2 c1b — pytest pins for the transactional batch endpoint.

Coverage:
  - Multi-edit success path: all edits commit atomically, response
    carries the updated sidecar + template + updated_at.
  - Mid-batch failure: a single bad edit rolls back EVERY prior
    mutation; the response is SidecarBatchErrorResponse with the
    1-based failing index.
  - Empty edits list → 400 (no-op batches shouldn't bump updated_at).
  - Each Edit kind dispatches correctly (smoke + one assertion each).
  - Optimistic-concurrency check (`expected_updated_at` mismatch → 409).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from engine.models import Station

# Reuse the editor_api fixture from the existing test module +
# the public `seed_station` helper from the shared module
# (§16.2.6.t12 P8 — moved out of test_editor_api's private surface).
from tests.test_editor_api import editor_api
from tests._helpers import seed_station as _seed_station, minimal_template as _minimal_template


def _sidecar_for(template: dict) -> dict:
    """Minimal sidecar matching the 3-slot fixture template."""
    return {
        "schema_version": 1,
        "sections": [
            {"id": "morning", "name": "Morning", "blocks": [
                {"kind": "atomic", "slot_index": 0},
                {"kind": "atomic", "slot_index": 1},
            ]},
            {"id": "evening", "name": "Evening", "blocks": [
                {"kind": "atomic", "slot_index": 2},
            ]},
        ],
        "macros": [],
        "layout_order": ["morning", "evening"],
    }


@pytest.fixture
def seeded(editor_api):
    client, Session, _dir = editor_api
    template = _seed_station(Session, slug="ts")
    # Inline the sidecar via the DB column.
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "ts").first()
        s.sidecar = _sidecar_for(template)
        db.commit()
    return client, Session, template


def test_empty_edits_list_returns_400(seeded):
    client, _Session, _t = seeded
    r = client.post("/api/stations/ts/sidecar/batch", json={"edits": []})
    assert r.status_code == 400
    body = r.json()
    assert body["failed_at_idx"] == 0
    assert "at least one" in body["error"]


def test_multi_edit_success_commits_atomically(seeded):
    """Two patchBlocks + one renameSection all land in one commit."""
    client, Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [
                {"kind": "patchBlock", "address": {"slot_index": 0}, "fields": {"cursor_id": "C"}},
                {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
                {"kind": "patchBlock", "address": {"slot_index": 1}, "fields": {"cursor_id": "C"}},
            ],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["sidecar"]["sections"][0]["name"] == "AM"
    # template still has 3 slots.
    assert len(body["template"]["layout"]["flat_slots"]) == 3
    # updated_at bumped.
    assert body["updated_at"] is not None


def test_mid_batch_failure_rolls_back_prior_mutations(seeded):
    """A second-edit failure must undo the first edit's mutation."""
    client, Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [
                {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
                # This one fails: section_id doesn't exist.
                {"kind": "renameSection", "sectionId": "ghost", "name": "X"},
                {"kind": "renameSection", "sectionId": "evening", "name": "PM"},
            ],
        },
    )
    assert r.status_code == 404
    body = r.json()
    assert body["failed_at_idx"] == 2  # 1-based
    assert body["kind"] == "renameSection"
    assert "ghost" in body["error"]
    # Verify Tab A's edit DIDN'T land.
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "ts").first()
        sections = s.sidecar["sections"]
        assert sections[0]["name"] == "Morning"  # NOT "AM"
        assert sections[1]["name"] == "Evening"


def test_optimistic_concurrency_409(seeded):
    """Bad `expected_updated_at` → 409 before any apply runs."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{"kind": "renameSection", "sectionId": "morning", "name": "AM"}],
            "expected_updated_at": "2000-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 409


def test_dispatches_each_kind(seeded):
    """Smoke test: one example of each non-destructive kind in a
    single batch (omit moveBlock + sectionDelete + structuralBlockEdit
    which require setup; they're covered in separate tests below)."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [
                {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
                {"kind": "sectionCreate", "sectionId": "afternoon", "name": "Afternoon"},
                {"kind": "reorderSections", "layoutOrder": ["morning", "afternoon", "evening"]},
            ],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    sec_ids = [s["id"] for s in body["sidecar"]["sections"]]
    assert "afternoon" in sec_ids
    assert body["sidecar"]["layout_order"] == ["morning", "afternoon", "evening"]


def test_move_block(seeded):
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{
                "kind": "moveBlock",
                "address": {"slot_index": 0},
                "targetSectionId": "evening",
                "targetIndex": 0,
            }],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    morning = next(s for s in body["sidecar"]["sections"] if s["id"] == "morning")
    evening = next(s for s in body["sidecar"]["sections"] if s["id"] == "evening")
    assert all(b["slot_index"] != 0 for b in morning["blocks"])
    assert evening["blocks"][0]["slot_index"] == 0


def test_structural_size(seeded):
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{
                "kind": "structuralBlockEdit",
                "edit": {"op": "size", "slot_index": 0, "new_size": 5},
            }],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["template"]["layout"]["flat_slots"][0]["size"] == 5


def test_failed_at_idx_for_first_edit(seeded):
    """failed_at_idx is 1-based — first-edit failure is `1`, not `0`."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [
                # First edit fails: ghost section.
                {"kind": "renameSection", "sectionId": "ghost", "name": "X"},
            ],
        },
    )
    assert r.status_code == 404
    assert r.json()["failed_at_idx"] == 1


def test_unknown_station_returns_404(editor_api):
    client, _Session, _dir = editor_api
    r = client.post(
        "/api/stations/nope/sidecar/batch",
        json={"edits": [{"kind": "renameSection", "sectionId": "x", "name": "Y"}]},
    )
    assert r.status_code == 404


def test_t6_single_edit_parse_template_failure_attributes_to_edit_1(
    seeded, monkeypatch,
):
    """Slice-review T6 honesty: for a 1-edit batch, parse_template
    failure IS attributable to edit 1 (no composition window).
    failed_at_idx=1 + kind=<the edit's kind>, NOT the sentinel.
    Multi-edit batches keep the _finalize sentinel (see
    test_p3_finalize_failure_uses_failed_at_idx_zero_sentinel)."""
    import api.routers.editor as editor_module
    from engine.template import TemplateError

    client, _Session, _t = seeded

    def _boom_finalize(_template):
        raise TemplateError("synthetic single-edit invalidation")

    monkeypatch.setattr(editor_module, "parse_template", _boom_finalize)
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={"edits": [
            {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
        ]},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["failed_at_idx"] == 1
    assert body["kind"] == "renameSection"
    assert "synthetic single-edit" in body["error"]
    # Message text reflects single-edit framing ("edit produces"
    # NOT "batch produces").
    assert body["error"].startswith("edit produces")


def test_p3_finalize_failure_uses_failed_at_idx_zero_sentinel(
    seeded, monkeypatch,
):
    """c1b review P3: when parse_template at the END of the batch
    fails, `failed_at_idx` is 0 (NOT len(edits)) — the breakage is
    a composition effect across all edits; pointing at "edit N of N"
    misleads the operator. UI branches on kind=='_finalize'."""
    import api.routers.editor as editor_module
    from engine.template import TemplateError

    client, _Session, _t = seeded

    def _boom_finalize(_template):
        raise TemplateError("synthetic cross-edit composition failure")

    monkeypatch.setattr(editor_module, "parse_template", _boom_finalize)
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={"edits": [
            {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
            {"kind": "renameSection", "sectionId": "evening", "name": "PM"},
        ]},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["failed_at_idx"] == 0  # NOT len(edits); P3 sentinel.
    assert body["kind"] == "_finalize"
    assert "synthetic cross-edit" in body["error"]


def test_p4_non_http_exception_returns_structured_500_envelope(
    seeded, monkeypatch,
):
    """c1b review P4: a non-HTTPException from `_apply_batch_edit`
    (KeyError/IndexError/etc. from malformed in-memory state) must
    return the SidecarBatchErrorResponse envelope at status 500 +
    explicitly rollback. Pre-fix the request would 500 with FastAPI's
    default `{"detail": ...}` shape that the c2 Retry/Drop modal
    wouldn't recognize."""
    import api.routers.editor as editor_module

    client, Session, _t = seeded

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic non-HTTP failure")

    monkeypatch.setattr(editor_module, "_apply_batch_edit", _boom)
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={"edits": [
            {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
        ]},
    )
    assert r.status_code == 500
    body = r.json()
    # Envelope shape preserved — c2 modal can branch on this.
    assert body["failed_at_idx"] == 1
    assert body["kind"] == "renameSection"
    assert "internal error" in body["error"].lower()
    # Rollback verified — original sidecar untouched.
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "ts").first()
        assert s.sidecar["sections"][0]["name"] == "Morning"


# §16.2.6.t2 slice-review sweep T2: per-kind dispatch tests for the
# 4 Edit kinds the c1b tests skipped (patchBlock, patchMacro,
# retypeBlock, sectionDelete). Each exercises the dispatcher's
# branch independently so a future inline-apply drift between the
# dispatcher + per-route handler surfaces at the batch layer too.


def test_dispatches_patch_block(seeded):
    """patchBlock applies to template's flat_slots[idx] via the batch."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{
                "kind": "patchBlock",
                "address": {"slot_index": 0},
                "fields": {"cursor_id": "C"},
            }],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    # Cursor_id is in the allowlist for `sequential` strategy.
    assert body["template"]["layout"]["flat_slots"][0]["cursor_id"] == "C"


def test_dispatches_section_delete(seeded):
    """sectionDelete moves blocks to _unsorted + removes the section."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{"kind": "sectionDelete", "sectionId": "morning"}],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    # morning gone; _unsorted carries its 2 atomic refs.
    sec_ids = [s["id"] for s in body["sidecar"]["sections"]]
    assert "morning" not in sec_ids
    unsorted = next(s for s in body["sidecar"]["sections"] if s["id"] == "_unsorted")
    assert len(unsorted["blocks"]) == 2


def test_dispatches_retype_block(seeded):
    """retypeBlock swaps strategy + filters fields by allowlist.

    Retype sequential → weighted. cursor_id (sequential's field)
    isn't in weighted's allowlist (`window`, `allowlist_sources`),
    so it should drop. Used weighted (not pick) because pick is
    only valid in row_template — flat_slots can't carry it."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{
                "kind": "retypeBlock",
                "address": {"slot_index": 0},
                "newStrategy": "weighted",
                "newSource": "A",
                "newFields": {},
            }],
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    block = body["template"]["layout"]["flat_slots"][0]
    assert block["strategy"] == "weighted"
    # cursor_id (sequential's required field) dropped — weighted has
    # no cursor_id in its allowlist.
    assert "cursor_id" not in block


def test_t12_p7_compound_rollback_structural_add_then_failure_undoes_shift(seeded):
    """§16.2.6.t12 P7 — compound-rollback regression test.

    Stages [structuralAdd(idx=0), renameSection(ghost)]:
      - structuralAdd at idx=0 would: insert a new flat_slot at 0;
        shift every existing atomic ref's slot_index by +1; ensure
        `_unsorted` bin exists; append {kind:atomic, slot_index:0}
        to that bin.
      - renameSection on a non-existent section ghosts a 404 →
        the batch rolls back the WHOLE transaction.

    Post-rollback we must verify NOT JUST that the rename didn't
    land (the simple test_mid_batch_failure_rolls_back_prior_mutations
    covers that for rename-only batches), but that the structural
    add's compounding effects ALSO unwound:
      - morning still has blocks at slot_index 0/1 (NOT shifted).
      - _unsorted bin doesn't exist.
      - flat_slots length is still 3 (not 4).

    The all-or-nothing transaction guarantees this implicitly, but
    no prior test pinned the structural invariants after a roll-
    back. The fundamental SQL transaction guarantee should hold
    regardless of how many compounding effects a single edit has.
    """
    client, Session, _t = seeded
    new_block = {
        "source": "A", "strategy": "sequential",
        "size": 1, "cursor_id": "C",
    }
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [
                {
                    "kind": "structuralBlockEdit",
                    "edit": {
                        "op": "add", "slot_index": 0, "new_block": new_block,
                    },
                },
                # Failing edit: ghost section.
                {"kind": "renameSection", "sectionId": "ghost", "name": "X"},
            ],
        },
    )
    assert r.status_code == 404
    # Verify ALL structural-add effects rolled back.
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "ts").first()
        # flat_slots length unchanged.
        assert len(s.template["layout"]["flat_slots"]) == 3
        # morning's atomic refs: slot_index 0 + 1 (NOT shifted to 1 + 2).
        morning = next(sec for sec in s.sidecar["sections"] if sec["id"] == "morning")
        assert [b["slot_index"] for b in morning["blocks"]] == [0, 1]
        # _unsorted bin should NOT exist (would have been auto-created
        # by structural add, but the rollback unwound it).
        sec_ids = [sec["id"] for sec in s.sidecar["sections"]]
        assert "_unsorted" not in sec_ids


def test_dispatches_patch_macro_404_when_macro_missing(seeded):
    """patchMacro 404s when macro_id doesn't exist in sidecar.

    The minimal seeded sidecar has macros: [], so any patchMacro
    edit 404s. That's enough to exercise the dispatcher's macro
    lookup branch — a full macro fixture would over-couple this
    test to engine internals."""
    client, _Session, _t = seeded
    r = client.post(
        "/api/stations/ts/sidecar/batch",
        json={
            "edits": [{
                "kind": "patchMacro",
                "macroId": "m1",
                "fields": {"strategy": "window"},
            }],
        },
    )
    assert r.status_code == 404
    body = r.json()
    assert body["failed_at_idx"] == 1
    assert body["kind"] == "patchMacro"
    assert "m1" in body["error"]
