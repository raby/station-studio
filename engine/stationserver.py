"""Sending weeks to Station Server, and asking it what's on air.

Station Server (server/ in this repo) plays each week's playlist on the
station's own computer. With its Settings › Station Studio turned on, it
listens on a link port (8772 unless changed) and shows a link code.
Station Studio keeps that address and code, and which Station Server
station each Studio station feeds; then a week goes over the network as
`PUT /api/link/stations/<id>/weeks/<NNN>.xml` (the same iTunes XML that
Export to disk writes) and `GET /api/link` says what each station is
playing and which weeks it holds. See server/station_server/studiolink.py.

The settings are one JSON value in the Config table:

    {"address": "192.168.1.20:8772", "code": "k7m2-9qxr-4hpt",
     "stations": {"<studio slug>": {"station": "<server id>", "auto": true}}}
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from sqlalchemy.orm import Session

from engine.models import Config, Playlist

CONFIG_KEY = "station_server"
LINK_PORT = 8772
SEND_TIMEOUT = 30  # seconds; a big week takes a moment to check on the other end
ASK_TIMEOUT = 6  # asking what's on air: the station's page waits on it


class LinkError(Exception):
    """Station Server couldn't be reached, or said no; the message says why."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def load(db: Session) -> dict:
    row = db.query(Config).filter(Config.key == CONFIG_KEY).first()
    try:
        data = json.loads(row.value) if row and row.value else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    stations = data.get("stations") if isinstance(data.get("stations"), dict) else {}
    return {
        "address": str(data.get("address") or ""),
        "code": str(data.get("code") or ""),
        "stations": {
            slug: {"station": str(v.get("station") or ""), "auto": bool(v.get("auto"))}
            for slug, v in stations.items() if isinstance(v, dict)
        },
    }


def save(db: Session, data: dict) -> dict:
    row = db.query(Config).filter(Config.key == CONFIG_KEY).first()
    value = json.dumps(data)
    if row:
        row.value = value
    else:
        db.add(Config(key=CONFIG_KEY, value=value))
    db.commit()
    return data


def base_url(address: str) -> str:
    """'192.168.1.20', '192.168.1.20:8772' or 'http://pc.local:8772/' ->
    'http://192.168.1.20:8772'. Raises LinkError for something that isn't
    an address."""
    raw = address.strip()
    if not raw:
        raise LinkError("Add Station Server's address in Settings › Station Server", 400)
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port or LINK_PORT
    except ValueError:
        raise LinkError(f"{address} isn't an address like 192.168.1.20:8772", 400) from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LinkError(f"{address} isn't an address like 192.168.1.20:8772", 400)
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}:{port}"


def call(settings: dict, method: str, path: str, body: bytes | None = None,
         timeout: float = SEND_TIMEOUT) -> dict:
    """One request to Station Server's link; its JSON answer, or LinkError."""
    url = base_url(settings["address"]) + path
    if not settings.get("code"):
        raise LinkError("Add the link code from Station Server's Settings › Station Studio", 400)
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {settings['code']}",
        "Content-Type": "application/xml" if body is not None else "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("error")
        except (ValueError, AttributeError, OSError):
            message = None
        # Station Server's own words are best; a 404 with none is
        # something else answering on that port.
        if not message:
            message = (f"Something other than Station Server answered at {settings['address']}"
                       if exc.code == 404 else f"Station Server said {exc.code} {exc.reason}")
        raise LinkError(message, exc.code if exc.code < 500 else 502) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        raise LinkError(
            f"Couldn't reach Station Server at {settings['address']} ({reason}). Check it's open with "
            "Settings › Station Studio turned on, and that this computer can reach it "
            "(same network, or a VPN, and its firewall lets the link port in).") from None
    except ValueError:
        raise LinkError(f"Something other than Station Server answered at {settings['address']}") from None


def status(db: Session) -> dict:
    """Station Server's answer to GET /api/link, or why there isn't one.
    Never raises: the page shows `error`."""
    settings = load(db)
    if not settings["address"] or not settings["code"]:
        return {"configured": False, "connected": False, "error": None, "stations": []}
    try:
        got = call(settings, "GET", "/api/link", timeout=ASK_TIMEOUT)
    except LinkError as exc:
        return {"configured": True, "connected": False, "error": str(exc), "stations": []}
    return {"configured": True, "connected": True, "error": None, "server": got.get("server"),
            "version": got.get("version"), "stations": got.get("stations") or []}


def target(db: Session, slug: str) -> dict:
    return load(db)["stations"].get(slug) or {"station": "", "auto": False}


def send_week(db: Session, playlist_id: int, xml_bytes: bytes | None = None) -> dict:
    """Send a playlist's current version to the Station Server station its
    Studio station feeds. Returns Station Server's answer (tracks found,
    missing, when it goes on air); raises LinkError."""
    from engine.exporter import export_playlist  # the exporter imports models; keep this module light

    p = db.query(Playlist).filter(Playlist.id == playlist_id).first()
    if p is None:
        raise LinkError(f"Playlist {playlist_id} not found", 404)
    settings = load(db)
    sid = (settings["stations"].get(p.station.slug) or {}).get("station")
    if not sid:
        raise LinkError(f"Choose which Station Server station {p.station.name} sends to, on its page", 400)
    body = xml_bytes if xml_bytes is not None else export_playlist(db, playlist_id)
    return call(settings, "PUT", f"/api/link/stations/{quote(sid, safe='')}/weeks/{p.week:03d}.xml", body)
