"""The station's clock: where in the week's playlist we should be right now.

A station-studio week is an ordered list of tracks with no times on it.
Station Server pins the first track to the start of the week (Monday
00:00 by default) and lets the tracks' lengths say when every other one
airs. So after a restart, a crash or a power cut, the station picks up
where the clock says, not from track one, and the running order can show
the time each track goes out.

A week that runs short loops; one that runs long is cut at the end of
the week by the next week's start.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from station_player.library import Track
from station_player.tags import read_tags

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
# Used for a track whose length neither the playlist nor the file gives.
GUESS_SECONDS = 210.0


@dataclass(frozen=True)
class WeekStart:
    day: int = 0  # 0 = Monday
    hour: int = 0
    minute: int = 0

    @classmethod
    def parse(cls, data) -> "WeekStart":
        """From settings: {"day": 0, "time": "00:00"}; anything odd -> Monday 00:00."""
        if not isinstance(data, dict):
            return cls()
        try:
            day = int(data.get("day", 0))
            hour, minute = (int(x) for x in str(data.get("time", "00:00")).split(":"))
        except (TypeError, ValueError):
            return cls()
        if not (0 <= day <= 6 and 0 <= hour <= 23 and 0 <= minute <= 59):
            return cls()
        return cls(day, hour, minute)

    def to_json(self) -> dict:
        return {"day": self.day, "time": f"{self.hour:02d}:{self.minute:02d}"}

    def label(self) -> str:
        return f"{DAYS[self.day]} {self.hour:02d}:{self.minute:02d}"

    def latest(self, now: datetime) -> datetime:
        """The most recent start of the week at or before `now` (local time)."""
        start = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        start -= timedelta(days=(now.weekday() - self.day) % 7)
        if start > now:
            start -= timedelta(days=7)
        return start


def track_seconds(track: Track) -> float:
    """How long a track plays: from the playlist, else the file, else a guess."""
    if track.duration_ms:
        return track.duration_ms / 1000
    if track.path is not None:
        try:
            ms = read_tags(track.path).duration_ms
        except OSError:
            ms = None
        if ms:
            track.duration_ms = ms  # remembered for next time
            return ms / 1000
    return GUESS_SECONDS


def position(lengths: list[float], since_start: float) -> tuple[int, float]:
    """(track index, seconds into it) for a point `since_start` seconds into
    the week, looping a playlist shorter than that."""
    total = sum(lengths)
    if total <= 0:
        return 0, 0.0
    t = since_start % total
    for i, length in enumerate(lengths):
        if t < length:
            return i, t
        t -= length
    return 0, 0.0


def where_now(tracks: list[Track], week: WeekStart, now: datetime) -> tuple[int, float]:
    """Which track should be on air at `now` (local, timezone-aware), and how
    far into it. The week starts at the wall-clock time, and the time since
    is real elapsed time, so a clock change mid-week doesn't jump an hour."""
    anchor = week.latest(now.replace(tzinfo=None)).astimezone()
    since = (now - anchor).total_seconds()
    return position([track_seconds(t) for t in tracks], since)


def running_order(tracks: list[Track], index: int, started: datetime, count: int = 40,
                  loop: bool = True) -> list[dict]:
    """The next `count` tracks from `index` (the one playing, which started
    at `started`), each with the time it goes out."""
    out = []
    at = started
    n = len(tracks)
    i = index
    for _ in range(min(count, n if not loop else count)):
        if i >= n:
            if not loop:
                break
            i = 0
        t = tracks[i]
        secs = track_seconds(t)
        out.append({
            "index": i,
            "at": at.isoformat(timespec="seconds"),
            "title": t.name,
            "artist": t.artist,
            "seconds": round(secs, 1),
        })
        at += timedelta(seconds=secs)
        i += 1
    return out
