"""The Station Studio link: Station Studio sends each week's playlist
straight to Station Server over the network, and asks what's on air.

With Settings › Station Studio on, a small server of its own listens on
the link port (8772 unless changed). Station Studio is given this
computer's address and the link code shown there. Every request carries
the code (`Authorization: Bearer <code>`), and after a few wrong codes an
address is turned away for a while.

    GET /api/link
        Station Server's version and each station: whether it's on air,
        what's playing, the week on air, the next week and when it goes
        on, and the weeks it holds.
    PUT /api/link/stations/<id>/weeks/<number>.xml
        A week's playlist (station-studio's iTunes XML export). It's
        checked, then kept in the station's folder of weeks, where it goes
        on air as any exported week does (weeks.py). The answer says how
        many of its tracks this computer has, and when it goes on.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from station_server import __version__
from station_server.live import lan_addresses
from station_server.onair import ServerApp, playlist_file
from station_server.playlist import PlaylistError, load_queue
from station_server.schedule import track_seconds
from station_server.web import _Guesses, _Server
from station_server.weeks import week_files

logger = logging.getLogger(__name__)

MOST_BYTES = 32 * 1024 * 1024  # a week of thousands of tracks is a few MB
_CODE_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # nothing to misread (no l, 1, o, 0, i)
_WEEK_RE = re.compile(r"(\d{1,4})\.xml")
_PUT_RE = re.compile(r"^/api/link/stations/([0-9a-zA-Z_-]+)/weeks/([^/]+)$")


class LinkRefused(Exception):
    """A week that can't be taken, with the reason for Station Studio to show."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def new_code() -> str:
    """A link code to type into Station Studio, like k7m2-9qxr-4hpt."""
    raw = "".join(secrets.choice(_CODE_LETTERS) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def same_code(given: str, expected: str) -> bool:
    """Whether `given` is the link code, however it was typed (spaces,
    dashes, capitals)."""
    tidy = lambda c: re.sub(r"[\s-]", "", c).lower()  # noqa: E731
    return bool(expected) and hmac.compare_digest(tidy(given).encode(), tidy(expected).encode())


def _week_number(path: Path | None) -> int | None:
    m = _WEEK_RE.fullmatch(path.name) if path else None
    return int(m.group(1)) if m else None


def weeks_folder(app: ServerApp, sid: str, make: bool = False) -> Path:
    """The folder a station's weeks go in: its playlist folder, or for a
    station with no playlist yet, one of Station Server's own (made, and
    set as its playlist, when `make`)."""
    st = app.config.station(sid)
    if st is None:
        raise LinkRefused("There's no such station in Station Server", HTTPStatus.NOT_FOUND)
    if not st.playlist:
        folder = app.store.home / "weeks" / sid
        if make:
            folder.mkdir(parents=True, exist_ok=True)
            app.save_station({"playlist": str(folder)}, sid)
        return folder
    folder = Path(st.playlist).expanduser()
    if folder.is_dir():
        return folder
    if folder.exists():
        raise LinkRefused(f"{st.name} plays one playlist file, not a folder of weeks. In Station Server, "
                          "Edit › Station, choose a folder for its playlist (or leave it blank).",
                          HTTPStatus.CONFLICT)
    raise LinkRefused(f"{st.name}'s playlist folder can't be found on the station's computer: {folder}",
                      HTTPStatus.CONFLICT)


def receive_week(app: ServerApp, sid: str, name: str, body: bytes) -> dict:
    """Keep a week Station Studio sent, once it reads as a playlist."""
    m = _WEEK_RE.fullmatch(name)
    if not m:
        raise LinkRefused(f"{name} isn't a week's file name, like 013.xml")
    folder = weeks_folder(app, sid, make=True)
    st = app.config.station(sid)
    incoming = folder / f".incoming-{name}"  # not a week until it's checked (weeks.py ignores it)
    try:
        incoming.write_bytes(body)
        try:
            queue = load_queue(incoming, st.music_folders, st.remaps)
        except PlaylistError as exc:
            raise LinkRefused(f"That isn't a playlist Station Server can read: {exc}") from None
        if not queue.tracks and not queue.missing:
            raise LinkRefused("That playlist has no tracks in it")
        target = folder / name
        replaced = target.exists()
        os.replace(incoming, target)
    except OSError as exc:
        raise LinkRefused(f"Couldn't keep it in {folder}: {exc.strerror or exc}",
                          HTTPStatus.INTERNAL_SERVER_ERROR) from None
    finally:
        incoming.unlink(missing_ok=True)
    logger.info("Station Studio sent %s for %s: %d tracks, %d not found", name, st.name,
                len(queue.tracks), len(queue.missing))
    out = {
        "station": st.name, "week": name, "replaced": replaced,
        "tracks": len(queue.tracks), "missing_count": len(queue.missing),
        "missing": [{"artist": t.artist, "title": t.name, "location": str(t.location)} for t in queue.missing[:20]],
        "hours": round(sum(track_seconds(t) for t in queue.tracks) / 3600, 1),
    }
    return {**out, **_when(app, sid, target)}


def _when(app: ServerApp, sid: str, target: Path) -> dict:
    """When a week in the station's folder goes on air: `when` is "now"
    (the week to play now), "next" (with `goes_on`), "later" (after the
    next) or "old" (before the week on air: not played)."""
    st = app.config.station(sid)
    try:
        now_xml, upcoming = playlist_file(st, app.weeks, remember=False)
    except PlaylistError:
        return {"when": "now", "goes_on": None}
    if now_xml.name == target.name:
        return {"when": "now", "goes_on": None}
    if upcoming and upcoming[0].name == target.name:
        return {"when": "next", "goes_on": upcoming[1].isoformat(timespec="minutes")}
    on, this = _week_number(now_xml), _week_number(target)
    if on is not None and this is not None and this > on:
        return {"when": "later", "goes_on": None}
    return {"when": "old", "goes_on": None}


def link_status(app: ServerApp) -> dict:
    """What Station Studio is shown: each station, on air or not, and its weeks."""
    out = []
    for st in app.config.stations:
        s = app.runners[st.id].status()
        now = s.get("now") or {}
        row = {
            "id": st.id, "name": st.name, "state": s["state"], "rehearsal": s["rehearsal"],
            "now": {"title": now.get("show") or now.get("title"), "artist": None if now.get("show") else now.get("artist")}
            if now else None,
            "live": (s.get("live") or {}).get("presenter") if s.get("live") else None,
            "week": None, "next": None, "weeks": [], "problem": None,
        }
        if s.get("live") and not row["live"]:
            row["live"] = "a presenter"
        try:
            folder = weeks_folder(app, st.id)
            row["weeks"] = sorted(week_files(folder)) if folder.is_dir() else []
            if row["weeks"]:
                now_xml, upcoming = playlist_file(st, app.weeks, remember=False)
                row["week"] = now_xml.name
                if upcoming:
                    row["next"] = {"week": upcoming[0].name, "at": upcoming[1].isoformat(timespec="minutes")}
        except (LinkRefused, PlaylistError) as exc:
            row["problem"] = str(exc)
        out.append(row)
    return {"server": "Station Server", "version": __version__, "stations": out}


class StudioLink:
    """The link's server, running while Settings › Station Studio is on."""

    def __init__(self, app: ServerApp, host: str = "0.0.0.0") -> None:
        self.app, self.host = app, host
        self._lock = threading.Lock()
        self._server: _Server | None = None
        self.guesses = _Guesses()
        self.error: str | None = None
        self.last: dict | None = None  # the last week sent: {at, station, week, tracks, missing_count}
        self._addresses: tuple[float, list[str]] = (0.0, [])

    @property
    def port(self) -> int | None:
        return self._server.server_address[1] if self._server else None

    def apply(self) -> None:
        """Start, stop or move it to match the settings."""
        cfg = self.app.config
        want = cfg.studio_link and bool(cfg.studio_code)
        with self._lock:
            if self._server and (not want or self._server.server_address[1] != cfg.studio_port):
                self._stop()
            if want and not self._server:
                try:
                    server = _Server((self.host, cfg.studio_port), _handler(self))
                except OSError as exc:
                    self.error = f"Port {cfg.studio_port} is in use or not allowed ({exc.strerror or exc})"
                    logger.warning("Station Studio link: %s", self.error)
                    return
                server.daemon_threads = True
                self._server, self.error = server, None
                threading.Thread(target=server.serve_forever, name="studio-link", daemon=True).start()
                logger.info("Station Studio link on, port %s", cfg.studio_port)
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
            logger.info("Station Studio link off")

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
            "enabled": cfg.studio_link, "port": cfg.studio_port, "code": cfg.studio_code,
            "running": port is not None, "error": self.error, "last": self.last,
            "addresses": [f"{a}:{port}" for a in self.addresses()] if port else [],
        }


