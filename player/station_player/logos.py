"""Station logos kept on this computer.

A logo is saved once (when a station is added from the finder, found
for it, or chosen from a file) and named after its contents, so the
page, the now-playing bar and the system's media panel show it from the
player itself, with no request to the station's website each time.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024
TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
         "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico"}
SERVE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
               "ico": "image/x-icon"}
GRACE_SECONDS = 120
NAME = re.compile(r"[0-9a-f]{16}\.(?:png|jpg|gif|webp|ico)")


def sniff(data: bytes) -> str | None:
    """The image type its first bytes show, whatever it claims to be."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return None


# A station's logo published beside its what's-on feed by Station Server
# (rebirth-radio-logo.png): the only kind of name a feed may give.
FEED_NAME = re.compile(r"[a-z0-9-]+-logo\.(?:png|jpg|gif|webp|ico)")


def feed_name(slug: str, ctype: str) -> str:
    """The file a station's logo is published as, beside its feed."""
    return f"{slug}-logo.{TYPES.get(ctype, 'png')}"


def is_name(value) -> bool:
    return isinstance(value, str) and NAME.fullmatch(value) is not None


class LogoStore:
    def __init__(self, folder: Path, grace: float = GRACE_SECONDS) -> None:
        self.folder = folder
        self.grace = grace
        self._lock = threading.Lock()  # saving and pruning, one at a time

    def save(self, data: bytes) -> str:
        """Keep an image; returns its name. ValueError if it isn't a small
        PNG, JPEG, GIF, WebP or icon."""
        ctype = sniff(data)
        if ctype is None:
            raise ValueError("That isn't a PNG, JPEG, GIF or WebP picture")
        if len(data) > MAX_BYTES:
            raise ValueError("That picture is too big (the limit is 256 KB)")
        name = f"{hashlib.sha256(data).hexdigest()[:16]}.{TYPES[ctype]}"
        path = self.folder / name
        with self._lock:
            if path.is_file() and path.stat().st_size == len(data):
                os.utime(path)  # just used again: see prune
                return name
            self.folder.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.folder, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        return name

    def get(self, name: str) -> tuple[bytes, str] | None:
        if not is_name(name):
            return None
        try:
            data = (self.folder / name).read_bytes()
        except OSError:
            return None
        return data, SERVE_TYPES[name.rsplit(".", 1)[1]]

    def has(self, name: str) -> bool:
        """Whether that logo is kept; marks it just used (see prune)."""
        if not is_name(name):
            return False
        with self._lock:
            try:
                os.utime(self.folder / name)
            except OSError:
                return False
        return True

    def prune(self, keep: set[str]) -> None:
        """Delete logos nothing uses any more. Ones saved in the last
        `grace` seconds stay: a station may be about to take them."""
        try:
            files = list(self.folder.iterdir())
        except OSError:
            return
        now = time.time()
        with self._lock:
            self._prune(files, keep, now)

    def _prune(self, files: list[Path], keep: set[str], now: float) -> None:
        for path in files:
            if is_name(path.name) and path.name not in keep:
                try:
                    if now - path.stat().st_mtime < self.grace:
                        continue
                    path.unlink()
                except OSError as exc:
                    logger.debug("Could not delete %s: %s", path, exc)
