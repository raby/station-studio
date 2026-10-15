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
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".aac", ".mp4", ".flac", ".wav", ".ogg", ".oga",
    ".opus", ".webm", ".aif", ".aiff",
})

# Leading track numbers in filenames: "01 ", "01 - ", "1. ", "A2 - ".
_TRACK_NO_RE = re.compile(r"^\s*(?:[A-Da-d]?\d{1,3})\s*(?:[-.)_]\s*|\s+)")


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
    """Rewrite a path prefix, e.g. a Mac library path onto a PC drive."""
    as_text = path.as_posix()
    for remap in remaps:
        src = str(remap.get("from", "")).replace("\\", "/").rstrip("/")
        dst = str(remap.get("to", "")).replace("\\", "/").rstrip("/")
        if src and (as_text == src or as_text.startswith(src + "/")):
            return Path(dst + as_text[len(src):])
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


class Library:
    """In-memory index of everything playable. Rebuilt by `load()`."""

    def __init__(self) -> None:
        self.tracks: dict[str, Track] = {}
        self.playlists: dict[str, Playlist] = {}
        self.errors: list[str] = []
        # filename (lowercased) -> path, from scanned music folders; used
        # to find tracks whose iTunes Location points somewhere else.
        self._by_filename: dict[str, Path] = {}

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
        for raw in music_folders:
            folder = Path(raw).expanduser()
            if not folder.is_dir():
                self.errors.append(f"Music folder not found: {folder}")
                continue
            files = scan_audio_files(folder)
            folder_files.append((folder, files))
            for f in files:
                self._by_filename.setdefault(f.name.lower(), f)

        for raw in playlist_sources:
            src = Path(raw).expanduser()
            if src.is_dir():
                xmls = sorted(src.rglob("*.xml"), key=lambda p: p.as_posix().lower())
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
        if candidate is not None:
            candidate = apply_remaps(candidate, remaps)
            if candidate.is_file():
                return candidate
            found = self._by_filename.get(candidate.name.lower())
            if found:
                return found
        return None

    def _load_itunes_xml(self, xml: Path, remaps: list[dict], quiet: bool = False) -> None:
        try:
            with xml.open("rb") as fh:
                data = plistlib.load(fh)
        except Exception as exc:  # malformed / not a plist
            if not quiet:
                self.errors.append(f"Could not read {xml.name}: {exc}")
            return
        if not isinstance(data, dict) or "Tracks" not in data:
            if not quiet:
                self.errors.append(f"{xml.name} is not an iTunes library XML")
            return

        # iTunes Track ID (scoped to this file) -> our track id
        local_ids: dict[int, str] = {}
        for key, raw in (data.get("Tracks") or {}).items():
            if not isinstance(raw, dict):
                continue
            path = self._resolve(raw, remaps)
            pid = raw.get("Persistent ID") or f"{xml}:{key}"
            tid = _short_hash("itunes", str(pid), str(path or ""))
            if tid not in self.tracks:
                self.tracks[tid] = Track(
                    id=tid,
                    name=raw.get("Name") or (path.stem if path else "Unknown"),
                    artist=raw.get("Artist"),
                    album=raw.get("Album"),
                    genre=raw.get("Genre"),
                    duration_ms=raw.get("Total Time"),
                    path=path,
                )
            track_id = raw.get("Track ID")
            if isinstance(track_id, int):
                local_ids[track_id] = tid
            elif str(key).isdigit():
                local_ids[int(key)] = tid

        playlists = [
            p for p in (data.get("Playlists") or [])
            if isinstance(p, dict)
            and not p.get("Master")
            and not p.get("Folder")
            and "Distinguished Kind" not in p
            and p.get("Playlist Items")
        ]
        if not playlists:
            # A library with no usable playlists: offer every track.
            playlists = [{
                "Name": xml.stem,
                "Playlist Items": [{"Track ID": k} for k in local_ids],
            }]

        for p in playlists:
            name = p.get("Name") or xml.stem
            pl = Playlist(
                id=_short_hash("pl", str(xml), str(p.get("Playlist Persistent ID") or name)),
                name=name,
                kind="itunes",
                source=str(xml),
            )
            for item in p.get("Playlist Items", []):
                tid = local_ids.get(item.get("Track ID"))
                if tid:
                    pl.track_ids.append(tid)
            if pl.id in self.playlists:  # same playlist loaded twice
                continue
            self.playlists[pl.id] = pl

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
