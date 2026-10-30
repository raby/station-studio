"""Live shows: a presenter's encoder (BUTT, Mixxx, Winamp's DSP) connects
to Station Server, which puts them on air in place of the music.

Each station with live shows on listens on its own port, as a SHOUTcast or
Icecast server would, so a presenter's existing settings work:

- Icecast: port P, any mount (like /live), user `source`, the live password.
- SHOUTcast (v1, "legacy"): port P, the live password. The encoder sends
  the audio to port P+1, as it does to a SHOUTcast server.

Song titles the encoder sends (Icecast's /admin/metadata or SHOUTcast's
/admin.cgi, both on port P) go out with the broadcast. Each show can be
kept as a recording: exactly what the encoder sent, saved as it arrives.

What arrives is decoded by ffmpeg to the same PCM as the music, and the
channel (channel.py) plays it in place of the playlist until the presenter
disconnects. One presenter at a time: a second is turned away.
"""
from __future__ import annotations

import base64
import collections
import hmac
import logging
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from station_server import __version__
from station_server.audio import BYTES_PER_SECOND, CHUNK, PCM_ARGS

logger = logging.getLogger(__name__)

# The encoder's stream type -> ffmpeg's name for it, when it says.
_FORMATS = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/aac": "aac", "audio/aacp": "aac",
            "application/ogg": "ogg", "audio/ogg": "ogg", "audio/opus": "ogg", "audio/flac": "flac"}
# A recording's file extension, by the same.
_EXTENSIONS = {"mp3": ".mp3", "aac": ".aac", "ogg": ".ogg", "flac": ".flac"}
_MAX_HEADER = 16384
_HELLO_SECONDS = 10.0  # to send its request or password
# Closing: how long, and how much, to read what an encoder sent after our
# last word (see _close_gently).
_CLOSE_SECONDS = 0.5
_CLOSE_BYTES = 65536
_MAX_CONNECTIONS = 32  # at once; more are turned away
# A presenter whose audio stops arriving for this long is taken off air
# (their connection has died without closing).
QUIET_SECONDS = 10.0
# Audio piling up beyond this (the presenter's clock runs a little fast)
# is let go, so they don't drift further and further behind.
MOST_BUFFERED_SECONDS = 4.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Evening out a presenter's volume, as it comes: towards the loudness the
# songs are levelled to (about -18 LUFS), up by at most 18 dB, peaks kept
# below full scale. It looks 1.5 s ahead, which the hand-over absorbs.
LEVEL_FILTER = "dynaudnorm=f=200:g=15:p=0.9:m=8:r=0.15:b=1"


