"""Importer tests.

Build a synthetic iTunes plist at runtime, write it to a temp file, import it,
and verify the database state. No dependency on real iTunes exports.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, SourcePlaylist, SourcePlaylistEntry, Track
from engine.importer import import_source


# ---------- fixtures -----------------------------------------------------


@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def make_itunes_plist(
    path: Path,
    playlist_name: str,
    tracks: list[dict],
) -> None:
    """Build a minimal iTunes XML plist and write it to `path`."""
    tracks_dict: dict[str, dict] = {}
    for t in tracks:
        tracks_dict[str(t["Track ID"])] = t
    data = {
        "Library Persistent ID": "0000000000000001",
        "Tracks": tracks_dict,
        "Playlists": [
            {
                "Name": playlist_name,
                "Playlist Items": [{"Track ID": t["Track ID"]} for t in tracks],
            }
        ],
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def track(track_id: int, pid: str, name: str, artist: str | None = None) -> dict:
    d: dict = {
        "Track ID": track_id,
        "Persistent ID": pid,
        "Name": name,
    }
    if artist:
        d["Artist"] = artist
    return d


# ---------- tests --------------------------------------------------------


def test_round_trip_imports_tracks_and_entries(db_session, tmp_path):
    xml_path = tmp_path / "sample.xml"
    make_itunes_plist(xml_path, "SAMPLE", [
        track(101, "AAAA0000000001AA", "One",   "Artist A"),
        track(102, "AAAA0000000002AA", "Two",   "Artist B"),
        track(103, "AAAA0000000003AA", "Three", "Artist C"),
    ])

    summary = import_source(db_session, xml_path, "SAMPLE")
    assert summary == {"name": "SAMPLE", "tracks_imported": 3, "entries_imported": 3}

    sp = db_session.query(SourcePlaylist).filter_by(name="SAMPLE").one()
    entries = db_session.query(SourcePlaylistEntry).filter_by(source_playlist_id=sp.id).order_by(SourcePlaylistEntry.position).all()
    assert [e.position for e in entries] == [0, 1, 2]
    assert [e.track.name for e in entries] == ["One", "Two", "Three"]


def test_reimport_replaces_entries_but_reuses_tracks(db_session, tmp_path):
    """Re-importing the same pool with different entries must cleanly replace
    entries without duplicating the Track rows (dedup by Persistent ID)."""
    xml_path = tmp_path / "a.xml"
    make_itunes_plist(xml_path, "P", [
        track(1, "P0000000000000001", "One"),
        track(2, "P0000000000000002", "Two"),
    ])
    import_source(db_session, xml_path, "P")

    # Re-import with a third track added and one reordered
    xml_path_v2 = tmp_path / "a_v2.xml"
    make_itunes_plist(xml_path_v2, "P", [
        track(2, "P0000000000000002", "Two"),
        track(1, "P0000000000000001", "One"),
        track(3, "P0000000000000003", "Three"),
    ])
    import_source(db_session, xml_path_v2, "P")

    # Total Track rows: still only 3 unique persistent IDs
    assert db_session.query(Track).count() == 3

    # Only one SourcePlaylist named 'P'
    sps = db_session.query(SourcePlaylist).filter_by(name="P").all()
    assert len(sps) == 1

    entries = sorted(sps[0].entries, key=lambda e: e.position)
    assert [e.track.name for e in entries] == ["Two", "One", "Three"]


def test_tracks_shared_across_multiple_pools(db_session, tmp_path):
    """Two pools that share the same tracks (by Persistent ID) must reference
    the same Track rows, not duplicate them."""
    xml_a = tmp_path / "a.xml"
    xml_b = tmp_path / "b.xml"
    shared = [
        track(1, "SHARED001", "Shared One"),
        track(2, "SHARED002", "Shared Two"),
    ]
    make_itunes_plist(xml_a, "A", shared)
    make_itunes_plist(xml_b, "B", shared)

    import_source(db_session, xml_a, "A")
    import_source(db_session, xml_b, "B")

    # Two pools, two tracks total (not four)
    assert db_session.query(SourcePlaylist).count() == 2
    assert db_session.query(Track).count() == 2

    # Both pools have 2 entries referring to the same 2 tracks
    pools = {sp.name: sp for sp in db_session.query(SourcePlaylist).all()}
    a_track_ids = {e.track_id for e in pools["A"].entries}
    b_track_ids = {e.track_id for e in pools["B"].entries}
    assert a_track_ids == b_track_ids
