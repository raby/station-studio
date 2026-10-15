"""The phone remote: a small second web server, on the local network,
for controlling the player from a phone on the same Wi-Fi.

Off unless turned on in Settings. It listens on every network interface
(`remote_port`, 8766 by default) but answers only under a secret link,
`/r/<token>/`, which the Settings page shows with a QR code to scan.
Making a new link stops the old one working. It serves the remote page,
the player's status and cover art, and takes a short list of commands
(play/pause, next, volume, play a playlist or station, sleep timer).
Nothing else the player serves is reachable from the network.

Sound still plays on the computer: commands are handed to the player
page, which picks them up when it checks in (`PlayerApp.inbox`).
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static" / "remote"
_FILES = {"": ("index.html", "text/html; charset=utf-8"),
          "remote.js": ("remote.js", "text/javascript; charset=utf-8"),
          "remote.css": ("remote.css", "text/css; charset=utf-8")}
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'")
API_HEADER = "X-Station-Player"
COMMANDS = ("toggle", "next", "prev", "volume", "playlist", "station", "sleep")


def new_token() -> str:
    return secrets.token_urlsafe(16)  # 128 bits


def lan_address() -> str | None:
    """This computer's address on the local network, or None when it
    isn't on one. (Connecting a UDP socket sends nothing; it only picks
    the interface that a packet would leave by.)"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            addr = s.getsockname()[0]
        except OSError:
            return None
    return None if addr.startswith("127.") or addr == "0.0.0.0" else addr


def clean_command(data: dict) -> dict:
    """A command from the phone, checked. Raises ValueError."""
    cmd = data.get("cmd")
    if cmd not in COMMANDS:
        raise ValueError("Unknown command")
    out: dict = {"cmd": cmd}
    if cmd == "volume":
        value = data.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError("volume must be 0 to 1")
        out["value"] = float(value)
    elif cmd in ("playlist", "station"):
        if not isinstance(data.get("id"), str) or not data["id"] or len(data["id"]) > 100:
            raise ValueError("id must be a string")
        out["id"] = data["id"]
    elif cmd == "sleep":
        value = data.get("minutes")
        if value != "track" and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 600):
            raise ValueError("minutes must be 0 to 600, or \"track\"")
        out["minutes"] = value
    return out


class RemoteServer:
    """The remote's web server, running in a thread of its own."""

    def __init__(self, app, port: int, host: str = "0.0.0.0") -> None:
        self.app = app
        self.httpd = ThreadingHTTPServer((host, port), _make_handler(app))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="phone-remote", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "StationPlayerRemote"
        timeout = 10  # a connection that sends nothing doesn't hold a thread
        sys_version = ""

        def log_message(self, fmt, *args):
            logger.debug("remote %s - %s", self.address_string(), fmt % args)

        def _rest(self) -> str | None:
            """The path after /r/<token>/, or None when the link is wrong."""
            parts = urlparse(self.path).path.split("/", 3)
            token = app.store.config.remote_token
            if len(parts) < 4 or parts[1] != "r" or not token \
                    or not hmac.compare_digest(parts[2].encode(), token.encode()):
                return None
            return parts[3]

        def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # the link is the key
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, data, status=HTTPStatus.OK):
            self._send(json.dumps(data).encode(), "application/json", status)

        def _not_found(self):
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_GET(self):
            rest = self._rest()
            if rest is None:
                return self._not_found()
            if rest in _FILES:
                name, ctype = _FILES[rest]
                try:
                    body = (STATIC_DIR / name).read_bytes()
                except OSError:
                    return self._not_found()
                return self._send(body, ctype, extra={"Content-Security-Policy": _CSP} if name.endswith(".html") else None)
            if rest == "api/status":
                return self._json(app.remote_view())
            if rest.startswith("art/"):
                found = app.artwork(rest[4:])
                if found is None:
                    return self._not_found()
                return self._send(found[0], found[1])
            return self._not_found()

        do_HEAD = do_GET

        def do_POST(self):
            rest = self._rest()
            if rest is None:
                return self._not_found()
            # A custom header: other websites can't send it without asking first.
            if rest != "api/command" or self.headers.get(API_HEADER) != "1":
                return self._not_found()
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > 4096:
                    raise ValueError("Too long")
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                app.remote_command(clean_command(data))
            except ValueError as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return self._json(app.remote_view())

    return Handler