class LiveSource:
    """One presenter's connection: their audio, decoded, waiting to be played."""

    def __init__(self, ffmpeg: str, content_type: str = "", presenter: str = "", address: str = "",
                 decoder: Callable[..., subprocess.Popen] | None = None, record_dir: Path | None = None,
                 level: bool = False) -> None:
        self.presenter = presenter[:100]
        self.address = address
        self.since = time.time()
        self.title = ""  # the song title the encoder last sent
        self.ended = threading.Event()  # the connection has gone and its audio is all played
        self.has_audio = threading.Event()  # some of their audio is decoded and ready
        self._closed = False
        self._lock = threading.Condition()
        self._pcm: collections.deque[bytes] = collections.deque()
        self._buffered = 0
        self._last_audio = time.monotonic()
        self._on_close: list[Callable[[], None]] = []
        args = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-probesize", "32768"]
        fmt = _FORMATS.get(content_type.split(";")[0].strip().lower())
        if fmt:
            args += ["-f", fmt]
        self.recording: Path | None = None
        self._file = None
        if record_dir is not None:
            self._start_recording(record_dir, _EXTENSIONS.get(fmt or "mp3", ".mp3"))
        args += ["-i", "pipe:0", "-vn"] + (["-af", LEVEL_FILTER] if level else []) + PCM_ARGS + ["pipe:1"]
        self._proc = (decoder or _popen)(args)
        threading.Thread(target=self._read, name="live-decode", daemon=True).start()

    def _start_recording(self, folder: Path, ext: str) -> None:
        who = "".join(c for c in self.presenter if c.isalnum() or c in " -_'&.").strip() or "Live"
        path = folder / f"{datetime.now():%Y-%m-%d %H%M} {who}{ext}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self._file = path.open("wb")
            self.recording = path
        except OSError as exc:
            logger.warning("live: can't record the show to %s: %s", path, exc)

    def _stop_recording(self) -> None:
        f, self._file = self._file, None
        if f is not None:
            try:
                f.close()
            except OSError:
                pass

    # ---- the connection side ----------------------------------------------

    def feed(self, data: bytes) -> bool:
        """Audio from the encoder. False once the source is closed."""
        if self._closed:
            return False
        f = self._file
        if f is not None:
            try:
                f.write(data)
            except (OSError, ValueError) as exc:  # the disk is full, say: stop recording, not the show
                logger.warning("live: recording stopped: %s", exc)
                self._stop_recording()
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def finish(self) -> None:
        """The encoder has disconnected: play out what's left."""
        self._stop_recording()
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass

    def on_close(self, fn: Callable[[], None]) -> None:
        self._on_close.append(fn)

    def close(self) -> None:
        """Take them off air now (End live show, or off air)."""
        self._closed = True
        self._stop_recording()
        for fn in self._on_close:
            try:
                fn()
            except OSError:
                pass
        if self._proc.poll() is None:
            self._proc.kill()
        with self._lock:
            self._pcm.clear()
            self._buffered = 0
            self._lock.notify_all()

    def _read(self) -> None:
        out = self._proc.stdout
        try:
            while True:
                chunk = out.read(CHUNK)
                if not chunk:
                    break
                with self._lock:
                    self._pcm.append(chunk)
                    self._buffered += len(chunk)
                    self._last_audio = time.monotonic()
                    self.has_audio.set()
                    # Running behind: let the oldest go.
                    while self._buffered > MOST_BUFFERED_SECONDS * BYTES_PER_SECOND:
                        self._buffered -= len(self._pcm.popleft())
                    self._lock.notify_all()
        except (OSError, ValueError):
            pass
        finally:
            self._proc.wait()
            with self._lock:
                self._decoded = True
                self._lock.notify_all()
            self._check_ended()

    _decoded = False

    def _check_ended(self) -> None:
        with self._lock:
            if self._closed or (self._decoded and not self._pcm):
                self.ended.set()

    # ---- the channel side -------------------------------------------------

    def read(self, timeout: float) -> bytes | None:
        """The next piece of audio, or None if none came in `timeout` seconds."""
        with self._lock:
            if not self._pcm and not self._decoded and not self._closed:
                self._lock.wait(timeout)
            if self._pcm:
                chunk = self._pcm.popleft()
                self._buffered -= len(chunk)
                return chunk
        self._check_ended()
        return None

    def buffered(self) -> float:
        """Seconds of audio waiting to be played."""
        with self._lock:
            return self._buffered / BYTES_PER_SECOND

    def quiet(self) -> float:
        """Seconds since any audio arrived."""
        with self._lock:
            return time.monotonic() - self._last_audio


def _popen(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)


class _Guesses:
    """After a few wrong passwords from an address, it's turned away for a while."""

    def __init__(self, most: int = 5, seconds: float = 300.0) -> None:
        self.most, self.seconds = most, seconds
        self._wrong: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, who: str) -> bool:
        with self._lock:
            now = time.monotonic()
            self._wrong[who] = [t for t in self._wrong.get(who, []) if now - t < self.seconds]
            return len(self._wrong[who]) >= self.most

    def wrong(self, who: str) -> None:
        with self._lock:
            self._wrong.setdefault(who, []).append(time.monotonic())


