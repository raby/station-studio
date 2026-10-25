"""Decoding and encoding, done by ffmpeg.

Every track is decoded to the same raw format (PCM below), so the mixer can
join them with no gap and, later, crossfade, trim to the clock or switch to
a live input. One encoder runs for the whole broadcast: starting a new one
per track would leave a gap, or a reconnect, between songs.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

RATE = 44100
CHANNELS = 2
SAMPLE_BYTES = 2  # signed 16-bit little-endian
BYTES_PER_SECOND = RATE * CHANNELS * SAMPLE_BYTES
# About a tenth of a second of audio: small enough to pace smoothly, and a
# whole number of frames so a chunk never splits a sample.
CHUNK = 4410 * CHANNELS * SAMPLE_BYTES

PCM_ARGS = ["-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS)]
ENV_VAR = "STATION_SERVER_FFMPEG"

# Hide the console window ffmpeg would otherwise open on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FfmpegMissing(Exception):
    pass


def find_ffmpeg(explicit: str | None = None) -> str:
    """The ffmpeg to use: given on the command line, then $STATION_SERVER_FFMPEG,
    then whatever is on PATH."""
    for candidate in (explicit, os.environ.get(ENV_VAR, "").strip() or None):
        if candidate:
            if Path(candidate).expanduser().is_file():
                return str(Path(candidate).expanduser())
            raise FfmpegMissing(f"ffmpeg not found at {candidate}")
    found = shutil.which("ffmpeg")
    if not found:
        raise FfmpegMissing(
            "ffmpeg not found. Install it (macOS: brew install ffmpeg; "
            "Windows: winget install ffmpeg) or pass --ffmpeg PATH."
        )
    return found


def silence(seconds: float) -> Iterator[bytes]:
    """Digital silence, in CHUNK-sized pieces."""
    left = int(seconds * BYTES_PER_SECOND) // 4 * 4
    while left > 0:
        n = min(CHUNK, left)
        yield bytes(n)
        left -= n


def has_mp3_encoder(ffmpeg: str) -> bool:
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    ).stdout
    return "libmp3lame" in out


def decode(ffmpeg: str, path: Path, gain_db: float = 0.0, start: float = 0.0) -> Iterator[bytes]:
    """Raw PCM for one file, in CHUNK-sized pieces (the last may be short).

    `gain_db` is applied while decoding (volume levelling); `start` skips
    that many seconds into the file (picking up mid-track after a restart).
    If ffmpeg can't read the file the iterator simply ends early; the
    caller moves on.
    """
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path), "-vn"]
    if gain_db:
        args += ["-af", f"volume={gain_db:.2f}dB"]
    args += PCM_ARGS + ["pipe:1"]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW,
    )
    try:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        if proc.poll() is None:
            proc.kill()
        _, err = proc.communicate()
        if proc.returncode not in (0, None, -9) and err:
            logger.warning("ffmpeg could not fully decode %s: %s", path.name, err.decode(errors="replace").strip())


class Encoder:
    """One ffmpeg turning PCM into a continuous MP3 stream.

    `write` feeds it PCM; everything it encodes is handed to `on_data` from
    a reader thread, in order.
    """

    def __init__(self, ffmpeg: str, on_data: Callable[[bytes], None], bitrate: int = 128) -> None:
        self.bitrate = bitrate
        self._on_data = on_data
        self._proc = subprocess.Popen(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
            + PCM_ARGS + ["-i", "pipe:0",
                          "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
                          "-flush_packets", "1", "-f", "mp3", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        self._reader = threading.Thread(target=self._read, name="encoder-out", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self._proc.stdout is not None
        while True:
            data = self._proc.stdout.read1(16384)
            if not data:
                return
            try:
                self._on_data(data)
            except Exception:  # a sender bug must not stall the encoder
                logger.exception("sending encoded audio failed")

    def write(self, pcm: bytes) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(pcm)

    def close(self) -> None:
        """Finish encoding what was written, then stop."""
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._reader.join(timeout=5)
