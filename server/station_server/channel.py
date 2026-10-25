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

from station_server.audio import BYTES_PER_SECOND, Encoder, decode
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
    started: float


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
        level: bool = True,
        pacer: Pacer | None = None,
        decoder: Callable[..., Iterator[bytes]] = decode,
        on_track: Callable[[NowPlaying], None] | None = None,
    ) -> None:
        self.queue = queue
        self.sender = sender
        self.ffmpeg = ffmpeg
        self.bitrate = bitrate
        self.loop = loop
        self.start = start % len(queue.tracks)
        self.level = level
        self.pacer = pacer or Pacer()
        self._decode = decoder
        self._on_track = on_track
        self.stop_event = threading.Event()
        self._skip = threading.Event()
        self.now: NowPlaying | None = None

    def skip(self) -> None:
        """Move on to the next track straight away."""
        self._skip.set()

    def stop(self) -> None:
        self.stop_event.set()

    def _order(self) -> Iterator[int]:
        n = len(self.queue.tracks)
        i = self.start
        while True:
            yield i
            i += 1
            if i == n:
                if not self.loop:
                    return
                i = 0

    def run(self) -> None:
        encoder = Encoder(self.ffmpeg, self.sender.send, bitrate=self.bitrate)
        failures = 0
        try:
            for index in self._order():
                if self.stop_event.is_set():
                    break
                if self._play(index, encoder):
                    failures = 0
                else:
                    failures += 1
                    if failures >= len(self.queue.tracks):
                        raise RuntimeError("No track in the playlist could be played; stopping")
        finally:
            encoder.close()

    def _title_later(self, title: str, delay: float) -> None:
        if delay and self.stop_event.wait(delay):
            return
        self.sender.set_title(title)

    def _play(self, index: int, encoder: Encoder) -> bool:
        """Play one track; False when none of its audio could be read."""
        track = self.queue.tracks[index]
        assert track.path is not None
        gain = level_gain(track) if self.level else 0.0
        self.now = NowPlaying(index, track, time.time())
        title = now_playing(track)
        logger.info("[%d/%d] %s", index + 1, len(self.queue.tracks), title)
        # The audio sent ahead of the clock hasn't been heard yet, so the
        # title waits that long. It goes over the network in its own thread:
        # a slow server must never hold up the audio.
        threading.Thread(target=self._title_later, args=(title, self.pacer.ahead()), daemon=True).start()
        if self._on_track:
            self._on_track(self.now)
        self._skip.clear()
        played = 0
        chunks = self._decode(self.ffmpeg, track.path, gain_db=gain)
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
        if not played and not self.stop_event.is_set():
            logger.warning("skipped %s: no audio could be read from %s", title, track.path)
            return False
        return True
