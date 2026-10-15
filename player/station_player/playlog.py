"""The play log: every track played, kept in `play-log.jsonl` beside the
settings, for music reporting (PRS for Music and PPL returns).

One JSON line per play: when it started, how long it was listened to,
and the track's details at the time. The export reads the composer,
label and ISRC from the file then, so they're always current, and counts
plays of at least `MIN_SECONDS`.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path

from station_player.tags import read_tags

logger = logging.getLogger(__name__)

MIN_SECONDS = 30  # shorter plays (skips) aren't reported
COLUMNS = ("Date", "Time", "Title", "Artist", "Album", "Composer", "Label", "ISRC",
           "Duration", "Played")


def _clock(seconds) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _cell(value) -> str:
    """Text for a spreadsheet cell; never a formula (a title like
    "=HYPERLINK(...)" stays text)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class PlayLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def add(self, started: datetime, seconds: float, track: dict) -> None:
        """Record one play. `track` holds title, artist, album,
        duration_ms and path (any may be None)."""
        if self.path is None:
            return
        entry = {"at": started.astimezone().isoformat(timespec="seconds"), "seconds": round(seconds, 1)}
        for key in ("title", "artist", "album", "duration_ms", "path"):
            entry[key] = track.get(key)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except OSError as exc:
                logger.warning("Could not write the play log: %s", exc)

    def entries(self, first: date, last: date) -> list[dict]:
        """Plays that started on days first..last (local time), oldest first."""
        out = []
        with self._lock:
            try:
                fh = self.path.open(encoding="utf-8") if self.path else None
            except FileNotFoundError:
                return []
            if fh is None:
                return []
            with fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                        at = datetime.fromisoformat(entry["at"])
                    except (ValueError, KeyError, TypeError):
                        continue  # a damaged line (say, cut short by a crash)
                    if first <= at.date() <= last:
                        entry["at"] = at
                        out.append(entry)
        out.sort(key=lambda e: e["at"])
        return out

    def csv(self, first: date, last: date) -> tuple[str, int]:
        """The plays as CSV for a music return, and how many there are."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(COLUMNS)
        details: dict[str, object] = {}
        count = 0
        for e in self.entries(first, last):
            if not isinstance(e.get("seconds"), (int, float)) or e["seconds"] < MIN_SECONDS:
                continue
            path = e.get("path")
            if isinstance(path, str) and path not in details:
                try:
                    details[path] = read_tags(Path(path)) if Path(path).is_file() else None
                except OSError:
                    details[path] = None
            tags = details.get(path) if isinstance(path, str) else None
            duration = e.get("duration_ms")
            writer.writerow([_cell(v) for v in (
                e["at"].strftime("%Y-%m-%d"), e["at"].strftime("%H:%M:%S"),
                e.get("title"), e.get("artist"), e.get("album"),
                tags and tags.composer, tags and tags.label, tags and tags.isrc,
                _clock(duration / 1000) if isinstance(duration, (int, float)) else "",
                _clock(e["seconds"]),
            )])
            count += 1
        return buf.getvalue(), count
