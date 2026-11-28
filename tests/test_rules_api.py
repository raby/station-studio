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
from engine.models import Base, Rule, Station


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
    with TestClient(app, base_url="http://localhost") as client:
        yield client, Session
    app.dependency_overrides.clear()
    engine.dispose()


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
        "version_retention_cap",  # §16.4.t3
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
    from engine.models import SourcePlaylist, SourcePlaylistEntry, Station, Track
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
    """Setting `[]` via PUT is persisted (set_rule_list_value stores
    empty, §16.14.t5 c1), and the GET reads it back. `is_override`
    compares with the station's default, which for this station is
    the registry's `[]`: so an explicit `[]` reads as the default,
    not an override. (Pre-fix it meant "a row exists"; see
    test_template_declared_rule_* for why that changed.)"""
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
    assert body["is_override"] is False

    # GET confirms persistence: the row is there.
    with Session() as db:
        st = db.query(Station).filter(Station.slug == "rb").one()
        row = db.query(Rule).filter_by(
            station_id=st.id, key="cross_pool_dedup_sources",
        ).one()
        assert row.value == ""


# ---------- a template's own rule values (Clear override puts them back) --


def _seed_starter(Session, tmp_path, *, extra_rules: dict | None = None) -> None:
    """The bundled starter, seeded the way first start does it, with
    `extra_rules` added to its `rules:` block."""
    import json
    from pathlib import Path

    from engine.seeding import seed_station

    raw = json.loads((Path(__file__).parent.parent / "stations" / "starter.json").read_text())
    raw.setdefault("rules", {}).update(extra_rules or {})
    path = tmp_path / "starter.json"
    path.write_text(json.dumps(raw))
    with Session() as db:
        seed_station(db, path)
        db.commit()


def _rules(client) -> dict:
    return {
        r["key"]: r for r in client.get("/api/stations/starter/rules").json()["rules"]
    }


def test_template_declared_rule_is_the_stations_default_not_an_override(api, tmp_path):
    """A new install's starter: its template's value reads as its
    default, so the panel says "default" and offers no Reset."""
    client, Session = api
    _seed_starter(Session, tmp_path)
    rule = _rules(client)["cross_pool_dedup_sources"]
    assert rule["value"] == ["REFERENCE_ARCHIVE"]
    assert rule["default_value"] == ["REFERENCE_ARCHIVE"]
    assert rule["is_override"] is False


def test_clear_override_puts_the_templates_value_back(api, tmp_path):
    """The bug: Clear override dropped the starter's
    ["REFERENCE_ARCHIVE"] for the registry's [], and nothing in the app
    brought it back. Now the row goes and the rule reads as the
    template's value again, where generation reads it too."""
    from engine.rules import get_rule_list_value

    client, Session = api
    _seed_starter(Session, tmp_path)
    r = client.put(
        "/api/stations/starter/rules/cross_pool_dedup_sources", json={"value": []},
    )
    assert r.json()["is_override"] is True

    assert client.delete(
        "/api/stations/starter/rules/cross_pool_dedup_sources",
    ).status_code == 204
    rule = _rules(client)["cross_pool_dedup_sources"]
    assert rule["value"] == ["REFERENCE_ARCHIVE"]
    assert rule["is_override"] is False
    with Session() as db:
        st = db.query(Station).filter(Station.slug == "starter").one()
        assert get_rule_list_value(
            db, station_id=st.id, key="cross_pool_dedup_sources",
        ) == ["REFERENCE_ARCHIVE"]


def test_a_value_lost_to_the_old_clear_override_comes_back_by_itself(api, tmp_path):
    """A station already hit by the bug has no row. With no row a rule
    reads as the template's value, so it's back without anyone
    pressing Reset, for the panel and for generation alike."""
    from engine.rules import get_rule_list_value

    client, Session = api
    _seed_starter(Session, tmp_path)
    with Session() as db:
        db.query(Rule).filter_by(key="cross_pool_dedup_sources").delete()
        db.commit()
    rule = _rules(client)["cross_pool_dedup_sources"]
    assert rule["value"] == ["REFERENCE_ARCHIVE"]
    assert rule["is_override"] is False
    with Session() as db:
        st = db.query(Station).filter(Station.slug == "starter").one()
        assert get_rule_list_value(
            db, station_id=st.id, key="cross_pool_dedup_sources",
        ) == ["REFERENCE_ARCHIVE"]


