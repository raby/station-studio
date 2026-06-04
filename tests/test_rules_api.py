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
    """§16.14.t10: GET surfaces BOTH int + list rules, alphabetically
    interleaved (D5), with kind discriminator on every row (D1).
    Pre-t10 this list was int-only (4 keys); t10 added
    cross_pool_dedup_sources from RULE_LIST_DEFAULTS."""
    client, Session = api
    _seed_station(Session)
    r = client.get("/api/stations/rb/rules")
    assert r.status_code == 200
    body = r.json()
    keys = [rule["key"] for rule in body["rules"]]
    assert keys == [
        "artist_dedup_window",
        "cross_pool_dedup_sources",
        "cursor_advance_step",
        "cursor_reset_cycle",
        "mix_exclusion_weeks",
    ]
    # All defaults; no overrides.
    for rule in body["rules"]:
        assert rule["is_override"] is False
        assert rule["value"] == rule["default_value"]
    # D1 kind discriminator + D2 value shape per kind.
    by_key = {r["key"]: r for r in body["rules"]}
    assert by_key["artist_dedup_window"]["kind"] == "int"
    assert isinstance(by_key["artist_dedup_window"]["value"], int)
    assert by_key["cross_pool_dedup_sources"]["kind"] == "list"
    assert by_key["cross_pool_dedup_sources"]["value"] == []
    assert by_key["cross_pool_dedup_sources"]["max_value"] is None


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


# ---------- §16.14.t10 — list-rule CRUD path -----------------------------


def test_put_list_rule_creates_override(api):
    """PUT with a list body lands the override; GET reflects it."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": ["REFERENCE_ARCHIVE"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "cross_pool_dedup_sources"
    assert body["kind"] == "list"
    assert body["value"] == ["REFERENCE_ARCHIVE"]
    assert body["is_override"] is True
    # GET surfaces the persisted value.
    r = client.get("/api/stations/rb/rules")
    by_key = {ru["key"]: ru for ru in r.json()["rules"]}
    assert by_key["cross_pool_dedup_sources"]["value"] == ["REFERENCE_ARCHIVE"]


def test_put_list_rule_strips_whitespace_per_entry(api):
    """Validator strips per-entry whitespace and persists the
    canonical form. Read-back == cleaned form."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": ["  MASTER  ", "ARCHIVE"]},
    )
    assert r.status_code == 200
    assert r.json()["value"] == ["MASTER", "ARCHIVE"]


def test_put_list_rule_rejects_empty_entry(api):
    """Strict validator (parser-side semantics): empty entries are
    operator typos, not "drop silently" UX. 400 with the
    validator's message."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": ["MASTER", "  "]},
    )
    assert r.status_code == 400
    assert "non-empty" in r.json()["detail"]


def test_put_list_rule_rejects_comma_in_entry(api):
    """Comma is the on-disk separator; entries with commas
    would split silently on read. 400 with the validator's message."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": ["Rare, Imports"]},
    )
    assert r.status_code == 400
    assert "separator" in r.json()["detail"]


def test_put_list_rule_rejects_non_list_value(api):
    """`{"value": "not a list"}` for a list-rule key — 400 with the
    validator's "must be a list" message."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        # Pydantic accepts a str into `int | list[str]` as the union;
        # the validator then rejects it. Use a dict to force a
        # validator-level rejection.
        json={"value": {"a": "b"}},
    )
    # Pydantic 400/422 OR validator 400 — both are acceptable
    # "operator made a typo" surfaces.
    assert r.status_code in (400, 422)


def test_put_unknown_key_returns_400_with_known_keys_listed(api):
    """Unknown key → 400 with the union of valid keys in the
    error message (operator typo recovery)."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/totally_made_up_rule",
        json={"value": 42},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "totally_made_up_rule" in detail
    assert "artist_dedup_window" in detail
    assert "cross_pool_dedup_sources" in detail


def test_delete_list_rule_clears_override(api):
    """DELETE on a list-rule key clears the row; subsequent GET
    shows the registry default (empty list); idempotent re-DELETE
    returns 204."""
    client, Session = api
    _seed_station(Session)
    # Set then delete.
    client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": ["X"]},
    )
    r = client.delete("/api/stations/rb/rules/cross_pool_dedup_sources")
    assert r.status_code == 204

    by_key = {
        ru["key"]: ru for ru in
        client.get("/api/stations/rb/rules").json()["rules"]
    }
    assert by_key["cross_pool_dedup_sources"]["value"] == []
    assert by_key["cross_pool_dedup_sources"]["is_override"] is False

    # Idempotent — re-DELETE still 204.
    r = client.delete("/api/stations/rb/rules/cross_pool_dedup_sources")
    assert r.status_code == 204


