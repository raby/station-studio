"""Local HTTP server: JSON API, the web UI, and audio with Range support.

Binds to 127.0.0.1 only. Two guards keep other websites in the same
browser from driving it: requests must carry a localhost Host header
(blocks DNS rebinding), and every state-changing request must carry the
`X-Station-Player` header, which a cross-origin page can't send without
a CORS preflight this server never approves.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from station_player import __version__
from station_player.config import ConfigStore
from station_player.library import Library

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
API_HEADER = "X-Station-Player"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_EXTRA_TYPES = {
    ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac",
    ".opus": "audio/ogg", ".oga": "audio/ogg", ".ogg": "audio/ogg",
    ".wav": "audio/wav", ".aif": "audio/aiff", ".aiff": "audio/aiff",
    ".webm": "audio/webm", ".mp3": "audio/mpeg",
}


def content_type(path: Path) -> str:
    return _EXTRA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Parse a single `bytes=` range into inclusive (start, end).

    Returns None when the header is unusable (caller serves the whole
    file) and raises ValueError when it is unsatisfiable (416).
    """
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    first, last = m.groups()
    if first == "" and last == "":
        return None
    if first == "":  # suffix range: last N bytes
        n = int(last)
        if n == 0:
            raise ValueError("empty suffix range")
        return max(0, size - n), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


def resolve_stream_url(url: str, fetch=None) -> str:
    """Turn a .pls / .m3u playlist URL into the stream URL inside it."""
    path = urlparse(url).path.lower()
    if not path.endswith((".pls", ".m3u")):
        return url
    fetch = fetch or _fetch_text
    try:
        body = fetch(url)
    except Exception:
        return url
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.startswith(("http://", "https://")):
            return line
    return url


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=8) as resp:
        return resp.read(64 * 1024).decode("utf-8", "replace")


class PlayerApp:
    """Shared state for request handlers."""

    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self.library = Library()
        self._lock = threading.Lock()
        self.rescan()

    def rescan(self) -> None:
        cfg = self.store.config
        lib = Library()
        lib.load(cfg.playlist_sources, cfg.music_folders, cfg.path_remaps)
        with self._lock:
            self.library = lib

    def state(self) -> dict:
        lib = self.library
        return {
            "version": __version__,
            "playlists": lib.playlist_summaries(),
            "stations": self.store.config.stations,
            "config": self.store.config.to_json(),
            "errors": lib.errors,
        }

    # ---- mutations (all persist + rescan where needed) ----------------

    def add_source(self, kind: str, path: str) -> None:
        target = self._list_for(kind)
        clean = str(Path(path.strip()).expanduser())
        if not path.strip():
            raise ValueError("Path is empty")
        if not Path(clean).exists():
            raise ValueError(f"Not found: {clean}")
        if clean not in target:
            target.append(clean)
        self.store.save()
        self.rescan()

    def remove_source(self, kind: str, path: str) -> None:
        target = self._list_for(kind)
        if path in target:
            target.remove(path)
        self.store.save()
        self.rescan()

    def _list_for(self, kind: str) -> list[str]:
        cfg = self.store.config
        if kind == "playlist":
            return cfg.playlist_sources
        if kind == "music":
            return cfg.music_folders
        raise ValueError(f"Unknown source kind: {kind}")

    def add_remap(self, src: str, dst: str) -> None:
        if not src.strip() or not dst.strip():
            raise ValueError("Both 'from' and 'to' are needed")
        self.store.config.path_remaps.append({"from": src.strip(), "to": dst.strip()})
        self.store.save()
        self.rescan()

    def remove_remap(self, index: int) -> None:
        remaps = self.store.config.path_remaps
        if 0 <= index < len(remaps):
            remaps.pop(index)
        self.store.save()
        self.rescan()

    def add_station(self, name: str, url: str) -> dict:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Stream URL must start with http:// or https://")
        station = {"name": name.strip() or url, "url": resolve_stream_url(url)}
        self.store.config.stations.append(station)
        self.store.save()
        return station

    def remove_station(self, index: int) -> None:
        stations = self.store.config.stations
        if 0 <= index < len(stations):
            stations.pop(index)
        self.store.save()


def make_handler(app: PlayerApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"StationPlayer/{__version__}"

        def log_message(self, fmt, *args):  # quieter than stderr-per-request
            logger.debug("%s - %s", self.address_string(), fmt % args)

        # ---- helpers ----------------------------------------------------

        def _host_ok(self) -> bool:
            try:
                host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            except ValueError:
                return False
            return host in _ALLOWED_HOSTS

        def _send_json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status, message):
            self._send_json({"error": message}, status)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ValueError("Request too large")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data

        # ---- GET --------------------------------------------------------

        def do_HEAD(self):
            self.do_GET(head_only=True)

        def do_GET(self, head_only: bool = False):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Bad host")
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send_json(app.state())
            if path.startswith("/api/playlists/"):
                detail = app.library.playlist_detail(path.rsplit("/", 1)[1])
                if detail is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No such playlist")
                return self._send_json(detail)
            if path.startswith("/audio/"):
                return self._serve_audio(path.rsplit("/", 1)[1], head_only)
            return self._serve_static(path, head_only)

        def _serve_static(self, path: str, head_only: bool):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (STATIC_DIR / name).resolve()
            if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve_audio(self, track_id: str, head_only: bool):
            track = app.library.tracks.get(track_id)
            if track is None or track.path is None:
                return self._error(HTTPStatus.NOT_FOUND, "Track not available")
            try:
                size = track.path.stat().st_size
            except OSError:
                return self._error(HTTPStatus.NOT_FOUND, "File is gone")
            start, end, status = 0, size - 1, HTTPStatus.OK
            range_header = self.headers.get("Range")
            if range_header:
                try:
                    parsed = parse_range(range_header, size)
                except ValueError:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if parsed:
                    start, end = parsed
                    status = HTTPStatus.PARTIAL_CONTENT
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type(track.path))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            try:
                with track.path.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser seeks by dropping the connection

        # ---- POST -------------------------------------------------------

        def do_POST(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Bad host")
            if self.headers.get(API_HEADER) != "1":
                return self._error(HTTPStatus.FORBIDDEN, f"Missing {API_HEADER} header")
            path = urlparse(self.path).path
            try:
                data = self._read_json()
                if path == "/api/rescan":
                    app.rescan()
                elif path == "/api/sources":
                    app.add_source(str(data.get("kind", "")), str(data.get("path", "")))
                elif path == "/api/sources/remove":
                    app.remove_source(str(data.get("kind", "")), str(data.get("path", "")))
                elif path == "/api/remaps":
                    app.add_remap(str(data.get("from", "")), str(data.get("to", "")))
                elif path == "/api/remaps/remove":
                    app.remove_remap(int(data.get("index", -1)))
                elif path == "/api/stations":
                    app.add_station(str(data.get("name", "")), str(data.get("url", "")))
                elif path == "/api/stations/remove":
                    app.remove_station(int(data.get("index", -1)))
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "Not found")
            except (ValueError, TypeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return self._send_json(app.state())

    return Handler


def make_server(app: PlayerApp, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server

