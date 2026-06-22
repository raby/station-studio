"""FastAPI route-level tests.

Covers the endpoints touched or added during Session 2:

- POST /api/stations                       (wizard submit path)
- GET  /api/stations                       (landing-page list)
- GET  /api/stations/{slug}                (station detail)
- GET  /api/stations/{slug}/playlists      (station detail playlists table)
- GET  /api/playlists/{id}                 (playlist page)
- GET  /api/playlists/{id}/trace           (trace viewer)
- GET  /api/playlists/{id}/export          (browser download)
- POST /api/playlists/{id}/export-to-disk  (disk-export)

Each test runs against a fresh SQLite DB via a dependency-override on
api.deps.get_db, so nothing leaks between tests or onto the real DB.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.models import (
    Base, Config, CursorState, Playlist, PlaylistEntry, PlaylistVersion,
    SourcePlaylist, SourcePlaylistEntry, Station, Track,
)


# ---------- fixtures ------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient wired to a throwaway SQLite DB.

    Yields (client, Session). The test can use `client` to hit endpoints
    and `Session()` to seed preconditions or assert post-conditions.

    Sets `STATION_STUDIO_DB_URL` to the tmp path so the §sweep-1
    auto-seed lifespan handler (api/main.py:lifespan) points at the
    same DB as the dep-override — otherwise the lifespan would try
    to seed the real station_studio.db on every test run, which is
    both incorrect (clobbers dev DB) and slow.
    """
    db_url = f"sqlite:///{tmp_path}/api.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    # Opt out of the §sweep-1 auto-seed lifespan handler — most api
    # tests assume an empty Station table. Tests SPECIFICALLY
    # targeting auto-seed behaviour (test_lifespan_…) skip this fixture
    # and set up their own TestClient without this env var.
    monkeypatch.setenv("STATION_STUDIO_SKIP_AUTO_SEED", "1")

    engine = make_engine(db_url)
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


def _wizard_payload(slug: str = "test", name: str = "Test Station") -> dict:
    """The shape the wizard POSTs: a valid minimal template."""
    return {
        "slug": slug,
        "name": name,
        "template": {
            "station": slug,
            "name": name,
            "cadence": "weekly",
            "rng": "off",
            "sources": [{"name": "A", "colour": "#111", "role": "content"}],
            "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
            "layout": {
                "unit": "day",
                "unit_labels": ["Mon"],
                "row_start_offsets": [0],
                "row_template": [{"source": "A", "strategy": "pick", "size": 1}],
            },
        },
    }


def _seed_station_with_playlist(
    Session: sessionmaker,
    *,
    slug: str = "cls",
    name: str = "Classics",
    week: int = 1,
    tracks: list[dict] | None = None,
    with_trace: bool = True,
) -> tuple[int, int]:
    """Insert a minimal Station + Playlist + entries (+ optional Trace).

    Returns (station_id, playlist_id).
    """
    tracks = tracks or [
        {"persistent_id": "P1", "name": "One", "artist": "A", "itunes_track_id": 1},
    ]
    with Session() as db:
        st = Station(
            slug=slug,
            name=name,
            template={
                "station": slug, "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        ts = []
        for t in tracks:
            row = Track(
                persistent_id=t["persistent_id"],
                itunes_track_id=t.get("itunes_track_id"),
                name=t["name"],
                artist=t.get("artist"),
            )
            db.add(row)
            ts.append(row)
        db.flush()
        pl = Playlist(station_id=st.id, week=week, trace_id=f"trace-{slug}-{week}")
        db.add(pl)
        db.flush()
        for i, t in enumerate(ts, start=1):
            db.add(PlaylistEntry(
                playlist_id=pl.id, track_id=t.id, position=i, source_name="A",
            ))
        if with_trace:
            db.add(PlaylistVersion(
                playlist_id=pl.id,
                version_no=1,
                trace_id=pl.trace_id,
                station_slug=slug,
                week=week,
                rng="off",
                seed=None,
                generated_at=datetime.now(timezone.utc).isoformat(),
                slots=[{
                    "slot": i, "unit": "Mon", "row": 1, "block": 1,
                    "source": "A", "strategy": "pick", "pool_index": i,
                    "track_id": t.id, "track_name": t.name, "track_artist": t.artist,
                    "reason": "seeded",
                } for i, t in enumerate(ts, start=1)],
            ))
        db.commit()
        return st.id, pl.id


# ---------- POST /api/stations (wizard) ---------------------------------


def test_create_station_happy_path(api):
    client, _ = api
    r = client.post("/api/stations", json=_wizard_payload("alpha", "Alpha FM"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "alpha"
    assert body["name"] == "Alpha FM"
    assert body["template"]["station"] == "alpha"


def test_create_station_conflict_returns_409(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("alpha"))
    r2 = client.post("/api/stations", json=_wizard_payload("alpha"))
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"]


def test_create_station_invalid_template_returns_400(api):
    client, _ = api
    payload = _wizard_payload("bad")
    del payload["template"]["cursor"]  # required field
    r = client.post("/api/stations", json=payload)
    assert r.status_code == 400
    assert "invalid template" in r.json()["detail"]


# ---- §16.2.8 fork endpoint -----------------------------------------------


def test_fork_station_happy_path(api):
    """§16.2.8 — POST /api/stations/{slug}/fork copies template +
    sidecar with a new slug + name; returns the new StationOut."""
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("alpha", "Alpha FM"))

    r = client.post("/api/stations/alpha/fork", json={
        "new_slug": "alpha-fork", "new_name": "Alpha FM (fork)",
    })
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["slug"] == "alpha-fork"
    assert body["name"] == "Alpha FM (fork)"
    # Template content preserved.
    assert body["template"]["station"] == "alpha"
    # Source station still exists (fork doesn't move).
    r2 = client.get("/api/stations/alpha")
    assert r2.status_code == 200
    assert r2.json()["slug"] == "alpha"


def test_fork_station_source_404(api):
    client, _ = api
    r = client.post("/api/stations/missing/fork", json={
        "new_slug": "x", "new_name": "X",
    })
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_fork_station_new_slug_conflict_returns_409(api):
    """If the chosen new_slug is already taken, 409."""
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("alpha", "Alpha FM"))
    client.post("/api/stations", json=_wizard_payload("beta", "Beta FM"))

    r = client.post("/api/stations/alpha/fork", json={
        "new_slug": "beta",  # already exists
        "new_name": "Conflict",
    })
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_fork_station_invalid_new_slug_returns_422(api):
    """Pydantic pattern validation rejects invalid slug shapes (422,
    not 400 — matches the create-station path's Pydantic-level
    rejection at §14.7.t1's slug pattern)."""
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("alpha", "Alpha FM"))
    r = client.post("/api/stations/alpha/fork", json={
        "new_slug": "Invalid Slug",  # space + uppercase
        "new_name": "X",
    })
    assert r.status_code == 422


def test_fork_station_handles_null_sidecar(api):
    """§16.2.8 review R1 — fork should work cleanly when the source
    station has no committed sidecar (Station.sidecar IS NULL — the
    typical un-authored case post-wizard). The conditional in the
    fork route (`copy.deepcopy(src.sidecar) if src.sidecar is not
    None else None`) needs an explicit pin so a regression where it
    drops the None-guard + raises NoneType would surface here.
    """
    client, Session = api
    client.post("/api/stations", json=_wizard_payload("alpha", "Alpha FM"))
    # Confirm source has no sidecar (wizard never sets one).
    from engine.models import Station
    with Session() as db:
        src = db.query(Station).filter(Station.slug == "alpha").first()
        assert src.sidecar is None

    r = client.post("/api/stations/alpha/fork", json={
        "new_slug": "alpha-fork", "new_name": "Alpha FM (fork)",
    })
    assert r.status_code == 201, r.json()

    # New station also has sidecar=None — the deepcopy(None) path
    # didn't materialize an empty dict.
    with Session() as db:
        new = db.query(Station).filter(Station.slug == "alpha-fork").first()
        assert new.sidecar is None


def test_fork_station_deep_copies_template_and_sidecar(api):
    """Mutating the forked station's template / sidecar must not
    perturb the source's JSON blobs (ORM-attribute aliasing pitfall
    on JSON columns)."""
    client, Session = api
    client.post("/api/stations", json=_wizard_payload("alpha", "Alpha FM"))
    # Stamp a sidecar on the source so the deep-copy path covers it.
    from engine.models import Station
    # Build a row_template-compatible sidecar (the wizard payload
    # uses row_template, not flat_slots). 1 row × 1 block = 1 slot
    # → one atomic ref with row_idx=0 + block_idx=0.
    with Session() as db:
        src = db.query(Station).filter(Station.slug == "alpha").first()
        src.sidecar = {
            "schema_version": 1, "sections": [
                {"id": "morning", "name": "Morning", "blocks": [
                    {"kind": "atomic", "row_idx": 0, "block_idx": 0},
                ]},
            ], "layout_order": ["morning"], "macros": [],
        }
        db.commit()

    r = client.post("/api/stations/alpha/fork", json={
        "new_slug": "alpha-fork", "new_name": "Alpha FM (fork)",
    })
    assert r.status_code == 201, r.json()

    # Mutate the FORK's sidecar via the §16.2.5 rename endpoint.
    r2 = client.patch(
        "/api/stations/alpha-fork/sections/morning",
        json={"name": "Sunrise"},
    )
    assert r2.status_code == 200, r2.json()

    # Source station's sidecar.morning.name unchanged — deep-copy
    # isolated the two stations' JSON blobs.
    with Session() as db:
        src = db.query(Station).filter(Station.slug == "alpha").first()
        morning = next(s for s in src.sidecar["sections"] if s["id"] == "morning")
        assert morning["name"] == "Morning"


# ---------- GET /api/stations ------------------------------------------


def test_list_stations_empty(api):
    client, _ = api
    r = client.get("/api/stations")
    assert r.status_code == 200
    assert r.json() == []


def test_list_stations_ordered_by_slug(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("beta"))
    client.post("/api/stations", json=_wizard_payload("alpha"))
    r = client.get("/api/stations")
    assert [s["slug"] for s in r.json()] == ["alpha", "beta"]


# §16.12 — StationSummary aggregates on the list endpoint.


def test_list_stations_summary_fields_for_station_with_no_playlists(api):
    """A freshly-created station reports zero aggregates: count 0,
    null last-generated, but a non-null updated_at + the cadence
    from its template."""
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("fresh", "Fresh FM"))
    r = client.get("/api/stations")
    assert r.status_code == 200
    row = next(s for s in r.json() if s["slug"] == "fresh")
    assert row["cadence"] == "weekly"
    assert row["playlist_count"] == 0
    assert row["last_generated_week"] is None
    assert row["last_generated_at"] is None
    assert row["updated_at"] is not None
    # The heavy template blob is NOT in the list row.
    assert "template" not in row


