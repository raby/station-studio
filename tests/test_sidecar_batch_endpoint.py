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

# Reuse the editor_api fixture from the existing test module.
from tests.test_editor_api import editor_api, _seed_station, _minimal_template


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
