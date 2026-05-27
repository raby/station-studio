"""Tests for the §16.2.2 c1 editor sidecar endpoint.

Three resolution paths (per DESIGN.md §16.2.2 Decision E):
  (a) absent sidecar → synthesize trivial; sidecar_error=null
  (b) present + valid → return as-is; sidecar_error=null
  (c) present + invalid → trivial fallback + sidecar_error populated

Plus 404 for unknown station slug — the ONE failure mode that
SHOULD surface as a real not-found.

Tests override `get_stations_dir` to a tmp_path so the throwaway
sidecars don't pollute the repo's committed ones (and so cross-
test isolation is real).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db, get_stations_dir
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station


@pytest.fixture
def editor_api(tmp_path, monkeypatch):
    """TestClient + Session bound to a throwaway DB + tmp stations dir.

    Yields (client, Session, stations_dir). Tests write `.editor.json`
    fixtures into `stations_dir` before hitting the endpoint.
    """
    db_url = f"sqlite:///{tmp_path}/editor.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    monkeypatch.setenv("STATION_STUDIO_SKIP_AUTO_SEED", "1")

    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    Session: sessionmaker = make_sessionmaker(engine)

    stations_dir = tmp_path / "stations"
    stations_dir.mkdir()

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    def _override_stations_dir() -> Path:
        return stations_dir

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_stations_dir] = _override_stations_dir
    with TestClient(app) as client:
        yield client, Session, stations_dir
    app.dependency_overrides.clear()


def _minimal_template(slug: str = "ts") -> dict:
    """Minimal flat_slots template with 3 atomic slots — same shape
    used by the c1 jobs API tests. Deterministic + small."""
    return {
        "station": slug,
        "name": "Test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "A", "colour": "#abc", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"C": 0},
            "flat_slots": [
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C"}
            ] * 3,
        },
    }


def _seed_station(Session: sessionmaker, slug: str = "ts") -> dict:
    """Insert a Station with the minimal template. Returns the template
    for follow-up assertions."""
    template = _minimal_template(slug)
    with Session() as db:
        db.add(Station(slug=slug, name="Test", template=template))
        db.commit()
    return template


# ---------- 404 ----------------------------------------------------------


def test_unknown_station_returns_404(editor_api):
    """Decision E: 404 is reserved for "the station itself doesn't
    exist". Missing/broken sidecars do NOT 404 — they fall back to
    the trivial sidecar."""
    client, _Session, _dir = editor_api
    r = client.get("/api/stations/nope/sidecar")
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


# ---------- case (a): absent sidecar ------------------------------------


def test_absent_sidecar_returns_synthesized_trivial(editor_api):
    """No `stations/{slug}.editor.json` → server synthesizes a trivial
    sidecar (one "All slots" section, every block atomic). This is
    the DESIGN-intended path for un-authored stations, NOT an error
    state — sidecar_error stays null."""
    client, Session, _dir = editor_api
    template = _seed_station(Session, slug="trivial")

    r = client.get("/api/stations/trivial/sidecar")
    assert r.status_code == 200, r.json()
    body = r.json()

    # Template echoed back unchanged.
    assert body["template"] == template
    # sidecar_error null because absent ≠ broken.
    assert body["sidecar_error"] is None
    # Synthesized shape: one section, three atomic blocks (matches
    # flat_slots length).
    sc = body["sidecar"]
    assert sc["schema_version"] == 1
    assert sc["layout_order"] == ["all-slots"]
    assert len(sc["sections"]) == 1
    assert sc["sections"][0]["id"] == "all-slots"
    assert len(sc["sections"][0]["blocks"]) == 3
    assert all(b["kind"] == "atomic" for b in sc["sections"][0]["blocks"])
    assert sc["macros"] == []


# ---------- case (b): present + valid -----------------------------------


def test_present_valid_sidecar_returns_as_is(editor_api):
    """A hand-authored, valid sidecar passes through unchanged."""
    client, Session, stations_dir = editor_api
    _seed_station(Session, slug="authored")

    sidecar = {
        "schema_version": 1,
        "sections": [
            {
                "id": "weekdays",
                "name": "Weekdays",
                "blocks": [
                    {"kind": "atomic", "slot_index": 0},
                    {"kind": "atomic", "slot_index": 1},
                ],
            },
            {
                "id": "weekend",
                "name": "Weekend",
                "blocks": [
                    {"kind": "atomic", "slot_index": 2},
                ],
            },
        ],
        "macros": [],
        "layout_order": ["weekdays", "weekend"],
    }
    (stations_dir / "authored.editor.json").write_text(
        json.dumps(sidecar, indent=2),
    )

    r = client.get("/api/stations/authored/sidecar")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["sidecar"] == sidecar
    assert body["sidecar_error"] is None


# ---------- case (c): present + invalid ---------------------------------


def test_malformed_json_sidecar_falls_back_with_error_message(editor_api):
    """A sidecar file that's not valid JSON falls back to the trivial
    sidecar; `sidecar_error` carries the parser's error message so
    the frontend can render a warning banner pointing the operator
    at the bad file."""
    client, Session, stations_dir = editor_api
    _seed_station(Session, slug="badjson")
    (stations_dir / "badjson.editor.json").write_text("{not valid json,")

    r = client.get("/api/stations/badjson/sidecar")
    assert r.status_code == 200
    body = r.json()
    # Trivial fallback — 3 atomic slots in one section.
    assert len(body["sidecar"]["sections"]) == 1
    assert body["sidecar"]["sections"][0]["id"] == "all-slots"
    # Error message populated.
    assert body["sidecar_error"] is not None
    assert "malformed" in body["sidecar_error"].lower()


def test_drift_invalid_sidecar_falls_back_with_error_message(editor_api):
    """A structurally-valid JSON file that fails the drift rules
    (e.g. a slot_index out of range for the template's flat_slots)
    also triggers the trivial fallback + sidecar_error path."""
    client, Session, stations_dir = editor_api
    _seed_station(Session, slug="drift")

    # slot_index=99 — out of range for a 3-slot template.
    bad = {
        "schema_version": 1,
        "sections": [
            {
                "id": "s1", "name": "S1",
                "blocks": [{"kind": "atomic", "slot_index": 99}],
            }
        ],
        "macros": [],
        "layout_order": ["s1"],
    }
    (stations_dir / "drift.editor.json").write_text(json.dumps(bad))

    r = client.get("/api/stations/drift/sidecar")
    assert r.status_code == 200
    body = r.json()
    # Trivial fallback.
    assert body["sidecar"]["sections"][0]["id"] == "all-slots"
    # Drift-rule message — the exact text comes from the parser; we
    # don't pin the wording, just that the error is populated.
    assert body["sidecar_error"] is not None
    assert "slot_index" in body["sidecar_error"] or "99" in body["sidecar_error"]


# ---------- contract details --------------------------------------------


def test_response_carries_both_template_and_sidecar_in_one_request(editor_api):
    """The frontend's single-fetch contract: one GET returns
    everything needed to render the canvas. No second round-trip
    for the template."""
    client, Session, _dir = editor_api
    _seed_station(Session, slug="both")
    r = client.get("/api/stations/both/sidecar")
    assert r.status_code == 200
    body = r.json()
    # Both top-level fields present + non-null.
    assert "template" in body and body["template"] is not None
    assert "sidecar" in body and body["sidecar"] is not None
    assert "sidecar_error" in body  # may be null but key must exist


def test_non_editor_metadata_exception_falls_back_to_trivial(editor_api, monkeypatch):
    """c1 review 🟡-1 regression guard: any exception from sidecar
    loading — not just EditorMetadataError — must fall back to the
    trivial sidecar with `sidecar_error` populated. Decision E
    promises the frontend ALWAYS gets a valid sidecar; a 500 here
    would break that contract.

    Simulate by patching parse_sidecar to raise an unrelated
    exception class (the runner's broad-catch should kick in). Two
    known triggers in production: OSError from a file read racing
    the is_file() check, and KeyError from validate_sidecar reading
    a corrupted template — same end state for the caller, same
    test target."""
    client, Session, stations_dir = editor_api
    _seed_station(Session, slug="oops")
    # Create the file so the endpoint doesn't take the
    # "absent → trivial" path before reaching parse_sidecar.
    (stations_dir / "oops.editor.json").write_text("{}")

    import api.routers.editor as editor_mod

    def boom(*args, **kwargs):
        raise OSError("simulated read failure")

    monkeypatch.setattr(editor_mod, "parse_sidecar", boom)

    r = client.get("/api/stations/oops/sidecar")
    # Still 200 — Decision E contract.
    assert r.status_code == 200, r.json()
    body = r.json()
    # Trivial fallback present.
    assert body["sidecar"]["sections"][0]["id"] == "all-slots"
    # Error message carries class name + original text so the
    # operator can triage from the response.
    assert body["sidecar_error"] is not None
    assert "OSError" in body["sidecar_error"]
    assert "simulated read failure" in body["sidecar_error"]


def test_large_sidecar_response_is_gzip_compressed(editor_api):
    """§16.2.2.t3 — gzip middleware compresses large JSON responses.
    Closes the watch-list item: Rebirth Radio's synthesized-trivial
    sidecar (2,196 atomic blocks) shipped ~100KB of JSON per
    request. With `Accept-Encoding: gzip` on the wire (the default
    for browsers + httpx), starlette's GZipMiddleware compresses
    any response ≥1KB.

    Construct a station with enough slots to cross the threshold +
    assert the response carries `content-encoding: gzip`.
    """
    client, Session, _dir = editor_api
    # 600 atomic flat_slots → synthesized-trivial sidecar with 600
    # `{"kind":"atomic","slot_index":i}` entries. JSON size lands
    # ~20-25KB raw; comfortably above the 1KB minimum_size + small
    # enough to keep the test fast.
    with Session() as db:
        template = {
            "station": "big",
            "name": "Big",
            "cadence": "weekly",
            "rng": "off",
            "sources": [{"name": "A", "colour": "#abc", "role": "content"}],
            "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
            "layout": {
                "unit": "slot",
                "unit_labels": [],
                "row_start_offsets": [],
                "cursors_init": {"C": 0},
                "flat_slots": [
                    {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C"}
                ] * 600,
            },
        }
        db.add(Station(slug="big", name="Big", template=template))
        db.commit()

    r = client.get("/api/stations/big/sidecar")
    assert r.status_code == 200
    # TestClient sets Accept-Encoding: gzip,deflate by default; the
    # middleware activates above the 1000-byte threshold.
    assert r.headers.get("content-encoding") == "gzip", (
        f"expected gzip; got headers={dict(r.headers)}"
    )
    # Body still decodes correctly (httpx transparently un-gzips).
    body = r.json()
    assert len(body["sidecar"]["sections"][0]["blocks"]) == 600


def test_small_sidecar_response_skips_gzip(editor_api):
    """§16.2.2.t3 — minimum_size=1000 means tiny responses skip
    the compression overhead. Belt + suspenders: confirms we're not
    gzipping everything blindly, which would slow per-CRUD-call
    endpoints + complicate debugging in dev tools."""
    client, Session, _dir = editor_api
    _seed_station(Session, slug="tiny")  # 1-block station — <1KB JSON
    r = client.get("/api/stations/tiny/sidecar")
    assert r.status_code == 200
    assert r.headers.get("content-encoding") != "gzip"


def test_large_sidecar_response_honours_accept_encoding_identity(editor_api):
    """§16.2.2.t3 review 🟡-2 — clients can opt out of gzip via
    `Accept-Encoding: identity`. Belt-and-suspenders against the
    TestClient's default Accept-Encoding ever changing: this test
    pins the behaviour against an explicit identity request."""
    client, Session, _dir = editor_api
    # Same 600-slot setup as test_large_sidecar_response_is_gzip_compressed
    # — comfortably above the 1KB threshold.
    with Session() as db:
        template = {
            "station": "big-identity",
            "name": "Big Identity",
            "cadence": "weekly",
            "rng": "off",
            "sources": [{"name": "A", "colour": "#abc", "role": "content"}],
            "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
            "layout": {
                "unit": "slot",
                "unit_labels": [],
                "row_start_offsets": [],
                "cursors_init": {"C": 0},
                "flat_slots": [
                    {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C"}
                ] * 600,
            },
        }
        db.add(Station(slug="big-identity", name="Big Identity", template=template))
        db.commit()

    r = client.get(
        "/api/stations/big-identity/sidecar",
        headers={"Accept-Encoding": "identity"},
    )
    assert r.status_code == 200
    # Client opted out of gzip — middleware MUST honour it.
    assert r.headers.get("content-encoding") != "gzip"
    # Body still parses normally.
    assert len(r.json()["sidecar"]["sections"][0]["blocks"]) == 600