def test_list_stations_summary_aggregates_playlists(api):
    """count + last-generated reflect seeded playlists. The latest-
    by-TIMESTAMP week wins (argmax), not max(week)."""
    client, Session = api
    # Seed a station with week 1, then a LATER-timestamped regen of
    # week 1 — but ALSO a week 2. We want last_generated_week to be
    # whichever has the max generated_at.
    from datetime import timedelta
    base = datetime.now(timezone.utc)
    with Session() as db:
        st = Station(
            slug="agg", name="Agg FM",
            template={
                "station": "agg", "cadence": "monthly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [], "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        # week 2 generated EARLIER; week 1 regenerated LATER.
        db.add(Playlist(station_id=st.id, week=2, trace_id="t-agg-2",
                        generated_at=base - timedelta(days=2)))
        db.add(Playlist(station_id=st.id, week=1, trace_id="t-agg-1",
                        generated_at=base))
        db.commit()

    row = next(s for s in client.get("/api/stations").json() if s["slug"] == "agg")
    assert row["cadence"] == "monthly"
    assert row["playlist_count"] == 2
    # Latest BY TIMESTAMP is week 1 (regenerated last), NOT max-week 2.
    assert row["last_generated_week"] == 1
    assert row["last_generated_at"] is not None


def test_list_stations_summary_null_timestamp_keeps_week_and_at_consistent(api):
    """Mid-slice review 🟡-1: a station whose only playlist has a NULL
    generated_at must report BOTH last_generated_week AND
    last_generated_at as null — never a half-populated row. SQL
    max() ignores nulls (→ at=null); the ranked-week query now
    filters nulls so it agrees (→ week=null)."""
    client, Session = api
    with Session() as db:
        st = Station(
            slug="nullts", name="Null TS",
            template={
                "station": "nullts", "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [], "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        # Passing generated_at=None to the constructor doesn't stick —
        # the column's Python-side `default=datetime.now` fires at
        # INSERT for unset/None values. Insert normally, then UPDATE
        # the row to NULL to genuinely exercise the null-timestamp path.
        pl = Playlist(station_id=st.id, week=7, trace_id="t-nullts")
        db.add(pl)
        db.flush()
        pl.generated_at = None
        db.commit()

    row = next(s for s in client.get("/api/stations").json() if s["slug"] == "nullts")
    assert row["playlist_count"] == 1  # the playlist still counts
    assert row["last_generated_week"] is None
    assert row["last_generated_at"] is None


def test_list_stations_summary_cadence_fallback_on_missing_key(api):
    """Mid-slice review 🟢-4: a template missing the `cadence` key
    (corrupted / hand-edited row) falls back to "unknown" instead of
    500-ing the whole list."""
    client, Session = api
    with Session() as db:
        db.add(Station(
            slug="nocad", name="No Cadence",
            # template deliberately omits "cadence".
            template={
                "station": "nocad", "rng": "off", "sources": [],
                "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [], "row_start_offsets": [], "row_template": []},
            },
        ))
        db.commit()

    row = next(s for s in client.get("/api/stations").json() if s["slug"] == "nocad")
    assert row["cadence"] == "unknown"


def test_get_station_missing_returns_404(api):
    client, _ = api
    r = client.get("/api/stations/nope")
    assert r.status_code == 404


def test_get_station_happy_path(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("solo", "Solo FM"))
    r = client.get("/api/stations/solo")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "solo"
    assert body["name"] == "Solo FM"
    assert body["template"]["station"] == "solo"


# ---------- §16.19 GET /api/stations/{slug}/overview --------------------


def _overview_station(client, slug="ov", *, persist=False):
    """Create a station via the wizard payload, optionally swapping in a
    cursor.persist template (one sequential block bound to cursor 'C')."""
    payload = _wizard_payload(slug, f"{slug.upper()} FM")
    tmpl = payload["template"]
    if persist:
        tmpl["cursor"]["persist"] = True
        tmpl["layout"]["cursors_init"] = {"C": 0}
        tmpl["layout"]["row_template"] = [
            {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C"}
        ]
    r = client.post("/api/stations", json=payload)
    assert r.status_code == 201, r.json()
    return payload


def test_overview_unknown_station_404(api):
    client, _ = api
    r = client.get("/api/stations/nope/overview")
    assert r.status_code == 404


def test_overview_template_summary_and_pool_gap(api):
    """Template summary fields + the declared-but-not-imported pool gap
    (source A is in the template but no SourcePlaylist row exists)."""
    client, _ = api
    _overview_station(client, "ov1")
    body = client.get("/api/stations/ov1/overview").json()

    ts = body["template_summary"]
    assert ts["slot_count"] == 1            # one pick slot, one Mon row
    assert ts["layout_form"] == "row_template"
    assert ts["strategy_counts"] == {"pick": 1}
    assert ts["cursor_persist"] is False
    assert ts["source_count"] == 1

    # Source A is declared but never imported → imported=False, no count.
    assert len(body["pools"]) == 1
    pool = body["pools"][0]
    assert pool["name"] == "A"
    assert pool["imported"] is False
    assert pool["track_count"] is None


def test_overview_pool_imported_shows_track_count(api):
    """Once a pool is imported, the overview reports its track count +
    imported_at."""
    client, Session = api
    _overview_station(client, "ov2")
    with Session() as db:
        tracks = [
            Track(persistent_id=f"P{i}", name=f"T{i}", artist="X", itunes_track_id=i)
            for i in range(1, 4)
        ]
        db.add_all(tracks)
        db.commit()
        sp = SourcePlaylist(name="A", role="content")
        db.add(sp)
        db.commit()
        db.add_all([
            SourcePlaylistEntry(source_playlist_id=sp.id, position=i, track_id=tracks[i - 1].id)
            for i in range(1, 4)
        ])
        db.commit()
    body = client.get("/api/stations/ov2/overview").json()
    pool = body["pools"][0]
    assert pool["imported"] is True
    assert pool["track_count"] == 3
    assert pool["imported_at"] is not None


def test_overview_cursors_only_for_persist_templates(api):
    """Stateless template → empty cursors. persist=True → cursor rows
    from cursor_state surface."""
    client, Session = api
    # Stateless.
    _overview_station(client, "ov3")
    assert client.get("/api/stations/ov3/overview").json()["cursors"] == []

    # Persistent — seed a cursor_state row.
    _overview_station(client, "ov4", persist=True)
    with Session() as db:
        st = db.query(Station).filter_by(slug="ov4").first()
        db.add(CursorState(station_id=st.id, name="C", position=42))
        db.commit()
    cursors = client.get("/api/stations/ov4/overview").json()["cursors"]
    assert cursors == [{"name": "C", "position": 42}]


def test_overview_generations_rollup_and_staleness(api):
    """count + last_generated + stale_count (playlists generated BEFORE
    the last template edit)."""
    client, Session = api
    _overview_station(client, "ov5")
    with Session() as db:
        st = db.query(Station).filter_by(slug="ov5").first()
        # Two playlists generated in the PAST (before any edit).
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db.add_all([
            Playlist(station_id=st.id, week=1, trace_id="t1", generated_at=old),
            Playlist(station_id=st.id, week=2, trace_id="t2", generated_at=old),
        ])
        # Bump updated_at to NOW (simulates a template edit after those
        # playlists were generated) by touching name.
        st.name = "Edited"
        db.commit()

    gen = client.get("/api/stations/ov5/overview").json()["generations"]
    assert gen["count"] == 2
    assert gen["last_generated_week"] == 2  # tie on timestamp → higher week
    assert gen["last_generated_at"] is not None
    # Both pre-date the post-edit updated_at → both stale.
    assert gen["stale_count"] == 2


# ---------- GET /api/stations/{slug}/playlists --------------------------


def test_station_playlists_unknown_slug_404(api):
    client, _ = api
    r = client.get("/api/stations/nope/playlists")
    assert r.status_code == 404


def test_station_playlists_empty_for_new_station(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("empty"))
    r = client.get("/api/stations/empty/playlists")
    assert r.status_code == 200
    assert r.json() == []


def test_station_playlists_ordered_by_week(api):
    client, Session = api
    # Seed three playlists at weeks 3, 1, 2 — should come back in 1, 2, 3 order.
    with Session() as db:
        st = Station(
            slug="ord", name="Ord",
            template={
                "station": "ord", "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "day", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        for w in (3, 1, 2):
            db.add(Playlist(station_id=st.id, week=w, trace_id=f"t-{w}"))
        db.commit()

    r = client.get("/api/stations/ord/playlists")
    assert r.status_code == 200
    weeks = [row["week"] for row in r.json()]
    assert weeks == [1, 2, 3]


def test_station_playlists_entry_count(api):
    client, Session = api
    _seed_station_with_playlist(Session, slug="cnt", tracks=[
        {"persistent_id": "P1", "name": "A", "itunes_track_id": 1},
        {"persistent_id": "P2", "name": "B", "itunes_track_id": 2},
        {"persistent_id": "P3", "name": "C", "itunes_track_id": 3},
    ])
    r = client.get("/api/stations/cnt/playlists")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["entry_count"] == 3


# ---------- GET /api/playlists/{id} + /trace ---------------------------


def test_get_playlist_missing_404(api):
    client, _ = api
    r = client.get("/api/playlists/999")
    assert r.status_code == 404


def test_get_trace_missing_404(api):
    client, _ = api
    r = client.get("/api/playlists/999/trace")
    assert r.status_code == 404


def test_get_playlist_and_trace_for_seeded(api):
    client, Session = api
    _, pid = _seed_station_with_playlist(Session, slug="cls", week=1)

    p = client.get(f"/api/playlists/{pid}").json()
    assert p["station"] == "cls"
    assert p["week"] == 1
    assert len(p["entries"]) == 1

    t = client.get(f"/api/playlists/{pid}/trace").json()
    assert t["station"] == "cls"
    assert t["rng"] == "off"
    assert len(t["slots"]) == 1


# ---------- §16.4 trace versioning -------------------------------------


def test_get_trace_returns_latest_version_by_default(api):
    """§16.4 — `GET /trace` (no ?v) returns the CURRENT version
    (max version_no) for that playlist."""
    client, Session = api
    _seed_station_and_pool(Session, slug="ver", pool_tracks=3)
    client.post("/api/stations/ver/generate", json={"week": 1})
    r2 = client.post("/api/stations/ver/generate", json={"week": 1})
    expected_trace_id = r2.json()["trace_id"]
    pl_id = r2.json()["playlist_id"]

    body = client.get(f"/api/playlists/{pl_id}/trace").json()
    # Default selects max(version_no) → second generation.
    assert body["trace_id"] == expected_trace_id


def test_get_trace_v_param_selects_specific_version(api):
    """§16.4 — `?v=N` selects version N explicitly."""
    client, Session = api
    _seed_station_and_pool(Session, slug="vselect", pool_tracks=3)
    r1 = client.post("/api/stations/vselect/generate", json={"week": 1})
    v1_trace = r1.json()["trace_id"]
    r2 = client.post("/api/stations/vselect/generate", json={"week": 1})
    v2_trace = r2.json()["trace_id"]
    pl_id = r2.json()["playlist_id"]

    # v=1 returns the original trace.
    body = client.get(f"/api/playlists/{pl_id}/trace?v=1").json()
    assert body["trace_id"] == v1_trace
    # v=2 returns the regenerated trace.
    body = client.get(f"/api/playlists/{pl_id}/trace?v=2").json()
    assert body["trace_id"] == v2_trace


def test_get_trace_v_param_out_of_range_404_lists_available(api):
    """§16.4 — `?v=N` with an unknown N returns 404 + names the
    available range so the operator can correct the URL."""
    client, Session = api
    _seed_station_and_pool(Session, slug="vrange", pool_tracks=3)
    r = client.post("/api/stations/vrange/generate", json={"week": 1})
    pl_id = r.json()["playlist_id"]

    resp = client.get(f"/api/playlists/{pl_id}/trace?v=99")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "version 99 not found" in detail
    # Available range surfaced.
    assert "1..1" in detail


def test_get_versions_returns_oldest_first_without_slots(api):
    """§16.4 — `/versions` lists every version in order; slots JSON
    is INTENTIONALLY OMITTED from the listing payload."""
    client, Session = api
    _seed_station_and_pool(Session, slug="vlist", pool_tracks=3)
    r1 = client.post("/api/stations/vlist/generate", json={"week": 1})
    t1 = r1.json()["trace_id"]
    r2 = client.post("/api/stations/vlist/generate", json={"week": 1})
    t2 = r2.json()["trace_id"]
    pl_id = r2.json()["playlist_id"]

    body = client.get(f"/api/playlists/{pl_id}/versions").json()
    assert body["playlist_id"] == pl_id
    assert body["current_v"] == 2
    versions = body["versions"]
    assert len(versions) == 2
    assert [v["version_no"] for v in versions] == [1, 2]
    assert [v["trace_id"] for v in versions] == [t1, t2]
    # No `slots` field on per-version rows (palette would be too heavy).
    for v in versions:
        assert "slots" not in v


def test_get_versions_unknown_playlist_404(api):
    client, _ = api
    r = client.get("/api/playlists/9999/versions")
    assert r.status_code == 404
    assert "playlist 9999 not found" in r.json()["detail"]


# ---------- §16.4.t1 trace diff endpoint -------------------------------


def test_diff_returns_per_slot_changes_with_full_summary(api):
    """§16.4.t1 — `GET /diff?from=N&to=M` returns every slot with
    both sides + a `changed` flag. Summary carries total / changed /
    unchanged counts so the picker can render \"3 of N changed\"."""
    client, Session = api
    _seed_station_and_pool(Session, slug="diff", pool_tracks=3)
    r1 = client.post("/api/stations/diff/generate", json={"week": 1})
    r2 = client.post("/api/stations/diff/generate", json={"week": 1})
    pl_id = r2.json()["playlist_id"]

    body = client.get(f"/api/playlists/{pl_id}/diff?from=1&to=2").json()
    assert body["playlist_id"] == pl_id
    assert body["from"]["version_no"] == 1
    assert body["to"]["version_no"] == 2
    assert body["from"]["trace_id"] == r1.json()["trace_id"]
    assert body["to"]["trace_id"] == r2.json()["trace_id"]
    # Summary count tallies.
    s = body["summary"]
    assert s["total_slots"] == len(body["slots"])
    assert s["changed"] + s["unchanged"] == s["total_slots"]
    # Each slot has both sides + the changed flag.
    for row in body["slots"]:
        assert "from" in row
        assert "to" in row
        assert isinstance(row["changed"], bool)
        # Positional fields present (the row key).
        assert "slot" in row


def test_diff_predicate_detects_track_id_change(api):
    """A regen producing a different track_id at the same slot flips
    `changed: true` for that row."""
    client, Session = api
    # Default fixture: rng=off + identical 3-track pool → both
    # generations pick the same track. To force a track_id change,
    # we'd need rng=on with different seeds. Easier path: assert at
    # least one of the two-generation diff matches the EXPECTED
    # invariant — every row's `changed` matches whether from.track_id
    # != to.track_id. That's exactly the predicate's job; the test
    # pins it directly via fixture inspection.
    _seed_station_and_pool(Session, slug="pred", pool_tracks=3)
    client.post("/api/stations/pred/generate", json={"week": 1})
    r2 = client.post("/api/stations/pred/generate", json={"week": 1})
    pl_id = r2.json()["playlist_id"]
    body = client.get(f"/api/playlists/{pl_id}/diff?from=1&to=2").json()

    for row in body["slots"]:
        # Hand-check the predicate: changed iff any of the compared
        # fields differ. Source + strategy stay constant on the
        # same-template path → only track_id / pool_index can flip.
        from_track = row["from"]["track_id"] if row["from"] else None
        to_track = row["to"]["track_id"] if row["to"] else None
        from_pool = row["from"]["pool_index"] if row["from"] else None
        to_pool = row["to"]["pool_index"] if row["to"] else None
        expected_changed = (
            from_track != to_track or from_pool != to_pool
        )
        assert row["changed"] == expected_changed, (
            f"slot {row['slot']}: predicate mismatch"
        )


def test_diff_unknown_playlist_404(api):
    client, _ = api
    r = client.get("/api/playlists/9999/diff?from=1&to=2")
    assert r.status_code == 404
    assert "playlist 9999 not found" in r.json()["detail"]


def test_diff_unknown_version_404_lists_available_range(api):
    client, Session = api
    _seed_station_and_pool(Session, slug="rng", pool_tracks=3)
    r = client.post("/api/stations/rng/generate", json={"week": 1})
    pl_id = r.json()["playlist_id"]

    resp = client.get(f"/api/playlists/{pl_id}/diff?from=1&to=99")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "version 99" in detail and "to" in detail
    assert "1..1" in detail


def test_diff_same_version_400(api):
    """from == to is a degenerate diff (all-unchanged); the operator
    probably meant /trace?v=N. 400 nudges them to the right path."""
    client, Session = api
    _seed_station_and_pool(Session, slug="same", pool_tracks=3)
    r = client.post("/api/stations/same/generate", json={"week": 1})
    pl_id = r.json()["playlist_id"]

    resp = client.get(f"/api/playlists/{pl_id}/diff?from=1&to=1")
    assert resp.status_code == 400
    assert "same version" in resp.json()["detail"]


def test_diff_handles_slot_count_mismatch_across_versions(api):
    """Defensive case: the operator edited the template between
    regens, so v1 + v2 have different slot counts. The endpoint
    must walk both slot lists by slot-number (not zip-by-index)
    and mark mismatched slots as changed, with `null` on the
    missing side."""
    client, Session = api
    _seed_station_and_pool(Session, slug="shape", pool_tracks=3)
    r1 = client.post("/api/stations/shape/generate", json={"week": 1})
    r2 = client.post("/api/stations/shape/generate", json={"week": 1})
    pl_id = r2.json()["playlist_id"]

    # Hand-mutate version 2's slots to drop slot 1 (simulates a
    # template that shrunk between regens). Routes through the ORM
    # so the JSON column write is consistent with the model's
    # SQLAlchemy bindings.
    with Session() as db:
        v2 = (
            db.query(PlaylistVersion)
            .filter_by(playlist_id=pl_id, version_no=2)
            .one()
        )
        # Trace slot dicts carry `slot: int` (1-indexed). Drop the
        # one with slot=1.
        v2.slots = [s for s in v2.slots if s.get("slot") != 1]
        db.commit()

    body = client.get(f"/api/playlists/{pl_id}/diff?from=1&to=2").json()
    # The slot=1 row should be present with `to: null` and changed=true.
    row1 = next(r for r in body["slots"] if r["slot"] == 1)
    assert row1["changed"] is True
    assert row1["from"] is not None  # v1 has it
    assert row1["to"] is None  # v2 dropped it
    # Summary still tallies: changed includes this missing-slot case.
    s = body["summary"]
    assert s["changed"] >= 1
    assert s["total_slots"] == len(body["slots"])


def test_diff_missing_required_query_params_422(api):
    """`from` and `to` are required query params; FastAPI's
    validation returns 422 when either is missing (no hand-rolled
    message needed)."""
    client, Session = api
    _seed_station_and_pool(Session, slug="missing", pool_tracks=3)
    r = client.post("/api/stations/missing/generate", json={"week": 1})
    pl_id = r.json()["playlist_id"]

    resp = client.get(f"/api/playlists/{pl_id}/diff?from=1")
    assert resp.status_code == 422


def test_save_playlist_reuses_playlist_id_across_regenerations(api):
    """§16.4 — the Playlist row is REUSED across regenerations
    (same primary key). External references that store `playlist_id`
    (MixUsage, PlaylistEntry FK, future audit tables) stay valid."""
    client, Session = api
    _seed_station_and_pool(Session, slug="reuse", pool_tracks=3)
    r1 = client.post("/api/stations/reuse/generate", json={"week": 1})
    first_id = r1.json()["playlist_id"]
    r2 = client.post("/api/stations/reuse/generate", json={"week": 1})
    second_id = r2.json()["playlist_id"]
    assert first_id == second_id


def test_save_playlist_clears_entries_on_regenerate(api):
    """§16.4 — PlaylistEntry stays latest-only; the regenerate
    rewrites entries from scratch so the playable order matches the
    current version. Historical entries live in PlaylistVersion.slots.

    The default template generates 1 slot; what matters is that the
    entry count after a regen equals the entry count after the FIRST
    generation — never doubled."""
    client, Session = api
    _seed_station_and_pool(Session, slug="entries", pool_tracks=3)
    r1 = client.post("/api/stations/entries/generate", json={"week": 1})
    first_slot_count = r1.json()["slot_count"]
    client.post("/api/stations/entries/generate", json={"week": 1})
    r3 = client.post("/api/stations/entries/generate", json={"week": 1})
    pl_id = r3.json()["playlist_id"]

    with Session() as db:
        entries = db.query(PlaylistEntry).filter_by(playlist_id=pl_id).all()
        # Same row count after N regens — NOT N × slot_count.
        assert len(entries) == first_slot_count
        # Three versions persisted (history grows; entries don't).
        versions = db.query(PlaylistVersion).filter_by(playlist_id=pl_id).all()
        assert len(versions) == 3


# ---------- GET /api/playlists/{id}/export -----------------------------


def test_export_xml_returns_bytes_and_disposition(api):
    client, Session = api
    _, pid = _seed_station_with_playlist(Session, slug="cls", week=1)
    r = client.get(f"/api/playlists/{pid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "filename=\"cls-001.xml\"" in r.headers["content-disposition"]
    assert b"<?xml" in r.content
    assert b"//Apple Computer//DTD PLIST 1.0//EN" in r.content


def test_export_missing_playlist_404(api):
    client, _ = api
    r = client.get("/api/playlists/999/export")
    assert r.status_code == 404


# ---------- POST /api/playlists/{id}/export-to-disk --------------------


def test_export_to_disk_writes_file_and_returns_summary(api, tmp_path):
    client, Session = api
    # Point the disk exporter at an isolated directory via Config.
    with Session() as db:
        db.add(Config(key="playlist_output_dir", value=str(tmp_path / "out")))
        db.commit()
    _, pid = _seed_station_with_playlist(Session, slug="cls", week=4)

    r = client.post(f"/api/playlists/{pid}/export-to-disk")
    assert r.status_code == 200
    body = r.json()
    assert body["path"].endswith("/out/cls/004.xml")
    assert body["bytes"] > 0
    assert Path(body["path"]).exists()


def test_export_to_disk_missing_playlist_404(api):
    client, _ = api
    r = client.post("/api/playlists/999/export-to-disk")
    assert r.status_code == 404


# ---------- PUT /api/stations/{slug} -----------------------------------


def test_update_station_slug_change_returns_400(api):
    """Slug is immutable — attempting to rename via PUT must be rejected."""
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("original", "Original"))
    r = client.put("/api/stations/original", json=_wizard_payload("renamed", "Renamed"))
    assert r.status_code == 400
    assert "slug is immutable" in r.json()["detail"]

    # The station must be unchanged.
    r2 = client.get("/api/stations/original")
    assert r2.status_code == 200
    assert r2.json()["name"] == "Original"


def test_update_station_unknown_slug_404(api):
    client, _ = api
    r = client.put("/api/stations/nope", json=_wizard_payload("nope", "Nope"))
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_update_station_happy_path(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("upd", "Old Name"))

    new_payload = _wizard_payload("upd", "New Name")
    # Tweak the template too so we can prove it was actually replaced.
    new_payload["template"]["cursor"]["base_advance_per_week"] = 7
    r = client.put("/api/stations/upd", json=new_payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "upd"
    assert body["name"] == "New Name"
    assert body["template"]["cursor"]["base_advance_per_week"] == 7

    # And the change is persisted — a fresh GET sees it.
    r2 = client.get("/api/stations/upd")
    assert r2.json()["name"] == "New Name"


def test_update_station_invalid_template_400(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("upd2", "Before"))
    bad = _wizard_payload("upd2", "After")
    del bad["template"]["cursor"]  # required field
    r = client.put("/api/stations/upd2", json=bad)
    assert r.status_code == 400
    assert "invalid template" in r.json()["detail"]

    # The bad update must not have leaked through.
    r2 = client.get("/api/stations/upd2")
    assert r2.json()["name"] == "Before"


# ---------- POST /api/stations/{slug}/generate ------------------------


def _seed_station_and_pool(
    Session: sessionmaker,
    *,
    slug: str = "gen",
    name: str = "Gen FM",
    pool_name: str = "A",
    pool_tracks: int = 3,
    template: dict | None = None,
) -> tuple[int, int]:
    """Insert a Station + a SourcePlaylist named `pool_name` with N tracks.

    The default template has one row/unit and strategy=pick size=1, so a
    single slot is generated — just enough to prove the wiring.

    Returns (station_id, source_playlist_id).
    """
    template = template or {
        "station": slug,
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": pool_name, "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": pool_name, "strategy": "pick", "size": 1}],
        },
    }
    with Session() as db:
        st = Station(slug=slug, name=name, template=template)
        db.add(st)
        db.flush()
        sp = SourcePlaylist(name=pool_name)
        db.add(sp)
        db.flush()
        for i in range(1, pool_tracks + 1):
            tr = Track(
                persistent_id=f"{slug}-P{i}",
                itunes_track_id=1000 + i,
                name=f"Track {i}",
                artist="Artist",
            )
            db.add(tr)
            db.flush()
            db.add(SourcePlaylistEntry(
                source_playlist_id=sp.id, track_id=tr.id, position=i,
            ))
        db.commit()
        return st.id, sp.id


def test_generate_happy_path_writes_playlist_entries_and_trace(api):
    client, Session = api
    _seed_station_and_pool(Session, slug="gen", pool_tracks=3)
    r = client.post("/api/stations/gen/generate", json={"week": 1})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["station"] == "gen"
    assert body["week"] == 1
    assert body["slot_count"] == 1
    assert body["playlist_id"]
    assert body["trace_id"]

    # The playlist, entries, and trace should all be persisted.
    with Session() as db:
        pl = db.query(Playlist).filter_by(id=body["playlist_id"]).first()
        assert pl is not None
        assert pl.week == 1
        assert pl.trace_id == body["trace_id"]
        entries = db.query(PlaylistEntry).filter_by(playlist_id=pl.id).all()
        assert len(entries) == 1
        assert entries[0].source_name == "A"
        tr = db.query(PlaylistVersion).filter_by(playlist_id=pl.id).first()
        assert tr is not None
        assert tr.rng == "off"
        assert tr.version_no == 1  # §16.4: first generation
        assert len(tr.slots) == 1


def test_generate_unknown_station_404(api):
    client, _ = api
    r = client.post("/api/stations/nope/generate", json={"week": 1})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_generate_missing_source_pool_400(api):
    client, Session = api
    # Seed a station but no source pool.
    with Session() as db:
        db.add(Station(
            slug="sp", name="No Pool",
            template={
                "station": "sp", "cadence": "weekly", "rng": "off",
                "sources": [{"name": "A", "colour": "#111", "role": "content"}],
                "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {
                    "unit": "day", "unit_labels": ["Mon"],
                    "row_start_offsets": [0],
                    "row_template": [{"source": "A", "strategy": "pick", "size": 1}],
                },
            },
        ))
        db.commit()
    r = client.post("/api/stations/sp/generate", json={"week": 1})
    assert r.status_code == 400
    assert "source pool 'A' not imported" in r.json()["detail"]


def test_generate_with_corrupt_stored_template_500(api):
    client, Session = api
    # Seed a Station whose template dict is missing a required top-level key.
    with Session() as db:
        db.add(Station(
            slug="corrupt", name="Corrupt",
            template={"station": "corrupt"},  # missing cadence/sources/cursor/layout
        ))
        db.commit()
    r = client.post("/api/stations/corrupt/generate", json={"week": 1})
    assert r.status_code == 500
    assert "stored template is invalid" in r.json()["detail"]


def test_generate_strategy_failure_returns_400(api):
    client, Session = api
    # Template asks for window size=5 from pool A; we only provide 2 tracks.
    template = {
        "station": "toosmall",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "window", "size": 5}],
        },
    }
    _seed_station_and_pool(
        Session, slug="toosmall", name="Too Small",
        pool_tracks=2, template=template,
    )
    r = client.post("/api/stations/toosmall/generate", json={"week": 1})
    # Generator either succeeds with a short window or raises — either way,
    # if it raises the route must surface it as 400.
    if r.status_code != 200:
        assert r.status_code == 400
        assert "generation failed" in r.json()["detail"]


def test_generate_same_week_appends_version(api):
    """§16.4 — regenerating the same (station, week) APPENDS a new
    PlaylistVersion instead of replacing the prior trace. The
    Playlist row itself is REUSED (same id); `Playlist.trace_id`
    flips to the new version's id as a denormalized pointer.
    Pre-§16.4 this test was named `_replaces_previous`."""
    client, Session = api
    _seed_station_and_pool(Session, slug="twice", pool_tracks=3)
    r1 = client.post("/api/stations/twice/generate", json={"week": 1})
    assert r1.status_code == 201
    trace1 = r1.json()["trace_id"]
    with Session() as db:
        pl = db.query(Playlist).filter_by(week=1).one()
        first_pl_id = pl.id

    r2 = client.post("/api/stations/twice/generate", json={"week": 1})
    assert r2.status_code == 201
    trace2 = r2.json()["trace_id"]
    # Each generation mints a fresh trace_id.
    assert trace1 != trace2

    with Session() as db:
        # Still one Playlist row per week.
        pls = db.query(Playlist).filter_by(week=1).all()
        assert len(pls) == 1
        pl = pls[0]
        # Row REUSED — same id as before (pre-§16.4 the row was
        # deleted + recreated, breaking external pointers).
        assert pl.id == first_pl_id
        # Denormalized pointer flipped to the new version.
        assert pl.trace_id == trace2
        # Both versions persisted; version_no monotonically increases.
        versions = (
            db.query(PlaylistVersion)
            .filter_by(playlist_id=pl.id)
            .order_by(PlaylistVersion.version_no)
            .all()
        )
        assert [v.version_no for v in versions] == [1, 2]
        assert [v.trace_id for v in versions] == [trace1, trace2]


def test_generate_seed_is_threaded_through_to_trace(api):
    client, Session = api
    # RNG must be 'on' for seed to be honoured.
    template = {
        "station": "seeded",
        "cadence": "weekly",
        "rng": "on",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "pick", "size": 1}],
        },
    }
    _seed_station_and_pool(
        Session, slug="seeded", name="Seeded",
        pool_tracks=5, template=template,
    )
    r = client.post("/api/stations/seeded/generate",
                    json={"week": 1, "seed": 42})
    assert r.status_code == 201, r.text
    trace = client.get(f"/api/playlists/{r.json()['playlist_id']}/trace").json()
    assert trace["rng"] == "on"
    assert trace["seed"] == 42


