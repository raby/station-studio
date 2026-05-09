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

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.models import (
    Base, Config, Playlist, PlaylistEntry, PlaylistTrace,
    SourcePlaylist, SourcePlaylistEntry, Station, Track,
)


# ---------- fixtures ------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    """TestClient wired to a throwaway SQLite DB.

    Yields (client, Session). The test can use `client` to hit endpoints
    and `Session()` to seed preconditions or assert post-conditions.
    """
    engine = make_engine(f"sqlite:///{tmp_path}/api.db")
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
            db.add(PlaylistTrace(
                trace_id=pl.trace_id,
                playlist_id=pl.id,
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
    assert "cursor" in r.json()["detail"]  # surfaces the missing field


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
        tr = db.query(PlaylistTrace).filter_by(playlist_id=pl.id).first()
        assert tr is not None
        assert tr.rng == "off"
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


def test_generate_same_week_replaces_previous(api):
    client, Session = api
    _seed_station_and_pool(Session, slug="twice", pool_tracks=3)
    r1 = client.post("/api/stations/twice/generate", json={"week": 1})
    assert r1.status_code == 201
    trace1 = r1.json()["trace_id"]
    r2 = client.post("/api/stations/twice/generate", json={"week": 1})
    assert r2.status_code == 201
    trace2 = r2.json()["trace_id"]
    # Each generation mints a fresh trace_id; regenerating must produce a new
    # one. (SQLite may reuse the previous playlist's primary key after DELETE,
    # so we don't compare IDs — the "one playlist per week" invariant below
    # is the real guarantee.)
    assert trace1 != trace2
    with Session() as db:
        remaining = db.query(Playlist).filter_by(week=1).all()
        assert len(remaining) == 1
        assert remaining[0].trace_id == trace2


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
    assert summary["tracks_imported"] == 3
    assert summary["entries_imported"] == 3

    r2 = client.get("/api/sources")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "tiny"
    assert rows[0]["track_count"] == 3


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


def test_root_endpoint_identifies_the_service():
    """GET / returns a tiny service-identity blob — covers api/main.root()."""
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"service": "station-studio", "version": "0.1.0"}


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
        assert db.query(PlaylistTrace).filter_by(playlist_id=pid).first() is None


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
        db.add(PlaylistTrace(
            trace_id="trace-full-2", playlist_id=pl2.id,
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
        assert db.query(PlaylistTrace).filter(
            PlaylistTrace.playlist_id.in_([pid1, pid2])
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