def _handler(link: StudioLink) -> type[BaseHTTPRequestHandler]:
    app = link.app

    class Handler(BaseHTTPRequestHandler):
        server_version = f"StationServer/{__version__}"

        def handle(self):
            try:
                super().handle()
            except ConnectionError:
                pass

        def log_message(self, fmt, *args):
            logger.debug("studio link %s - %s", self.address_string(), fmt % args)

        def _json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json({"error": message}, status)

        def _allowed(self) -> bool:
            who = self.client_address[0]
            if link.guesses.blocked(who):
                self._error(HTTPStatus.TOO_MANY_REQUESTS, "Too many wrong link codes. Try again in a few minutes.")
                return False
            auth = self.headers.get("Authorization") or ""
            given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if same_code(given, app.config.studio_code):
                return True
            link.guesses.wrong(who)
            logger.warning("Station Studio link: wrong code from %s", who)
            self._error(HTTPStatus.UNAUTHORIZED, "That isn't the link code: check Settings › Station Studio "
                                                 "in Station Server")
            return False

        def do_GET(self):
            if self.path.split("?")[0] != "/api/link":
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            if self._allowed():
                self._json(link_status(app))

        def do_PUT(self):
            m = _PUT_RE.match(self.path.split("?")[0])
            if not m:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            if not self._allowed():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MOST_BYTES:
                self.close_connection = True
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > MOST_BYTES
                                   else HTTPStatus.LENGTH_REQUIRED, "Send the week's playlist, up to 32 MB")
            body = self.rfile.read(length)
            sid, name = m.groups()
            try:
                result = receive_week(app, sid, name, body)
            except LinkRefused as exc:
                return self._error(exc.status, str(exc))
            link.last = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "station": result["station"], "week": name,
                         "tracks": result["tracks"], "missing_count": result["missing_count"]}
            self._json(result)

    return Handler
