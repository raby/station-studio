"""Volume levelling: how loud each track is, so quiet and loud tracks can
be played at about the same volume.

A track's loudness comes from its ReplayGain / Sound Check tags when it
has them. Otherwise the browser measures it (it can decode every format
the player plays) and sends the result here to be remembered, keyed by
file path, size and modification time, in `loudness.json` beside the
settings.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ReplayGain 2 brings tracks to -18 LUFS, so a track gain of G dB means
# the track itself measures about -18 - G LUFS.
REPLAYGAIN_REFERENCE = -18.0
_MIN_LUFS, _MAX_LUFS = -70.0, 6.0
_MAX_ENTRIES = 50_000


def from_tags(gain_db: float | None, peak: float | None) -> dict | None:
    if gain_db is None:
        return None
    return {"lufs": round(REPLAYGAIN_REFERENCE - gain_db, 2), "peak": peak, "source": "tags"}


def _finite(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f"{name} out of range") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a number")
    return value


def valid(lufs, peak) -> tuple[float, float | None]:
    """Check a measurement sent by the browser; raises ValueError."""
    lufs = _finite(lufs, "lufs")
    if not _MIN_LUFS <= lufs <= _MAX_LUFS:
        raise ValueError("lufs out of range")
    if peak is not None:
        peak = _finite(peak, "peak")
        if not 0 < peak <= 16:
            raise ValueError("peak out of range")
        peak = round(peak, 6)
    return round(lufs, 2), peak


class LoudnessStore:
    """Measured loudness by file path, size and modification time."""

    VERSION = 1

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()  # one writer of the file at a time
        self._entries: dict[str, list] = {}
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("version") == self.VERSION:
                    entries = data.get("entries")
                    if isinstance(entries, dict):
                        self._entries = entries
            except (OSError, ValueError):
                pass

    @staticmethod
    def _stamp(file: Path) -> list | None:
        try:
            st = file.stat()
        except OSError:
            return None
        return [st.st_size, st.st_mtime_ns]

    def get(self, file: Path) -> dict | None:
        stamp = self._stamp(file)
        with self._lock:
            hit = self._entries.get(str(file))
        if stamp is None or not isinstance(hit, list) or len(hit) != 3 or hit[0] != stamp:
            return None
        lufs, peak = hit[1], hit[2]
        try:
            lufs, peak = valid(lufs, peak)
        except ValueError:
            return None
        return {"lufs": lufs, "peak": peak, "source": "measured"}

    def put(self, file: Path, lufs: float, peak: float | None) -> None:
        stamp = self._stamp(file)
        if stamp is None:
            return
        # Snapshot and write under one lock, so an older snapshot can
        # never be written over a newer one.
        with self._write_lock:
            with self._lock:
                self._entries.pop(str(file), None)
                self._entries[str(file)] = [stamp, lufs, peak]
                while len(self._entries) > _MAX_ENTRIES:  # oldest first
                    del self._entries[next(iter(self._entries))]
                payload = {"version": self.VERSION, "entries": dict(self._entries)}
            if self.path is None:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as exc:
                logger.warning("Could not save loudness measurements: %s", exc)
