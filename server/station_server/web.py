"""Local HTTP server: the JSON API and the web page.

Binds to 127.0.0.1 only, with station-player's two guards against other
websites in the same browser driving it: requests must carry a localhost
Host header (blocks DNS rebinding), and every state-changing request must
carry the `X-Station-Server` header, which a cross-origin page can't send
without a CORS preflight this server never approves.

With Settings › Phone access on, a second server (`RemoteAccess`) answers
other devices on the network, on its own port, behind a password. From
there the stations can be watched, listened to and put on or off air;
everything else is left to the station's own computer.
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
import queue as queues
import re
import socket
import sys
import threading
import time
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from station_player.setup import Picker, PickerBusy, PickerFailed, PickerUnavailable

from station_server import __version__
from station_server.listen import TooManyListeners
from station_server.live import lan_addresses
from station_server.onair import ServerApp
from station_server.playlist import PlaylistError
from station_server.selfupdate import Updater
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
_STATION_RE = re.compile(r"^/api/stations/([0-9a-zA-Z_-]+)(?:/([a-z0-9.]+))?$")
# What another device may do, with phone access on.
_REMOTE_ACTIONS = {"on", "skip", "restart", "endlive"}
_REMOTE_REALM = 'Basic realm="Station Server", charset="UTF-8"'


class _Guesses:
    """Slows down anyone guessing the phone access password: after a few
    wrong ones from an address, it's turned away for a while."""

    def __init__(self, most: int = 5, minutes: float = 5.0, clock=time.monotonic) -> None:
        self.most, self.seconds, self._clock = most, minutes * 60, clock
        self._lock = threading.Lock()
        self._wrong: dict[str, list[float]] = {}

    def blocked(self, who: str) -> bool:
        with self._lock:
            now = self._clock()
            recent = [t for t in self._wrong.get(who, []) if now - t < self.seconds]
            self._wrong[who] = recent
            return len(recent) >= self.most

    def wrong(self, who: str) -> None:
        with self._lock:
            self._wrong.setdefault(who, []).append(self._clock())


def _state(app: ServerApp, remote: bool) -> dict:
    """What the page shows. Another device isn't told the Station Studio
    link code."""
    state = {**app.state(), "remote_view": remote}
    if remote and state.get("studio"):
        state["studio"] = {k: v for k, v in state["studio"].items() if k != "code"}
    return state


def make_handler(app: ServerApp, picker: Picker, quit, remote: bool = False,
                 guesses: _Guesses | None = None) -> type[BaseHTTPRequestHandler]:
    guesses = guesses or _Guesses()

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

        def _allowed(self) -> bool:
            """Whether to answer at all; sends the refusal when not."""
            if not remote:
                if self._host_ok():
                    return True
                self._error(HTTPStatus.FORBIDDEN, "Bad host")
                return False
            who = self.client_address[0]
            if guesses.blocked(who):
                self._error(HTTPStatus.TOO_MANY_REQUESTS, "Too many wrong passwords. Try again in a few minutes.")
                return False
            expected = app.config.remote_password
            given = ""
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("basic "):
                try:
                    given = base64.b64decode(auth[6:].strip()).decode("utf-8").partition(":")[2]
                except ValueError:
                    given = ""
            if app.config.remote and expected and hmac.compare_digest(given.encode(), expected.encode()):
                return True
            if given:
                guesses.wrong(who)
                logger.warning("phone access: wrong password from %s", who)
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", _REMOTE_REALM)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            body = b"Station Server needs its phone access password.\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return False

        def _host_ok(self) -> bool:
            try:
                host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            except ValueError:
                return False
            return host in _ALLOWED_HOSTS

        def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK, extra: dict | None = None,
                  cache: str = "no-store"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
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
            if not self._allowed():
                return None
            url = urlparse(self.path)
            path = url.path
            if path == "/api/state":
                return self._json(_state(app, remote))
            if path == "/api/diagnostics":
                return self._send(app.diagnostics().encode("utf-8"), "text/plain; charset=utf-8")
            if path == "/api/qr.svg":  # a link for a phone to scan (Settings › Internet)
                from station_player.qr import svg
                text = parse_qs(url.query).get("text", [""])[0][:300]
                return self._send(svg(text).encode("utf-8"), "image/svg+xml")
            m = _STATION_RE.match(path)
            if m and m.group(2) == "listen.mp3":
                return self._listen(m.group(1))
            if m and m.group(2) == "logo":
                runner = app.runners.get(m.group(1))
                got = runner.logo() if runner else None
                if got is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No logo")
                # The page asks for it as ?v=<its name>, which changes with the picture.
                return self._send(got[0], got[1], cache="private, max-age=86400")
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

        def _listen(self, sid: str):
            """The broadcast as it goes out, for as long as the page plays it."""
            runner = app.runners.get(sid)
            if runner is None:
                return self._error(HTTPStatus.NOT_FOUND, "No such station")
            if runner.state != "on":
                return self._error(HTTPStatus.CONFLICT, "The station isn't on air")
            if self.command == "HEAD":
                return self._send(b"", "audio/mpeg")
            try:
                q = runner.listeners.open()
            except TooManyListeners as exc:
                return self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            self.close_connection = True
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = q.get(timeout=30)
                    if chunk is None:
                        break
                    self.wfile.write(chunk)
            except (OSError, queues.Empty):
                pass  # the listener went, or the audio stopped
            finally:
                runner.listeners.close(q)
            return None

        # ---- POST -------------------------------------------------------

        def do_POST(self):
            if not self._allowed():
                return None
            if self.headers.get(API_HEADER) != "1":
                return self._error(HTTPStatus.FORBIDDEN, f"Missing {API_HEADER} header")
            path = urlparse(self.path).path
            if remote:
                m = _STATION_RE.match(path)
                if not m or m.group(2) not in _REMOTE_ACTIONS:
                    return self._error(HTTPStatus.FORBIDDEN, "That can only be done on the station's computer")
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
            if path == "/api/check/diagnose":
                return self._json(app.diagnose_server(data))
            if path == "/api/settings":
                app.save_settings(data)
                return self._json(app.state())
            if path == "/api/feed/publish":
                if app.feed is not None:
                    app.feed.publish_now()
                return self._json(app.state())
            if path == "/api/internet/check":
                from station_server.netcheck import CheckError
                try:
                    app.check_internet()
                except CheckError as exc:
                    return self._error(HTTPStatus.BAD_GATEWAY, str(exc))
                return self._json(app.state())
            if path == "/api/internet/router":
                port = data.get("port")
                app.router(str(data.get("action") or "look"), int(port) if port else None)
                return self._json(app.state())
            if path == "/api/internet/duckdns":
                app.duckdns.update_now()
                return self._json(app.state())
            if path == "/api/alerts/test":
                return self._json({"sent": app.test_alert(), **app.state()})
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
                try:
                    app.check_for_updates(force=True)
                except Exception as exc:
                    reason = getattr(exc, "reason", None) or exc
                    return self._error(HTTPStatus.BAD_GATEWAY, f"Couldn't check for a new version: {reason}")
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
                app.set_on_air(sid, bool(data.get("on")), rehearse=bool(data.get("rehearse")))
            elif action == "check":
                return self._json({"checks": app.ready_check(sid)})
            elif action == "skip":
                app.skip(sid)
            elif action == "endlive":
                app.end_live(sid)
            elif action == "restart":
                app.restart(sid)
            elif action == "delete":
                app.delete_station(sid)
            elif action == "logo":
                image = data.get("image")
                try:
                    picture = base64.b64decode(image, validate=True) if image else None
                except (TypeError, ValueError):
                    raise ValueError("That picture couldn't be read") from None
                app.set_logo(sid, picture)
            elif action == "listeners":
                app.set_listener_cap(sid, int(data.get("max") or 0))
            else:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            return self._json(_state(app, remote))

    return Handler


