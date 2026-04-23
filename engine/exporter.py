"""Export a generated Playlist as an iTunes XML plist file.

Generalised from weekly/app/services/exporter.py. Differences:
  * Station-scoped: playlist name and Persistent ID derive from the station
    row, not a hardcoded "WEEKLY PLAYLIST NNN" convention.
  * Model names updated (Playlist / PlaylistEntry vs WeeklyPlaylist / ...).
  * All the XML-format tricks (key ordering, same-line key+value, Apple
    Computer DOCTYPE, synthetic Track IDs for trackless fixtures) are kept
    verbatim — that code has been battle-tested against real iTunes XML.
"""
from __future__ import annotations

import logging
import plistlib
import re
from collections import OrderedDict
from datetime import datetime
from io import BytesIO

from sqlalchemy.orm import Session

from engine.models import Playlist, PlaylistEntry, Track, Config, Station


logger = logging.getLogger(__name__)

# Known iTunes metadata keys that contain datetime values. Legacy fallback
# for data imported before the type-marker approach.
_DATETIME_KEYS = frozenset({
    "Date Added",
    "Date Modified",
    "Play Date UTC",
})

# Starting value for synthetic Track IDs assigned to tracks that have no
# original iTunes Track ID (e.g. test fixtures). High to avoid collisions
# with real iTunes IDs.
_SYNTHETIC_TRACK_ID_START = 100_000

# Fixed iTunes top-level key order.
_TOP_LEVEL_KEY_ORDER = [
    "Major Version",
    "Minor Version",
    "Application Version",
    "Features",
    "Show Content Ratings",
    "Music Folder",
    "Library Persistent ID",
    "Tracks",
    "Playlists",
]

# Fixed iTunes playlist dict key order.
_PLAYLIST_KEY_ORDER = [
    "Name",
    "Playlist ID",
    "Playlist Persistent ID",
    "All Items",
    "Playlist Items",
]

# Defaults used when no source file has been imported yet (tests, dev).
_DEFAULT_LIBRARY_PERSISTENT_ID = "STATION_STUDIO"
_DEFAULT_MUSIC_FOLDER = "file://localhost/"


# ---------- helpers -------------------------------------------------------


def _restore_metadata_value(key: str, value):
    if isinstance(value, dict) and value.get("__type__") == "datetime":
        try:
            return datetime.fromisoformat(value["value"])
        except (ValueError, KeyError):
            return value.get("value", value)
    if isinstance(value, str) and key in _DATETIME_KEYS:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _track_to_itunes_dict(track: Track, itunes_track_id: int) -> OrderedDict:
    """Convert a Track row back into an ordered iTunes plist track dict.

    Preserves the original iTunes XML field order by iterating through
    itunes_metadata (which was stored insertion-ordered at import).
    Column values (name, artist, ...) override metadata copies so any
    post-import edits are reflected.
    """
    meta = track.itunes_metadata or {}

    if not meta:
        # Minimal fallback for tracks without stored metadata (fixtures).
        d = OrderedDict()
        d["Track ID"] = itunes_track_id
        d["Name"] = track.name
        if track.artist:
            d["Artist"] = track.artist
        if track.album:
            d["Album"] = track.album
        if track.genre:
            d["Genre"] = track.genre
        if track.total_time is not None:
            d["Total Time"] = track.total_time
        d["Persistent ID"] = track.persistent_id
        d["Track Type"] = "File"
        return d

    column_overrides = {
        "Track ID": itunes_track_id,
        "Name": track.name,
        "Artist": track.artist,
        "Album": track.album,
        "Genre": track.genre,
        "Total Time": track.total_time,
        "Persistent ID": track.persistent_id,
    }

    d = OrderedDict()
    for k, v in meta.items():
        if k in column_overrides:
            val = column_overrides[k]
            if val is not None:
                d[k] = val
        else:
            d[k] = _restore_metadata_value(k, v)

    # Inject any column overrides that weren't in metadata (legacy imports).
    for k in ("Track ID", "Name", "Artist", "Album", "Genre", "Total Time",
              "Persistent ID"):
        if k not in d:
            val = column_overrides.get(k)
            if val is not None:
                d[k] = val

    return d


def _playlist_display_name(station: Station, week: int) -> str:
    return f"{station.name} {week:03d}"


def _playlist_persistent_id(station: Station, week: int) -> str:
    """Stable, iTunes-acceptable Playlist Persistent ID.

    Uppercase, underscores only. 'rebirth-classics' + week 1 → 'REBIRTH_CLASSICS_001'.
    """
    slug = station.slug.upper().replace("-", "_")
    return f"{slug}_{week:03d}"


