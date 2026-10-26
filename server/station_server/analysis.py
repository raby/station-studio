"""Measuring tracks: how loud each one is, and the silence at either end.

Many tracks carry no ReplayGain or Sound Check tags, so without measuring
they go out at whatever level they were mastered. And most have a second
or two of silence at the start or end, which on air sounds like dead air
between songs. ffmpeg reads each file once, in the background and at low
priority, for:

- its loudness (EBU R128) and true peak, for volume levelling when the
  file has no levelling tags of its own;
- where the sound starts and ends, so the silence either side is left out.

The results are remembered in `analysis.json`, keyed by path, size and
modification time, so each file is measured once. A track is played as it
is until it has been measured.

The clock (schedule.track_seconds) uses the trimmed length once known, so
trimming doesn't push the station ahead of the week's clock.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from station_player.library import Track

logger = logging.getLogger(__name__)

# Quieter than this counts as silence. Low enough that a quiet intro or
# the tail of a fade is kept; only real digital or near-digital silence goes.
SILENCE_DB = -60
SILENCE_MIN_SECONDS = 0.3
# ReplayGain 2 brings tracks to -18 LUFS; measured tracks are levelled to match.
REFERENCE_LUFS = -18.0
_MAX_ENTRIES = 50_000

_LOW_PRIORITY = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FLOAT = r"(-?\d+(?:\.\d+)?)"


@dataclass(frozen=True)
class Measured:
    lufs: float | None  # None for a silent track
    peak_db: float | None  # true peak, dBFS
    start: float  # seconds: where the sound starts
    end: float  # seconds: where it ends

    @property
    def gain_db(self) -> float | None:
        """The ReplayGain-style gain that brings it to the reference."""
        return None if self.lufs is None else REFERENCE_LUFS - self.lufs

    @property
    def peak(self) -> float | None:
        """True peak as a linear amplitude (1.0 = full scale), like tags give."""
        return None if self.peak_db is None else 10 ** (self.peak_db / 20)


def parse(stderr: str) -> Measured | None:
    """Read ffmpeg's silencedetect and ebur128 report."""
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not times:
        return None
    h, m, s = times[-1]
    duration = int(h) * 3600 + int(m) * 60 + float(s)
    if duration <= 0:
        return None
    starts = [float(x) for x in re.findall(r"silence_start: " + _FLOAT, stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: " + _FLOAT, stderr)]
    start, end = 0.0, duration
    if starts and starts[0] <= 0.05 and ends:
        start = ends[0]  # silence from the very start
    if starts and (len(ends) < len(starts) or ends[-1] >= duration - 0.05):
        end = starts[-1]  # silence to the very end
    if end - start < 1.0:  # (nearly) all silence: play it as it is
        start, end = 0.0, duration
    lufs = re.search(r"I:\s+" + _FLOAT + r" LUFS", stderr)
    peak = re.search(r"Peak:\s+" + _FLOAT + r" dBFS", stderr)
    lufs_v = float(lufs.group(1)) if lufs else None
    if lufs_v is not None and not (-70 < lufs_v < 10):
        lufs_v = None
    peak_v = float(peak.group(1)) if peak else None
    if peak_v is not None and not math.isfinite(peak_v):
        peak_v = None
    return Measured(lufs_v, peak_v, round(start, 3), round(end, 3))


def measure(ffmpeg: str, path: Path) -> Measured | None:
    """Read the whole file once (a few seconds for a song)."""
    args = [ffmpeg, "-nostdin", "-hide_banner", "-i", str(path), "-vn",
            "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_SECONDS},ebur128=peak=true:framelog=verbose",
            "-f", "null", "-"]
    kw: dict = {"creationflags": _LOW_PRIORITY}
    if sys.platform != "win32":
        kw = {"preexec_fn": lambda: os.nice(10)}  # noqa: PLW1509 (only os.nice)
    try:
        proc = subprocess.run(args, capture_output=True, timeout=600, **kw)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("couldn't measure %s: %s", path.name, exc)
        return None
    if proc.returncode != 0:
        return None
    return parse(proc.stderr.decode(errors="replace"))


class Analyser:
    """Measures tracks one at a time in the background and remembers them.

    `want(tracks)` asks for tracks in the order they'll play; each track
    gets its results (see `apply`) as soon as they're known.
    """

    def __init__(self, ffmpeg: str | None, path: Path | None, measure=measure) -> None:
        self.ffmpeg = ffmpeg
        self.path = path
        self._measure = measure
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._queue: deque[Track] = deque()
        self._queued: set[str] = set()
        self._entries: dict[str, list] = self._read()
        self._dirty = 0
        self._thread: threading.Thread | None = None
        self.measured = 0  # this run, for the page

    def _read(self) -> dict:
        if self.path is None:
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else None
            return entries if isinstance(entries, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            while len(self._entries) > _MAX_ENTRIES:  # oldest first
                del self._entries[next(iter(self._entries))]
            payload = json.dumps({"version": 1, "entries": self._entries})
            self._dirty = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("couldn't save track measurements: %s", exc)

    @staticmethod
    def _stamp(file: Path) -> list | None:
        try:
            st = file.stat()
        except OSError:
            return None
        return [st.st_size, st.st_mtime_ns]

    def known(self, file: Path) -> Measured | None:
        stamp = self._stamp(file)
        with self._lock:
            hit = self._entries.get(str(file))
        if stamp is None or not isinstance(hit, list) or len(hit) != 2 or hit[0] != stamp:
            return None
        try:
            return Measured(**hit[1])
        except TypeError:
            return None

    @staticmethod
    def apply(track: Track, result: Measured) -> None:
        """Give a track its measurements (read by channel.level_gain,
        schedule.track_seconds and the decoder)."""
        track.measured = result  # type: ignore[attr-defined]

    def want(self, tracks: list[Track]) -> int:
        """Apply what's known; queue the rest, in this order, ahead of
        anything asked for earlier. Returns how many still need measuring."""
        fresh = []
        for t in tracks:
            if t.path is None:
                continue
            result = self.known(t.path)
            if result is not None:
                self.apply(t, result)
            else:
                fresh.append(t)
        if not self.ffmpeg or not fresh:
            return len(fresh)
        with self._lock:
            for t in reversed(fresh):
                key = str(t.path)
                if key in self._queued:
                    try:
                        self._queue.remove(next(q for q in self._queue if str(q.path) == key))
                    except (StopIteration, ValueError):
                        pass
                self._queue.appendleft(t)
                self._queued.add(key)
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, name="measure", daemon=True)
                self._thread.start()
            self._wake.notify()
        return len(fresh)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    if self._dirty:
                        break
                    self._wake.wait()
                if not self._queue:
                    track = None
                else:
                    track = self._queue.popleft()
                    self._queued.discard(str(track.path))
            if track is None:
                self._save()
                continue
            assert track.path is not None
            known = self.known(track.path)
            result = known or self._measure(self.ffmpeg, track.path)
            if result is None:
                continue
            self.apply(track, result)
            if known is None:
                stamp = self._stamp(track.path)
                if stamp is None:
                    continue
                with self._lock:
                    self._entries.pop(str(track.path), None)
                    self._entries[str(track.path)] = [stamp, asdict(result)]
                    self._dirty += 1
                    self.measured += 1
                    save = self._dirty >= 20
                if save:
                    self._save()
