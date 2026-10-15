"""Build a playable library from iTunes XML files, audio folders and config.

Everything the server can play goes through `Library.tracks`: a dict of
opaque track id -> Track. The audio endpoint only serves paths found
there, so the player never becomes a general file server.
"""
from __future__ import annotations

import hashlib
import logging
import plistlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".aac", ".mp4", ".flac", ".wav", ".ogg", ".oga",
    ".opus", ".webm", ".aif", ".aiff",
})

# Leading track numbers in filenames: "01 ", "01 - ", "1. ", "A2 - ".
# A bare number followed only by a space is stripped just when it has a
# leading zero, so "99 Luftballons" and "7 Seconds" keep their titles.
_TRACK_NO_RE = re.compile(r"^\s*(?:[A-Da-d]?\d{1,3}\s*[-.)_]\s*|0\d{1,2}\s+)")


def name_key(name: str) -> str:
    """Compare file and folder names the way people expect: ignoring case
    (Windows and macOS do) and Unicode composition (macOS writes "é" as
    two code points, most other systems as one)."""
    return unicodedata.normalize("NFC", name).casefold()


@dataclass
class Track:
    id: str
    name: str
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    duration_ms: int | None = None
    path: Path | None = None  # resolved local file, None when missing

    @property
    def available(self) -> bool:
        return self.path is not None

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "artist": self.artist,
            "album": self.album,
            "genre": self.genre,
            "duration_ms": self.duration_ms,
            "available": self.available,
        }


@dataclass
class Playlist:
    id: str
    name: str
    kind: str  # "itunes" | "folder"
    source: str  # the file or folder it came from
    track_ids: list[str] = field(default_factory=list)

    def summary(self, tracks: dict[str, Track]) -> dict:
        missing = sum(1 for t in self.track_ids if not tracks[t].available)
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "count": len(self.track_ids),
            "missing": missing,
        }


def _short_hash(*parts: str) -> str:
    h = hashlib.sha1("\0".join(parts).encode("utf-8", "surrogateescape"))
    return h.hexdigest()[:16]


def location_to_path(location: str) -> Path | None:
    """Turn an iTunes `Location` (a file:// URL) into a local Path.

    Handles the macOS form (file:///Users/...), the Windows form
    (file://localhost/C:/...) and percent-encoding. Non-file URLs
    return None.
    """
    if not location:
        return None
    parsed = urlparse(location)
    if parsed.scheme and parsed.scheme != "file":
        return None
    raw = unquote(parsed.path if parsed.scheme else location)
    # Windows drive letter: "/C:/Music/x.mp3" -> "C:/Music/x.mp3"
    if re.match(r"^/[A-Za-z]:[/\\]", raw):
        raw = raw[1:]
    elif parsed.netloc and parsed.netloc not in ("", "localhost"):
        raw = f"//{parsed.netloc}{raw}"  # UNC share
    return Path(raw)


def apply_remaps(path: Path, remaps: list[dict]) -> Path:
    """Rewrite a path prefix, e.g. a Mac library path onto a PC drive.

    Matches whole folder names, ignoring case and Unicode composition, so
    "c:/users/rob/music" rewrites "C:/Users/Rob/Music/..." too.
    """
    parts = path.as_posix().split("/")
    for remap in remaps:
        src = str(remap.get("from", "")).replace("\\", "/").rstrip("/")
        dst = str(remap.get("to", "")).replace("\\", "/").rstrip("/")
        if not src:
            continue
        src_parts = src.split("/")
        head = parts[:len(src_parts)]
        if len(head) == len(src_parts) and all(
            name_key(a) == name_key(b) for a, b in zip(head, src_parts)
        ):
            rest = parts[len(src_parts):]
            return Path("/".join([dst, *rest]) if rest else dst)
    return path


def title_from_filename(path: Path) -> tuple[str, str | None]:
    """Best-effort (title, artist) from "01 - Artist - Title.mp3"."""
    stem = _TRACK_NO_RE.sub("", path.stem, count=1) or path.stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip() or None
    return stem.strip(), None


