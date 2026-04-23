"""Exporter tests.

Validates that export_playlist:
  * Produces parseable XML with iTunes-shaped DOCTYPE and key ordering
  * Reconstructs track metadata fields in their original order
  * Uses station-derived playlist Name and Persistent ID
  * Falls back to defaults when library metadata is absent
  * Uses library metadata from Config when present
  * Re-importing exported XML preserves all tracks and positions (round-trip)
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.exporter import export_playlist
from engine.importer import import_source
from engine.models import (
    Base, Config, Playlist, PlaylistEntry, Station, Track,
    SourcePlaylist, SourcePlaylistEntry,
)


# ---------- fixtures ------------------------------------------------------


@pytest.fixture
def db(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/export.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _make_station_and_playlist(db: Session, *, slug: str, name: str, week: int,
                               track_data: list[dict]) -> Playlist:
    """Create a Station + SourcePlaylist + Playlist with entries for testing.

    track_data items: {persistent_id, name, artist, itunes_track_id, metadata?}
    """
    station = Station(
        slug=slug,
        name=name,
        template={"station": slug, "cadence": "weekly", "rng": "off",
                  "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                  "layout": {"unit": "day", "unit_labels": [], "row_start_offsets": [],
                             "row_template": []}},
    )
    db.add(station)
    db.flush()

    tracks: list[Track] = []
    for td in track_data:
        t = Track(
            persistent_id=td["persistent_id"],
            itunes_track_id=td.get("itunes_track_id"),
            name=td["name"],
            artist=td.get("artist"),
            album=td.get("album"),
            genre=td.get("genre"),
            total_time=td.get("total_time"),
            itunes_metadata=td.get("metadata"),
        )
        db.add(t)
        tracks.append(t)
    db.flush()

    playlist = Playlist(
        station_id=station.id, week=week, trace_id=f"test-{slug}-{week}",
    )
    db.add(playlist)
    db.flush()

    for i, t in enumerate(tracks, start=1):
        db.add(PlaylistEntry(
            playlist_id=playlist.id, track_id=t.id, position=i,
            source_name="TEST",
        ))
    db.commit()
    return playlist


# ---------- basic shape ---------------------------------------------------


def test_export_produces_parseable_plist(db):
    pl = _make_station_and_playlist(db, slug="alpha", name="Alpha FM", week=1,
        track_data=[
            {"persistent_id": "A0000000000001", "name": "One", "artist": "X", "itunes_track_id": 1000},
            {"persistent_id": "A0000000000002", "name": "Two", "artist": "Y", "itunes_track_id": 1001},
        ],
    )
    xml = export_playlist(db, pl.id)
    parsed = plistlib.loads(xml)

    assert "Tracks" in parsed
    assert "Playlists" in parsed
    assert len(parsed["Playlists"]) == 1
    assert len(parsed["Playlists"][0]["Playlist Items"]) == 2


def test_export_uses_apple_computer_doctype(db):
    pl = _make_station_and_playlist(db, slug="x", name="X", week=1,
        track_data=[{"persistent_id": "P1", "name": "One", "itunes_track_id": 1}])
    xml = export_playlist(db, pl.id).decode()
    assert "//Apple Computer//DTD PLIST 1.0//EN" in xml
    assert "//Apple//DTD PLIST 1.0//EN" not in xml


def test_export_merges_key_and_value_onto_same_line(db):
    """iTunes places leaf <key>…</key><value>…</value> on one line."""
    pl = _make_station_and_playlist(db, slug="x", name="X", week=1,
        track_data=[{"persistent_id": "P1", "name": "One", "itunes_track_id": 1}])
    xml = export_playlist(db, pl.id).decode()
    # The Major Version key/value should be on the same line.
    assert "<key>Major Version</key><integer>1</integer>" in xml


# ---------- playlist identity -------------------------------------------


def test_playlist_name_derived_from_station(db):
    pl = _make_station_and_playlist(db, slug="rebirth-classics", name="Rebirth Classics", week=5,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])
    parsed = plistlib.loads(export_playlist(db, pl.id))
    assert parsed["Playlists"][0]["Name"] == "Rebirth Classics 005"


def test_playlist_persistent_id_derived_from_slug(db):
    pl = _make_station_and_playlist(db, slug="rebirth-classics", name="Rebirth Classics", week=12,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])
    parsed = plistlib.loads(export_playlist(db, pl.id))
    # 'rebirth-classics' -> 'REBIRTH_CLASSICS'
    assert parsed["Playlists"][0]["Playlist Persistent ID"] == "REBIRTH_CLASSICS_012"


# ---------- library metadata --------------------------------------------


def test_export_uses_defaults_without_config(db):
    pl = _make_station_and_playlist(db, slug="x", name="X", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])
    parsed = plistlib.loads(export_playlist(db, pl.id))
    assert parsed["Library Persistent ID"] == "STATION_STUDIO"
    assert parsed["Music Folder"] == "file://localhost/"


def test_export_uses_config_library_metadata(db):
    db.add(Config(key="library_persistent_id", value="ABCD1234DEADBEEF"))
    db.add(Config(key="music_folder", value="file://localhost/Users/me/Music/"))
    db.commit()
    pl = _make_station_and_playlist(db, slug="x", name="X", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])
    parsed = plistlib.loads(export_playlist(db, pl.id))
    assert parsed["Library Persistent ID"] == "ABCD1234DEADBEEF"
    assert parsed["Music Folder"] == "file://localhost/Users/me/Music/"


# ---------- track metadata fidelity -------------------------------------


def test_export_preserves_track_metadata_order(db):
    """A track with itunes_metadata keeps its original key ordering."""
    metadata = {
        "Track ID": 42,
        "Name": "Original",
        "Artist": "Original Artist",
        "Kind": "MPEG audio file",
        "Size": 12345,
        "Persistent ID": "P0000000000001",
        "Track Type": "File",
    }
    pl = _make_station_and_playlist(db, slug="x", name="X", week=1,
        track_data=[{
            "persistent_id": "P0000000000001", "name": "Original",
            "artist": "Original Artist", "itunes_track_id": 42,
            "metadata": metadata,
        }])
    parsed = plistlib.loads(export_playlist(db, pl.id))
    track = parsed["Tracks"]["42"]
    assert list(track.keys()) == list(metadata.keys())


def test_export_assigns_synthetic_id_when_itunes_id_missing(db):
    """Tracks without itunes_track_id get a synthetic ID ≥ 100_000."""
    pl = _make_station_and_playlist(db, slug="x", name="X", week=1,
        track_data=[{"persistent_id": "P1", "name": "T"}])  # no itunes_track_id
    parsed = plistlib.loads(export_playlist(db, pl.id))
    assert "100000" in parsed["Tracks"]


# ---------- round-trip --------------------------------------------------


def test_export_round_trip_preserves_positions(db, tmp_path):
    """Round-trip: export -> file -> import -> check entries match in order."""
    pl = _make_station_and_playlist(db, slug="cls", name="Classics", week=1,
        track_data=[
            {"persistent_id": "P0001", "name": "One",   "artist": "A", "itunes_track_id": 1,
             "metadata": {"Track ID": 1, "Name": "One",   "Artist": "A",
                          "Persistent ID": "P0001", "Track Type": "File"}},
            {"persistent_id": "P0002", "name": "Two",   "artist": "B", "itunes_track_id": 2,
             "metadata": {"Track ID": 2, "Name": "Two",   "Artist": "B",
                          "Persistent ID": "P0002", "Track Type": "File"}},
            {"persistent_id": "P0003", "name": "Three", "artist": "C", "itunes_track_id": 3,
             "metadata": {"Track ID": 3, "Name": "Three", "Artist": "C",
                          "Persistent ID": "P0003", "Track Type": "File"}},
        ],
    )

    # Export and write to disk
    xml = export_playlist(db, pl.id)
    out_path = tmp_path / "roundtrip.xml"
    out_path.write_bytes(xml)

    # Re-import into a fresh DB so we can compare
    fresh_engine = make_engine(f"sqlite:///{tmp_path}/fresh.db")
    Base.metadata.create_all(fresh_engine)
    Fresh = make_sessionmaker(fresh_engine)
    with Fresh() as fresh:
        import_source(fresh, out_path, "ROUNDTRIP")
        sp = fresh.query(SourcePlaylist).filter_by(name="ROUNDTRIP").one()
        entries = sorted(sp.entries, key=lambda e: e.position)
        assert [e.track.persistent_id for e in entries] == ["P0001", "P0002", "P0003"]
        assert [e.track.name for e in entries] == ["One", "Two", "Three"]
        assert [e.track.artist for e in entries] == ["A", "B", "C"]