def test_a_fork_keeps_its_templates_rule_values(api, tmp_path):
    """Forks copy the template but not Rule rows (§16.2.8 D1). The
    template's values still hold for the fork: not an override, and
    what generation reads."""
    from engine.rules import get_rule_list_value

    client, Session = api
    _seed_starter(Session, tmp_path)
    r = client.post("/api/stations/starter/fork", json={
        "new_slug": "my-starter", "new_name": "My starter",
    })
    assert r.status_code == 201, r.json()
    rules = {
        x["key"]: x
        for x in client.get("/api/stations/my-starter/rules").json()["rules"]
    }
    rule = rules["cross_pool_dedup_sources"]
    assert (rule["value"], rule["is_override"]) == (["REFERENCE_ARCHIVE"], False)
    with Session() as db:
        st = db.query(Station).filter(Station.slug == "my-starter").one()
        assert db.query(Rule).filter_by(station_id=st.id).count() == 0
        assert get_rule_list_value(
            db, station_id=st.id, key="cross_pool_dedup_sources",
        ) == ["REFERENCE_ARCHIVE"]


def test_clear_override_puts_a_templates_int_value_back(api, tmp_path):
    from engine.rules import get_rule_value

    client, Session = api
    _seed_starter(Session, tmp_path, extra_rules={"artist_dedup_window": 25})
    assert _rules(client)["artist_dedup_window"]["default_value"] == 25
    assert _rules(client)["artist_dedup_window"]["is_override"] is False

    client.put("/api/stations/starter/rules/artist_dedup_window", json={"value": 40})
    assert _rules(client)["artist_dedup_window"]["is_override"] is True
    client.delete("/api/stations/starter/rules/artist_dedup_window")
    rule = _rules(client)["artist_dedup_window"]
    assert (rule["value"], rule["is_override"]) == (25, False)
    with Session() as db:
        st = db.query(Station).filter(Station.slug == "starter").one()
        assert get_rule_value(db, station_id=st.id, key="artist_dedup_window") == 25


def test_clear_override_without_a_template_value_still_goes_to_the_registry(api, tmp_path):
    """Undeclared keys are as before: the row goes, the registry
    default stands."""
    client, Session = api
    _seed_starter(Session, tmp_path)
    client.put("/api/stations/starter/rules/mix_exclusion_weeks", json={"value": 3})
    client.delete("/api/stations/starter/rules/mix_exclusion_weeks")
    rule = _rules(client)["mix_exclusion_weeks"]
    assert (rule["value"], rule["default_value"], rule["is_override"]) == (20, 20, False)
    with Session() as db:
        assert db.query(Rule).filter_by(key="mix_exclusion_weeks").first() is None


def test_station_rule_defaults_reads_the_rules_block():
    from engine.seeding import station_rule_defaults

    assert station_rule_defaults("x", None) == {}
    assert station_rule_defaults("x", {}) == {}
    # An unreadable rules block: registry defaults stand.
    assert station_rule_defaults("x", {"rules": "nope"}) == {}
    assert station_rule_defaults("x", {"rules": {"artist_dedup_window": -1}}) == {}
    # The rest of the template needn't parse: only the block is read.
    assert station_rule_defaults("x", {"rules": {
        "artist_dedup_window": 5, "cross_pool_dedup_sources": ["A"],
    }}) == {"artist_dedup_window": 5, "cross_pool_dedup_sources": ["A"]}


def test_a_fork_generates_the_same_week_as_its_original(api, tmp_path):
    """End to end through generation: the starter and a fork of it, same
    seed, same songs. Before, the fork had no Rule rows, so it skipped
    the starter's cross-pool dedup and could play REFERENCE_ARCHIVE
    tracks the starter rules out."""
    from engine.models import Playlist
    from engine.seeding import _REPO_STATIONS_DIR, auto_import_starter_pools

    client, Session = api
    _seed_starter(Session, tmp_path)
    with Session() as db:
        auto_import_starter_pools(db, _REPO_STATIONS_DIR)
        db.commit()
    assert client.post("/api/stations/starter/fork", json={
        "new_slug": "my-starter", "new_name": "My starter",
    }).status_code == 201

    for slug in ("starter", "my-starter"):
        r = client.post(f"/api/stations/{slug}/generate", json={"week": 1, "seed": 5})
        assert r.status_code == 201, r.json()

    with Session() as db:
        weeks = {
            p.station.slug: [e.track.persistent_id for e in p.entries]
            for p in db.query(Playlist).all()
        }
    assert weeks["starter"] and weeks["starter"] == weeks["my-starter"]