# ---------- /api/sources -----------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


def test_list_sources_empty(api):
    client, _ = api
    r = client.get("/api/sources")
    assert r.status_code == 200
    assert r.json() == []


def test_list_sources_after_import(api):
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "tiny",
    })
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["name"] == "tiny"
    assert summary["role"] == "content"  # §14.35 default
    assert summary["tracks_imported"] == 3
    assert summary["entries_imported"] == 3

    r2 = client.get("/api/sources")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "tiny"
    assert rows[0]["track_count"] == 3
    assert rows[0]["role"] == "content"  # §14.35 default surfaces on GET


def test_import_with_role_reference_persists_via_api(api):
    """§14.35: API accepts role='reference' and the GET surfaces it."""
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "tiny-ref",
        "role": "reference",
    })
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "reference"
    rows = client.get("/api/sources").json()
    assert rows[0]["role"] == "reference"


def test_import_with_invalid_role_returns_422(api):
    """Pydantic's Literal type rejects invalid roles at the validation
    layer (HTTP 422). The engine's runtime check in import_source is
    defense-in-depth for non-API callers (e.g. the manifest CLI in
    commit 2). Mirrors §14.32/§14.34's "strict allowlist at the
    earliest layer" pattern."""
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "tiny",
        "role": "banana",
    })
    assert r.status_code == 422
    # Pydantic's error body identifies the bad field + the allowed values.
    body = r.json()
    assert any("role" in str(err.get("loc", [])) for err in body["detail"])


