"""API tests for the §14.29 per-station rules CRUD endpoints.

Mirrors the test_api.py fixture pattern (throwaway SQLite DB via
dependency-override) so nothing leaks between tests or onto the real
DB.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station


@pytest.fixture
def api(tmp_path):
    """TestClient wired to a throwaway SQLite DB, plus a Session
    factory for seeding preconditions / asserting state."""
    engine = make_engine(f"sqlite:///{tmp_path}/rules_api.db")
    Base.metadata.create_all(engine)
    Session: sessionmaker = make_sessionmaker(engine)

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client, Session
    app.dependency_overrides.clear()


def _seed_station(Session: sessionmaker, slug: str = "rb") -> int:
    with Session() as db:
        st = Station(
            slug=slug, name=slug,
            template={
                "station": slug, "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.commit()
        return st.id


# ---------- GET ---------------------------------------------------------


def test_list_rules_returns_all_defaults_when_no_overrides(api):
    client, Session = api
    _seed_station(Session)
    r = client.get("/api/stations/rb/rules")
    assert r.status_code == 200
    body = r.json()
    keys = [rule["key"] for rule in body["rules"]]
    assert keys == [
        "artist_dedup_window",
        "cursor_advance_step",
        "cursor_reset_cycle",
        "mix_exclusion_weeks",
    ]
    # All defaults; no overrides.
    for rule in body["rules"]:
        assert rule["is_override"] is False
        assert rule["value"] == rule["default_value"]


def test_list_rules_unknown_station_returns_404(api):
    client, _ = api
    r = client.get("/api/stations/nope/rules")
    assert r.status_code == 404


# ---------- PUT ---------------------------------------------------------


def test_put_rule_creates_override(api):
    client, Session = api
    _seed_station(Session)
    r = client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": 25})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == 25
    assert body["default_value"] == 10
    assert body["is_override"] is True

    # Confirm via GET.
    listing = client.get("/api/stations/rb/rules").json()["rules"]
    by_key = {x["key"]: x for x in listing}
    assert by_key["artist_dedup_window"]["value"] == 25
    assert by_key["artist_dedup_window"]["is_override"] is True


def test_put_rule_upserts_existing_override(api):
    client, Session = api
    _seed_station(Session)
    client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": 15})
    r = client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": 50})
    assert r.status_code == 200
    assert r.json()["value"] == 50


def test_put_rule_unknown_key_returns_400(api):
    client, Session = api
    _seed_station(Session)
    r = client.put("/api/stations/rb/rules/bogus_key", json={"value": 10})
    assert r.status_code == 400
    assert "bogus_key" in r.json()["detail"]


def test_put_rule_negative_value_returns_400(api):
    client, Session = api
    _seed_station(Session)
    r = client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": -1})
    assert r.status_code == 400
    assert "non-negative" in r.json()["detail"]


def test_put_rule_above_max_returns_400(api):
    client, Session = api
    _seed_station(Session)
    r = client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": 9999})
    assert r.status_code == 400
    assert "at most 1000" in r.json()["detail"]


def test_put_rule_unknown_station_returns_404(api):
    client, _ = api
    r = client.put("/api/stations/nope/rules/artist_dedup_window", json={"value": 10})
    assert r.status_code == 404


# ---------- DELETE ------------------------------------------------------


def test_delete_rule_reverts_to_default(api):
    client, Session = api
    _seed_station(Session)
    client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": 50})
    r = client.delete("/api/stations/rb/rules/artist_dedup_window")
    assert r.status_code == 204

    listing = client.get("/api/stations/rb/rules").json()["rules"]
    by_key = {x["key"]: x for x in listing}
    assert by_key["artist_dedup_window"]["value"] == 10  # default
    assert by_key["artist_dedup_window"]["is_override"] is False


def test_delete_rule_idempotent(api):
    """Clearing a non-existent override is a 204 no-op, not 404."""
    client, Session = api
    _seed_station(Session)
    r = client.delete("/api/stations/rb/rules/artist_dedup_window")
    assert r.status_code == 204


def test_delete_rule_unknown_key_returns_400(api):
    client, Session = api
    _seed_station(Session)
    r = client.delete("/api/stations/rb/rules/bogus_key")
    assert r.status_code == 400


def test_delete_rule_unknown_station_returns_404(api):
    client, _ = api
    r = client.delete("/api/stations/nope/rules/artist_dedup_window")
    assert r.status_code == 404


# ---------- isolation ---------------------------------------------------


def test_rule_overrides_dont_bleed_between_stations(api):
    client, Session = api
    _seed_station(Session, slug="a")
    _seed_station(Session, slug="b")
    client.put("/api/stations/a/rules/artist_dedup_window", json={"value": 99})

    a_rules = {r["key"]: r for r in client.get("/api/stations/a/rules").json()["rules"]}
    b_rules = {r["key"]: r for r in client.get("/api/stations/b/rules").json()["rules"]}
    assert a_rules["artist_dedup_window"]["value"] == 99
    assert b_rules["artist_dedup_window"]["value"] == 10  # default

    # Deleting A's override doesn't affect B.
    client.delete("/api/stations/a/rules/artist_dedup_window")
    a_rules = {r["key"]: r for r in client.get("/api/stations/a/rules").json()["rules"]}
    assert a_rules["artist_dedup_window"]["value"] == 10


# ---------- §14.33 review fix: rule precedence end-to-end ---------------


def test_rule_override_threads_through_to_generate(api):
    """Closes the §14.33 review's identified gap: there was no test
    exercising "PUT a rule → generate → assert override applied."
    Engine-level precedence is unit-tested; this hits the full HTTP
    chain (rules router writes → generate router reads → engine
    consumes via weighted_window_default kwarg → trace records
    effective window).

    Uses a weighted slot since artist_dedup_window is the most
    visibly-wired rule. Default window=10; this test sets the rule
    to 25 and verifies the trace's window reflects the override.
    """
    from engine.models import Base, SourcePlaylist, SourcePlaylistEntry, Station, Track
    client, Session = api

    # Seed a station whose template has ONE weighted block (no per-block
    # BlockSpec.window override — so the rule's value is the effective
    # window).
    template = {
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot", "unit_labels": ["1"], "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "weighted", "size": 1},
            ],
        },
    }
    with Session() as db:
        st = Station(slug="rb", name="Rebirth", template=template)
        db.add(st)
        db.flush()
        sp = SourcePlaylist(name="A")
        db.add(sp)
        db.flush()
        for i, name in enumerate(["A1", "A2", "A3"], start=1):
            tr = Track(persistent_id=f"P{i}", name=name, artist="Artist X")
            db.add(tr)
            db.flush()
            db.add(SourcePlaylistEntry(
                source_playlist_id=sp.id, track_id=tr.id, position=i,
            ))
        db.commit()

    # Baseline: no rule override; effective window = engine default 10.
    r = client.post("/api/stations/rb/generate", json={"week": 1})
    assert r.status_code == 201
    trace = client.get(f"/api/playlists/{r.json()['playlist_id']}/trace").json()
    assert "window=10" in trace["slots"][0]["reason"]

    # Set the rule override.
    r = client.put("/api/stations/rb/rules/artist_dedup_window", json={"value": 25})
    assert r.status_code == 200

    # Re-generate; effective window now = 25 (rule overrides engine constant).
    r = client.post("/api/stations/rb/generate", json={"week": 2})
    assert r.status_code == 201
    trace = client.get(f"/api/playlists/{r.json()['playlist_id']}/trace").json()
    assert "window=25" in trace["slots"][0]["reason"]

    # Clear the override; back to engine default.
    r = client.delete("/api/stations/rb/rules/artist_dedup_window")
    assert r.status_code == 204

    r = client.post("/api/stations/rb/generate", json={"week": 3})
    trace = client.get(f"/api/playlists/{r.json()['playlist_id']}/trace").json()
    assert "window=10" in trace["slots"][0]["reason"]
