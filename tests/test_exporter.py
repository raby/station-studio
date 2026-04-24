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
from engine.exporter import export_playlist, export_playlist_to_disk
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


# ---------- disk export --------------------------------------------------


def test_export_to_disk_writes_to_configured_dir(db, tmp_path):
    db.add(Config(key="playlist_output_dir", value=str(tmp_path)))
    db.commit()
    pl = _make_station_and_playlist(db, slug="cls", name="Classics", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])

    path = export_playlist_to_disk(db, pl.id)

    assert path == (tmp_path / "cls" / "001.xml").resolve()
    assert path.exists()
    # And the bytes are the same as export_playlist produces
    assert path.read_bytes() == export_playlist(db, pl.id)


def test_export_to_disk_creates_station_subdirectory(db, tmp_path):
    db.add(Config(key="playlist_output_dir", value=str(tmp_path)))
    db.commit()
    pl = _make_station_and_playlist(db, slug="rebirth-classics", name="Rebirth Classics", week=7,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])

    path = export_playlist_to_disk(db, pl.id)

    assert path.parent == (tmp_path / "rebirth-classics").resolve()
    assert path.parent.is_dir()
    assert path.name == "007.xml"


def test_export_to_disk_overwrites_on_re_export(db, tmp_path):
    db.add(Config(key="playlist_output_dir", value=str(tmp_path)))
    db.commit()
    pl = _make_station_and_playlist(db, slug="cls", name="Classics", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])

    p1 = export_playlist_to_disk(db, pl.id)
    first_bytes = p1.read_bytes()
    # Second export to the same (station, week) produces the same path and
    # overwrites the existing file without error.
    p2 = export_playlist_to_disk(db, pl.id)
    assert p2 == p1
    assert p2.read_bytes() == first_bytes


def test_export_to_disk_returns_absolute_path(db, tmp_path, monkeypatch):
    """Even when Config holds a relative path, the returned Path is absolute."""
    monkeypatch.chdir(tmp_path)
    db.add(Config(key="playlist_output_dir", value="relative-out"))
    db.commit()
    pl = _make_station_and_playlist(db, slug="cls", name="Classics", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])

    path = export_playlist_to_disk(db, pl.id)

    assert path.is_absolute()
    assert path == (tmp_path / "relative-out" / "cls" / "001.xml").resolve()


def test_export_to_disk_uses_default_dir_without_config(db, tmp_path, monkeypatch):
    """No Config row -> writes to '<cwd>/out/'."""
    monkeypatch.chdir(tmp_path)
    pl = _make_station_and_playlist(db, slug="cls", name="Classics", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])

    path = export_playlist_to_disk(db, pl.id)

    assert path == (tmp_path / "out" / "cls" / "001.xml").resolve()
    assert path.exists()