def test_put_then_delete_round_trip_for_list_rule(api):
    """The §16.14.t9 c2-review F1 + §16.14.t10 design pass
    contract: PUT → state X, DELETE → state default. The full
    revert-via-API path that engine-level
    test_seed_station_api_delete_then_reseed_restores_template_default
    pinned at engine level now works through HTTP."""
    client, Session = api
    _seed_station(Session)

    # Start clean.
    r = client.get("/api/stations/rb/rules")
    by_key = {ru["key"]: ru for ru in r.json()["rules"]}
    assert by_key["cross_pool_dedup_sources"]["value"] == []

    # PUT a value.
    client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": ["A", "B"]},
    )
    by_key = {
        ru["key"]: ru for ru in
        client.get("/api/stations/rb/rules").json()["rules"]
    }
    assert by_key["cross_pool_dedup_sources"]["value"] == ["A", "B"]

    # DELETE returns 204 + back to default.
    r = client.delete("/api/stations/rb/rules/cross_pool_dedup_sources")
    assert r.status_code == 204
    by_key = {
        ru["key"]: ru for ru in
        client.get("/api/stations/rb/rules").json()["rules"]
    }
    assert by_key["cross_pool_dedup_sources"]["value"] == []


def test_put_list_value_to_int_key_dispatches_to_int_validator(api):
    """§16.14.t10 c1 review F10: cross-shape dispatch pin.
    PUT a list body to an INT-rule URL — the int handler runs,
    the int validator rejects with "must be int". Catches a
    future routing bug that mis-dispatches by key string rather
    than registry membership."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/artist_dedup_window",
        json={"value": ["X"]},
    )
    assert r.status_code == 400
    assert "must be int" in r.json()["detail"]


def test_put_int_value_to_list_key_dispatches_to_list_validator(api):
    """§16.14.t10 c1 review F10: cross-shape dispatch pin.
    PUT an int body to a LIST-rule URL — the list handler runs,
    the list validator rejects with "must be a list"."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": 5},
    )
    assert r.status_code == 400
    assert "must be a list" in r.json()["detail"]


def test_put_bool_value_rejected_at_schema_layer(api):
    """§16.14.t10 c1 review F1: Pydantic StrictInt rejects JSON
    `true` at the schema layer BEFORE our validator runs. Pre-
    fix, Pydantic coerced `true` → int=1, silently bypassing the
    D6 bool-rejection contract that engine.rules
    validate_int_rule_value enforces.

    StrictInt's rejection surfaces as a Pydantic ValidationError
    → 422 (FastAPI's default Pydantic error mapping). Either
    400 OR 422 is acceptable — both signal "operator typo" to
    the frontend."""
    client, Session = api
    _seed_station(Session)
    r = client.put(
        "/api/stations/rb/rules/artist_dedup_window",
        json={"value": True},
    )
    assert r.status_code in (400, 422)


def test_list_rule_explicit_empty_persists_distinct_from_default(api):
    """Setting `[]` via PUT IS persisted as an explicit "no
    exclusion" override (is_override=True). Distinct from the
    registry default which also reads as [] but with
    is_override=False. This is the engine-level distinction
    (§16.14.t5 c1: set_rule_list_value persists empty), now
    pinned at the API surface."""
    client, Session = api
    _seed_station(Session)

    # Pre-PUT: default.
    by_key = {
        ru["key"]: ru for ru in
        client.get("/api/stations/rb/rules").json()["rules"]
    }
    assert by_key["cross_pool_dedup_sources"]["is_override"] is False

    # PUT [] (explicit empty).
    r = client.put(
        "/api/stations/rb/rules/cross_pool_dedup_sources",
        json={"value": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == []
    assert body["is_override"] is True

    # GET confirms persistence.
    by_key = {
        ru["key"]: ru for ru in
        client.get("/api/stations/rb/rules").json()["rules"]
    }
    assert by_key["cross_pool_dedup_sources"]["is_override"] is True
