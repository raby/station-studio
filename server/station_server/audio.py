"""Decoding and encoding, done by ffmpeg.

Every track is decoded to the same raw format (PCM below), so the mixer can
join them with no gap or crossfade them (mix), and later switch to a live
input. One encoder runs for the whole broadcast: starting a new one
per track would leave a gap, or a reconnect, between songs.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
from array import array
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


def bundled_ffmpeg() -> Path | None:
    """The ffmpeg inside the downloaded app (see packaging/station-server.spec)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    exe = Path(base) / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    return exe if exe.is_file() else None


# Where Homebrew puts ffmpeg: an app opened from the Finder doesn't get the
# terminal's PATH, so look there too.
_MORE_PLACES = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")


def find_ffmpeg(explicit: str | None = None) -> str:
    """The ffmpeg to use: given on the command line, then $STATION_SERVER_FFMPEG,
    then the one inside the downloaded app, then whatever is on PATH."""
    for candidate in (explicit, os.environ.get(ENV_VAR, "").strip() or None):
        if candidate:
            if Path(candidate).expanduser().is_file():
                return str(Path(candidate).expanduser())
            raise FfmpegMissing(f"ffmpeg not found at {candidate}")
    inside = bundled_ffmpeg()
    if inside:
        return str(inside)
    found = shutil.which("ffmpeg") or next((p for p in _MORE_PLACES if os.path.isfile(p)), None)
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


# Above this, what the fading tail adds is eased towards full scale instead
# of clipped. The incoming song itself is never changed.
_KNEE = 24576


def _soft(v: int, head: int) -> int:
    base = max(_KNEE, head)
    if v <= base:
        return v
    room, excess = 32767 - base, v - base
    return base + room * excess // (excess + room) if room > 0 else 32767


def mix(tail: bytes, head: bytes, done: int, total: int) -> bytes:
    """A crossfade, a piece at a time: `tail` (the end of one track, `done`
    bytes into a fade `total` bytes long) fading out under `head` (the
    start of the next) at full volume. `head` may be shorter (a very short
    track): the rest of the tail fades out over silence.

    The tail fades on a curve, quickly at first, so the two songs aren't
    both loud for long, and where they add up past full scale the peaks are
    rounded off softly (no harsh clipping)."""
    t = array("h")
    t.frombytes(tail)
    h = array("h")
    h.frombytes(head[:len(tail)].ljust(len(tail), b"\0"))
    if sys.byteorder == "big":
        t.byteswap()
        h.byteswap()
    first, frames = done // 4, max(1, total // 4)
    out = array("h", h)
    for i in range(len(t)):
        g = 1.0 - (first + i // 2) / frames
        v = h[i] + int(t[i] * g * g)
        if v > _KNEE:
            v = _soft(v, h[i])
        elif v < -_KNEE:
            v = -_soft(-v, -h[i])
        out[i] = v
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes() + head[len(tail):]


def has_mp3_encoder(ffmpeg: str) -> bool:
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    ).stdout
    return "libmp3lame" in out


def decode(ffmpeg: str, path: Path, gain_db: float = 0.0, start: float = 0.0,
           length: float | None = None) -> Iterator[bytes]:
    """Raw PCM for one file, in CHUNK-sized pieces (the last may be short).

    `gain_db` is applied while decoding (volume levelling); `start` skips
    that many seconds into the file (silence at the start, or picking up
    mid-track after a restart) and `length` stops after that many seconds
    (before the silence at the end).
    If ffmpeg can't read the file the iterator simply ends early; the
    caller moves on.
    """
    args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path), "-vn"]
    if length is not None:
        args += ["-t", f"{max(0.0, length):.3f}"]
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
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        self._reader = threading.Thread(target=self._read, name="encoder-out", daemon=True)
        self._reader.start()
        threading.Thread(target=self._read_errors, name="encoder-err", daemon=True).start()

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

    def _read_errors(self) -> None:
        """Put what the encoder complains about in the log, so a failure says why."""
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            text = line.decode(errors="replace").strip()
            if text:
                logger.warning("encoder: %s", text)

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
