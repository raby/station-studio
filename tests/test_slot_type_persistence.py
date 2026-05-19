"""Tests for the §14.31 PlaylistEntry.slot_type column.

Covers:
- save_playlist populates slot_type from trace slots' strategy field
- The column is nullable (pre-§14.31 rows remain valid)
- API GET /api/playlists/{id} returns slot_type per entry
- Pydantic schema PlaylistEntryOut handles null + populated values
- Backfill script: populates null slot_types from PlaylistTrace;
  idempotent re-runs; dry-run reports without writing; station filter
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.generator import generate
from engine.models import (
    Base, Playlist, PlaylistEntry, PlaylistTrace,
    SourcePlaylist, SourcePlaylistEntry, Station, Track,
)
from engine.persistence import load_pools, save_playlist
from engine.template import parse_template
from scripts.backfill_slot_types import backfill


# ---------- fixtures -----------------------------------------------------


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker:
    engine = make_engine(f"sqlite:///{tmp_path}/slot_type.db")
    Base.metadata.create_all(engine)
    return make_sessionmaker(engine)


@pytest.fixture
def api(tmp_path):
    """TestClient against a throwaway SQLite DB."""
    engine = make_engine(f"sqlite:///{tmp_path}/slot_type_api.db")
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


def _seed_station_with_multi_strategy_template(
    Session: sessionmaker,
) -> tuple[int, dict]:
    """Insert a station whose template has TWO different strategies
    side-by-side so slot_type round-trip is testable across strategies."""
    template = {
        "station": "multi",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "A", "colour": "#111", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "window", "size": 2},
                {"source": "A", "strategy": "pick", "size": 1},
            ],
        },
    }
    with Session() as db:
        st = Station(slug="multi", name="Multi", template=template)
        db.add(st)
        db.flush()
        sp = SourcePlaylist(name="A")
        db.add(sp)
        db.flush()
        for i in range(1, 6):
            tr = Track(
                persistent_id=f"P{i}",
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
        return st.id, template


# ---------- save_playlist records slot_type per strategy -----------------


def test_save_playlist_records_slot_type_for_window_and_pick(session_factory):
    Session = session_factory
    station_id, template_dict = _seed_station_with_multi_strategy_template(Session)
    tmpl = parse_template(template_dict)

    with Session() as db:
        station = db.query(Station).filter_by(id=station_id).one()
        pools = load_pools(db, tmpl)
        result = generate(tmpl, pools, week=1)
        save_playlist(db, station, result, week=1, template=tmpl)
        db.commit()

        entries = (
            db.query(PlaylistEntry)
            .order_by(PlaylistEntry.position)
            .all()
        )
        # window block produced positions 1–2; pick block produced position 3.
        assert [e.slot_type for e in entries] == ["window", "window", "pick"]


def test_slot_type_column_is_nullable(session_factory):
    """Sanity: hand-construct a PlaylistEntry without slot_type — the
    schema must accept the null. This is what pre-§14.31 rows look like
    before backfill."""
    Session = session_factory
    with Session() as db:
        st = Station(
            slug="x", name="X",
            template={
                "station": "x", "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "s", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        tr = Track(persistent_id="P1", name="T1")
        db.add(tr)
        db.flush()
        pl = Playlist(station_id=st.id, week=1, trace_id="trace-x-1")
        db.add(pl)
        db.flush()
        db.add(PlaylistEntry(
            playlist_id=pl.id, track_id=tr.id, position=1,
            source_name="A",
            # slot_type omitted → null
        ))
        db.commit()

        loaded = db.query(PlaylistEntry).one()
        assert loaded.slot_type is None


# ---------- API GET /api/playlists/{id} round-trip -----------------------


def test_get_playlist_includes_slot_type_per_entry(api):
    """End-to-end: generate via HTTP, then GET the playlist — each
    entry's slot_type matches the dispatched strategy."""
    client, Session = api
    station_id, _ = _seed_station_with_multi_strategy_template(Session)

    r = client.post("/api/stations/multi/generate", json={"week": 1})
    assert r.status_code == 201
    playlist_id = r.json()["playlist_id"]

    body = client.get(f"/api/playlists/{playlist_id}").json()
    slot_types = [e["slot_type"] for e in body["entries"]]
    assert slot_types == ["window", "window", "pick"]


