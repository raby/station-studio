"""Local HTTP server: JSON API, the web UI, and audio with Range support.

Binds to 127.0.0.1 only. Two guards keep other websites in the same
browser from driving it: requests must carry a localhost Host header
(blocks DNS rebinding), and every state-changing request must carry the
`X-Station-Player` header, which a cross-origin page can't send without
a CORS preflight this server never approves.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import socket
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from station_player import __version__, radio
from station_player.config import ConfigStore
from station_player.desktop import Desktop
from station_player.library import Library, _short_hash
from station_player.tags import MAX_ART_BYTES, TagCache, folder_cover, image_type, read_art

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
API_HEADER = "X-Station-Player"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
# Fixed tables rather than `mimetypes`, which on Windows reads (sometimes
# wrong) registry mappings. Audio is only ever served with an audio type.
AUDIO_TYPES = {
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".opus": "audio/ogg", ".oga": "audio/ogg",
    ".ogg": "audio/ogg", ".wav": "audio/wav", ".aif": "audio/aiff",
    ".aiff": "audio/aiff", ".webm": "audio/webm", ".mp3": "audio/mpeg",
}
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
# The UI only talks to this server; audio may come from radio stations.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' http: https:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_STREAM_FETCH_SECONDS = 10


def content_type(path: Path) -> str | None:
    return AUDIO_TYPES.get(path.suffix.lower())


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
    if size == 0:
        raise ValueError("empty file has no satisfiable range")
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
    """Turn a .pls / .m3u playlist URL into the stream URL inside it.

    Raises ValueError when a playlist link can't be read or holds no
    stream, rather than saving a station that can never play.
    """
    path = urlparse(url).path.lower()
    if not path.endswith((".pls", ".m3u")):
        return url
    fetch = fetch or _fetch_text
    try:
        body = fetch(url)
    except Exception as exc:
        raise ValueError(f"Couldn't read that playlist link: {exc}") from None
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.startswith(("http://", "https://")):
            return line
    raise ValueError("That playlist link doesn't contain a stream URL")


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme not in ("http", "https"):
            raise urllib.error.URLError(f"refusing redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpOnlyRedirects)


def _fetch_text(url: str, deadline: float = _STREAM_FETCH_SECONDS) -> str:
    """Fetch up to 64 KB, giving up after `deadline` seconds overall
    (urllib's own timeout is per socket read, so a slow-drip server
    could otherwise hold a request thread indefinitely)."""
    result: dict = {}

    def work():
        try:
            with _opener.open(url, timeout=deadline) as resp:
                result["body"] = resp.read(64 * 1024).decode("utf-8", "replace")
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(deadline)
    if "body" in result:
        return result["body"]
    raise result.get("error") or TimeoutError("timed out")


class PlayerApp:
    """Shared state for request handlers.

    A lock serialises config changes (mutate, then save; a failed save
    rolls the in-memory config back). Library rebuilds run outside the
    lock from a snapshot of the config, so reads stay responsive during a
    long scan; each change bumps a generation counter and a rebuild only
    installs its result if no newer change has happened since, so a slow
    rebuild can never replace a newer library with a stale one.
    """

    def __init__(self, store: ConfigStore, scan: bool = True, desktop: Desktop | None = None) -> None:
        self.store = store
        self.library = Library()
        self.desktop = desktop or Desktop()
        self.tag_cache = TagCache(store.home / "tag-cache.json")
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()  # one disk scan at a time
        self._generation = 0
        self._installed = -1
        self._titles: dict[str, str | None] = {}  # station url -> song
        if scan:
            self._rebuild()

    def scan_in_background(self, on_done=None) -> threading.Thread:
        """First scan without blocking startup; the UI shows progress."""
        def work():
            self._rebuild()
            if on_done:
                on_done()

        worker = threading.Thread(target=work, name="library-scan", daemon=True)
        worker.start()
        return worker

    def _rebuild(self) -> None:
        # Requests that pile up behind a running scan share its result:
        # whoever gets the scan lock next scans the newest settings, and
        # the rest find that generation already installed and return.
        with self._scan_lock:
            with self._lock:
                generation = self._generation
                if generation == self._installed:
                    return
                cfg = copy.deepcopy(self.store.config)
            lib = Library()
            try:
                lib.load(cfg.playlist_sources, cfg.music_folders, cfg.path_remaps, self.tag_cache)
            except Exception as exc:
                # Never leave the UI waiting on a scan that died.
                logger.exception("Library scan failed")
                lib = Library()
                lib.errors.append(f"Reading the library failed: {exc}")
            finally:
                self.tag_cache.save()
            with self._lock:
                if generation == self._generation:
                    self.library = lib
                    self._installed = generation

    def rescan(self) -> None:
        with self._lock:
            self._generation += 1
        self._rebuild()

    def state(self) -> dict:
        with self._lock:
            lib = self.library
            config = copy.deepcopy(self.store.config.to_json())
            scanning = self._installed != self._generation
        stations = [{**s, "id": station_id(s["url"])} for s in config["stations"]]
        return {
            "version": __version__,
            "playlists": lib.playlist_summaries(),
            "stations": stations,
            "config": config,
            "errors": self.store.warnings + list(lib.errors),
            "scanning": scanning,
            "desktop": self.desktop.status(),
        }

    # ---- radio -----------------------------------------------------------

    def station_url(self, sid: str) -> str | None:
        with self._lock:
            for s in self.store.config.stations:
                if station_id(s["url"]) == sid:
                    return s["url"]
        return None

    def set_title(self, url: str, title: str | None) -> None:
        with self._lock:
            self._titles[url] = title

    def title(self, url: str) -> str | None:
        with self._lock:
            return self._titles.get(url)

    # ---- artwork -----------------------------------------------------------

    def artwork(self, track_id: str) -> tuple[bytes, str] | None:
        """A track's embedded picture, else a cover image in its folder."""
        track = self.library.tracks.get(track_id)
        if track is None or track.path is None:
            return None
        found = read_art(track.path)
        if found:
            return found
        cover = folder_cover(track.path)
        if cover is None:
            return None
        try:
            if cover.stat().st_size > MAX_ART_BYTES:
                return None
            data = cover.read_bytes()
        except OSError:
            return None
        mime = image_type(data)
        return (data, mime) if mime else None

    @contextmanager
    def _change(self, rescan: bool = True):
        """Apply a config change atomically and save it, then rebuild."""
        with self._lock:
            snapshot = copy.deepcopy(self.store.config)
            try:
                yield self.store.config
                self.store.save()
            except BaseException:
                self.store.config = snapshot
                raise
            if rescan:
                self._generation += 1
        if rescan:
            self._rebuild()

    # ---- mutations ------------------------------------------------------

    def add_source(self, kind: str, path: str) -> None:
        if not path.strip():
            raise ValueError("Path is empty")
        # Same normalisation as the CLI, so one folder is stored one way.
        clean = str(Path(path.strip()).expanduser().resolve())
        if not Path(clean).exists():
            raise ValueError(f"Not found: {clean}")
        with self._change() as cfg:
            target = _source_list(cfg, kind)
            if clean not in target:
                target.append(clean)

    def remove_source(self, kind: str, path: str) -> None:
        with self._change() as cfg:
            target = _source_list(cfg, kind)
            if path in target:
                target.remove(path)

    def add_remap(self, src: str, dst: str) -> None:
        if not src.strip() or not dst.strip():
            raise ValueError("Both 'from' and 'to' are needed")
        remap = {"from": src.strip(), "to": dst.strip()}
        with self._change() as cfg:
            if remap not in cfg.path_remaps:
                cfg.path_remaps.append(remap)

    def remove_remap(self, src: str, dst: str) -> None:
        with self._change() as cfg:
            cfg.path_remaps[:] = [
                r for r in cfg.path_remaps if not (r.get("from") == src and r.get("to") == dst)
            ]

    def add_station(self, name: str, url: str) -> dict:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Stream URL must start with http:// or https://")
        # Network fetch for .pls/.m3u happens outside the lock.
        station = {"name": name.strip() or url, "url": resolve_stream_url(url)}
        with self._change(rescan=False) as cfg:
            if all(s.get("url") != station["url"] for s in cfg.stations):
                cfg.stations.append(station)
        return station

    def remove_station(self, url: str) -> None:
        with self._change(rescan=False) as cfg:
            cfg.stations[:] = [s for s in cfg.stations if s.get("url") != url]


