"""Where the encoded stream goes: a SHOUTcast or Icecast server, or a file.

Every sender has the same three calls: `connect`, `send` (encoded audio) and
`set_title` (now playing). A sender that loses its server keeps quiet,
drops audio while it is away, and reconnects by itself every few seconds,
so a server restart never stops the playout.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from station_server import __version__

logger = logging.getLogger(__name__)

USER_AGENT = f"station-server/{__version__}"
# SHOUTcast 1.x only answers admin.cgi for browsers.
ADMIN_USER_AGENT = f"Mozilla/5.0 ({USER_AGENT})"
RETRY_SECONDS = 5.0


class SenderError(Exception):
    """The server refused us (wrong password, mount in use, not a server)."""


@dataclass
class StreamInfo:
    """What the server shows about the station."""
    name: str = "Station Server"
    genre: str = ""
    url: str = ""
    description: str = ""
    public: bool = False
    bitrate: int = 128
    content_type: str = "audio/mpeg"


class _Sender:
    """Reconnect and title bookkeeping shared by the network senders."""

    describe = "server"

    def __init__(self, info: StreamInfo, timeout: float = 10.0, clock=time.monotonic) -> None:
        self.info = info
        self.timeout = timeout
        self._clock = clock
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._next_try = 0.0
        self._title: str | None = None
        self.connected = False

    # ---- subclass hooks -------------------------------------------------

    def _open(self) -> socket.socket:
        raise NotImplementedError

    def _send_title(self, title: str) -> None:
        raise NotImplementedError

    # ---- public ---------------------------------------------------------

    def connect(self) -> None:
        """Connect now, raising SenderError or OSError on failure (used at
        start-up, so a wrong password is reported instead of retried)."""
        with self._lock:
            self._sock = self._open()
            self.connected = True
        logger.info("connected to %s", self.describe)

    def send(self, data: bytes) -> None:
        with self._lock:
            if self._sock is None:
                if self._clock() < self._next_try:
                    return
                try:
                    self._sock = self._open()
                except (OSError, SenderError) as exc:
                    self._next_try = self._clock() + RETRY_SECONDS
                    logger.warning("can't reach %s (%s); retrying in %.0fs", self.describe, exc, RETRY_SECONDS)
                    return
                self.connected = True
                logger.info("reconnected to %s", self.describe)
                if self._title:
                    threading.Thread(target=self._title_quietly, args=(self._title,), daemon=True).start()
            try:
                self._sock.sendall(data)
            except OSError as exc:
                logger.warning("lost %s: %s", self.describe, exc)
                self._drop()

    def set_title(self, title: str) -> None:
        """Tell the server what's playing. Never raises: a missed title
        isn't worth interrupting the broadcast for."""
        self._title = title
        if self.connected:
            self._title_quietly(title)

    def close(self) -> None:
        with self._lock:
            self._drop()

    # ---- helpers --------------------------------------------------------

    def _title_quietly(self, title: str) -> None:
        try:
            self._send_title(title)
        except Exception as exc:
            logger.warning("couldn't update the title on %s: %s", self.describe, exc)

    def _drop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.connected = False
        self._next_try = self._clock() + RETRY_SECONDS

    def _read_reply(self, sock: socket.socket) -> bytes:
        """The server's reply to our handshake, up to the blank line."""
        reply = b""
        while b"\r\n\r\n" not in reply and b"\n\n" not in reply and len(reply) < 4096:
            part = sock.recv(1024)
            if not part:
                break
            reply += part
        return reply