def test_reimport_via_api_can_change_role(api):
    """POST role=content, then POST role=reference → GET reflects the
    new role. Mirrors the engine-layer test_reimport_can_change_role
    through the API surface."""
    client, _ = api
    r1 = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "swap", "role": "content",
    })
    assert r1.status_code == 200, r1.text
    assert r1.json()["role"] == "content"

    r2 = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "swap", "role": "reference",
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["role"] == "reference"

    rows = client.get("/api/sources").json()
    swap = next(r for r in rows if r["name"] == "swap")
    assert swap["role"] == "reference"


def test_import_path_not_found_400(api):
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": "/definitely/does/not/exist.xml",
        "name": "nope",
    })
    assert r.status_code == 400
    assert "path not found" in r.json()["detail"]


def test_import_malformed_xml_500(api):
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "malformed-library.xml"),
        "name": "broken",
    })
    assert r.status_code == 500
    assert "import failed" in r.json()["detail"]


# ---------- app plumbing -----------------------------------------------


def test_root_endpoint_identifies_the_service(monkeypatch):
    """GET / returns either:
      * the tiny service-identity blob — when no static frontend is built
        yet (`<repo>/web/build/` is absent, CI's typical state); or
      * `index.html` from the SvelteKit SPA — when a build IS present
        (a dev who ran `npm run build` locally before pytest).

    Both are correct §16.13 c2 behaviour; the test accepts either so
    `npm run build` and `pytest` don't clobber each other in dev. The
    SPA-mount-with-fake-dir test in `test_spa_mount.py` covers the
    static path comprehensively."""
    from api.main import STATIC_DIR

    monkeypatch.setenv("STATION_STUDIO_SKIP_AUTO_SEED", "1")
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    if STATIC_DIR is None:
        # Dev / CI without a frontend build → service-identity blob.
        assert r.json() == {"service": "station-studio", "version": "0.1.0"}
    else:
        # A frontend build was found at module-load time → SPA index.
        assert r.headers["content-type"].startswith("text/html")
        assert "<html" in r.text.lower()