def station_id(url: str) -> str:
    return _short_hash("station", url)


def _source_list(cfg, kind: str) -> list[str]:
    if kind == "playlist":
        return cfg.playlist_sources
    if kind == "music":
        return cfg.music_folders
    raise ValueError(f"Unknown source kind: {kind}")


def make_handler(app: PlayerApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"StationPlayer/{__version__}"

        def handle(self):
            # Browsers drop audio connections whenever they seek or skip.
            # That surfaces as BrokenPipe/ConnectionReset on macOS and
            # Linux and ConnectionAborted on Windows; none is an error.
            try:
                super().handle()
            except ConnectionError:
                pass

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

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
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error(self, status, message):
            self._send_json({"error": message}, status)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 1_000_000:
                raise ValueError("Bad Content-Length")
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
            # Browsers say where a request came from. Other websites may
            # not embed the player's audio, art or radio relay (that would
            # also tell them it's running and what's in the library).
            if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                return self._error(HTTPStatus.FORBIDDEN, "Cross-site request")
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
            if path.startswith("/art/"):
                return self._serve_art(path.rsplit("/", 1)[1], head_only)
            if path.startswith("/radio/"):
                return self._serve_radio(path.rsplit("/", 1)[1], head_only)
            if path == "/api/nowplaying":
                sid = parse_qs(urlparse(self.path).query).get("station", [""])[0]
                url = app.station_url(sid)
                if url is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No such station")
                return self._send_json({"title": app.title(url)})
            return self._serve_static(path, head_only)

        def _serve_art(self, track_id: str, head_only: bool):
            found = app.artwork(track_id)
            if found is None:
                return self._error(HTTPStatus.NOT_FOUND, "No artwork")
            data, mime = found
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def _serve_radio(self, sid: str, head_only: bool):
            """Relay a station, keeping the song titles it sends."""
            url = app.station_url(sid)
            if url is None:
                return self._error(HTTPStatus.NOT_FOUND, "No such station")
            if head_only:
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Use GET")
            app.set_title(url, None)  # no stale song from the last visit
            try:
                stream = radio.open_stream(url, user_agent=f"StationPlayer/{__version__}")
            except radio.StreamError as exc:
                logger.warning("Radio relay for %s failed, playing it directly: %s", url, exc)
                return self._error(HTTPStatus.BAD_GATEWAY, "Couldn't connect to this station")
            try:
                ctype = stream.content_type
                if ctype is None:
                    return self._error(HTTPStatus.BAD_GATEWAY, "This station isn't a plain audio stream")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                # A paused tab stops reading; don't hold the station forever.
                self.connection.settimeout(radio.READ_SECONDS)
                for chunk in radio.relay(stream, lambda t: app.set_title(url, t)):
                    self.wfile.write(chunk)
            except OSError:
                pass  # the listener or the station went away
            finally:
                stream.close()

        def _serve_static(self, path: str, head_only: bool):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (STATIC_DIR / name).resolve()
            if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            ctype = _STATIC_TYPES.get(target.suffix.lower())
            if ctype is None:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            if target.suffix.lower() == ".html":
                self.send_header("Content-Security-Policy", _CSP)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve_audio(self, track_id: str, head_only: bool):
            track = app.library.tracks.get(track_id)
            ctype = content_type(track.path) if track and track.path else None
            if ctype is None:  # unknown id, missing file, or not audio
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
            self.send_header("Content-Type", ctype)
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
            except ConnectionError:
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
                    app.remove_remap(str(data.get("from", "")), str(data.get("to", "")))
                elif path == "/api/stations":
                    app.add_station(str(data.get("name", "")), str(data.get("url", "")))
                elif path == "/api/stations/remove":
                    app.remove_station(str(data.get("url", "")))
                elif path == "/api/desktop":
                    try:
                        if "shortcut" in data:
                            app.desktop.set_shortcut(bool(data["shortcut"]))
                        if "login" in data:
                            app.desktop.set_login(bool(data["login"]))
                    except OSError as exc:
                        return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Couldn't change shortcuts: {exc}")
                elif path == "/api/quit":
                    self._send_json({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "Not found")
            except (ValueError, TypeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError as exc:
                logger.warning("Could not save settings: %s", exc)
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Could not save settings: {exc}")
            return self._send_json(app.state())

    return Handler


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program bind a port that is
    # already in use, so two copies could both "start"; ask for the port
    # exclusively there instead.
    if sys.platform == "win32":
        allow_reuse_address = False

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


def make_server(app: PlayerApp, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = _Server((host, port), make_handler(app))
    server.daemon_threads = True
    return server

