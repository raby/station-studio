"""Finding radio stations: search Radio Browser (radio-browser.info), the
free community directory of internet radio stations, and list SomaFM's
channels (somafm.com).

The page can't reach other sites itself (its security policy allows only
the player), so searches and station logos go through here. Logos are
fetched only from addresses the directory itself returned, and only
small images, so the player can't be used to fetch anything else.
"""
from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import logging
import random
import socket
import threading
import time
import urllib.request
from collections import OrderedDict
from typing import Callable
from urllib.parse import urlencode, urlparse

from station_player.radio import tls_context

logger = logging.getLogger(__name__)

# Radio Browser's public mirrors. all.api.radio-browser.info points at
# whichever are up; the named ones are tried after it.
SERVERS = ["all.api.radio-browser.info", "de1.api.radio-browser.info", "de2.api.radio-browser.info",
           "fi1.api.radio-browser.info", "nl1.api.radio-browser.info"]
TIMEOUT = 8
MAX_RESULTS = 60
MAX_LOGO_BYTES = 256 * 1024
# Not SVG: it can carry script.
LOGO_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon", "image/vnd.microsoft.icon")
ORDERS = {"votes": "votes", "popular": "clickcount", "name": "name"}
SOMAFM_LIST = "https://somafm.com/channels.json"


