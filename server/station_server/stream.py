"""Streaming from this computer: Station Server is the station's server.

Listeners connect straight to Station Server, as they would to a SHOUTcast
DNAS or Icecast, with nothing else installed (`serve://:8000` as the
station's server; see senders.sender_from_url):

- any path plays the stream (`/`, `/;`, `/stream`, `/live.mp3`...), so old
  DNAS links keep working; a browser opening `/` gets a small page with a
  player instead
- song titles go to players that ask for them (ICY metadata)
- a few seconds of audio go out at once, so players start quickly
- `/7.html`, `/stats`, `/currentsong` (SHOUTcast) and `/status-json.xsl`
  (Icecast) answer as those servers do, for listener counts and directories
- `/listen.pls` and `/listen.m3u` for players that want a playlist file
- at most `most` listeners; one who can't keep up is let go rather than
  holding up the rest
"""
from __future__ import annotations

import html
import json
import logging
import queue as queues
import select
import socket
import sys
import threading
import time
import urllib.parse
from collections import deque

from station_server import __version__

logger = logging.getLogger(__name__)

SERVER_NAME = f"Station Server/{__version__}"
METAINT = 16000
# About 4 seconds at 128 kbps sent on connect, so players start straight away.
BURST_BYTES = 64 * 1024
# About a minute of audio waiting for a slow listener, then they go.
BACKLOG = 240
REQUEST_SECONDS = 10.0
SEND_SECONDS = 30.0
MOST_LISTENERS = 500
# Connections still sending their request, beyond the listeners: more
# than this at once (someone holding connections open) are turned away.
MOST_WAITING = 64


class PortInUse(OSError):
    pass