class _Server(ThreadingHTTPServer):
    # As in station-player: on Windows ask for the port exclusively, so a
    # second copy can't also "start" on it.
    if sys.platform == "win32":
        allow_reuse_address = False

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


class RemoteAccess:
    """The second server, for phones and other computers on the network,
    running while Settings › Phone access is on."""

    def __init__(self, app: ServerApp, picker: Picker, quit, host: str = "0.0.0.0") -> None:
        self.app, self.picker, self.quit, self.host = app, picker, quit, host
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.error: str | None = None
        # This computer's addresses, looked up at most once a minute: the
        # page asks every 2 seconds, and a lookup can take seconds.
        self._addresses: tuple[float, list[str]] = (0.0, [])

    @property
    def port(self) -> int | None:
        return self._server.server_address[1] if self._server else None

    def apply(self) -> None:
        """Start, stop or move it to match the settings."""
        cfg = self.app.config
        want = cfg.remote and bool(cfg.remote_password)
        with self._lock:
            if self._server and (not want or self._server.server_address[1] != cfg.remote_port):
                self._stop()
            if want and not self._server:
                try:
                    server = _Server((self.host, cfg.remote_port), make_handler(self.app, self.picker, self.quit,
                                                                                remote=True))
                except OSError as exc:
                    self.error = f"Port {cfg.remote_port} is in use or not allowed ({exc.strerror or exc})"
                    logger.warning("phone access: %s", self.error)
                    return
                server.daemon_threads = True
                self._server, self.error = server, None
                threading.Thread(target=server.serve_forever, name="phone-access", daemon=True).start()
                logger.info("phone access on, port %s", cfg.remote_port)
            elif not want:
                self.error = None

    def stop(self) -> None:
        with self._lock:
            self._stop()

    def _stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            logger.info("phone access off")

    def addresses(self) -> list[str]:
        at, found = self._addresses
        if time.monotonic() - at > 60 or not found:
            found = lan_addresses()
            self._addresses = (time.monotonic(), found)
        return found

    def status(self) -> dict:
        cfg = self.app.config
        port = self.port
        return {
            "enabled": cfg.remote, "port": cfg.remote_port, "has_password": bool(cfg.remote_password),
            "running": port is not None, "error": self.error,
            "addresses": [f"http://{a}:{port}/" for a in self.addresses()] if port else [],
        }


def make_server(app: ServerApp, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                picker: Picker | None = None, updater: Updater | None = None,
                remote_host: str = "0.0.0.0") -> ThreadingHTTPServer:
    holder: dict = {}

    def quit() -> None:
        app.shutdown()
        holder["server"].shutdown()

    picker = picker or Picker()
    server = _Server((host, port), make_handler(app, picker, quit))
    server.daemon_threads = True
    holder["server"] = server
    app.updater = updater or Updater(quit, port=port)
    app.remote = RemoteAccess(app, picker, quit, host=remote_host)
    app.remote.apply()
    from station_server.studiolink import StudioLink  # it builds on this module
    app.studio = StudioLink(app, host=remote_host)
    app.studio.apply()
    from station_server.feed import FeedPublisher
    app.feed = FeedPublisher(app)
    app.feed.apply()
    app.duckdns.apply()
    return server