# ---------- §sweep-1 auto-seed lifespan handler -----------------------


def test_lifespan_auto_derives_missing_sidecars(tmp_path, monkeypatch):
    """§16.14.t1 c2-review 🟡-1: api/main.py lifespan must call
    `auto_derive_missing_sidecars` so `make dev` (direct uvicorn,
    bypasses bootstrap) gets the same auto-derive behaviour the CLI
    path does via bootstrap. Pin the symmetry so a future refactor
    can't silently regress the dev-mode workflow.

    Approach: point the stations dir at a tmp_path with ONE station
    JSON and no sidecar; boot the app; confirm the sidecar appears.
    """
    import json
    import shutil

    db_url = f"sqlite:///{tmp_path}/derive.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)

    # Build a tmp stations dir with starter.json (which has a
    # well-formed template the derive helper handles) but NO sidecar.
    stations_dir = tmp_path / "stations"
    stations_dir.mkdir()
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(stations_dir))
    repo_root = Path(__file__).resolve().parent.parent
    shutil.copy(repo_root / "stations" / "starter.json", stations_dir)
    # Sanity: no sidecar yet.
    sidecar = stations_dir / "starter.editor.json"
    assert not sidecar.exists()

    with TestClient(app) as _client:
        pass  # lifespan fires on context entry/exit

    # The lifespan derived the missing sidecar.
    assert sidecar.exists(), (
        "api/main.py lifespan did not auto-derive the missing "
        "starter sidecar. The `make dev` path now lies about the "
        "stations/README.md 'how to add jingles' recipe — see "
        "§16.14.t1 c2-review 🟡-1."
    )
    # And the derived content parses as valid JSON (basic smoke).
    json.loads(sidecar.read_text())


