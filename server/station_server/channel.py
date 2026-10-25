"""One station on air: plays its queue in real time into an encoder and a sender.

The flow is decode (per track) -> mixer -> pacer -> one encoder -> sender.
For now the mixer just joins tracks end to end with no gap, applying each
track's volume levelling. It is the seam where crossfades, clock trimming
and a live input will go later.
"""
from __future__ import annotations

import logging
import math
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
    held back so the loudest sample stays below full scale. 0 when the file
    carries no levelling information (measuring comes in a later version)."""
    if track.path is None:
        return 0.0
    tags = read_tags(track.path)
    if tags.gain_db is None:
        return 0.0
    gain = min(tags.gain_db, MAX_BOOST_DB)
    if tags.peak and tags.peak > 0:
        headroom = -20 * math.log10(tags.peak)
        gain = min(gain, headroom)
    return gain


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
    ) -> None:
        """`offset` starts the first track that many seconds in. `on_track`
        is called as each track starts, `on_played` as it ends with the
        seconds actually played. With `keep_going`, a playlist where no
        track can be read sends silence and tries again, instead of
        raising RuntimeError (the app wants that; the command line not)."""
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
        self._next: tuple[Queue, Callable[[Queue], tuple[int, float]]] | None = None
        self.now: NowPlaying | None = None
        self.stuck = False  # nothing in the playlist could be read

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
            self._next = (queue, where or (lambda q: (index, offset)))
        if now:
            self._skip.set()

    def _take_next(self) -> tuple[int, float] | None:
        with self._lock:
            pending, self._next = self._next, None
        if pending is None:
            return None
        self.queue, where = pending
        index, offset = where(self.queue)
        return index % len(self.queue.tracks), offset

    def run(self) -> None:
        encoder = Encoder(self.ffmpeg, self.sender.send, bitrate=self.bitrate)
        index, offset = self.start, self.offset
        failures = 0
        try:
            while not self.stop_event.is_set():
                if self._play(index, offset, encoder):
                    failures = 0
                    self.stuck = False
                else:
                    failures += 1
                    if failures >= len(self.queue.tracks):
                        if not self.keep_going:
                            raise RuntimeError("No track in the playlist could be played; stopping")
                        self.stuck = True
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
                if index >= len(self.queue.tracks):
                    if not self.loop:
                        break
                    index = 0
        finally:
            encoder.close()

    def _title_later(self, title: str, delay: float) -> None:
        if delay and self.stop_event.wait(delay):
            return
        self.sender.set_title(title)

    def _play(self, index: int, offset: float, encoder: Encoder) -> bool:
        """Play one track; False when none of its audio could be read."""
        track = self.queue.tracks[index]
        assert track.path is not None
        gain = level_gain(track) if self.level else 0.0
        ahead = self.pacer.ahead()
        now = NowPlaying(index, track, time.time() + ahead - offset, gain, offset)
        title = now_playing(track)
        logger.info("[%d/%d] %s%s", index + 1, len(self.queue.tracks), title,
                    f" (from {offset:.0f}s)" if offset >= 1 else "")
        # The audio sent ahead of the clock hasn't been heard yet, so the
        # title waits that long. It goes over the network in its own thread:
        # a slow server must never hold up the audio.
        threading.Thread(target=self._title_later, args=(title, ahead), daemon=True).start()
        self.now = now
        if self._on_track:
            self._on_track(now)
        self._skip.clear()
        played = 0
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