def _build_ordered_plist(
    tracks_dict: OrderedDict,
    playlist_items: list[dict],
    *,
    station: Station,
    week: int,
    library_persistent_id: str,
    music_folder: str,
) -> OrderedDict:
    playlist = OrderedDict()
    for key in _PLAYLIST_KEY_ORDER:
        if key == "Name":
            playlist[key] = _playlist_display_name(station, week)
        elif key == "Playlist ID":
            playlist[key] = 1
        elif key == "Playlist Persistent ID":
            playlist[key] = _playlist_persistent_id(station, week)
        elif key == "All Items":
            playlist[key] = True
        elif key == "Playlist Items":
            playlist[key] = playlist_items

    plist_data = OrderedDict()
    for key in _TOP_LEVEL_KEY_ORDER:
        if key == "Major Version":
            plist_data[key] = 1
        elif key == "Minor Version":
            plist_data[key] = 1
        elif key == "Application Version":
            plist_data[key] = "10.3"
        elif key == "Features":
            plist_data[key] = 5
        elif key == "Show Content Ratings":
            plist_data[key] = True
        elif key == "Music Folder":
            plist_data[key] = music_folder
        elif key == "Library Persistent ID":
            plist_data[key] = library_persistent_id
        elif key == "Tracks":
            plist_data[key] = tracks_dict
        elif key == "Playlists":
            plist_data[key] = [playlist]

    return plist_data


def _itunes_format_xml(xml_bytes: bytes) -> bytes:
    """Post-process plistlib output to match iTunes's exact formatting."""
    text = xml_bytes.decode("utf-8")

    # Fix DOCTYPE: Apple → Apple Computer (iTunes legacy).
    text = text.replace(
        "//Apple//DTD PLIST 1.0//EN",
        "//Apple Computer//DTD PLIST 1.0//EN",
    )

    # Merge <key>…</key> + following value onto the same line, but only for
    # leaf value elements (never <dict>, <array>, </dict>, </array>).
    text = re.sub(
        r"(<key>[^<]+</key>)\n\s*"
        r"(<(?:string|integer|real|date|data)>[^<]*</(?:string|integer|real|date|data)>|<(?:true|false)/>)",
        r"\1\2",
        text,
    )

    return text.encode("utf-8")


# ---------- public API ----------------------------------------------------


def export_playlist(db: Session, playlist_id: int) -> bytes:
    """Export a generated Playlist as iTunes XML bytes.

    Raises ValueError if the playlist doesn't exist.
    """
    p = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if p is None:
        raise ValueError(f"Playlist {playlist_id} not found")

    station = p.station
    entries = (
        db.query(PlaylistEntry)
        .filter(PlaylistEntry.playlist_id == p.id)
        .order_by(PlaylistEntry.position)
        .all()
    )

    # Library metadata: read from Config, fall back to defaults.
    lib_pid_row = db.query(Config).filter(Config.key == "library_persistent_id").first()
    music_folder_row = db.query(Config).filter(Config.key == "music_folder").first()
    library_persistent_id = lib_pid_row.value if lib_pid_row else _DEFAULT_LIBRARY_PERSISTENT_ID
    music_folder = music_folder_row.value if music_folder_row else _DEFAULT_MUSIC_FOLDER

    # Build Tracks dict (unique tracks) and Playlist Items (in-order references).
    tracks_dict: OrderedDict = OrderedDict()
    playlist_items: list[dict] = []
    pid_to_id: dict[str, int] = {}
    next_synthetic_id = _SYNTHETIC_TRACK_ID_START

    for entry in entries:
        track = entry.track
        if track.persistent_id not in pid_to_id:
            if track.itunes_track_id is not None:
                itunes_id = track.itunes_track_id
            else:
                itunes_id = next_synthetic_id
                next_synthetic_id += 1
            pid_to_id[track.persistent_id] = itunes_id
            tracks_dict[str(itunes_id)] = _track_to_itunes_dict(track, itunes_id)
        else:
            itunes_id = pid_to_id[track.persistent_id]
        playlist_items.append({"Track ID": itunes_id})

    # iTunes orders the Tracks dict by numeric Track ID ascending.
    tracks_dict = OrderedDict(
        sorted(tracks_dict.items(), key=lambda kv: int(kv[0]))
    )

    plist_data = _build_ordered_plist(
        tracks_dict,
        playlist_items,
        station=station,
        week=p.week,
        library_persistent_id=library_persistent_id,
        music_folder=music_folder,
    )

    buf = BytesIO()
    plistlib.dump(plist_data, buf, fmt=plistlib.FMT_XML, sort_keys=False)

    xml_bytes = _itunes_format_xml(buf.getvalue())

    logger.info(
        "Exported playlist %d (%s week %d): %d entries, %d unique tracks, %d bytes",
        p.id, station.slug, p.week, len(playlist_items), len(tracks_dict), len(xml_bytes),
    )
    return xml_bytes
