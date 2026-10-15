"""Internet radio streams: connect, follow redirects, and read the song
titles broadcasters embed in the audio (SHOUTcast / Icecast "ICY"
metadata).

Browsers can't see those titles, so the player relays each station
through the local server: it asks for the metadata, passes the audio on
untouched and keeps the latest title for the UI. A raw socket client is
used because SHOUTcast servers answer "ICY 200 OK", which http.client
rejects.
"""
from __future__ import annotations

import logging
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.parse import urljoin, urlparse

CONNECT_SECONDS = 10      # to connect and receive the response headers
READ_SECONDS = 30         # silence from the station before giving up
MAX_METAINT = 1 << 20     # an absurd metadata interval means junk
_MAX_HEADER_BYTES = 64 * 1024
_MAX_REDIRECTS = 4
# Audio types the relay passes on; anything else (an HTML error page, an
# HLS playlist) is refused so the UI falls back to playing the URL itself.
_AUDIO_TYPE_RE = re.compile(r"^(audio/[a-z0-9.+-]+|application/ogg)$")


logger = logging.getLogger(__name__)


class StreamError(Exception):
    pass


_tls_lock = threading.Lock()
_tls: ssl.SSLContext | None = None


def _macos_root_certs() -> str:
    """The system's trusted roots, as PEM, from the macOS keychain."""
    out = subprocess.run(
        ["/usr/bin/security", "find-certificate", "-a", "-p",
         "/System/Library/Keychains/SystemRootCertificates.keychain"],
        capture_output=True, text=True, timeout=10, check=True)
    return out.stdout


def tls_context(platform: str | None = None, load_roots=_macos_root_certs) -> ssl.SSLContext:
    """A verifying TLS context. Python from python.org on macOS ships with
    no certificates until "Install Certificates.command" is run (browsers
    use the system's), so there the keychain's roots are loaded instead."""
    global _tls
    with _tls_lock:
        if _tls is not None and platform is None:
            return _tls
        ctx = ssl.create_default_context()
        if (platform or sys.platform) == "darwin" and not ctx.cert_store_stats().get("x509_ca"):
            try:
                ctx.load_verify_locations(cadata=load_roots())
            except (OSError, subprocess.SubprocessError, ssl.SSLError, ValueError) as exc:
                logger.warning("No trusted certificates for https stations: %s", exc)
        if platform is None:
            _tls = ctx
        return ctx


class _Body:
    """The response body: bytes already read with the headers, then the
    socket. `read1` returns whatever is available, like a buffered file."""

    def __init__(self, sock: socket.socket, pending: bytes) -> None:
        self._sock = sock
        self._pending = pending

    def read1(self, n: int) -> bytes:
        if self._pending:
            out, self._pending = self._pending[:n], self._pending[n:]
            return out
        return self._sock.recv(n)

    def read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.read1(n - len(out))
            if not chunk:
                break
            out += chunk
        return out


@dataclass
class Stream:
    sock: socket.socket
    body: _Body
    status: int
    headers: dict[str, str]
    url: str

    @property
    def content_type(self) -> str | None:
        raw = self.headers.get("content-type", "audio/mpeg")
        ctype = raw.split(";", 1)[0].strip().lower()
        return ctype if _AUDIO_TYPE_RE.match(ctype) else None

    @property
    def metaint(self) -> int:
        try:
            n = int(self.headers.get("icy-metaint", "0").strip())
        except ValueError:
            return 0
        return n if 0 < n <= MAX_METAINT else 0

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def open_stream(url: str, user_agent: str = "StationPlayer", timeout: float = CONNECT_SECONDS) -> Stream:
    """Connect to a stream asking for ICY metadata; follows redirects."""
    deadline = time.monotonic() + timeout
    for _ in range(_MAX_REDIRECTS + 1):
        stream = _request(url, user_agent, deadline)
        if stream.status in (301, 302, 303, 307, 308):
            location = stream.headers.get("location", "")
            stream.close()
            nxt = urljoin(url, location)
            if not location or urlparse(nxt).scheme not in ("http", "https"):
                raise StreamError("bad redirect")
            url = nxt
            continue
        if stream.status != 200:
            stream.close()
            raise StreamError(f"station answered {stream.status}")
        if "chunked" in stream.headers.get("transfer-encoding", "").lower():
            stream.close()
            raise StreamError("chunked streams are not relayed")
        stream.sock.settimeout(READ_SECONDS)
        return stream
    raise StreamError("too many redirects")


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise StreamError("timed out")
    return left


