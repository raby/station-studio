"""One station on air: plays its queue in real time into an encoder and a sender.

The flow is decode (per track) -> mixer -> pacer -> one encoder -> sender.
For now the mixer just joins tracks end to end with no gap, applying each
track's volume levelling. It is the seam where crossfades, clock trimming
and a live input will go later.
"""
from __future__ import annotations

import logging
import math
import queue as queues
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from station_player.library import Track
from station_player.tags import read_tags

from station_server.audio import BYTES_PER_SECOND, Encoder, decode, silence
from station_server.playlist import Queue, now_playing

logger = logging.getLogger(__name__)

# Levelling never pushes a track by more than this, so a quiet intro or a
# wrong tag can't turn into a blast.
MAX_BOOST_DB = 10.0


def level_gain(track: Track) -> float:
    """The volume change for a track from its ReplayGain / Sound Check tags,
    or else from measuring it (analysis.py), held back so the loudest
    sample stays below full scale. 0 when neither is known yet."""
    return level(track)[0]


def level(track: Track) -> tuple[float, str | None]:
    """(gain in dB, where it came from: "tags", "measured" or None)."""
    if track.path is None:
        return 0.0, None
    tags = read_tags(track.path)
    gain_db, peak, source = tags.gain_db, tags.peak, "tags"
    if gain_db is None:
        measured = getattr(track, "measured", None)
        if measured is None or measured.gain_db is None:
            return 0.0, None
        gain_db, peak, source = measured.gain_db, measured.peak, "measured"
    gain = min(gain_db, MAX_BOOST_DB)
    if peak and peak > 0:
        headroom = -20 * math.log10(peak)
        gain = min(gain, headroom)
    return gain, source


class Pacer:
    """Holds the audio to real time: a stream server expects one second of
    audio per second. Up to `lead` seconds may be sent early, which fills
    the server's buffer at the start so listeners connect quickly. If we
    fall behind (a slow disk, a busy computer) the clock catches up rather
    than bursting to make up the lost time."""

    def __init__(self, lead: float = 2.0, clock=time.monotonic, sleep=time.sleep) -> None:
        self.lead = lead
        self._clock, self._sleep = clock, sleep
        self._start: float | None = None
        self._sent = 0.0  # seconds of audio passed so far

    def wait(self, nbytes: int) -> None:
        now = self._clock()
        if self._start is None:
            self._start = now
        elapsed = now - self._start
        if self._sent < elapsed - 1.0:  # behind by over a second: don't burst
            self._sent = elapsed
        ahead = self._sent - elapsed
        if ahead > self.lead:
            self._sleep(ahead - self.lead)
        self._sent += nbytes / BYTES_PER_SECOND

    def ahead(self) -> float:
        """How far the audio sent so far runs ahead of the clock, in seconds."""
        if self._start is None:
            return 0.0
        return max(0.0, self._sent - (self._clock() - self._start))


@dataclass
class NowPlaying:
    index: int
    track: Track
    started: float  # when listeners heard (or will hear) the track's start, as time.time()
    gain_db: float = 0.0
    offset: float = 0.0  # seconds into the track it started from (after a restart)
    levelled_by: str | None = None  # "tags" or "measured"


# When no track in the playlist can be read, the station sends this much
# silence before trying the playlist again, so the server keeps the
# station up rather than dropping listeners.
STUCK_SILENCE_SECONDS = 30.0