def test_lifespan_auto_seeds_when_station_table_is_empty(tmp_path, monkeypatch):
    """README's "Quick start" promises Rebirth Classics + Rebirth Radio
    appear after `make install && make dev`. The lifespan handler
    seeds `stations/*.json` on first boot when the Station table is
    empty.

    This test verifies the seed actually fires + creates the expected
    rows. Uses the real stations/ dir so it tests the production path.
    """
    db_url = f"sqlite:///{tmp_path}/lifespan.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)

    # Confirm empty DB pre-boot.
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    Session = make_sessionmaker(engine)
    with Session() as db:
        assert db.query(Station).count() == 0

    # Boot the app → lifespan handler fires.
    with TestClient(app) as client:
        # Bootstrap done. Confirm seeded rows.
        with Session() as db:
            slugs = {s.slug for s in db.query(Station).all()}
            # At least the two example stations from stations/*.json.
            assert "rebirth-classics" in slugs, slugs
            assert "rebirth-radio" in slugs, slugs


def test_lifespan_does_not_re_seed_when_station_table_is_populated(
    tmp_path, monkeypatch,
):
    """Idempotency: re-running with existing stations doesn't clobber
    them. Specifically: a station with a hand-edited name keeps its
    edits across the second boot."""
    db_url = f"sqlite:///{tmp_path}/lifespan.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)

    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    Session = make_sessionmaker(engine)

    # First boot — seeds happens.
    with TestClient(app):
        pass

    # Hand-edit a station's name to verify the second boot doesn't
    # overwrite it.
    with Session() as db:
        s = db.query(Station).filter_by(slug="rebirth-classics").one()
        s.name = "MY CUSTOM EDIT"
        db.commit()

    # Second boot — should be a no-op.
    with TestClient(app):
        pass

    with Session() as db:
        s = db.query(Station).filter_by(slug="rebirth-classics").one()
        assert s.name == "MY CUSTOM EDIT", (
            "lifespan re-seeded over the user's edit"
        )


def test_lifespan_swallows_seed_errors_so_app_still_boots(
    tmp_path, monkeypatch, caplog,
):
    """If `stations/*.json` is broken, the lifespan logs + continues —
    the API still comes up so the user can investigate. Set the
    stations dir to an empty tmp path to trigger the "no stations
    found" branch."""
    db_url = f"sqlite:///{tmp_path}/lifespan.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    # §16.13.t9 / t9-review 🟢-1: the canonical way to point seeding
    # at an empty dir is the `STATION_STUDIO_STATIONS_DIR` env var
    # (engine.seeding._resolve_stations_dir reads it at call time).
    # Pre-fix this test monkey-patched the SHIM's `STATIONS_DIR`
    # constant — which `_collect_paths` never reads, so the patch
    # was a no-op. The assertion below is loose enough that the
    # test still passed, but it wasn't exercising the empty-dir
    # path it claimed to. Now: env-var override that actually drives
    # the resolve helper.
    empty_dir = tmp_path / "no_stations"
    empty_dir.mkdir()
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(empty_dir))

    with caplog.at_level("INFO"):
        with TestClient(app) as client:
            r = client.get("/")
        assert r.status_code == 200
    # Either "no stations found" (default path) or "auto-seed failed"
    # (error path) — both are acceptable; what matters is the app
    # served the root endpoint.


# ---------- DELETE /api/playlists/{id} --------------------------------


def test_delete_playlist_happy_path(api):
    client, Session = api
    _, pid = _seed_station_with_playlist(Session, slug="del", week=1)

    r = client.delete(f"/api/playlists/{pid}")
    assert r.status_code == 204
    assert r.content == b""

    # Subsequent GET returns 404.
    assert client.get(f"/api/playlists/{pid}").status_code == 404


def test_delete_playlist_missing_404(api):
    client, _ = api
    r = client.delete("/api/playlists/999")
    assert r.status_code == 404


def test_delete_playlist_cascades_entries_and_trace(api):
    client, Session = api
    _, pid = _seed_station_with_playlist(
        Session, slug="casc", week=1,
        tracks=[
            {"persistent_id": "P1", "name": "One", "artist": "A", "itunes_track_id": 1},
            {"persistent_id": "P2", "name": "Two", "artist": "A", "itunes_track_id": 2},
        ],
        with_trace=True,
    )

    r = client.delete(f"/api/playlists/{pid}")
    assert r.status_code == 204

    with Session() as db:
        assert db.query(PlaylistEntry).filter_by(playlist_id=pid).count() == 0
        assert db.query(PlaylistVersion).filter_by(playlist_id=pid).first() is None


def test_delete_playlist_leaves_station_intact(api):
    client, Session = api
    _, pid = _seed_station_with_playlist(Session, slug="keep", week=1)

    client.delete(f"/api/playlists/{pid}")

    assert client.get("/api/stations/keep").status_code == 200