def test_get_playlist_handles_null_slot_type(api):
    """Pre-§14.31 entries (slot_type null) should round-trip through
    the API as `null` — not raise a Pydantic validation error."""
    client, Session = api
    with Session() as db:
        st = Station(
            slug="legacy", name="L",
            template={
                "station": "legacy", "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "s", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        tr = Track(persistent_id="LP1", name="LT1")
        db.add(tr)
        db.flush()
        pl = Playlist(station_id=st.id, week=1, trace_id="legacy-trace")
        db.add(pl)
        db.flush()
        db.add(PlaylistEntry(
            playlist_id=pl.id, track_id=tr.id, position=1,
            source_name="A",  # slot_type left null
        ))
        db.commit()
        pid = pl.id

    body = client.get(f"/api/playlists/{pid}").json()
    assert body["entries"][0]["slot_type"] is None


# ---------- backfill_slot_types script ----------------------------------


def _seed_legacy_playlist_with_trace(
    Session: sessionmaker,
    *,
    slug: str = "legacy",
    week: int = 1,
    slot_strategies: list[str] | None = None,
) -> tuple[int, int]:
    """Insert a Playlist + entries (slot_type null) + a matching
    PlaylistTrace whose slots carry the supplied strategies. Returns
    (station_id, playlist_id)."""
    slot_strategies = slot_strategies or ["window", "window", "pick"]
    with Session() as db:
        st = Station(
            slug=slug, name=slug,
            template={
                "station": slug, "cadence": "weekly", "rng": "off",
                "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                "layout": {"unit": "s", "unit_labels": [],
                           "row_start_offsets": [], "row_template": []},
            },
        )
        db.add(st)
        db.flush()
        tracks = []
        for i, _ in enumerate(slot_strategies, start=1):
            tr = Track(persistent_id=f"{slug}-P{i}", name=f"{slug}-T{i}")
            db.add(tr)
            db.flush()
            tracks.append(tr)
        pl = Playlist(station_id=st.id, week=week, trace_id=f"trace-{slug}-{week}")
        db.add(pl)
        db.flush()
        slots_json = []
        for i, (tr, strat) in enumerate(zip(tracks, slot_strategies), start=1):
            db.add(PlaylistEntry(
                playlist_id=pl.id, track_id=tr.id, position=i,
                source_name="A",  # slot_type left null
            ))
            slots_json.append({
                "slot": i, "unit": "Mon", "row": 1, "block": 1,
                "source": "A", "strategy": strat, "pool_index": i,
                "track_id": tr.id, "track_name": tr.name, "track_artist": None,
                "reason": "seeded",
            })
        db.add(PlaylistTrace(
            trace_id=pl.trace_id,
            playlist_id=pl.id,
            station_slug=slug,
            week=week,
            rng="off",
            seed=None,
            generated_at="2026-05-19T00:00:00Z",
            slots=slots_json,
        ))
        db.commit()
        return st.id, pl.id


def test_backfill_populates_null_slot_types(session_factory):
    Session = session_factory
    _, playlist_id = _seed_legacy_playlist_with_trace(
        Session, slot_strategies=["window", "pick", "jingle"],
    )
    playlists, rows = backfill(Session)
    assert (playlists, rows) == (1, 3)

    with Session() as db:
        entries = (
            db.query(PlaylistEntry)
            .filter_by(playlist_id=playlist_id)
            .order_by(PlaylistEntry.position)
            .all()
        )
        assert [e.slot_type for e in entries] == ["window", "pick", "jingle"]


def test_backfill_is_idempotent(session_factory):
    """Running backfill twice should leave the second run with 0 updates."""
    Session = session_factory
    _seed_legacy_playlist_with_trace(Session)

    first = backfill(Session)
    second = backfill(Session)
    assert first[1] > 0       # first run did work
    assert second == (0, 0)   # second run is a no-op


def test_backfill_dry_run_does_not_write(session_factory):
    Session = session_factory
    _, playlist_id = _seed_legacy_playlist_with_trace(Session)
    playlists, rows = backfill(Session, dry_run=True)
    assert (playlists, rows) == (1, 3)  # counted but not written

    with Session() as db:
        entries = db.query(PlaylistEntry).filter_by(playlist_id=playlist_id).all()
        assert all(e.slot_type is None for e in entries)  # unchanged


def test_backfill_station_filter(session_factory):
    """A specific --station filter only touches that station's
    playlists. Other stations are left untouched."""
    Session = session_factory
    _seed_legacy_playlist_with_trace(Session, slug="alpha")
    _seed_legacy_playlist_with_trace(Session, slug="beta")
    playlists, rows = backfill(Session, station_slug="alpha")
    assert (playlists, rows) == (1, 3)

    with Session() as db:
        # alpha's entries are populated.
        alpha = db.query(Station).filter_by(slug="alpha").one()
        for e in db.query(PlaylistEntry).join(Playlist).filter(
            Playlist.station_id == alpha.id
        ).all():
            assert e.slot_type is not None
        # beta's entries are untouched.
        beta = db.query(Station).filter_by(slug="beta").one()
        for e in db.query(PlaylistEntry).join(Playlist).filter(
            Playlist.station_id == beta.id
        ).all():
            assert e.slot_type is None


def test_backfill_unknown_station_is_noop(session_factory):
    """--station with a non-existent slug logs an error + returns (0, 0)
    rather than crashing."""
    Session = session_factory
    _seed_legacy_playlist_with_trace(Session, slug="alpha")
    playlists, rows = backfill(Session, station_slug="does-not-exist")
    assert (playlists, rows) == (0, 0)