class Channel:
    """Plays `queue` to `sender` until stopped (or to the end, if not looping)."""

    def __init__(
        self,
        queue: Queue,
        sender,
        ffmpeg: str,
        bitrate: int = 128,
        loop: bool = True,
        start: int = 0,
        offset: float = 0.0,
        level: bool = True,
        pacer: Pacer | None = None,
        decoder: Callable[..., Iterator[bytes]] = decode,
        on_track: Callable[[NowPlaying], None] | None = None,
        on_played: Callable[[NowPlaying, float], None] | None = None,
        keep_going: bool = False,
        after: Callable[[], tuple[Queue, int, float]] | None = None,
        fallback: Callable[[], tuple[Queue, Callable[[], tuple[Queue, int, float]]] | None] | None = None,
    ) -> None:
        """`offset` starts the first track that many seconds in. `on_track`
        is called as each track starts, `on_played` as it ends with the
        seconds actually played. With `keep_going`, a playlist where no
        track can be read sends silence and tries again, instead of
        raising RuntimeError (the app wants that; the command line not).
        With `after`, `queue` is played once (a show) and `after()` then
        says what to play next: (queue, track index, seconds in).
        `fallback()`, when no track can be read, gives something to play
        once instead of silence, and what to try after it (as `after`)."""
        self.queue = queue
        self.sender = sender
        self.ffmpeg = ffmpeg
        self.bitrate = bitrate
        self.loop = loop
        self.start = start % len(queue.tracks)
        self.offset = offset
        self.level = level
        self.pacer = pacer or Pacer()
        self._decode = decoder
        self._on_track = on_track
        self._on_played = on_played
        self.keep_going = keep_going
        self.stop_event = threading.Event()
        self._skip = threading.Event()
        self._lock = threading.Lock()
        self._next: tuple[Queue, Callable[[Queue], tuple[int, float]], Callable | None] | None = None
        self._after = after
        self._fallback = fallback
        self._fallback_after: Callable | None = None
        self.now: NowPlaying | None = None
        self.stuck = False  # nothing in the playlist could be read
        # Titles waiting to go to the server: (title, when it's heard, as time.monotonic()).
        self._titles: queues.Queue[tuple[str, float]] = queues.Queue()

    def skip(self) -> None:
        """Move on to the next track straight away."""
        self._skip.set()

    def stop(self) -> None:
        self.stop_event.set()

    def load(self, queue: Queue, index: int = 0, offset: float = 0.0, now: bool = False,
             where: Callable[[Queue], tuple[int, float]] | None = None) -> None:
        """Switch to another playlist (next week's, say) from track `index`,
        `offset` seconds in. It takes over when the playing track ends, or
        straight away with `now`. `where`, if given, picks the track and
        offset at the moment of the switch instead (to follow the clock)."""
        with self._lock:
            self._next = (queue, where or (lambda q: (index, offset)), None)
        if now:
            self._skip.set()

    def insert(self, queue: Queue, after: Callable[[], tuple[Queue, int, float]], now: bool = True,
               offset: float = 0.0) -> None:
        """Play `queue` once through (a show), straight away or when the
        playing track ends, then whatever `after()` says: (queue, track
        index, seconds in)."""
        with self._lock:
            self._next = (queue, lambda q: (0, offset), after)
        if now:
            self._skip.set()

    def _play_fallback(self, queue: Queue, after: Callable[[], tuple[Queue, int, float]]) -> None:
        """Play `queue` once through instead of silence, then try `after()`."""
        with self._lock:
            if self._next is None:  # a new week or a show waiting goes first
                self._fallback_after = after
                self._next = (queue, lambda q: (0, 0.0), after)

    @property
    def on_fallback(self) -> bool:
        """Playing the fallback, because nothing in the playlist could be read."""
        return self._after is not None and self._after is self._fallback_after

    @property
    def in_show(self) -> bool:
        """Playing something once through (see insert)."""
        return self._after is not None

    def _take_next(self) -> tuple[int, float] | None:
        with self._lock:
            pending, self._next = self._next, None
        if pending is None:
            return None
        self.queue, where, self._after = pending
        index, offset = where(self.queue)
        return index % len(self.queue.tracks), offset

    def run(self) -> None:
        encoder = Encoder(self.ffmpeg, self.sender.send, bitrate=self.bitrate)
        threading.Thread(target=self._send_titles, name="titles", daemon=True).start()
        index, offset = self.start, self.offset
        failures = 0
        try:
            while not self.stop_event.is_set():
                if self._play(index, offset, encoder):
                    failures = 0
                    self.stuck = False
                else:
                    failures += 1
                    # A show that can't be read just ends: the music comes back.
                    if failures >= len(self.queue.tracks) and self._after is None:
                        if not self.keep_going:
                            raise RuntimeError("No track in the playlist could be played; stopping")
                        self.stuck = True
                        fallback = self._fallback() if self._fallback else None
                        if fallback is not None:
                            logger.error("no track in the playlist can be played; playing the fallback")
                            self._play_fallback(*fallback)
                            failures = 0
                            pending = self._take_next()
                            index, offset = pending if pending else (0, 0.0)
                            continue
                        logger.error("no track in the playlist can be played; sending silence")
                        for chunk in silence(STUCK_SILENCE_SECONDS):
                            if self.stop_event.is_set() or self._next is not None:
                                break
                            self.pacer.wait(len(chunk))
                            encoder.write(chunk)
                        failures = 0
                offset = 0.0
                pending = self._take_next()
                if pending is not None:
                    index, offset = pending
                    failures = 0
                    continue
                index += 1
                if index >= len(self.queue.tracks) and self._after is not None:
                    after, self._after = self._after, None  # the show is over
                    self.queue, index, offset = after()
                    index %= len(self.queue.tracks)
                    continue
                if index >= len(self.queue.tracks):
                    if not self.loop:
                        break
                    index = 0
        finally:
            encoder.close()

    def _send_titles(self) -> None:
        """Send each title when its track is heard, one at a time and in
        order, so two quick skips can't leave the older title showing. A
        slow server holds up only the titles, never the audio."""
        while not self.stop_event.is_set():
            try:
                title, due = self._titles.get(timeout=0.5)
            except queues.Empty:
                continue
            delay = due - time.monotonic()
            if delay > 0 and self.stop_event.wait(delay):
                return
            self.sender.set_title(title)

    def _play(self, index: int, offset: float, encoder: Encoder) -> bool:
        """Play one track; False when none of its audio could be read."""
        track = self.queue.tracks[index]
        assert track.path is not None
        gain, levelled_by = level(track) if self.level else (0.0, None)
        ahead = self.pacer.ahead()
        now = NowPlaying(index, track, time.time() + ahead - offset, gain, offset, levelled_by)
        title = now_playing(track)
        logger.info("[%d/%d] %s%s", index + 1, len(self.queue.tracks), title,
                    f" (from {offset:.0f}s)" if offset >= 1 else "")
        # The audio sent ahead of the clock hasn't been heard yet, so the
        # title waits that long.
        self._titles.put((title, time.monotonic() + ahead))
        self.now = now
        if self._on_track:
            self._on_track(now)
        self._skip.clear()
        played = 0
        # Once measured, the silence at either end is left out.
        measured = getattr(track, "measured", None)
        if measured is not None:
            chunks = self._decode(self.ffmpeg, track.path, gain_db=gain, start=measured.start + offset,
                                  length=measured.end - measured.start - offset)
        else:
            chunks = self._decode(self.ffmpeg, track.path, gain_db=gain, start=offset)
        try:
            for chunk in chunks:
                if self.stop_event.is_set() or self._skip.is_set():
                    break
                self.pacer.wait(len(chunk))
                encoder.write(chunk)
                played += len(chunk)
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()
        if self._on_played and played:
            self._on_played(now, played / BYTES_PER_SECOND)
        if not played and not self.stop_event.is_set() and not self._skip.is_set():
            logger.warning("skipped %s: no audio could be read from %s", title, track.path)
            return False
        return True
