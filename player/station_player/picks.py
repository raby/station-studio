"""Picks for the Home page ("From Rebirth"): a short list kept in the
repository (player/station_player/static/picks.json) and published with the docs on
GitHub Pages, so what's suggested can change without a new version.

Each pick can show at set times ("from"/"to", local time; days of the
week; first and last dates), so the breakfast mix is offered in the
morning. The player fetches the list at most once an hour and keeps the
last good copy; until the first fetch it uses the copy it shipped with.
Images come only from the same site as the list.

The same file can list sport stations ("sport") that the globe's Sport
switch always shows, picked by hand to add to what the directory has.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

from station_player.logos import sniff

logger = logging.getLogger(__name__)

FEED_URL = "https://raby.github.io/station-studio/feed/picks.json"
BUNDLED = Path(__file__).parent / "static" / "picks.json"
REFRESH = 60 * 60
MAX_PICKS = 20
MAX_SPORT = 40
MAX_FEED_BYTES = 200_000
MAX_IMAGE_BYTES = 1_000_000
_TIMEOUT = 15
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _text(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _url(value) -> str:
    return value.strip() if isinstance(value, str) and value.strip().startswith(("http://", "https://")) else ""


def _place(s: dict) -> dict:
    """{"lat", "lon"} when a sport entry says where it is, else {}."""
    try:
        lat, lon = float(s.get("lat")), float(s.get("lon"))
    except (TypeError, ValueError):
        return {}
    return {"lat": round(lat, 4), "lon": round(lon, 4)} if -90 <= lat <= 90 and -180 <= lon <= 180 else {}


def clean_sport(raw) -> list[dict]:
    """The hand-picked sport stations: a name, and a stream link or the
    name to look up in the station directory ("find", else the name), with
    an optional country (two letters) and position."""
    sport = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict) or len(sport) >= MAX_SPORT:
            continue
        name = _text(s.get("name"), 80)
        if not name:
            continue
        item = {"name": name, **_place(s)}
        station = _url(s.get("station"))
        if station:
            item["station"] = station
        else:
            item["find"] = _text(s.get("find"), 80) or name
        country = _text(s.get("country"), 2).upper()
        if len(country) == 2 and country.isalpha():
            item["country"] = country
        sport.append(item)
    return sport


def clean(data, base: str = FEED_URL) -> dict:
    """The parts of a picks file the player uses, checked; anything odd
    is dropped rather than shown."""
    if not isinstance(data, dict):
        return {"title": "", "picks": [], "sport": []}
    picks = []
    raw = data.get("picks")
    for i, p in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(p, dict) or len(picks) >= MAX_PICKS:
            continue
        title, station = _text(p.get("title"), 80), _url(p.get("station"))
        if not title or not station:
            continue
        pick = {"id": str(i), "title": title, "text": _text(p.get("text"), 200),
                "button": _text(p.get("button"), 40) or "Listen", "station": station,
                "name": _text(p.get("name"), 80) or title}
        image = p.get("image")
        if isinstance(image, str) and image.strip():
            url = urljoin(base, image.strip())
            if urlparse(url).netloc == urlparse(base).netloc and url.startswith("https://"):
                pick["image"] = url
        for key in ("from", "to"):
            if isinstance(p.get(key), str) and _TIME.fullmatch(p[key]):
                pick[key] = p[key]
        if ("from" in pick) != ("to" in pick):
            pick.pop("from", None)
            pick.pop("to", None)
        days = p.get("days")
        if isinstance(days, list):
            chosen = [d for d in DAYS if d in [str(x).lower()[:3] for x in days]]
            if chosen:
                pick["days"] = chosen
        for key in ("starts", "ends"):
            if isinstance(p.get(key), str) and _DATE.fullmatch(p[key]):
                pick[key] = p[key]
        picks.append(pick)
    # No "sport" at all (a list published before there was one): None, so
    # the shipped list is used instead.
    sport = clean_sport(data["sport"]) if "sport" in data else None
    return {"title": _text(data.get("title"), 60), "picks": picks, "sport": sport}


def _fetch(url: str, limit: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "StationPlayer"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        body = resp.read(limit + 1)
        return body, resp.headers.get("Content-Type", "")


class Picks:
    def __init__(self, home: Path, url: str = FEED_URL, fetch=_fetch, bundled: Path = BUNDLED) -> None:
        self.url = url
        self._fetch = fetch
        self._bundled = bundled
        self._cache = home / "picks.json"
        self._images = home / "picks-images"
        self._lock = threading.Lock()
        self._checked = 0.0
        self._busy = False
        self._data = self._read(self._cache) or self._read(bundled) or {"title": "", "picks": [], "sport": []}
        self._shipped_sport = (self._read(bundled) or {}).get("sport") or []

    def _read(self, path: Path) -> dict | None:
        try:
            return clean(json.loads(path.read_text(encoding="utf-8")), self.url)
        except (OSError, ValueError):
            return None

    def current(self) -> dict:
        """The picks as last fetched; an hour on, they're fetched again
        in the background."""
        with self._lock:
            due = not self._busy and time.time() - self._checked >= REFRESH
            if due:
                self._busy = True
            data = self._data
        if due:
            threading.Thread(target=self.refresh, name="picks", daemon=True).start()
        return data

    def refresh(self) -> None:
        try:
            body, _ = self._fetch(self.url, MAX_FEED_BYTES)
            if len(body) > MAX_FEED_BYTES:
                raise ValueError("picks file is too large")
            raw = json.loads(body)
            data = clean(raw, self.url)
            try:
                self._cache.parent.mkdir(parents=True, exist_ok=True)
                # Whole or not at all: a half-written file would lose the
                # last good list to the shipped one on the next start.
                tmp = self._cache.with_suffix(".tmp")
                tmp.write_text(json.dumps(raw), encoding="utf-8")
                os.replace(tmp, self._cache)
            except OSError:
                pass
            with self._lock:
                self._data = data
        except Exception as exc:  # offline, or a broken file: keep what we have
            logger.info("Couldn't fetch the Home page picks: %s", exc)
        finally:
            with self._lock:
                self._checked = time.time()
                self._busy = False

    def lists(self, url: str) -> bool:
        """Whether a pick, or a hand-picked sport station, plays this
        stream (so the player may relay it)."""
        with self._lock:
            return any(p["station"] == url for p in self._data["picks"]) \
                or any(s.get("station") == url for s in self._sport())

    def sport(self) -> list[dict]:
        """The hand-picked sport stations, as last fetched."""
        self.current()  # fetched again once an hour has gone
        with self._lock:
            return [dict(s) for s in self._sport()]

    def _sport(self) -> list[dict]:
        sport = self._data["sport"]
        return self._shipped_sport if sport is None else sport

    def image(self, pid: str) -> tuple[bytes, str] | None:
        with self._lock:
            pick = next((p for p in self._data["picks"] if p["id"] == pid), None)
        url = pick and pick.get("image")
        if not url:
            return None
        # The pictures shipped with the player need no download.
        shipped = None
        if url.startswith(urljoin(self.url, "picks/")):
            shipped = self._bundled.parent / "picks" / Path(urlparse(url).path).name
        path = shipped if shipped and shipped.is_file() else self._images / hashlib.sha256(url.encode()).hexdigest()[:24]
        try:
            body = path.read_bytes()
        except OSError:
            try:
                body, _ = self._fetch(url, MAX_IMAGE_BYTES)
            except Exception as exc:
                logger.info("Couldn't fetch a pick's image: %s", exc)
                return None
            if len(body) > MAX_IMAGE_BYTES or not sniff(body):
                return None
            try:
                self._images.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            except OSError:
                pass
        ctype = sniff(body)
        return (body, ctype) if ctype else None
