"""Playlists made in the player ("My playlists"), kept in
`my-playlists.json` beside the settings.

Each entry remembers the track's id and also its file path, name and
artist, so a playlist survives the library being rebuilt: an entry whose
id has gone is found again by its path, and one whose file has gone
still shows (as missing) with its name.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_NAME = 200
MAX_ENTRIES = 40_000


def clean_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("A playlist needs a name")
    return " ".join(name.split())[:MAX_NAME]


def _entry(raw) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
        return None
    out = {"id": raw["id"]}
    for key in ("path", "name", "artist", "album"):
        value = raw.get(key)
        out[key] = value if isinstance(value, str) else None
    return out


class PlaylistStore:
    VERSION = 1

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._playlists: list[dict] = []
        self.warning: str | None = None
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("playlists"), list):
                raise ValueError("not a playlists file")
        except (OSError, ValueError) as exc:
            # Never overwrite someone's playlists with an empty list.
            backup = path.with_name(f"{path.stem}.broken{path.suffix}")
            try:
                path.replace(backup)
            except OSError:
                pass
            self.warning = f"Your playlists file couldn't be read ({exc}); it was moved to {backup}"
            logger.warning(self.warning)
            return
        for raw in data["playlists"]:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            try:
                name = clean_name(raw.get("name"))
            except ValueError:
                continue
            entries = [e for e in map(_entry, raw.get("entries") or []) if e]
            self._playlists.append({"id": raw["id"], "name": name, "entries": entries[:MAX_ENTRIES]})

    # ---- reading --------------------------------------------------------

    def all(self) -> list[dict]:
        with self._lock:
            return [{"id": p["id"], "name": p["name"], "entries": list(p["entries"])} for p in self._playlists]

    def get(self, pid: str) -> dict | None:
        with self._lock:
            for p in self._playlists:
                if p["id"] == pid:
                    return {"id": p["id"], "name": p["name"], "entries": list(p["entries"])}
        return None

    # ---- changing -------------------------------------------------------

    def _find(self, pid: str) -> dict:
        for p in self._playlists:
            if p["id"] == pid:
                return p
        raise LookupError("No such playlist")

    def _change(self, fn):
        """Apply `fn` to the list, save, and roll back if saving fails."""
        with self._lock:
            before = [{**p, "entries": list(p["entries"])} for p in self._playlists]
            try:
                result = fn()
                self._save()
            except BaseException:
                self._playlists = before
                raise
            return result

    def create(self, name, entries: list[dict]) -> str:
        name = clean_name(name)
        pid = "mine-" + secrets.token_hex(6)

        def add():
            self._playlists.append({"id": pid, "name": name, "entries": entries[:MAX_ENTRIES]})
            return pid
        return self._change(add)

    def rename(self, pid: str, name) -> None:
        name = clean_name(name)
        self._change(lambda: self._find(pid).__setitem__("name", name))

    def delete(self, pid: str) -> None:
        def remove():
            self._playlists.remove(self._find(pid))
        self._change(remove)

    def add(self, pid: str, entries: list[dict]) -> None:
        def append():
            p = self._find(pid)
            if len(p["entries"]) + len(entries) > MAX_ENTRIES:
                raise ValueError("That playlist is full")
            p["entries"].extend(entries)
        self._change(append)

    def set_entries(self, pid: str, entries: list[dict]) -> None:
        def replace():
            self._find(pid)["entries"] = entries[:MAX_ENTRIES]
        self._change(replace)

    def _save(self) -> None:
        if self.path is None:
            return
        payload = {"version": self.VERSION, "playlists": self._playlists}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        tmp.replace(self.path)


def m3u(name: str, tracks: list[dict], paths: list[str | None]) -> str:
    """An extended M3U playlist (UTF-8, so .m3u8 in spirit) of the tracks
    that have a file; other players read it with absolute paths."""
    lines = ["#EXTM3U", f"#PLAYLIST:{name}"]
    for t, path in zip(tracks, paths):
        if not path:
            continue
        seconds = (t.get("duration_ms") or 0) // 1000 or -1
        title = " - ".join(v for v in (t.get("artist"), t.get("name")) if v)
        lines.append(f"#EXTINF:{seconds},{' '.join(title.split())}")
        lines.append(path)
    return "\n".join(lines) + "\n"
