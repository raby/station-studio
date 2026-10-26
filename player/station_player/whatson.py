"""What's on a station, from the feed its Station Server publishes
(Settings › Website: server/station_server/feed.py).

A feed holds the next few hours with the time each song or show goes out,
so what's on now, next and just played come from the clock; the feed
itself is fetched at most once a minute per station.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from station_player import __version__
from station_player.radio import tls_context

logger = logging.getLogger(__name__)

FRESH_SECONDS = 60  # a feed is fetched again after this
STALE = timedelta(minutes=90)  # published at least hourly on air: older means its computer is off
MOST_BYTES = 2 * 1024 * 1024


def clean_feed_url(value) -> str:
    """'' or an http(s) address; raises ValueError for anything else."""
    url = str(value or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("The what's-on feed must be an http:// or https:// address")
    return url


def view(feed: dict | None, now: datetime, ahead: int = 3, back: int = 3) -> dict:
    """{state: on|live|off|unknown, now, next[], recent[]} from a feed at
    `now`. Songs are {at, title, artist}; a show is {at, show}."""
    if not feed:  # not published yet, or not reachable: say nothing rather than "Off air"
        return {"state": "unknown", "now": None, "next": [], "recent": []}
    if not feed.get("on_air"):
        return {"state": "off", "now": None, "next": [], "recent": (feed.get("recent") or [])[:back]}
    try:
        if now - datetime.fromisoformat(feed["updated"]) > STALE:
            return {"state": "unknown", "now": None, "next": [], "recent": []}
    except (KeyError, TypeError, ValueError):
        return {"state": "unknown", "now": None, "next": [], "recent": []}
    if feed.get("live"):
        return {"state": "live", "now": {"live": feed["live"].get("presenter") or "Live show"}, "next": [],
                "recent": (feed.get("recent") or [])[:back]}
    items = []
    for i in feed.get("items") or []:
        try:
            start = datetime.fromisoformat(i["at"])
            items.append((start, start + timedelta(seconds=float(i["seconds"])), i))
        except (KeyError, TypeError, ValueError):
            continue
    at = next((n for n, (s, e, _) in enumerate(items) if s <= now < e), None)
    played = [_song(i) for s, e, i in reversed(items) if e <= now and "show" not in i]
    recent = (played + list(feed.get("recent") or []))[:back]
    if at is None:
        return {"state": "unknown", "now": None, "next": [], "recent": recent}
    return {"state": "on", "now": _song(items[at][2]), "next": [_song(i) for _, _, i in items[at + 1:at + 1 + ahead]],
            "recent": recent}


def _song(item: dict) -> dict:
    if "show" in item:
        return {"at": item.get("at"), "show": item["show"]}
    return {"at": item.get("at"), "title": item.get("title"), "artist": item.get("artist")}


def _urlopen(req, timeout):
    # The Mac app's Python has no certificates of its own (radio.tls_context)
    return urllib.request.urlopen(req, timeout=timeout, context=tls_context())


class Feeds:
    """Fetches stations' feeds, each at most once a minute."""

    def __init__(self, opener=_urlopen, clock=time.monotonic) -> None:
        self._open, self._clock = opener, clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict | None]] = {}

    def get(self, url: str) -> dict | None:
        with self._lock:
            hit = self._cache.get(url)
        if hit and self._clock() - hit[0] < FRESH_SECONDS:
            return hit[1]
        feed = hit[1] if hit else None  # keep the last one if this fetch fails
        req = urllib.request.Request(f"{url}{'&' if '?' in url else '?'}t={int(time.time())}",
                                     headers={"User-Agent": f"StationPlayer/{__version__}"})
        try:
            with self._open(req, timeout=8) as resp:
                got = json.loads(resp.read(MOST_BYTES))
            if isinstance(got, dict):
                feed = got
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.debug("what's on %s: %s", url, exc)
        with self._lock:
            self._cache[url] = (self._clock(), feed)
        return feed
