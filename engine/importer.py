"""Import iTunes XML plist files into a source pool.

Generalised from weekly/app/services/importer.py. Key changes:
  * Dropped library-metadata capture (Config table). Station Studio doesn't
    need it for v1. Will be reintroduced when an exporter lands.
  * Parameter `name` names the source pool explicitly; no per-station
    assumptions baked in.
  * Idempotent: re-importing a pool replaces entries cleanly. Tracks remain
    deduplicated by Persistent ID across all pools.
"""
from __future__ import annotations

import logging
import plistlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from engine.models import SourcePlaylist, Track, SourcePlaylistEntry, Config


logger = logging.getLogger(__name__)


def _store_library_metadata(db: Session, data: dict) -> None:
    """Persist library-level iTunes metadata on first import so the exporter
    can reconstruct an XML file iTunes will accept. First-import wins —
    subsequent imports do not overwrite.
    """
    for config_key, plist_key in [
        ("library_persistent_id", "Library Persistent ID"),
        ("music_folder", "Music Folder"),
    ]:
        value = data.get(plist_key)
        if value and not db.query(Config).filter(Config.key == config_key).first():
            db.add(Config(key=config_key, value=str(value)))


def _parse_itunes_xml(path: Path) -> dict:
    with open(path, "rb") as f:
        return plistlib.load(f)


def _get_or_create_track(db: Session, raw: dict) -> Track:
    """Find a track by Persistent ID, or create it. Update mutable fields on re-import."""
    pid = raw["Persistent ID"]
    track = db.query(Track).filter(Track.persistent_id == pid).first()

    name = raw.get("Name", "Unknown")
    artist = raw.get("Artist")
    album = raw.get("Album")
    genre = raw.get("Genre")
    total_time = raw.get("Total Time")
    itunes_track_id = raw.get("Track ID")

    # Preserve full iTunes metadata (for fidelity on future export)
    metadata: dict = {}
    for k, v in raw.items():
        if isinstance(v, datetime):
            metadata[k] = {"__type__": "datetime", "value": v.isoformat()}
        else:
            metadata[k] = v

    if track is None:
        track = Track(
            persistent_id=pid,
            itunes_track_id=itunes_track_id,
            name=name,
            artist=artist,
            album=album,
            genre=genre,
            total_time=total_time,
            itunes_metadata=metadata or None,
        )
        db.add(track)
    else:
        # First-import-wins for itunes_track_id (matches weekly's MASTER-first convention)
        if track.itunes_track_id is None and itunes_track_id is not None:
            track.itunes_track_id = itunes_track_id
        track.name = name
        track.artist = artist
        track.album = album
        track.genre = genre
        track.total_time = total_time
        track.itunes_metadata = metadata or None

    return track


def import_source(db: Session, path: Path, name: str) -> dict:
    """Import an iTunes XML file as a named source pool.

    Returns a summary with tracks / entries imported.
    Raises ValueError if the file is not an iTunes library XML.
    """
    data = _parse_itunes_xml(path)
    if "Tracks" not in data:
        raise ValueError(
            f"not an iTunes library XML: 'Tracks' key missing in {path.name}. "
            "Export a library or playlist from iTunes/Music and try again."
        )
    raw_tracks = data.get("Tracks", {})
    playlists = data.get("Playlists", [])

    # Capture library-level metadata from the first source imported.
    # The exporter needs this to produce an XML file iTunes will accept.
    _store_library_metadata(db, data)

    existing = db.query(SourcePlaylist).filter(SourcePlaylist.name == name).first()
    if existing:
        logger.info("Replacing existing source '%s'", name)
        db.delete(existing)
        db.flush()

    sp = SourcePlaylist(name=name, imported_at=datetime.now(timezone.utc))
    db.add(sp)
    db.flush()

    id_to_track: dict[int, Track] = {}
    for _raw_id_str, raw in raw_tracks.items():
        track = _get_or_create_track(db, raw)
        id_to_track[raw["Track ID"]] = track
    db.flush()  # batch flush so new track IDs are assigned before entries reference them

    entries_created = 0
    if playlists:
        playlist_items = playlists[0].get("Playlist Items", [])
        for pos, item in enumerate(playlist_items):
            itunes_track_id = item["Track ID"]
            track = id_to_track.get(itunes_track_id)
            if track is None:
                continue
            entry = SourcePlaylistEntry(
                source_playlist_id=sp.id,
                track_id=track.id,
                position=pos,
            )
            db.add(entry)
            entries_created += 1

    db.commit()

    logger.info(
        "Imported source '%s': %d tracks, %d entries",
        name, len(id_to_track), entries_created,
    )

    return {
        "name": name,
        "tracks_imported": len(id_to_track),
        "entries_imported": entries_created,
    }