# ---------- DELETE /api/stations/{slug} --------------------------------


def test_delete_station_happy_path(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("nuke", "Nuke FM"))

    r = client.delete("/api/stations/nuke")
    assert r.status_code == 204
    assert r.content == b""

    assert client.get("/api/stations/nuke").status_code == 404


def test_delete_station_missing_404(api):
    client, _ = api
    r = client.delete("/api/stations/ghost")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_delete_station_cascades_playlists_entries_traces(api):
    client, Session = api
    # Two playlists under the same station.
    sid, pid1 = _seed_station_with_playlist(Session, slug="full", week=1, with_trace=True)
    with Session() as db:
        st = db.query(Station).filter_by(id=sid).first()
        pl2 = Playlist(station_id=st.id, week=2, trace_id="trace-full-2")
        db.add(pl2)
        db.flush()
        db.add(PlaylistVersion(
            playlist_id=pl2.id,
            version_no=1,
            trace_id="trace-full-2",
            station_slug="full", week=2, rng="off", seed=None,
            generated_at=datetime.now(timezone.utc).isoformat(),
            slots=[],
        ))
        db.commit()
        pid2 = pl2.id

    r = client.delete("/api/stations/full")
    assert r.status_code == 204

    with Session() as db:
        assert db.query(Playlist).filter(Playlist.id.in_([pid1, pid2])).count() == 0
        assert db.query(PlaylistEntry).filter_by(playlist_id=pid1).count() == 0
        assert db.query(PlaylistVersion).filter(
            PlaylistVersion.playlist_id.in_([pid1, pid2])
        ).count() == 0


def test_delete_station_with_no_playlists(api):
    client, _ = api
    client.post("/api/stations", json=_wizard_payload("empty-nuke", "Empty Nuke"))
    r = client.delete("/api/stations/empty-nuke")
    assert r.status_code == 204


# ---------- end of delete tests ----------------------------------------


# ---------- DELETE /api/sources/{id} -----------------------------------


def test_delete_source_happy_path(api):
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "to-delete",
    })
    assert r.status_code == 200
    source_id = client.get("/api/sources").json()[0]["id"]

    r = client.delete(f"/api/sources/{source_id}")
    assert r.status_code == 204
    assert r.content == b""
    assert client.get("/api/sources").json() == []


def test_delete_source_missing_404(api):
    client, _ = api
    r = client.delete("/api/sources/9999")
    assert r.status_code == 404


# ---------- StationCreate slug validation ------------------------------


def test_create_station_invalid_slug_returns_422(api):
    """Slug must match ^[a-z0-9][a-z0-9-]*$ — Pydantic rejects it before the handler."""
    client, _ = api
    for bad_slug in ("", "Has-Uppercase", "-starts-with-dash", "has spaces"):
        payload = _wizard_payload("placeholder", "X")
        payload["slug"] = bad_slug
        r = client.post("/api/stations", json=payload)
        assert r.status_code == 422, f"expected 422 for slug={bad_slug!r}, got {r.status_code}"


# ---------- import path validation ------------------------------------


def test_import_path_traversal_blocked_by_env(api, monkeypatch, tmp_path):
    """When STATION_STUDIO_IMPORT_BASE_DIR is set, paths outside it are rejected."""
    client, _ = api
    monkeypatch.setenv("STATION_STUDIO_IMPORT_BASE_DIR", str(tmp_path / "allowed"))
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "blocked",
    })
    assert r.status_code == 400
    assert "must be within" in r.json()["detail"]


def test_import_non_itunes_xml_returns_400(api, tmp_path):
    """A valid plist that isn't an iTunes library must be rejected with 400."""
    client, _ = api
    # Write a minimal plist without a 'Tracks' key.
    non_itunes = tmp_path / "other.xml"
    non_itunes.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        '<key>ApplicationVersion</key><string>1.0</string>'
        '</dict></plist>',
        encoding="utf-8",
    )
    r = client.post("/api/sources/import", json={"path": str(non_itunes), "name": "x"})
    assert r.status_code == 400
    assert "not an iTunes library XML" in r.json()["detail"]


# ---------- template colour validation ---------------------------------


def test_create_station_invalid_colour_returns_400(api):
    """Source colour must be a valid hex — the template validator rejects bad values."""
    client, _ = api
    payload = _wizard_payload("coltest", "Colour Test")
    payload["template"]["sources"][0]["colour"] = "red"  # not a hex colour
    r = client.post("/api/stations", json=payload)
    assert r.status_code == 400
    assert "hex colour" in r.json()["detail"]


# ---------- generate returns 201 -------------------------------------


def test_generate_returns_201(api):
    client, Session = api
    _seed_station_and_pool(Session, slug="s201", pool_tracks=3)
    r = client.post("/api/stations/s201/generate", json={"week": 1})
    assert r.status_code == 201, r.text


# ---------- list_sources uses single query (no N+1) ------------------


def test_list_sources_count_correct_after_import(api):
    """list_sources must return accurate track_count for each pool."""
    client, _ = api
    client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "tiny2",
    })
    sources = client.get("/api/sources").json()
    assert len(sources) == 1
    assert sources[0]["name"] == "tiny2"
    assert sources[0]["track_count"] == 3


# ---------- §16.25 c1 — POST /api/sources/upload (multipart) -----------


def _upload(client, name, role, fixture):
    """POST `fixture` as a multipart upload — mirrors the browser's
    FormData payload (file + name + role) so test setup reads as the
    real call shape."""
    return client.post(
        "/api/sources/upload",
        files={"file": (fixture.name, fixture.read_bytes(), "application/xml")},
        data={"name": name, "role": role},
    )


def test_upload_happy_path(api):
    """Multipart upload of tiny-library.xml + role=content → 200; the
    new pool surfaces on GET /api/sources with the right track_count
    and role. End-to-end byte path: upload → temp file → import_source
    → SourcePlaylist + entries."""
    client, _ = api
    r = _upload(client, "tiny-up", "content", _FIXTURES / "tiny-library.xml")
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["name"] == "tiny-up"
    assert summary["role"] == "content"
    assert summary["tracks_imported"] == 3

    rows = client.get("/api/sources").json()
    assert any(p["name"] == "tiny-up" and p["track_count"] == 3 for p in rows)


def test_upload_role_reference_persists(api):
    """role=reference flows through the multipart Form (Literal validation
    accepts it) and surfaces on GET — mirrors /import's role surface."""
    client, _ = api
    r = _upload(client, "ref-up", "reference", _FIXTURES / "tiny-library.xml")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "reference"
    rows = client.get("/api/sources").json()
    assert any(p["name"] == "ref-up" and p["role"] == "reference" for p in rows)


def test_upload_invalid_role_returns_422(api):
    """Form's `Literal[...]` validation rejects roles outside the
    allowlist — same 422 contract as /import's JSON Literal. The XML
    bytes are irrelevant since validation fires before the upload is
    consumed."""
    client, _ = api
    r = client.post(
        "/api/sources/upload",
        files={"file": ("tiny.xml", b"<plist></plist>", "application/xml")},
        data={"name": "x", "role": "banana"},
    )
    assert r.status_code == 422
    assert any("role" in str(e.get("loc", [])) for e in r.json()["detail"])


def test_upload_malformed_xml_500(api):
    """Non-parseable XML → 500, matching /import's contract via the
    shared `_import_to_pool` helper."""
    client, _ = api
    r = _upload(client, "broken-up", "content", _FIXTURES / "malformed-library.xml")
    assert r.status_code == 500
    assert "import failed" in r.json()["detail"]


def test_upload_temp_file_cleanup_on_parse_failure(api):
    """G2: a failed import still cleans up its temp file. Verified by
    counting `ss-upload-*.xml` in $TMPDIR before vs after — any leak
    would show up as a new file. Comparison is `<= before` rather than
    `== before` because the test harness shares $TMPDIR with other
    runners and we don't own ambient state."""
    tmpdir = Path(tempfile.gettempdir())
    before = set(tmpdir.glob("ss-upload-*.xml"))

    client, _ = api
    r = _upload(client, "cleanup-broken", "content", _FIXTURES / "malformed-library.xml")
    assert r.status_code == 500

    after = set(tmpdir.glob("ss-upload-*.xml"))
    leaked = after - before
    assert not leaked, f"temp file leaked after failed upload: {leaked}"


def test_upload_temp_file_cleanup_on_happy_path(api):
    """Symmetric to the failure-path cleanup test: a successful import
    also unlinks its temp file. Together they prove the `finally`-unlink
    fires regardless of outcome."""
    tmpdir = Path(tempfile.gettempdir())
    before = set(tmpdir.glob("ss-upload-*.xml"))

    client, _ = api
    r = _upload(client, "cleanup-ok", "content", _FIXTURES / "tiny-library.xml")
    assert r.status_code == 200, r.text

    after = set(tmpdir.glob("ss-upload-*.xml"))
    leaked = after - before
    assert not leaked, f"temp file leaked after successful upload: {leaked}"