def _icy_meta(title: str) -> bytes:
    """One ICY metadata block: a length byte (in 16s), then StreamTitle."""
    text = f"StreamTitle='{title.replace(chr(39), chr(8217))}';".encode("utf-8", "replace")[:16 * 255]
    blocks = -(-len(text) // 16)
    return bytes([blocks]) + text.ljust(blocks * 16, b"\0")


class _Listener:
    def __init__(self, address: str, metadata: bool, burst: int = 0) -> None:
        self.address = address
        self.metadata = metadata
        self.queue: queues.Queue = queues.Queue(BACKLOG + burst)
        self.since = time.time()


class StreamServer:
    """One station's stream, served on `port` (all addresses unless `host`)."""

    def __init__(self, port: int, name: str = "Station Server", genre: str = "", url: str = "",
                 public: bool = False, bitrate: int = 128, most: int = 20, host: str = "",
                 content_type: str = "audio/mpeg") -> None:
        self.port, self.host = port, host
        self.name, self.genre, self.url, self.public = name, genre, url, public
        self.bitrate, self.content_type = bitrate, content_type
        self.most = max(1, min(int(most or 1), MOST_LISTENERS))
        self.title = ""
        self.peak = 0
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._listeners: list[_Listener] = []
        self._burst: deque[bytes] = deque()
        self._burst_bytes = 0
        self._title_changed = 0  # bumped on every new title
        self._waiting = 0  # connections still sending their request

    # ---- the station side -----------------------------------------------

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform != "win32":  # on Windows this would let two programs share the port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError as exc:
            sock.close()
            raise PortInUse(f"Port {self.port} is in use on this computer: is the SHOUTcast DNAS, Icecast "
                            "or another station still running here? Stop it, or choose another port.") from exc
        sock.listen(64)
        self.port = sock.getsockname()[1]  # the one chosen, for port 0
        self._sock = sock
        threading.Thread(target=self._accept, args=(sock,), name=f"stream-{self.port}", daemon=True).start()
        logger.info("streaming on port %d", self.port)

    def stop(self) -> None:
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        with self._lock:
            for who in self._listeners:
                self._end(who)
            self._listeners.clear()
            self._burst.clear()
            self._burst_bytes = 0

    def publish(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._burst.append(data)
            self._burst_bytes += len(data)
            while self._burst_bytes - len(self._burst[0]) >= BURST_BYTES:
                self._burst_bytes -= len(self._burst.popleft())
            for who in list(self._listeners):
                try:
                    who.queue.put_nowait(data)
                except queues.Full:  # can't keep up: let them go
                    logger.info("port %d: %s couldn't keep up", self.port, who.address)
                    self._listeners.remove(who)
                    self._end(who)

    def set_title(self, title: str) -> None:
        with self._lock:
            self.title = title or ""
            self._title_changed += 1

    @property
    def listening(self) -> int:
        with self._lock:
            return len(self._listeners)

    def counts(self) -> dict:
        with self._lock:
            return {"listeners": len(self._listeners), "peak": self.peak, "max": self.most, "source": True}

    # ---- the listener side ----------------------------------------------

    def _accept(self, sock: socket.socket) -> None:
        # A short timeout, so stop() frees the port promptly everywhere: a
        # thread blocked in accept() keeps a closed socket listening on Linux.
        sock.settimeout(0.25)
        while self._sock is sock:
            try:
                conn, addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return  # stopped
            with self._lock:
                busy = self._waiting >= MOST_WAITING
                if not busy:
                    self._waiting += 1
            if busy:
                conn.close()
                continue
            conn.settimeout(None)
            threading.Thread(target=self._serve, args=(conn, addr[0]), daemon=True).start()

    def _serve(self, conn: socket.socket, address: str) -> None:
        try:
            try:
                conn.settimeout(REQUEST_SECONDS)
                request = self._read_request(conn)
            finally:
                with self._lock:
                    self._waiting -= 1
            if request is None:
                return
            method, target, headers = request
            path = urllib.parse.urlsplit(target).path
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
            if method not in ("GET", "HEAD"):
                return self._reply(conn, 405, "text/plain", b"Only GET\n")
            page = self._page(path, query, headers)
            if page is not None:
                status, ctype, body = page
                return self._reply(conn, status, ctype, b"" if method == "HEAD" else body)
            self._stream(conn, address, method, headers)
        except OSError:
            pass  # the listener went
        except Exception:
            logger.exception("port %d: a request from %s failed", self.port, address)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _read_request(conn: socket.socket):
        data = b""
        while b"\r\n\r\n" not in data and b"\n\n" not in data:
            part = conn.recv(4096)
            if not part:
                return None
            data += part
            if len(data) > 16384:
                return None
        lines = data.split(b"\n")
        try:
            method, target, _ = lines[0].decode("latin-1").strip().split(" ", 2)
        except ValueError:
            return None
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.decode("latin-1").partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        return method.upper(), target, headers

    def _page(self, path: str, query: dict, headers: dict):
        """(status, content type, body) for anything that isn't the stream."""
        c = self.counts()
        if path == "/7.html":
            fields = [c["listeners"], 1, c["peak"], c["max"], c["listeners"], self.bitrate, self.title]
            return 200, "text/html", f"<html><body>{','.join(map(str, fields))}</body></html>".encode()
        if path == "/currentsong":
            return 200, "text/plain; charset=utf-8", self.title.encode()
        if path in ("/stats", "/statistics"):
            stats = {"currentlisteners": c["listeners"], "peaklisteners": c["peak"], "maxlisteners": c["max"],
                     "uniquelisteners": c["listeners"], "streamstatus": 1, "bitrate": str(self.bitrate),
                     "servertitle": self.name, "servergenre": self.genre, "serverurl": self.url,
                     "songtitle": self.title, "content": self.content_type, "version": SERVER_NAME}
            if query.get("json", ["0"])[0] == "1":
                return 200, "application/json", json.dumps(stats).encode()
            xml = "".join(f"<{k.upper()}>{html.escape(str(v))}</{k.upper()}>" for k, v in stats.items())
            return 200, "text/xml", f"<?xml version=\"1.0\"?><SHOUTCASTSERVER>{xml}</SHOUTCASTSERVER>".encode()
        if path == "/status-json.xsl":
            source = {"listenurl": f"http://{self._host(headers)}/stream", "listeners": c["listeners"],
                      "listener_peak": c["peak"], "max_listeners": c["max"], "server_name": self.name,
                      "genre": self.genre, "server_url": self.url, "bitrate": self.bitrate,
                      "server_type": self.content_type, "title": self.title}
            return 200, "application/json", json.dumps({"icestats": {"server_id": SERVER_NAME, "source": source}}).encode()
        if path in ("/listen.pls", "/listen.m3u"):
            url = f"http://{self._host(headers)}/;"
            if path.endswith(".m3u"):
                return 200, "audio/x-mpegurl", f"#EXTM3U\n#EXTINF:-1,{self.name}\n{url}\n".encode()
            return 200, "audio/x-scpls", f"[playlist]\nNumberOfEntries=1\nFile1={url}\nTitle1={self.name}\nLength1=-1\nVersion=2\n".encode()
        if path == "/favicon.ico":
            return 404, "text/plain", b"Not found\n"
        if path == "/" and "text/html" in headers.get("accept", "") and "icy-metadata" not in headers:
            return 200, "text/html; charset=utf-8", self._html(c).encode()
        return None

    def _host(self, headers: dict) -> str:
        return headers.get("host") or f"localhost:{self.port}"

    def _html(self, c: dict) -> str:
        e = html.escape
        return (
            "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{e(self.name)}</title><style>body{{font:16px system-ui,sans-serif;max-width:32rem;margin:2rem auto;"
            "padding:0 1rem;background:#111;color:#eee}audio{width:100%}small{color:#999}</style></head><body>"
            f"<h1>{e(self.name)}</h1><p>{e(self.title) or '&nbsp;'}</p>"
            "<audio controls preload='none' src='/;'></audio>"
            f"<p><small>{c['listeners']} listening · {self.bitrate} kbps · "
            "<a href='/listen.pls' style='color:#aaa'>listen.pls</a> · "
            f"{e(SERVER_NAME)}</small></p></body></html>")

    def _stream(self, conn: socket.socket, address: str, method: str, headers: dict) -> None:
        metadata = headers.get("icy-metadata") == "1"
        with self._lock:
            full = len(self._listeners) >= self.most
            if not full and method == "GET":
                who = _Listener(address, metadata, len(self._burst))
                for chunk in self._burst:
                    who.queue.put_nowait(chunk)
                self._listeners.append(who)
                self.peak = max(self.peak, len(self._listeners))
                seen = -1  # send the title straight away
        if full:
            return self._reply(conn, 503, "text/plain", f"{self.name} is full ({self.most} listening)\n".encode())
        head = [
            "HTTP/1.0 200 OK", f"Content-Type: {self.content_type}", f"Server: {SERVER_NAME}",
            f"icy-name: {self.name}", f"icy-genre: {self.genre}", f"icy-url: {self.url}",
            f"icy-pub: {1 if self.public else 0}", f"icy-br: {self.bitrate}",
            "Cache-Control: no-cache, no-store", "Access-Control-Allow-Origin: *", "Connection: close",
        ]
        if metadata:
            head.append(f"icy-metaint: {METAINT}")
        conn.settimeout(SEND_SECONDS)
        if method == "HEAD":
            conn.sendall(("\r\n".join(head) + "\r\n\r\n").encode("utf-8", "replace"))
            return
        logger.debug("port %d: %s listening", self.port, address)
        try:
            conn.sendall(("\r\n".join(head) + "\r\n\r\n").encode("utf-8", "replace"))
            until_meta = METAINT
            quiet = 0.0
            while True:
                try:
                    data = who.queue.get(timeout=1.0)
                    quiet = 0.0
                except queues.Empty:
                    quiet += 1.0
                    if quiet >= SEND_SECONDS or self._gone(conn):
                        return
                    continue
                if data is None:  # off air
                    return
                if not metadata:
                    conn.sendall(data)
                    continue
                while data:
                    part, data = data[:until_meta], data[until_meta:]
                    conn.sendall(part)
                    until_meta -= len(part)
                    if until_meta == 0:
                        with self._lock:
                            changed, title = self._title_changed, self.title
                        conn.sendall(_icy_meta(title) if changed != seen else b"\0")
                        seen = changed
                        until_meta = METAINT
        finally:
            with self._lock:
                if who in self._listeners:
                    self._listeners.remove(who)
            logger.debug("port %d: %s stopped listening", self.port, address)

    @staticmethod
    def _gone(conn: socket.socket) -> bool:
        """Whether the listener has hung up (seen between chunks of audio)."""
        try:
            readable, _, _ = select.select([conn], [], [], 0)
            return bool(readable) and conn.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    @staticmethod
    def _reply(conn: socket.socket, status: int, ctype: str, body: bytes) -> None:
        reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 503: "Service Unavailable"}[status]
        conn.sendall((f"HTTP/1.0 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                      f"Server: {SERVER_NAME}\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n").encode()
                     + body)

    @staticmethod
    def _end(who: _Listener) -> None:
        try:
            who.queue.put_nowait(None)
        except queues.Full:
            try:
                who.queue.get_nowait()
                who.queue.put_nowait(None)
            except (queues.Empty, queues.Full):
                pass