def scan_audio_files(folder: Path) -> list[Path]:
    files = [
        p for p in folder.rglob("*")
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file()
        and not p.name.startswith(".")
    ]
    return sorted(files, key=lambda p: p.as_posix().lower())


class _NotItunes(Exception):
    pass


def _text(value) -> str | None:
    """iTunes text fields, tolerating numbers (e.g. an album called 1999)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


class Library:
    """In-memory index of everything playable. Rebuilt by `load()`."""

    def __init__(self) -> None:
        self.tracks: dict[str, Track] = {}
        self.playlists: dict[str, Playlist] = {}
        self.errors: list[str] = []
        # filename (lowercased) -> paths, from scanned music folders; used
        # to find tracks whose iTunes Location points somewhere else.
        self._by_filename: dict[str, list[Path]] = {}

    # ---- loading --------------------------------------------------------

    def load(
        self,
        playlist_sources: list[str],
        music_folders: list[str],
        remaps: list[dict] | None = None,
    ) -> None:
        self.tracks.clear()
        self.playlists.clear()
        self.errors.clear()
        self._by_filename.clear()
        remaps = remaps or []

        folder_files: list[tuple[Path, list[Path]]] = []
        indexed: set[Path] = set()  # overlapping folders must not double-count
        for raw in music_folders:
            folder = Path(raw).expanduser()
            if not folder.is_dir():
                self.errors.append(f"Music folder not found: {folder}")
                continue
            try:
                files = scan_audio_files(folder)
            except OSError as exc:
                self.errors.append(f"Could not read music folder {folder}: {exc}")
                continue
            folder_files.append((folder, files))
            for f in files:
                key = f.resolve()
                if key in indexed:
                    continue
                indexed.add(key)
                self._by_filename.setdefault(name_key(f.name), []).append(f)

        for raw in playlist_sources:
            src = Path(raw).expanduser()
            if src.is_dir():
                try:
                    xmls = sorted(src.rglob("*.xml"), key=lambda p: p.as_posix().lower())
                except OSError as exc:
                    self.errors.append(f"Could not read playlist folder {src}: {exc}")
                    continue
                for xml in xmls:
                    self._load_itunes_xml(xml, remaps, quiet=True)
            elif src.is_file():
                self._load_itunes_xml(src, remaps)
            else:
                self.errors.append(f"Playlist source not found: {src}")

        for folder, files in folder_files:
            self._add_folder_playlist(folder, files)

    def _resolve(self, raw: dict, remaps: list[dict]) -> Path | None:
        loc = raw.get("Location")
        candidate = location_to_path(loc) if isinstance(loc, str) else None
        # Only audio is ever playable: a crafted XML pointing at, say,
        # ~/.ssh/id_rsa or an .html file must not make it servable.
        if candidate is not None and candidate.suffix.lower() in AUDIO_EXTENSIONS:
            candidate = apply_remaps(candidate, remaps)
            if candidate.is_file():
                return candidate
            return self._find_moved(candidate)
        return None

    def _find_moved(self, original: Path) -> Path | None:
        """Find a moved file by name, refusing to guess between namesakes.

        "01 Intro.mp3" exists on many albums, so a bare name match is only
        trusted when it is unique; otherwise the parent folders (album,
        then artist) must match too.
        """
        matches = self._by_filename.get(name_key(original.name), [])
        for depth in range(0, 3):
            if depth:
                want = [name_key(p.name) for p in original.parents[:depth]]
                matches = [
                    m for m in matches
                    if [name_key(p.name) for p in m.parents[:depth]] == want
                ]
            if len(matches) == 1:
                return matches[0]
            if not matches:
                return None
        return None

    def _load_itunes_xml(self, xml: Path, remaps: list[dict], quiet: bool = False) -> None:
        """Load one XML. Any file that isn't a usable iTunes library is
        reported (or skipped quietly when found by a folder scan), never
        allowed to take the whole library down with it."""
        try:
            with xml.open("rb") as fh:
                data = plistlib.load(fh)
            tracks, playlists = self._parse_itunes(xml, data, remaps)
        except _NotItunes:
            if not quiet:
                self.errors.append(f"{xml.name} is not an iTunes library XML")
            return
        except Exception as exc:  # malformed plist, unreadable file
            if not quiet:
                self.errors.append(f"Could not read {xml.name}: {exc}")
            return
        for t in tracks:
            self.tracks.setdefault(t.id, t)
        for pl in playlists:
            self.playlists.setdefault(pl.id, pl)  # same playlist loaded twice

    def _parse_itunes(self, xml: Path, data, remaps: list[dict]) -> tuple[list[Track], list[Playlist]]:
        if not isinstance(data, dict) or not isinstance(data.get("Tracks"), dict):
            raise _NotItunes()

        tracks: list[Track] = []
        # iTunes Track ID (scoped to this file) -> our track id
        local_ids: dict[int, str] = {}
        for key, raw in data["Tracks"].items():
            if not isinstance(raw, dict):
                continue
            path = self._resolve(raw, remaps)
            pid = _text(raw.get("Persistent ID")) or f"{xml}:{key}"
            tid = _short_hash("itunes", pid, str(path or ""))
            duration = raw.get("Total Time")
            tracks.append(Track(
                id=tid,
                name=_text(raw.get("Name")) or (path.stem if path else "Unknown"),
                artist=_text(raw.get("Artist")),
                album=_text(raw.get("Album")),
                genre=_text(raw.get("Genre")),
                duration_ms=duration if isinstance(duration, int) and not isinstance(duration, bool) else None,
                path=path,
            ))
            track_id = raw.get("Track ID")
            if isinstance(track_id, int):
                local_ids[track_id] = tid
            elif str(key).isdigit():
                local_ids[int(key)] = tid

        raw_playlists = data.get("Playlists")
        candidates = [
            p for p in (raw_playlists if isinstance(raw_playlists, list) else [])
            if isinstance(p, dict)
            and not p.get("Master")
            and not p.get("Folder")
            and "Distinguished Kind" not in p
            and isinstance(p.get("Playlist Items"), list)
            and p["Playlist Items"]
        ]
        if not candidates:
            # A library with no usable playlists: offer every track.
            candidates = [{
                "Name": xml.stem,
                "Playlist Items": [{"Track ID": k} for k in local_ids],
            }]

        playlists: list[Playlist] = []
        for p in candidates:
            name = _text(p.get("Name")) or xml.stem
            pl = Playlist(
                id=_short_hash("pl", str(xml), _text(p.get("Playlist Persistent ID")) or name),
                name=name,
                kind="itunes",
                source=str(xml),
            )
            for item in p["Playlist Items"]:
                if not isinstance(item, dict):
                    continue
                ref = item.get("Track ID")
                tid = local_ids.get(ref) if isinstance(ref, int) else None
                if tid:
                    pl.track_ids.append(tid)
            playlists.append(pl)
        return tracks, playlists

    def _add_folder_playlist(self, folder: Path, files: list[Path]) -> None:
        pl = Playlist(
            id=_short_hash("folder", str(folder)),
            name=folder.name or str(folder),
            kind="folder",
            source=str(folder),
        )
        for f in files:
            tid = _short_hash("file", str(f))
            if tid not in self.tracks:
                title, artist = title_from_filename(f)
                album = f.parent.name if f.parent != folder else None
                self.tracks[tid] = Track(id=tid, name=title, artist=artist, album=album, path=f)
            pl.track_ids.append(tid)
        self.playlists[pl.id] = pl

    # ---- queries --------------------------------------------------------

    def playlist_summaries(self) -> list[dict]:
        items = [p.summary(self.tracks) for p in self.playlists.values()]
        return sorted(items, key=lambda s: (s["kind"] != "itunes", s["name"].lower()))

    def playlist_detail(self, playlist_id: str) -> dict | None:
        pl = self.playlists.get(playlist_id)
        if pl is None:
            return None
        out = pl.summary(self.tracks)
        out["tracks"] = [self.tracks[t].to_json() for t in pl.track_ids]
        return out
