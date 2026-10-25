"""Turn a station-studio export into the list of files to put on air.

Reading the XML, finding moved files and rewriting paths are station-player's
job already (`station_player.library`); this module only picks the playlist
and keeps the tracks that can actually be played, in order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from station_player.library import Library, Track, name_key


class PlaylistError(Exception):
    """The playlist can't be played: missing, unreadable, or nothing in it."""


@dataclass
class Queue:
    name: str
    tracks: list[Track]
    # Tracks in the playlist whose audio file couldn't be found.
    missing: list[Track] = field(default_factory=list)


def load_queue(
    xml: Path,
    music_folders: list[str] | None = None,
    remaps: list[dict] | None = None,
    playlist: str | None = None,
) -> Queue:
    """The tracks of one playlist in `xml`, in play order.

    A station-studio export holds one playlist (the week); an iTunes
    library export can hold many, so `playlist` names the one wanted.
    `music_folders` are searched for files that have moved since the XML
    was written, and `remaps` rewrite path prefixes (a Mac library played
    on a PC), exactly as in station-player.
    """
    xml = Path(xml).expanduser()
    if not xml.is_file():
        raise PlaylistError(f"Playlist not found: {xml}")
    lib = Library()
    lib.load([str(xml)], music_folders or [], remaps or [])
    lists = [pl for pl in lib.playlists.values() if pl.kind == "itunes"]
    if not lists:
        reason = lib.errors[0] if lib.errors else f"No playlists in {xml.name}"
        raise PlaylistError(reason)

    if playlist:
        chosen = [pl for pl in lists if name_key(pl.name) == name_key(playlist)]
        if not chosen:
            names = ", ".join(sorted(pl.name for pl in lists))
            raise PlaylistError(f'No playlist called "{playlist}" in {xml.name} (it has: {names})')
        pl = chosen[0]
    elif len(lists) == 1:
        pl = lists[0]
    else:
        names = ", ".join(sorted(pl.name for pl in lists))
        raise PlaylistError(f"{xml.name} has several playlists; choose one with --playlist ({names})")

    tracks = [lib.tracks[t] for t in pl.track_ids]
    playable = [t for t in tracks if t.available]
    if not playable:
        raise PlaylistError(f'None of the {len(tracks)} tracks in "{pl.name}" could be found')
    return Queue(name=pl.name, tracks=playable, missing=[t for t in tracks if not t.available])


def now_playing(track: Track) -> str:
    """The title listeners see: "Artist - Title", or just the title."""
    return f"{track.artist} - {track.name}" if track.artist else track.name