class LiveInput:
    """The live input of one station: ports P and P+1 (see above).

    `on_source(source)` is called when a presenter connects with the right
    password (the shared one, or a presenter's own: `presenters` maps each
    presenter's password to their name, shown and logged in place of what
    their encoder calls itself); it says whether they go on air. `on_title(source)` when the
    encoder sends a new song title.
    """

    def __init__(self, port: int, password: str, ffmpeg: str,
                 on_source: Callable[[LiveSource], bool], on_title: Callable[[LiveSource], None] | None = None,
                 host: str = "0.0.0.0", decoder=None, record_dir: Path | None = None, level: bool = False,
                 presenters: dict[str, str] | None = None) -> None:
        self.port = port
        self.password = password
        self.presenters = dict(presenters or {})
        self.ffmpeg = ffmpeg
        self._on_source = on_source
        self._on_title = on_title
        self._host = host
        self._decoder = decoder
        self.record_dir = record_dir  # where each show is recorded, or None for no recordings
        self.level = level  # even out presenters' volume
        self.source: LiveSource | None = None
        self.blocked_until = 0.0  # End live show: the presenter can't come straight back
        self._guesses = _Guesses()
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._slots = threading.BoundedSemaphore(_MAX_CONNECTIONS)

    def start(self) -> None:
        """Start listening. Raises OSError if a port is taken."""
        try:
            for port, handle in ((self.port, self._http), (self.port + 1, self._shoutcast)):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: really refuse a port in use
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self._host, port))
                s.listen(8)
                self._sockets.append(s)
                threading.Thread(target=self._accept, args=(s, handle), name=f"live-{port}", daemon=True).start()
        except OSError:
            self.stop()
            raise
        logger.info("live input listening on ports %d and %d", self.port, self.port + 1)

    def stop(self) -> None:
        self._stopped.set()
        for s in self._sockets:
            try:
                s.close()
            except OSError:
                pass
        self._sockets.clear()
        self.end()

    def end(self, block_seconds: float = 0.0) -> None:
        """Take the presenter off air (and keep them off for a while, since
        an encoder reconnects by itself)."""
        if block_seconds:
            self.blocked_until = time.monotonic() + block_seconds
        with self._lock:
            source, self.source = self.source, None
        if source:
            source.close()

    # ---- connections ------------------------------------------------------

    def _accept(self, sock: socket.socket, handle) -> None:
        while not self._stopped.is_set():
            try:
                conn, addr = sock.accept()
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn, addr[0], handle), daemon=True).start()

    def _serve(self, conn: socket.socket, who: str, handle) -> None:
        try:
            conn.settimeout(_HELLO_SECONDS)
            if self._guesses.blocked(who):
                conn.sendall(b"HTTP/1.0 429 Too Many Requests\r\n\r\nToo many wrong passwords\r\n")
                return
            handle(conn, who)
        except (OSError, ValueError) as exc:
            logger.debug("live input: %s: %s", who, exc)
        finally:
            self._slots.release()
            _close_gently(conn)

    def _right(self, given: str, who: str) -> str | None:
        """Who the password belongs to: a presenter's name, "" for the
        shared password, or None (and a wrong guess) if it's no one's."""
        found = None
        for password, name in [(self.password, "")] + list(self.presenters.items()):
            if password and hmac.compare_digest(given.encode(), password.encode()):
                found = name
        if found is None:
            self._guesses.wrong(who)
            logger.warning("live input: wrong password from %s", who)
        return found

    def _http(self, conn: socket.socket, who: str) -> None:
        """Port P: an Icecast source (SOURCE or PUT), or a title update."""
        head, rest = _read_until(conn, b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, _, target = lines[0].partition(" ")
        target = target.rsplit(" ", 1)[0] if " HTTP/" in target else target
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        url = urlsplit(target)
        query = _query(url.query)
        given = _basic_password(headers.get("authorization", ""))
        if method == "GET" and url.path in ("/admin/metadata", "/admin.cgi", "/admin/metadata.xsl"):
            if self._right(given or query.get("pass", ""), who) is None:
                conn.sendall(b"HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Basic realm=\"Live\"\r\n\r\n")
                return
            self._title(query.get("song", ""))
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/xml\r\n\r\n"
                         b"<?xml version=\"1.0\"?>\n<iceresponse><message>Metadata update successful</message>"
                         b"<return>1</return></iceresponse>\n")
            return
        if method not in ("SOURCE", "PUT"):
            body = f"Station Server {__version__}: a live input, for a presenter's encoder.\r\n".encode()
            conn.sendall(b"HTTP/1.0 404 Not Found\r\nContent-Type: text/plain\r\n\r\n" + body)
            return
        named = self._right(given, who)
        if named is None:
            conn.sendall(b"HTTP/1.0 401 Unauthorized\r\nWWW-Authenticate: Basic realm=\"Live\"\r\n\r\n")
            return
        source = self._open(who, headers.get("content-type", "audio/mpeg"),
                            named or headers.get("ice-name") or headers.get("icy-name") or "")
        if source is None:
            conn.sendall(b"HTTP/1.0 403 Forbidden\r\n\r\nSomeone is live already, or the station isn't on air\r\n")
            return
        conn.sendall(b"HTTP/1.0 200 OK\r\nServer: Icecast 2.4.4 (Station Server)\r\n\r\n")
        self._stream(conn, source, rest)

    def _shoutcast(self, conn: socket.socket, who: str) -> None:
        """Port P+1: a SHOUTcast v1 source: the password, then icy headers, then audio."""
        line, rest = _read_until(conn, b"\n")
        password = line.decode("latin-1").strip()
        if ":#" in password:  # SHOUTcast 2 style: password:#stream
            password = password.split(":#")[0]
        named = self._right(password, who)
        if named is None:
            conn.sendall(b"invalid password\r\n")
            return
        if self.blocked_until > time.monotonic() or self.source is not None:
            conn.sendall(b"Stream In Use\r\n")
            return
        conn.sendall(b"OK2\r\nicy-caps:11\r\n\r\n")
        head, rest = _read_headers(conn, rest)
        headers = {}
        for h in head.decode("latin-1").splitlines():
            name, _, value = h.partition(":")
            headers[name.strip().lower()] = value.strip()
        source = self._open(who, headers.get("content-type", "audio/mpeg"), named or headers.get("icy-name", ""))
        if source is None:
            return
        self._stream(conn, source, rest)

    def _open(self, who: str, content_type: str, presenter: str) -> LiveSource | None:
        if self.blocked_until > time.monotonic():
            return None
        with self._lock:
            if self.source is not None:
                return None
            source = LiveSource(self.ffmpeg, content_type, presenter, who, decoder=self._decoder,
                                record_dir=self.record_dir, level=self.level)
            self.source = source
        try:
            ok = self._on_source(source)
        except Exception:
            logger.exception("live input: couldn't put the presenter on air")
            ok = False
        if not ok:
            with self._lock:
                self.source = None
            source.close()
            if source.recording is not None:  # nothing was recorded
                source.recording.unlink(missing_ok=True)
            return None
        logger.info("live: %s connected from %s", presenter or "a presenter", who)
        return source

    def _stream(self, conn: socket.socket, source: LiveSource, first: bytes) -> None:
        source.on_close(lambda: conn.shutdown(socket.SHUT_RDWR))
        conn.settimeout(QUIET_SECONDS)
        try:
            if first and not source.feed(first):
                return
            while True:
                data = conn.recv(65536)
                if not data or not source.feed(data):
                    break
        except OSError:
            pass
        finally:
            source.finish()
            with self._lock:
                if self.source is source:
                    self.source = None
            logger.info("live: %s disconnected", source.presenter or "the presenter")

    def _title(self, song: str) -> None:
        source = self.source
        if source is None or not song or song == source.title:
            return
        source.title = song[:300]
        if self._on_title:
            self._on_title(source)