def test_export_to_disk_expands_home_prefix(db, tmp_path, monkeypatch):
    """'~/foo' in Config is expanded against $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    db.add(Config(key="playlist_output_dir", value="~/exports"))
    db.commit()
    pl = _make_station_and_playlist(db, slug="cls", name="Classics", week=1,
        track_data=[{"persistent_id": "P1", "name": "T", "itunes_track_id": 1}])

    path = export_playlist_to_disk(db, pl.id)

    assert path == (tmp_path / "exports" / "cls" / "001.xml").resolve()


def test_export_to_disk_raises_for_missing_playlist(db, tmp_path):
    db.add(Config(key="playlist_output_dir", value=str(tmp_path)))
    db.commit()
    with pytest.raises(ValueError, match="Playlist 999 not found"):
        export_playlist_to_disk(db, 999)


# ---------- edge branches -----------------------------------------------


def test_export_raises_for_missing_playlist(db):
    """export_playlist must raise ValueError when the playlist id is unknown."""
    with pytest.raises(ValueError, match="Playlist 999 not found"):
        export_playlist(db, 999)


def test_export_deduplicates_repeated_track(db):
    """When the same track appears twice in a playlist, the Tracks dict has
    one entry and Playlist Items has two entries pointing at the same Track ID."""
    station = Station(
        slug="dup", name="Dup",
        template={"station": "dup", "cadence": "weekly", "rng": "off",
                  "sources": [], "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
                  "layout": {"unit": "day", "unit_labels": [],
                             "row_start_offsets": [], "row_template": []}},
    )
    db.add(station)
    db.flush()
    track = Track(persistent_id="DUP1", itunes_track_id=7, name="Loop", artist="A")
    db.add(track)
    db.flush()
    pl = Playlist(station_id=station.id, week=1, trace_id="t-dup-1")
    db.add(pl)
    db.flush()
    # Same track at positions 1 and 2.
    db.add(PlaylistEntry(playlist_id=pl.id, track_id=track.id, position=1, source_name="A"))
    db.add(PlaylistEntry(playlist_id=pl.id, track_id=track.id, position=2, source_name="A"))
    db.commit()

    xml = export_playlist(db, pl.id)
    parsed = plistlib.loads(xml)
    assert len(parsed["Tracks"]) == 1         # deduped
    assert len(parsed["Playlists"][0]["Playlist Items"]) == 2  # both references kept
    tid = parsed["Playlists"][0]["Playlist Items"][0]["Track ID"]
    assert parsed["Playlists"][0]["Playlist Items"][1]["Track ID"] == tid


def test_export_minimal_fallback_includes_optional_fields_when_set(db):
    """Tracks with no itunes_metadata hit the minimal-fallback branch.
    Album/Genre/Total Time are only emitted when set — this exercises all
    three 'if set' branches together."""
    pl = _make_station_and_playlist(db, slug="min", name="Min", week=1,
        track_data=[{
            "persistent_id": "M1", "name": "Full", "artist": "A",
            "album": "Alb", "genre": "Rock", "total_time": 180000,
            "itunes_track_id": 9001,
            # no 'metadata' key → itunes_metadata is None → minimal fallback
        }],
    )
    xml = export_playlist(db, pl.id)
    parsed = plistlib.loads(xml)
    t = list(parsed["Tracks"].values())[0]
    assert t["Album"] == "Alb"
    assert t["Genre"] == "Rock"
    assert t["Total Time"] == 180000


def test_export_restores_typed_datetime_metadata(db):
    """Stored {__type__: 'datetime', value: iso} must deserialise to datetime."""
    pl = _make_station_and_playlist(db, slug="dt", name="DT", week=1,
        track_data=[{
            "persistent_id": "D1", "name": "T", "itunes_track_id": 1,
            "metadata": {
                "Track ID": 1,
                "Name": "T",
                "Persistent ID": "D1",
                "Date Added": {"__type__": "datetime", "value": "2024-01-15T10:30:00"},
            },
        }],
    )
    xml = export_playlist(db, pl.id)
    parsed = plistlib.loads(xml)
    t = list(parsed["Tracks"].values())[0]
    # plistlib parses <date> back to datetime.
    from datetime import datetime as _dt
    assert isinstance(t["Date Added"], _dt)
    assert t["Date Added"].year == 2024


def test_export_restores_bad_typed_datetime_as_raw_value(db):
    """If the stored ISO string is malformed, fall back to the raw value."""
    pl = _make_station_and_playlist(db, slug="bad", name="Bad", week=1,
        track_data=[{
            "persistent_id": "B1", "name": "T", "itunes_track_id": 1,
            "metadata": {
                "Track ID": 1, "Name": "T", "Persistent ID": "B1",
                "Date Added": {"__type__": "datetime", "value": "not-a-date"},
            },
        }],
    )
    xml = export_playlist(db, pl.id)
    # We only care that export didn't crash on the malformed datetime;
    # the value survives as a string in the XML.
    assert b"not-a-date" in xml


def test_export_restores_legacy_string_datetime(db):
    """Legacy imports stored datetimes as plain ISO strings (no __type__ wrapper).
    For known DATETIME_KEYS these must still deserialise to datetime."""
    pl = _make_station_and_playlist(db, slug="leg", name="Leg", week=1,
        track_data=[{
            "persistent_id": "L1", "name": "T", "itunes_track_id": 1,
            "metadata": {
                "Track ID": 1, "Name": "T", "Persistent ID": "L1",
                "Date Added": "2024-02-20T11:00:00",
            },
        }],
    )
    xml = export_playlist(db, pl.id)
    parsed = plistlib.loads(xml)
    t = list(parsed["Tracks"].values())[0]
    from datetime import datetime as _dt
    assert isinstance(t["Date Added"], _dt)
    assert t["Date Added"].month == 2


def test_export_restores_bad_legacy_string_datetime_as_string(db):
    """Malformed legacy string datetimes should survive as raw strings."""
    pl = _make_station_and_playlist(db, slug="leg2", name="Leg2", week=1,
        track_data=[{
            "persistent_id": "L2", "name": "T", "itunes_track_id": 1,
            "metadata": {
                "Track ID": 1, "Name": "T", "Persistent ID": "L2",
                "Date Added": "not-a-date-either",
            },
        }],
    )
    xml = export_playlist(db, pl.id)
    assert b"not-a-date-either" in xml


def test_export_injects_column_override_missing_from_metadata(db):
    """Legacy imports may lack some column keys in itunes_metadata. The
    exporter must still inject them from the Track row columns."""
    pl = _make_station_and_playlist(db, slug="inj", name="Inj", week=1,
        track_data=[{
            "persistent_id": "I1", "name": "RealName", "artist": "RealArtist",
            "itunes_track_id": 1,
            # metadata has only Name + Persistent ID — Artist must be injected.
            "metadata": {
                "Track ID": 1,
                "Name": "RealName",
                "Persistent ID": "I1",
            },
        }],
    )
    xml = export_playlist(db, pl.id)
    parsed = plistlib.loads(xml)
    t = list(parsed["Tracks"].values())[0]
    assert t["Artist"] == "RealArtist"