class DirectoryError(Exception):
    """The directory couldn't be reached or answered oddly."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an error: it could point anywhere


def _fetch(url: str, user_agent: str, limit: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
    opener = urllib.request.build_opener(_NoRedirects, urllib.request.HTTPSHandler(context=tls_context()))
    with opener.open(req, timeout=TIMEOUT) as resp:
        body = resp.read(limit + 1)
        return body, resp.headers.get("Content-Type", "")


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to an address already checked, not to whatever the name
    resolves to by then (so DNS can't be switched to a local address)."""

    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _fetch_public(url: str, user_agent: str, limit: int, resolve=socket.getaddrinfo,
                  allowed=lambda ip: ip.is_global) -> tuple[bytes, str]:
    """GET `url` from a host on the public internet only, connecting to
    the very address that was checked; no redirects."""
    parts = urlparse(url)
    host, https = parts.hostname or "", parts.scheme == "https"
    port = parts.port or (443 if https else 80)
    try:
        addrs = [info[4][0] for info in resolve(host, port, type=socket.SOCK_STREAM)]
    except UnicodeError as exc:
        raise OSError(str(exc)) from exc
    if not addrs or not all(allowed(ipaddress.ip_address(a.split("%")[0])) for a in addrs):
        raise OSError(f"{host} isn't on the public internet")
    conn = (_PinnedHTTPS(host, port, addrs[0], context=tls_context(), timeout=TIMEOUT) if https
            else _PinnedHTTP(host, port, addrs[0], timeout=TIMEOUT))
    try:
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        conn.request("GET", path, headers={"User-Agent": user_agent, "Accept": "image/*"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status}")
        return resp.read(limit + 1), resp.headers.get("Content-Type", "")
    finally:
        conn.close()


def is_public_host(host: str, resolve=socket.getaddrinfo) -> bool:
    """Whether every address `host` has is on the public internet (not this
    computer or the local network), so a logo address from the directory
    can't be used to reach things at home."""
    try:
        infos = resolve(host, None)
    except (OSError, UnicodeError):
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


def _text(value, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _http_url(value) -> str:
    url = _text(value, 2000)
    return url if urlparse(url).scheme in ("http", "https") and urlparse(url).hostname else ""


def clean_station(raw: dict) -> dict | None:
    """A directory entry, cut down to what the page shows; None when it
    has no usable stream address."""
    if not isinstance(raw, dict):
        return None
    url = _http_url(raw.get("url_resolved")) or _http_url(raw.get("url"))
    name = _text(raw.get("name"))
    if not url or not name:
        return None
    tags = [t for t in (_text(x, 30) for x in str(raw.get("tags") or "").split(",")) if t][:6]
    try:
        bitrate = max(0, int(raw.get("bitrate") or 0))
    except (TypeError, ValueError):
        bitrate = 0
    try:
        votes = max(0, int(raw.get("votes") or 0))
    except (TypeError, ValueError):
        votes = 0
    return {
        "uuid": _text(raw.get("stationuuid"), 64),
        "name": name,
        "url": url,
        "homepage": _http_url(raw.get("homepage")),
        "logo": _http_url(raw.get("favicon")),
        "tags": tags,
        "country": _text(raw.get("country"), 60),
        "countrycode": _text(raw.get("countrycode"), 2).upper(),
        "codec": _text(raw.get("codec"), 20),
        "bitrate": bitrate,
        "votes": votes,
        "hls": bool(raw.get("hls")),
    }


def clean_somafm(raw: dict) -> dict | None:
    """A SomaFM channel as a station (its 128k MP3 stream); None when it
    has no MP3 stream or no usable id."""
    if not isinstance(raw, dict):
        return None
    cid = _text(raw.get("id"), 40)
    title = _text(raw.get("title"))
    playlists = raw.get("playlists") if isinstance(raw.get("playlists"), list) else []
    if not cid or not title or not all(c.isalnum() or c in "-_" for c in cid) \
            or not any(isinstance(p, dict) and p.get("format") == "mp3" for p in playlists):
        return None
    try:
        listeners = max(0, int(raw.get("listeners") or 0))
    except (TypeError, ValueError):
        listeners = 0
    genres = [t for t in (_text(g, 30) for g in str(raw.get("genre") or "").split("|")) if t][:4]
    return {
        "uuid": "", "name": f"SomaFM {title}", "url": f"https://ice1.somafm.com/{cid}-128-mp3",
        "homepage": f"https://somafm.com/{cid}/", "logo": _http_url(raw.get("image")),
        "tags": genres, "country": _text(raw.get("description"), 120), "countrycode": "",
        "codec": "MP3", "bitrate": 128, "votes": listeners, "hls": False,
    }


class Directory:
    def __init__(self, user_agent: str = "StationPlayer", fetch: Callable = _fetch,
                 servers: list[str] | None = None, public: Callable[[str], bool] = is_public_host,
                 logo_fetch: Callable | None = None) -> None:
        self.user_agent = user_agent
        self._fetch = fetch
        self._public = public
        # Logos come from stations' own hosts: only public ones, pinned to
        # the checked address. (Tests that fake the API fake these too.)
        self._logo_fetch = logo_fetch or (_fetch_public if fetch is _fetch else fetch)
        self.servers = list(servers or SERVERS)
        self._good: str | None = None  # the mirror that answered last
        self._lock = threading.Lock()
        self._logos: OrderedDict[str, None] = OrderedDict()  # addresses it handed out
        self._logo_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._soma: tuple[float, list[dict]] | None = None  # (when, channels)

    def _api(self, path: str, params: dict) -> list:
        query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
        with self._lock:
            order = ([self._good] if self._good else []) + \
                [s for s in random.sample(self.servers, len(self.servers)) if s != self._good]
        last: Exception | None = None
        for host in order:
            try:
                body, _ = self._fetch(f"https://{host}{path}?{query}", self.user_agent, 4 * 1024 * 1024)
                data = json.loads(body)
            except (OSError, ValueError, http.client.HTTPException) as exc:
                last = exc
                continue
            with self._lock:
                self._good = host
            if not isinstance(data, list):
                raise DirectoryError("The station directory answered with something unexpected")
            return data
        logger.info("Station directory unreachable: %s", last)
        raise DirectoryError("Couldn't reach the station directory (radio-browser.info). Check the internet connection and try again.")

    def search(self, q: str = "", tag: str = "", country: str = "", order: str = "votes",
               limit: int = 40) -> list[dict]:
        """Stations matching a name, a genre tag and/or a country code; with
        none of them, the most popular stations."""
        limit = max(1, min(MAX_RESULTS, int(limit)))
        params = {
            "name": _text(q, 100), "tag": _text(tag, 50).lower(), "countrycode": _text(country, 2).upper(),
            "order": ORDERS.get(order, "votes"), "reverse": "false" if order == "name" else "true",
            "hidebroken": "true", "limit": str(limit * 2),  # some are dropped below
        }
        found = []
        seen = set()
        for raw in self._api("/json/stations/search", params):
            st = clean_station(raw)
            if st is None or st["hls"] or st["url"] in seen:
                continue  # HLS streams can't be played or recorded here
            seen.add(st["url"])
            found.append(st)
            if len(found) >= limit:
                break
        self._remember_logos(found)
        return found

    def _remember_logos(self, found: list[dict]) -> None:
        with self._lock:
            for st in found:
                if st["logo"]:
                    self._logos[st["logo"]] = None
                    self._logos.move_to_end(st["logo"])
            while len(self._logos) > 2000:
                self._logos.popitem(last=False)

    def somafm(self) -> list[dict]:
        """SomaFM's channels, from its own public list (kept for ten minutes)."""
        with self._lock:
            if self._soma and time.monotonic() - self._soma[0] < 600:
                return copy.deepcopy(self._soma[1])
        try:
            body, _ = self._fetch(SOMAFM_LIST, self.user_agent, 1024 * 1024)
            channels = json.loads(body).get("channels")
        except (OSError, ValueError, AttributeError, http.client.HTTPException) as exc:
            logger.info("SomaFM unreachable: %s", exc)
            raise DirectoryError("Couldn't reach SomaFM (somafm.com). Check the internet connection and try again.") from exc
        found = []
        for ch in channels if isinstance(channels, list) else []:
            st = clean_somafm(ch)
            if st:
                found.append(st)
        found.sort(key=lambda st: -st["votes"])  # most listened first
        self._remember_logos(found)
        with self._lock:
            self._soma = (time.monotonic(), found)
        return copy.deepcopy(found)

    def clicked(self, uuid: str) -> None:
        """Tell the directory a station was played (it ranks stations by
        this); in the background, and failures don't matter."""
        uuid = _text(uuid, 64)
        if not uuid or not all(c in "0123456789abcdef-" for c in uuid.lower()):
            return

        def send():
            try:
                self._api(f"/json/url/{uuid}", {})
            except (DirectoryError, OSError):
                pass
        threading.Thread(target=send, name="directory-click", daemon=True).start()

    def logo(self, url: str) -> tuple[bytes, str] | None:
        """A station's logo, if the directory listed that address and it's a
        small image; None otherwise."""
        with self._lock:
            if url not in self._logos:
                return None
            if url in self._logo_cache:
                self._logo_cache.move_to_end(url)
                return self._logo_cache[url]
        if not self._public(urlparse(url).hostname or ""):
            return None
        try:
            body, ctype = self._logo_fetch(url, self.user_agent, MAX_LOGO_BYTES)
        except (OSError, ValueError, http.client.HTTPException):
            return None
        ctype = ctype.split(";")[0].strip().lower()
        if len(body) > MAX_LOGO_BYTES or ctype not in LOGO_TYPES:
            return None
        with self._lock:
            self._logo_cache[url] = (body, ctype)
            while len(self._logo_cache) > 300:
                self._logo_cache.popitem(last=False)
        return body, ctype