def lan_addresses() -> list[str]:
    """This computer's address on the local network, for Settings to show."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # picks the route out; sends nothing
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
            if addr not in found:
                found.append(addr)
    except OSError:
        pass
    return [a for a in found if not a.startswith("127.")]


def port_free(port: int) -> bool:
    """Whether nothing else is listening on `port` (for the Ready check)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _query(qs: str) -> dict[str, str]:
    """A URL's query: UTF-8 as a modern encoder sends it, else Latin-1 as
    older SHOUTcast encoders do."""
    try:
        parsed = parse_qs(qs, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        parsed = parse_qs(qs, encoding="latin-1")
    return {k: v[0] for k, v in parsed.items()}


def _read_headers(conn: socket.socket, data: bytes) -> tuple[bytes, bytes]:
    """A SHOUTcast encoder's icy- headers, which end with a blank line
    (some send bare newlines): (the headers, the audio after them)."""
    while True:
        for end in (b"\r\n\r\n", b"\n\n"):
            if data.startswith(end[len(end) // 2:]):  # no headers at all
                return b"", data[len(end) // 2:]
            if end in data:
                before, _, after = data.partition(end)
                return before, after
        if len(data) > _MAX_HEADER:
            raise ValueError("too much before the audio")
        more = conn.recv(4096)
        if not more:
            raise ValueError("closed before any audio")
        data += more


def _close_gently(conn: socket.socket) -> None:
    """Close so the encoder reads our reply before the connection ends.

    Closing a socket with unread data in it resets the connection, and on
    Windows a reset throws away a reply the encoder hasn't read yet: a
    locked-out encoder got a dropped connection instead of "429 Too Many
    Requests" (we answer that before reading its request). So: say we've
    finished sending, read what's left (a little, briefly: an encoder may
    still be sending audio), then close."""
    try:
        conn.shutdown(socket.SHUT_WR)
        conn.settimeout(_CLOSE_SECONDS)
        deadline, left = time.monotonic() + _CLOSE_SECONDS, _CLOSE_BYTES
        while left > 0 and time.monotonic() < deadline:
            more = conn.recv(min(left, 4096))
            if not more:  # they closed too: a clean end
                break
            left -= len(more)
    except OSError:
        pass  # already gone, or a timeout: close anyway
    try:
        conn.close()
    except OSError:
        pass


def _read_until(conn: socket.socket, end: bytes, data: bytes = b"") -> tuple[bytes, bytes]:
    """Read up to `end`: (what came before it, what came after)."""
    while end not in data:
        more = conn.recv(4096)
        if not more:
            raise ValueError("closed before it said anything")
        data += more
        if len(data) > _MAX_HEADER:
            raise ValueError("too much before the audio")
    before, _, after = data.partition(end)
    return before, after


def _basic_password(auth: str) -> str:
    if not auth.lower().startswith("basic "):
        return ""
    try:
        return base64.b64decode(auth[6:].strip()).decode("utf-8").partition(":")[2]
    except ValueError:
        return ""
