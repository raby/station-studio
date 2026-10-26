"""Which week's playlist is on air, when the folder holds several.

station-studio exports each week as `<number>.xml` (`012.xml`, then
`013.xml`), with no dates in it, and the next week is often exported days
ahead. So a higher week number doesn't go on air the moment it appears:
it waits for the start of the next week (the station's week start,
Monday 00:00 by default). A week exported late, after its week has
already begun, goes on straight away. Edits to the week on air are
picked up at once (onair.py watches the file).

What it remembers, per station, in `weeks.json`: the week on air, the
week start it went on at, and when each week file was first seen.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from station_server.schedule import WeekStart

logger = logging.getLogger(__name__)

_WEEK_RE = re.compile(r"(\d+)\.xml", re.I)


def week_files(folder: Path) -> dict[str, tuple[int, Path]]:
    """The numbered week exports in `folder`: name -> (week number, path).
    Archived versions (`012-v2.xml`) aren't weeks."""
    out = {}
    for p in folder.glob("*.xml"):
        m = _WEEK_RE.fullmatch(p.name)
        if m:
            out[p.name] = (int(m.group(1)), p)
    return out


def _week_start(week: WeekStart, at: datetime) -> datetime:
    """The latest week start at or before `at` (local, timezone-aware)."""
    return week.latest(at.replace(tzinfo=None)).astimezone()


class WeekPicker:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._state = self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("couldn't remember the week on air: %s", exc)

    def pick(self, sid: str, folder: Path, week: WeekStart, now: datetime | None = None,
             remember: bool = True) -> tuple[Path | None, tuple[Path, datetime] | None]:
        """(the week to play now, (the next week, when it goes on) or None).
        The first is None when the folder has no numbered weeks."""
        now = now or datetime.now().astimezone()
        files = week_files(folder)
        if not files:
            return None, None
        start = _week_start(week, now)
        with self._lock:
            st = self._state.get(sid)
            st = dict(st) if isinstance(st, dict) else {}
            seen = dict(st.get("seen") or {})
            for name, (_, path) in files.items():
                if name not in seen:
                    try:  # its export time, when it's older than now
                        first = min(now, datetime.fromtimestamp(path.stat().st_mtime).astimezone())
                    except OSError:
                        first = now
                    seen[name] = first.isoformat()
            seen = {k: v for k, v in seen.items() if k in files}
            order = sorted(files, key=lambda n: files[n][0])
            current = st.get("file") if st.get("file") in files else None
            since = _parse(st.get("since")) if current else None
            if current is None:
                # Starting afresh: the newest week that was there before this week began.
                ready = [n for n in order if _parse(seen[n]) < start]
                current, since = (ready or order)[-1], start
            elif since is None or since < start:
                # One or more weeks have begun since this one went on: move
                # on that many weeks, as far as there are exports.
                steps = max(1, round((start - (since or start)).total_seconds() / (7 * 86400)))
                later = [n for n in order if files[n][0] > files[current][0]]
                if later:
                    current, since = later[min(steps, len(later)) - 1], start
            later = [n for n in order if files[n][0] > files[current][0]]
            upcoming = None
            if later:
                upcoming = (files[later[0]][1], _week_start(week, now + timedelta(days=7)))
            new = {"file": current, "since": since.isoformat() if since else start.isoformat(), "seen": seen}
            if remember and new != self._state.get(sid):
                self._state[sid] = new
                self._write()
            return files[current][1], upcoming

    def forget(self, sid: str) -> None:
        with self._lock:
            if self._state.pop(sid, None) is not None:
                self._write()


def _parse(text) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None
