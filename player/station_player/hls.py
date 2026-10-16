"""HLS radio (.m3u8 playlists of short audio files, as the BBC uses),
turned into one continuous audio stream.

Browsers other than Safari can't play HLS in an <audio> element, and the
recorder writes a single stream to disk, so the player does the HLS work
itself: it reads the playlist, fetches each new segment in turn and hands
on the audio inside. Segments are MPEG-TS (the audio is taken out of the
transport stream, giving plain AAC or MP3), bare AAC / MP3 (passed on),
or fragmented MP4 (passed on after the init section, as audio/mp4).
Encrypted streams aren't supported.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

PLAYLIST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl")
MAX_PLAYLIST_BYTES = 512 * 1024
MAX_SEGMENT_BYTES = 16 * 1024 * 1024
LIVE_EDGE = 3          # start this many segments from the end, as players do
MAX_STALE_RELOADS = 6  # playlist reloads with nothing new before giving up


class HlsError(Exception):
    """Not a playable HLS stream."""


def is_playlist(content_type: str, url: str) -> bool:
    ctype = content_type.split(";", 1)[0].strip().lower()
    return ctype in PLAYLIST_TYPES or url.split("?", 1)[0].lower().endswith(".m3u8")


def _attrs(text: str) -> dict[str, str]:
    """KEY=VALUE,KEY="quoted, value" attribute lists."""
    out, key, val, quoted = {}, "", "", False
    reading_key = True
    for ch in text + ",":
        if reading_key:
            if ch == "=":
                reading_key = False
            elif ch != ",":
                key += ch
        elif ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            out[key.strip().upper()] = val
            key, val, reading_key = "", "", True
        else:
            val += ch
    return out


def parse_playlist(text: str, base: str) -> dict:
    """{"variants": [(bandwidth, url, codecs)], "segments": [(seq, url)],
    "map": url | None, "target": seconds, "ended": bool, "encrypted": bool}."""
    if not text.lstrip("﻿").startswith("#EXTM3U"):
        raise HlsError("not an HLS playlist")
    variants, segments = [], []
    seq, target, ended, encrypted, init = 0, 6.0, False, False, None
    pending_variant = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            a = _attrs(line.split(":", 1)[1])
            try:
                bw = int(a.get("BANDWIDTH", "0"))
            except ValueError:
                bw = 0
            pending_variant = (bw, a.get("CODECS", ""))
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target = max(1.0, min(30.0, float(line.split(":", 1)[1])))
            except ValueError:
                pass
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif line.startswith("#EXT-X-KEY:"):
            if _attrs(line.split(":", 1)[1]).get("METHOD", "NONE").upper() != "NONE":
                encrypted = True
        elif line.startswith("#EXT-X-MAP:"):
            uri = _attrs(line.split(":", 1)[1]).get("URI")
            if uri:
                init = urljoin(base, uri)
        elif line.startswith("#"):
            continue
        elif pending_variant is not None:
            variants.append((pending_variant[0], urljoin(base, line), pending_variant[1]))
            pending_variant = None
        else:
            segments.append((seq, urljoin(base, line)))
            seq += 1
    return {"variants": variants, "segments": segments, "map": init, "target": target,
            "ended": ended, "encrypted": encrypted}


def pick_variant(variants: list[tuple[int, str, str]]) -> str:
    """The best audio variant: the highest bandwidth, leaving out ones
    that carry video."""
    audio = [v for v in variants if not any(c.strip().startswith(("avc", "hvc", "hev", "vp0", "av01"))
                                            for c in v[2].split(","))] or variants
    return max(audio, key=lambda v: v[0])[1]


# ---- MPEG-TS -------------------------------------------------------------------

def ts_audio(data: bytes, state: dict | None = None) -> bytes:
    """The audio elementary stream inside an MPEG transport stream: the
    payload of the first audio PES stream (stream ids 0xC0-0xDF), which is
    ADTS AAC or MPEG audio as broadcast. `state` keeps the chosen PID
    between segments."""
    state = state if state is not None else {}
    out = bytearray()
    start = data.find(b"\x47")
    if start < 0:
        return b""
    for i in range(start, len(data) - 187, 188):
        pkt = data[i:i + 188]
        if pkt[0] != 0x47:
            continue  # lost sync; the next aligned packet usually recovers
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        unit_start = bool(pkt[1] & 0x40)
        afc = (pkt[3] >> 4) & 3
        if afc in (0, 2):
            continue  # no payload
        offset = 4
        if afc == 3:
            offset += 1 + pkt[4]
        if offset >= 188:
            continue
        payload = pkt[offset:]
        if unit_start and payload[:3] == b"\x00\x00\x01" and len(payload) >= 9:
            stream_id = payload[3]
            if 0xC0 <= stream_id <= 0xDF and state.get("pid") in (None, pid):
                state["pid"] = pid
                payload = payload[9 + payload[8]:]
            elif pid != state.get("pid"):
                continue
            else:
                payload = payload[9 + payload[8]:]
        elif pid != state.get("pid"):
            continue
        out += payload
    return bytes(out)


def _strip_id3(data: bytes) -> bytes:
    """Packed audio segments may start with an ID3 tag (timestamps)."""
    while data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        data = data[10 + size + (10 if data[5] & 0x10 else 0):]
    return data


def audio_type(data: bytes) -> str | None:
    """audio/aac for ADTS, audio/mpeg for MP3/MP2 frames, else None."""
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xF0) == 0xF0:
        return "audio/aac" if (data[1] & 0x06) == 0 else "audio/mpeg"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return None


# ---- the stream ------------------------------------------------------------------

class _NoSocket:
    """Stands in for a socket where callers set read timeouts."""

    def settimeout(self, _seconds) -> None:
        pass


class _Body:
    def __init__(self, owner: "HlsStream") -> None:
        self._owner = owner

    def read1(self, n: int) -> bytes:
        return self._owner._read(n)

    def read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self._owner._read(n - len(out))
            if not chunk:
                break
            out += chunk
        return out


class HlsStream:
    """Reads like radio.Stream (content_type, metaint, body, headers,
    close) but plays an HLS playlist."""

    metaint = 0

    def __init__(self, url: str, playlist: str, fetch: Callable[[str, int], bytes],
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """`fetch(url, limit)` returns a whole response body (or raises
        OSError); `playlist` is the text already read from `url`."""
        self.url = url
        self._fetch = fetch
        self._sleep = sleep
        self._closed = threading.Event()
        self._buffer = b""
        self._ts: dict = {}
        self._next_seq: int | None = None
        info = parse_playlist(playlist, url)
        for _ in range(3):  # a master playlist, perhaps nested
            if not info["variants"]:
                break
            self.url = pick_variant(info["variants"])
            info = parse_playlist(self._text(self.url), self.url)
        if info["encrypted"]:
            raise HlsError("this stream is encrypted")
        if not info["segments"]:
            raise HlsError("the playlist lists no audio")
        self._info = info
        self._mp4 = info["map"] is not None
        self._next_seq = info["segments"][max(0, len(info["segments"]) - LIVE_EDGE)][0] if not info["ended"] \
            else info["segments"][0][0]
        if self._mp4:
            self._buffer = self._fetch(info["map"], MAX_SEGMENT_BYTES)
            self.content_type = "audio/mp4"
        else:
            first = self._next_audio()
            ctype = audio_type(first)
            if ctype is None:
                raise HlsError("its audio isn't in a format the player can pass on")
            self._buffer = first
            self.content_type = ctype
        self.headers = {"content-type": self.content_type}
        self.sock = _NoSocket()
        self.body = _Body(self)

    def _text(self, url: str) -> str:
        return self._fetch(url, MAX_PLAYLIST_BYTES).decode("utf-8", "replace")

    def _segment_audio(self, url: str) -> bytes:
        data = self._fetch(url, MAX_SEGMENT_BYTES)
        if self._mp4:
            return data
        if data[:1] == b"\x47" and data[188:189] in (b"\x47", b""):
            return ts_audio(data, self._ts)
        return _strip_id3(data)

    def _next_audio(self) -> bytes:
        """The audio of the next segment, reloading the playlist (live)
        until one appears. b"" when the stream has ended or was closed."""
        stale = 0
        while not self._closed.is_set():
            for seq, url in self._info["segments"]:
                if seq >= self._next_seq:
                    self._next_seq = seq + 1
                    audio = self._segment_audio(url)
                    if audio:
                        return audio
            if self._info["ended"]:
                return b""
            stale += 1
            if stale > MAX_STALE_RELOADS:
                raise OSError("the station stopped publishing audio")
            self._sleep(self._info["target"] / 2)
            if self._closed.is_set():
                break
            info = parse_playlist(self._text(self.url), self.url)
            if info["segments"] and info["segments"][0][0] > self._next_seq:
                self._next_seq = info["segments"][0][0]  # fell behind: skip ahead
            self._info = info
        return b""

    def _read(self, n: int) -> bytes:
        if not self._buffer:
            if self._closed.is_set():
                return b""
            try:
                self._buffer = self._next_audio()
            except HlsError as exc:
                raise OSError(str(exc)) from None
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def close(self) -> None:
        self._closed.set()
