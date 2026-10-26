"""The station's clock: where in the week's playlist we should be right now.

A station-studio week is an ordered list of tracks with no times on it.
Station Server pins the first track to the start of the week (Monday
00:00 by default) and lets the tracks' lengths say when every other one
airs. So after a restart, a crash or a power cut, the station picks up
where the clock says, not from track one, and the running order can show
the time each track goes out.

A week that runs short loops; one that runs long is cut at the end of
the week by the next week's start.

Shows are pinned to times: "Saturday 20:00, Saturday Night Mix". At that
time the show (a recording, a playlist or a folder of recordings) plays
once through, straight away or after the playing song, and then the
music comes back where the clock says, as if it had played all along, so
the times in the running order stay true.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
    """How long a track plays: once measured, from where its sound starts
    to where it ends (see analysis.py); else from the playlist, else the
    file, else a guess."""
    measured = getattr(track, "measured", None)
    if measured is not None:
        return max(0.0, measured.end - measured.start)
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


def step_seconds(track: Track) -> float:
    """How far the clock moves on for a track: its length, less the
    crossfade into the next and plus its share of the jingles (see the
    runner's `step_adjust`)."""
    return max(1.0, track_seconds(track) + getattr(track, "step_adjust", 0.0))


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
    return position([step_seconds(t) for t in tracks], since)


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
        secs = step_seconds(t)
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


@dataclass(frozen=True)
class Show:
    id: str
    name: str
    source: str  # a recording, a playlist XML, or a folder of recordings
    days: tuple[int, ...]  # 0 = Monday; all seven for every day
    hour: int
    minute: int
    on_time: bool = True  # cut the playing song at the time; else start after it

    @classmethod
    def parse(cls, data) -> "Show":
        """From settings; raises ValueError with a message for the page."""
        if not isinstance(data, dict):
            raise ValueError("A show must be an object")
        name = str(data.get("name") or "").strip()[:80] or "Show"
        source = str(data.get("source") or "").strip()
        if not source:
            raise ValueError(f"Choose the recording or playlist for {name}")
        try:
            hour, minute = (int(x) for x in str(data.get("time", "")).split(":"))
        except ValueError:
            raise ValueError(f"Give {name} a time like 20:00") from None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Give {name} a time like 20:00")
        raw_days = data.get("days")
        days = sorted({int(d) for d in raw_days if str(d).isdigit() and 0 <= int(d) <= 6}) \
            if isinstance(raw_days, list) else []
        if not days:
            raise ValueError(f"Choose the days {name} goes out")
        sid = str(data.get("id") or "").strip()[:20] or uuid.uuid4().hex[:8]
        return cls(sid, name, source, tuple(days), hour, minute, bool(data.get("on_time", True)))

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "source": self.source, "days": list(self.days),
                "time": f"{self.hour:02d}:{self.minute:02d}", "on_time": self.on_time}

    def label(self) -> str:
        days = "Every day" if len(self.days) == 7 else (
            "Weekdays" if self.days == (0, 1, 2, 3, 4) else ", ".join(DAYS[d][:3] for d in self.days))
        return f"{days} {self.hour:02d}:{self.minute:02d}"

    def starts(self, after: datetime, before: datetime) -> list[datetime]:
        """When it starts between `after` (inclusive) and `before`, as
        timezone-aware local times. The times are wall-clock times, so a
        20:00 show stays at 20:00 across the clocks changing."""
        out = []
        day: date = (after - timedelta(days=1)).date()
        while day <= before.date():
            if day.weekday() in self.days:
                at = datetime(day.year, day.month, day.day, self.hour, self.minute).astimezone()
                if after <= at < before:
                    out.append(at)
            day += timedelta(days=1)
        return out


def plan(tracks: list[Track], index: int, started: datetime, now: datetime,
         shows: list[tuple[Show, float]], resume: Callable[[datetime, int], tuple[int, float]],
         count: int = 30, show_playing: tuple[Show, datetime, float] | None = None) -> list[dict]:
    """The running order from the track playing (`index`, heard from
    `started`), with each show at its time (`shows`: each with its length
    in seconds) and the music after it from `resume(time, index of the
    song the show interrupted)`. `show_playing`, when a show is on now:
    (show, when it started, its length)."""
    out: list[dict] = []
    n = len(tracks)
    offset = 0.0
    if show_playing:
        show, at, length = show_playing
        out.append({"show": show.name, "at": at.isoformat(timespec="seconds"), "seconds": round(length, 1)})
        t = at + timedelta(seconds=length)
        index, offset = resume(t, index)
    else:
        t = started
    horizon = now + timedelta(days=8)
    while len(out) < count and n and t < horizon:
        track = tracks[index % n]
        length = max(0.0, step_seconds(track) - offset)
        end = t + timedelta(seconds=length)
        upcoming = sorted(((at, show, slen) for show, slen in shows for at in show.starts(t, end)),
                          key=lambda x: x[0])
        row = {"index": index % n, "at": t.isoformat(timespec="seconds"), "title": track.name,
               "artist": track.artist, "seconds": round(length, 1)}
        if offset >= 1:
            row["from"] = round(offset, 1)
        if upcoming:
            at, show, slen = upcoming[0]
            if show.on_time:
                row["seconds"] = round((at - t).total_seconds(), 1)
                row["cut"] = True
            else:
                at = end
            if row["seconds"] > 0:  # a show on the hour cuts nothing
                out.append(row)
            if len(out) >= count:
                break
            out.append({"show": show.name, "at": at.isoformat(timespec="seconds"), "seconds": round(slen, 1)})
            t = at + timedelta(seconds=slen)
            index, offset = resume(t, index % n)
            continue
        out.append(row)
        t = end
        index, offset = (index + 1) % n, 0.0
    return out
