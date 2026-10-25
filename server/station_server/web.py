"""Local HTTP server: the JSON API and the web page.

Binds to 127.0.0.1 only, with station-player's two guards against other
websites in the same browser driving it: requests must carry a localhost
Host header (blocks DNS rebinding), and every state-changing request must
carry the `X-Station-Server` header, which a cross-origin page can't send
without a CORS preflight this server never approves.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import sys
import threading
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from station_player.setup import Picker, PickerBusy, PickerFailed, PickerUnavailable

from station_server import __version__
from station_server.selfupdate import Updater
from station_server.onair import ServerApp
from station_server.playlist import PlaylistError
from station_server.senders import SenderError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
API_HEADER = "X-Station-Server"
DEFAULT_PORT = 8770
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
}
_STATION_RE = re.compile(r"^/api/stations/([0-9a-zA-Z_-]+)(?:/([a-z.]+))?$")


def make_handler(app: ServerApp, picker: Picker, quit) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"StationServer/{__version__}"

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                pass

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def log_message(self, fmt, *args):
            logger.debug("%s - %s", self.address_string(), fmt % args)

        # ---- helpers ----------------------------------------------------

        def _host_ok(self) -> bool:
            try:
                host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            except ValueError:
                return False
            return host in _ALLOWED_HOSTS

        def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload, status=HTTPStatus.OK):
            self._send(json.dumps(payload).encode(), "application/json", status)

        def _error(self, status, message):
            self._json({"error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 1_000_000:
                raise ValueError("Bad Content-Length")
            data = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data

        # ---- GET --------------------------------------------------------

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Bad host")
            url = urlparse(self.path)
            path = url.path
            if path == "/api/state":
                return self._json(app.state())
            m = _STATION_RE.match(path)
            if m and m.group(2) == "playlog.csv":
                qs = parse_qs(url.query)
                try:
                    first = date.fromisoformat(qs.get("from", [""])[0])
                    last = date.fromisoformat(qs.get("to", [""])[0])
                    body, _count, name = app.playlog_csv(m.group(1), first, last)
                except KeyError:
                    return self._error(HTTPStatus.NOT_FOUND, "No such station")
                except ValueError:
                    return self._error(HTTPStatus.BAD_REQUEST, "Give from and to as YYYY-MM-DD")
                fname = f"{name} play log {first.isoformat()} to {last.isoformat()}.csv"
                safe = re.sub(r'[^\w .()-]+', "_", fname, flags=re.ASCII)
                return self._send(body.encode("utf-8-sig"), "text/csv; charset=utf-8", extra={
                    "Content-Disposition": f"attachment; filename=\"{safe}\"; filename*=UTF-8''{quote(fname)}",
                })
            if path == "/":
                path = "/index.html"
            target = (STATIC_DIR / path.lstrip("/")).resolve()
            if STATIC_DIR.resolve() not in target.parents or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            ctype = _STATIC_TYPES.get(target.suffix)
            if ctype is None:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            return self._send(target.read_bytes(), ctype)

        # ---- POST -------------------------------------------------------

        def do_POST(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Bad host")
            if self.headers.get(API_HEADER) != "1":
                return self._error(HTTPStatus.FORBIDDEN, f"Missing {API_HEADER} header")
            path = urlparse(self.path).path
            try:
                data = self._body()
                return self._post(path, data)
            except KeyError:
                return self._error(HTTPStatus.NOT_FOUND, "No such station")
            except (ValueError, PlaylistError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except SenderError as exc:
                return self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            except OSError as exc:
                return self._error(HTTPStatus.BAD_GATEWAY, f"Couldn't connect: {exc}")

        def _post(self, path: str, data: dict):
            if path == "/api/stations":
                st = app.save_station(data)
                return self._json({"id": st.id, **app.state()})
            if path == "/api/check/playlist":
                return self._json(app.check_playlist(data))
            if path == "/api/check/server":
                return self._json(app.check_server(data, data.get("id") or None))
            if path == "/api/settings":
                app.config.open_window = bool(data.get("open_window", app.config.open_window))
                app.config.check_updates = bool(data.get("check_updates", app.config.check_updates))
                if not app.config.check_updates:
                    app.latest = None  # no more notices until checked by hand
                app.store.save()
                if "login" in data:
                    app.login.set(bool(data["login"]))
                return self._json(app.state())
            if path == "/api/pick":
                try:
                    return self._json(picker.pick(str(data.get("kind", "folder"))))
                except PickerBusy as exc:
                    return self._error(HTTPStatus.CONFLICT, str(exc))
                except (PickerUnavailable, PickerFailed) as exc:
                    return self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            if path == "/api/update":
                if app.updater is None:
                    return self._error(HTTPStatus.BAD_REQUEST, "This Station Server can't update itself")
                app.updater.start(str(data.get("version") or ""))
                return self._json(app.state())
            if path == "/api/update/check":
                app.check_for_updates(force=True)
                return self._json(app.state())
            if path == "/api/window":
                # A browser window asking for the Mac app's own window.
                from station_server.window import mac_app_bundle, open_ui
                bundle = mac_app_bundle()
                if bundle is None:
                    return self._error(HTTPStatus.CONFLICT, "Only the Mac app has a window of its own")
                url = f"http://127.0.0.1:{self.server.server_address[1]}/"
                threading.Thread(target=open_ui, args=(url, app.store.home, True), kwargs={"app": bundle},
                                 daemon=True).start()
                return self._json({"ok": True})
            if path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=quit, daemon=True).start()
                return None
            m = _STATION_RE.match(path)
            if not m:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            sid, action = m.group(1), m.group(2)
            if action is None:
                app.save_station(data, sid)
            elif action == "on":
                app.set_on_air(sid, bool(data.get("on")))
            elif action == "skip":
                app.skip(sid)
            elif action == "restart":
                app.restart(sid)
            elif action == "delete":
                app.delete_station(sid)
            else:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            return self._json(app.state())

    return Handler


class _Server(ThreadingHTTPServer):
    # As in station-player: on Windows ask for the port exclusively, so a
    # second copy can't also "start" on it.
    if sys.platform == "win32":
        allow_reuse_address = False

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


def make_server(app: ServerApp, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                picker: Picker | None = None, updater: Updater | None = None) -> ThreadingHTTPServer:
    holder: dict = {}

    def quit() -> None:
        app.shutdown()
        holder["server"].shutdown()

    server = _Server((host, port), make_handler(app, picker or Picker(), quit))
    server.daemon_threads = True
    holder["server"] = server
    app.updater = updater or Updater(quit, port=port)
    return server