def _request(url: str, user_agent: str, deadline: float) -> Stream:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise StreamError("not an http(s) URL")
    secure = parts.scheme == "https"
    try:
        port = parts.port or (443 if secure else 80)
    except ValueError:
        raise StreamError("bad port") from None
    host = parts.hostname
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    host_header = host if ":" not in host else f"[{host}]"
    if parts.port:
        host_header += f":{parts.port}"
    try:
        sock = socket.create_connection((host, port), timeout=_remaining(deadline))
    except OSError as exc:
        raise StreamError(f"could not connect: {exc}") from None
    try:
        if secure:
            sock.settimeout(_remaining(deadline))
            sock = tls_context().wrap_socket(sock, server_hostname=host)
        request = (
            f"GET {path} HTTP/1.0\r\n"
            f"Host: {host_header}\r\n"
            f"User-Agent: {user_agent}\r\n"
            "Accept: */*\r\n"
            "Icy-MetaData: 1\r\n"
            "Connection: close\r\n\r\n"
        )
        sock.settimeout(_remaining(deadline))
        sock.sendall(request.encode("latin-1"))
        head, pending = _read_head(sock, deadline)
        lines = [line.rstrip("\r") for line in head.decode("latin-1").split("\n")]
        bits = lines[0].split(None, 2)
        if len(bits) < 2 or not (bits[0].startswith("HTTP/") or bits[0] == "ICY") or not bits[1].isdigit():
            raise StreamError("not an HTTP or ICY response")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return Stream(sock, _Body(sock, pending), int(bits[1]), headers, url)
    except StreamError:
        sock.close()
        raise
    except (OSError, ssl.SSLError, UnicodeError) as exc:
        sock.close()
        raise StreamError(str(exc) or exc.__class__.__name__) from None


def _read_head(sock: socket.socket, deadline: float) -> tuple[bytes, bytes]:
    """Read up to the blank line ending the headers. The deadline is
    checked between reads, so a server sending a byte at a time can't
    hold the connection open past it."""
    buf = b""
    while True:
        # Some old SHOUTcast servers end lines with a bare "\n".
        ends = [(i, len(sep)) for sep in (b"\r\n\r\n", b"\n\n") if (i := buf.find(sep)) >= 0]
        if ends:
            end, size = min(ends)
            return buf[:end], buf[end + size:]
        if len(buf) > _MAX_HEADER_BYTES:
            raise StreamError("response headers too long")
        sock.settimeout(_remaining(deadline))
        chunk = sock.recv(4096)
        if not chunk:
            raise StreamError("connection closed before the headers ended")
        buf += chunk


def parse_stream_title(block: bytes) -> str | None:
    """`StreamTitle='Artist - Song';` -> "Artist - Song". Returns "" for a
    blank title and None when the block has no StreamTitle at all."""
    raw = block.rstrip(b"\x00")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    key = "StreamTitle='"
    start = text.find(key)
    if start < 0:
        return None
    start += len(key)
    end = text.find("';", start)
    if end < 0:
        end = text.rfind("'")
    return text[start:end if end >= start else None].strip()


def relay(stream: Stream, on_title: Callable[[str | None], None], chunk: int = 16 * 1024) -> Iterator[bytes]:
    """Yield the stream's audio, with any ICY metadata taken out and
    handed to `on_title`. Ends when the station closes the connection."""
    body = stream.body
    metaint = stream.metaint
    if not metaint:
        while data := body.read1(chunk):
            yield data
        return
    left = metaint
    while True:
        data = body.read1(min(chunk, left))
        if not data:
            return
        left -= len(data)
        yield data
        if left:
            continue
        length = body.read(1)
        if not length:
            return
        size = length[0] * 16
        if size:
            block = body.read(size)
            if len(block) < size:
                return
            title = parse_stream_title(block)
            if title is not None:
                on_title(title or None)
        left = metaint
