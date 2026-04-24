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
    Base, Config, Playlist, PlaylistEntry, PlaylistTrace, Station, Track,
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