def test_upload_reimport_overwrites_existing_pool(api):
    """Re-uploading with the same name replaces the existing pool
    (idempotent on name, §14.35) — the engine-layer reimport contract
    surfaces through the upload endpoint too. Will be wrapped in a
    'Re-import' confirm UI in §16.25 c4 (D4)."""
    client, _ = api
    r1 = _upload(client, "swap-up", "content", _FIXTURES / "tiny-library.xml")
    assert r1.status_code == 200, r1.text
    r2 = _upload(client, "swap-up", "reference", _FIXTURES / "tiny-library.xml")
    assert r2.status_code == 200, r2.text
    assert r2.json()["role"] == "reference"
    rows = client.get("/api/sources").json()
    matches = [p for p in rows if p["name"] == "swap-up"]
    assert len(matches) == 1
    assert matches[0]["role"] == "reference"


def test_upload_missing_file_field_returns_422(api):
    """No `file` part → FastAPI's required-field 422. The `data` body
    alone (no `files=…`) reaches the endpoint, but the `File(...)`
    dependency raises a validation error before the handler runs."""
    client, _ = api
    r = client.post(
        "/api/sources/upload",
        data={"name": "no-file", "role": "content"},
    )
    assert r.status_code == 422


# c1 mid-slice review 🟡-1: name input validation (both endpoints).


def test_upload_empty_name_is_rejected(api):
    """Empty name is rejected by FastAPI's `Form(...)` layer with 422
    (multipart-empty is treated as a missing required field) BEFORE
    `validate_pool_name` (via `_validate_name`) is reached. The JSON
    `/import` path is different: Pydantic accepts `""` as a valid
    `str`, so empty there reaches the helper and surfaces as 400 (see
    `test_import_empty_name_returns_400`). Either way, the latent gap
    (persisting a blank-named pool) is closed — just at different
    layers per request shape.

    §16.13.t6: the rule body now lives in
    `engine.importer.validate_pool_name`; `_validate_name` is the
    HTTPException-translating wrapper.
    """
    client, _ = api
    r = _upload(client, "", "content", _FIXTURES / "tiny-library.xml")
    assert r.status_code == 422  # Form layer, before validate_pool_name


def test_upload_whitespace_only_name_returns_400(api):
    """Whitespace-only name → 400 (post-`.strip()` it's empty)."""
    client, _ = api
    r = _upload(client, "   ", "content", _FIXTURES / "tiny-library.xml")
    assert r.status_code == 400
    assert "whitespace" in r.json()["detail"].lower()


def test_upload_surrounding_whitespace_is_stripped(api):
    """`"  daytime  "` is stored as `"daytime"` — the helper strips
    before passing to the importer, so list_sources sorts cleanly."""
    client, _ = api
    r = _upload(client, "  trimmed  ", "content", _FIXTURES / "tiny-library.xml")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "trimmed"
    rows = client.get("/api/sources").json()
    assert any(p["name"] == "trimmed" for p in rows)
    assert not any(p["name"].startswith(" ") for p in rows)


def test_import_empty_name_returns_400(api):
    """The same `validate_pool_name` rule (via `_validate_name`) fires
    on the JSON `/import` endpoint — pre-c1 this was a latent gap
    that the c1 sweep closed in both places at once. §16.13.t6
    hoisted the rule body to engine/importer.py so the CLI shares
    the same check too."""
    client, _ = api
    r = client.post("/api/sources/import", json={
        "path": str(_FIXTURES / "tiny-library.xml"),
        "name": "",
    })
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_get_db_yields_closable_session(tmp_path, monkeypatch):
    """The real get_db() generator (not the test-override) opens a Session,
    yields it, and closes it on teardown. Verify by driving the generator
    directly against a temp DB.

    We monkeypatch api.deps._SessionLocal rather than reload() the module,
    because reload() permanently rebinds deps._engine to the tmp DB for
    the rest of the pytest process — every subsequent test that touched
    the real engine would race the tmp_path teardown.
    """
    import api.deps as deps
    from engine.database import make_engine, make_sessionmaker
    from engine.models import Base

    tmp_engine = make_engine(f"sqlite:///{tmp_path}/deps.db")
    Base.metadata.create_all(tmp_engine)
    monkeypatch.setattr(deps, "_SessionLocal", make_sessionmaker(tmp_engine))

    gen = deps.get_db()
    db = next(gen)
    # It's a live SQLAlchemy Session.
    assert hasattr(db, "query")
    # Close it via the generator's finally-block.
    with pytest.raises(StopIteration):
        next(gen)


# ---------- GET /api/meta (§14.19) -----------------------------------------


def test_meta_returns_all_four_lists_sorted(api):
    """Meta endpoint exposes the constants the frontend needs.

    The lists must be sorted (Python set iteration order isn't stable
    across runs/deploys; deterministic order is required so dropdown
    `<option>` ordering doesn't shuffle).
    """
    client, _ = api
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {"strategies", "rng", "roles", "cadences"}
    # Sorted-ness: each list equals its own sort.
    for key, value in body.items():
        assert value == sorted(value), f"{key} list is not sorted: {value}"


def test_meta_matches_engine_constants(api):
    """The endpoint's lists must match what engine/template.py validates.

    If a contributor adds 'sequential' to SUPPORTED_STRATEGIES but
    forgets to update the meta endpoint (or vice versa), this test
    catches the drift. Pulls the imports here so failure surfaces as
    "engine has X, endpoint exposes Y" rather than a vague mismatch.
    """
    from engine.template import (
        SUPPORTED_ROLES,
        SUPPORTED_RNG,
        SUPPORTED_STRATEGIES,
        SUGGESTED_CADENCES,
    )

    client, _ = api
    body = client.get("/api/meta").json()

    assert set(body["strategies"]) == SUPPORTED_STRATEGIES
    assert set(body["rng"]) == SUPPORTED_RNG
    assert set(body["roles"]) == SUPPORTED_ROLES
    assert set(body["cadences"]) == SUGGESTED_CADENCES


# ---------- §14.30: end-to-end cursor advance via HTTP -----------------


def test_generate_persistent_cursor_advances_via_http(api):
    """Full HTTP path: POST /generate against a cursor.persist=true
    template. Verifies cursor_state is read pre-gen + written post-save
    with the correct generation_count semantics.

    Closes the gap the mid-slice review flagged: cursor-persistence
    unit tests don't exercise the API's `payload.week - 1` derivation;
    this one does, end-to-end.
    """
    from engine.models import CursorState
    client, Session = api
    template = {
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1, "persist": True},
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "cursors_init": {"C1": 0},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 1,
                 "cursor_id": "C1"},
            ],
        },
    }
    station_id, _ = _seed_station_and_pool(
        Session, slug="rb", pool_name="A", pool_tracks=100, template=template,
    )

    # Default rules apply: cursor_advance_step=120, cursor_reset_cycle=0.
    # Week 1: cursor reads cursors_init (0); pick at pool[0] = Track 1.
    #         Post-save advance: gen=0, advance to (0+120)%100 = 20.
    r1 = client.post("/api/stations/rb/generate", json={"week": 1})
    assert r1.status_code == 201

    with Session() as db:
        cs = db.query(CursorState).filter_by(station_id=station_id, name="C1").one()
        assert cs.position == 20

    # Week 2: cursor reads 20; pick at pool[20] = Track 21.
    #         Post-save: gen=1, advance to (20+120)%100 = 40.
    r2 = client.post("/api/stations/rb/generate", json={"week": 2})
    assert r2.status_code == 201

    with Session() as db:
        cs = db.query(CursorState).filter_by(station_id=station_id, name="C1").one()
        assert cs.position == 40


def test_generate_stateless_cursor_does_not_write_cursor_state(api):
    """A persist=false template never writes to cursor_state. Confirms
    advance_cursors's no-op short-circuit fires for the default case."""
    from engine.models import CursorState
    client, Session = api
    template = {
        "station": "cls", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        # persist defaults to False — also explicit here for clarity.
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1, "persist": False},
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "cursors_init": {"C1": 0},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 1,
                 "cursor_id": "C1"},
            ],
        },
    }
    _seed_station_and_pool(
        Session, slug="cls", pool_name="A", pool_tracks=20, template=template,
    )

    client.post("/api/stations/cls/generate", json={"week": 1})
    client.post("/api/stations/cls/generate", json={"week": 2})

    with Session() as db:
        assert db.query(CursorState).count() == 0