class ShoutcastSender(_Sender):
    """A SHOUTcast DNAS, using the version 1 source protocol.

    Both DNAS 1.9.x and 2.x accept it: the source connects to the port above
    the listener port, sends its password, and on "OK2" sends the station's
    details and then the audio. DNAS 2.x takes `password:#2` for stream 2.
    Titles go to admin.cgi on the listener port.
    """

    def __init__(self, host: str, port: int, password: str, info: StreamInfo,
                 sid: int = 1, admin_password: str | None = None, **kw) -> None:
        super().__init__(info, **kw)
        self.host, self.port, self.password, self.sid = host, port, password, sid
        self.admin_password = admin_password or password
        self.describe = f"SHOUTcast {host}:{port}" + (f" stream {sid}" if sid != 1 else "")

    def _open(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port + 1), timeout=self.timeout)
        try:
            secret = self.password if self.sid == 1 else f"{self.password}:#{self.sid}"
            sock.sendall(secret.encode("utf-8") + b"\r\n")
            reply = self._read_reply(sock)
            if not reply.startswith(b"OK"):
                text = reply.decode("latin-1").strip() or "no reply (check the password)"
                raise SenderError(f"SHOUTcast refused the source: {text}")
            i = self.info
            headers = [
                f"icy-name:{i.name}",
                f"icy-genre:{i.genre}",
                f"icy-url:{i.url}",
                f"icy-pub:{1 if i.public else 0}",
                f"icy-br:{i.bitrate}",
                f"content-type:{i.content_type}",
            ]
            sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("utf-8"))
            sock.settimeout(self.timeout)
            return sock
        except BaseException:
            sock.close()
            raise

    def _send_title(self, title: str) -> None:
        query = urllib.parse.urlencode({
            "sid": self.sid, "pass": self.admin_password, "mode": "updinfo", "song": title,
        })
        req = urllib.request.Request(
            f"http://{self.host}:{self.port}/admin.cgi?{query}",
            headers={"User-Agent": ADMIN_USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    def count_listeners(self) -> dict:
        """How many are listening, from the server's 7.html (DNAS 1.9 and 2.x):
        current, status, peak, max, unique, bitrate, title."""
        req = urllib.request.Request(f"http://{self.host}:{self.port}/7.html?sid={self.sid}",
                                     headers={"User-Agent": ADMIN_USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            text = resp.read(65536).decode("latin-1")
        return parse_7html(text)


def parse_7html(text: str) -> dict:
    fields = re.sub(r"<[^>]*>", "", text).strip().split(",", 6)
    try:
        current, peak, most = int(fields[0]), int(fields[2]), int(fields[3])
    except (IndexError, ValueError):
        raise ValueError("the server's listener count couldn't be read") from None
    return {"listeners": current, "peak": peak, "max": most}


class IcecastSender(_Sender):
    """An Icecast 2.4+ server, as a source on one mount (HTTP PUT)."""

    def __init__(self, host: str, port: int, mount: str, password: str, info: StreamInfo,
                 user: str = "source", **kw) -> None:
        super().__init__(info, **kw)
        self.host, self.port, self.user, self.password = host, port, user, password
        self.mount = mount if mount.startswith("/") else f"/{mount}"
        self.describe = f"Icecast {host}:{port}{self.mount}"

    def _auth(self) -> str:
        token = base64.b64encode(f"{self.user}:{self.password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _open(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            i = self.info
            lines = [
                f"PUT {urllib.parse.quote(self.mount)} HTTP/1.1",
                f"Host: {self.host}:{self.port}",
                f"Authorization: {self._auth()}",
                f"User-Agent: {USER_AGENT}",
                f"Content-Type: {i.content_type}",
                f"Ice-Name: {i.name}",
                f"Ice-Genre: {i.genre}",
                f"Ice-Url: {i.url}",
                f"Ice-Description: {i.description}",
                f"Ice-Public: {1 if i.public else 0}",
                f"Ice-Audio-Info: bitrate={i.bitrate}",
                "Expect: 100-continue",
            ]
            sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("utf-8"))
            reply = self._read_reply(sock)
            status = reply.split(b"\r\n", 1)[0].decode("latin-1")
            parts = status.split(" ", 2)
            if len(parts) < 2 or parts[1] not in ("100", "200"):
                raise SenderError(f"Icecast refused the source: {status or 'no reply'}")
            sock.settimeout(self.timeout)
            return sock
        except BaseException:
            sock.close()
            raise

    def _send_title(self, title: str) -> None:
        query = urllib.parse.urlencode({
            "mount": self.mount, "mode": "updinfo", "song": title, "charset": "UTF-8",
        })
        req = urllib.request.Request(
            f"http://{self.host}:{self.port}/admin/metadata?{query}",
            headers={"User-Agent": USER_AGENT, "Authorization": self._auth()},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    def count_listeners(self) -> dict:
        """How many are listening to this mount, from status-json.xsl."""
        req = urllib.request.Request(f"http://{self.host}:{self.port}/status-json.xsl",
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read(2_000_000))
        return parse_icestats(data, self.mount)


def parse_icestats(data, mount: str) -> dict:
    sources = (data.get("icestats") or {}).get("source") if isinstance(data, dict) else None
    if isinstance(sources, dict):
        sources = [sources]
    for src in sources or []:
        url = str(src.get("listenurl") or "")
        if urllib.parse.urlsplit(url).path == mount:
            return {"listeners": int(src.get("listeners") or 0), "peak": int(src.get("listener_peak") or 0),
                    "max": None}
    raise ValueError(f"the server doesn't list {mount}")


class FileSender:
    """Writes the broadcast to an MP3 file instead, to hear exactly what
    listeners would get. Titles go to a .txt beside it with the time."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.describe = str(self.path)
        self.connected = False
        self._fh = None
        self._titles = None
        self._started = 0.0

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("wb")
        self._titles = self.path.with_suffix(".txt").open("w", encoding="utf-8")
        self._started = time.monotonic()
        self.connected = True

    def send(self, data: bytes) -> None:
        if self._fh:
            self._fh.write(data)

    def set_title(self, title: str) -> None:
        if self._titles:
            secs = int(time.monotonic() - self._started)
            self._titles.write(f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}  {title}\n")
            self._titles.flush()

    def count_listeners(self) -> None:
        return None

    def close(self) -> None:
        for fh in (self._fh, self._titles):
            if fh:
                fh.close()
        self._fh = self._titles = None
        self.connected = False


def sender_from_url(url: str, info: StreamInfo, password: str | None = None):
    """Build a sender from `--to`:

    - shoutcast://[:password@]host:port[?sid=2]
    - icecast://[source[:password]@]host:port/mount
    - anything else is a file path (record to MP3)

    A password given separately (the --password option or
    $STATION_SERVER_PASSWORD) wins over one in the URL, so it needn't be
    typed where it lands in shell history.
    """
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("shoutcast", "icecast"):
        return FileSender(Path(url))
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"{url}: give the server as host:port")
    secret = password or urllib.parse.unquote(parsed.password or "")
    if not secret:
        raise ValueError("No password: put it in the URL, or use --password / $STATION_SERVER_PASSWORD")
    if scheme == "shoutcast":
        qs = urllib.parse.parse_qs(parsed.query)
        sid = int(qs.get("sid", ["1"])[0])
        return ShoutcastSender(parsed.hostname, parsed.port, secret, info, sid=sid)
    mount = parsed.path.strip()
    if mount in ("", "/"):
        raise ValueError(f"{url}: give the Icecast mount, e.g. icecast://host:8000/radio")
    user = urllib.parse.unquote(parsed.username or "") or "source"
    return IcecastSender(parsed.hostname, parsed.port, mount, secret, info, user=user)
