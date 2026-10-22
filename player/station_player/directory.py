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

from station_player.logos import sniff
from station_player.radio import tls_context

logger = logging.getLogger(__name__)

# Radio Browser's public mirrors. all.api.radio-browser.info points at
# whichever are up; the named ones are tried after it.
SERVERS = ["all.api.radio-browser.info", "de1.api.radio-browser.info", "de2.api.radio-browser.info",
           "fi1.api.radio-browser.info", "nl1.api.radio-browser.info"]
TIMEOUT = 8
MAX_RESULTS = 60
MAX_LOGO_BYTES = 256 * 1024
MAX_OFFSET = 5000  # how far "Show more" goes
MAP_WORLD = 500  # stations on the whole globe
MAP_COUNTRY = 300  # more, once it's zoomed into one country
NEWS_TAGS = ("news", "talk")
SPOKEN = {"news", "talk", "sport", "sports", "information", "politics"}  # not music
# Not SVG: it can carry script.
LOGO_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/x-icon", "image/vnd.microsoft.icon")
ORDERS = {"votes": "votes", "popular": "clickcount", "name": "name"}
SOMAFM_LIST = "https://somafm.com/channels.json"
# Genre buttons that stand for more than one of the directory's tags
# ("techno / trance" asks for both). The directory matches part of a tag,
# so "afrobeat" also finds "afrobeats".
TAG_ALIASES = {"afrobeats": ("afrobeat",), "bhangra": ("bhangra", "punjabi"), "chart": ("chart", "top 40")}


def genre_tags(tag: str) -> list[str]:
    """The directory tags a genre stands for: "techno / trance" is both."""
    out = []
    for part in _text(tag, 80).lower().split("/"):
        for t in TAG_ALIASES.get(part.strip(), (part.strip(),)):
            if t and t not in out:
                out.append(t[:50])
    return out[:4]


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
        **_geo(raw),
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


def _geo(raw: dict) -> dict:
    """{"lat", "lon"} where the directory knows where a station is, else {}."""
    try:
        lat, lon = float(raw.get("geo_lat")), float(raw.get("geo_long"))
    except (TypeError, ValueError):
        return {}
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return {}  # 0, 0 is in the sea: a missing position written as zero
    return {"lat": round(lat, 4), "lon": round(lon, 4)}


def _spoken(raw: dict) -> bool:
    """Whether a station's tags say it's mostly talking, not music."""
    return any(w in SPOKEN for t in str(raw.get("tags") or "").lower().split(",") for w in t.split())


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
        self.public = public  # also approves hosts for preview relays
        # Logos come from stations' own hosts: only public ones, pinned to
        # the checked address. (Tests that fake the API fake these too.)
        self._logo_fetch = logo_fetch or (_fetch_public if fetch is _fetch else fetch)
        self.servers = list(servers or SERVERS)
        self._good: str | None = None  # the mirror that answered last
        self._lock = threading.Lock()
        self._logos: OrderedDict[str, None] = OrderedDict()  # addresses it handed out
        self._logo_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._soma: tuple[float, list[dict]] | None = None  # (when, channels)
        self._streams: OrderedDict[str, None] = OrderedDict()  # stream addresses it handed out
        self._maps: OrderedDict[tuple, tuple[float, list[dict]]] = OrderedDict()  # globe dots, kept a while

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
               limit: int = 40, offset: int = 0) -> list[dict]:
        return self.search_page(q, tag, country, order, limit, offset)["stations"]

    def search_page(self, q: str = "", tag: str = "", country: str = "", order: str = "votes",
                    limit: int = 40, offset: int = 0) -> dict:
        """{"stations": up to `limit` stations matching a name, a genre tag
        and/or a country code (with none of them, the most popular), "next":
        the offset that continues the list, or None at its end}."""
        limit = max(1, min(MAX_RESULTS, int(limit)))
        offset = max(0, min(MAX_OFFSET, int(offset)))
        tags = genre_tags(tag)
        if len(tags) > 1:
            return self._search_tags(q, tags, country, order, limit, offset)
        tag = tags[0] if tags else ""
        asked = limit * 2  # some are dropped below
        params = {
            "name": _text(q, 100), "tag": tag, "countrycode": _text(country, 2).upper(),
            "order": ORDERS.get(order, "votes"), "reverse": "false" if order == "name" else "true",
            "hidebroken": "true", "limit": str(asked), "offset": str(offset),
        }
        found = []
        seen = set()
        raws = self._api("/json/stations/search", params)
        used = len(raws)
        for i, raw in enumerate(raws):
            st = clean_station(raw)
            if st is None or st["url"] in seen:
                continue
            seen.add(st["url"])
            found.append(st)
            if len(found) >= limit:
                used = i + 1
                break
        self._remember(found)
        more = used < len(raws) or len(raws) >= asked
        return {"stations": found, "next": offset + used if more and offset + used <= MAX_OFFSET else None}

    def _search_tags(self, q: str, tags: list[str], country: str, order: str, limit: int, offset: int) -> dict:
        """A search for any of several tags: each tag's page, merged in the
        asked order. "Show more" moves every tag on by the same amount."""
        raws = []
        full = False
        for tag in tags:
            got = self._api("/json/stations/search", {
                "name": _text(q, 100), "tag": tag, "countrycode": _text(country, 2).upper(),
                "order": ORDERS.get(order, "votes"), "reverse": "false" if order == "name" else "true",
                "hidebroken": "true", "limit": str(limit), "offset": str(offset),
            })
            raws += got
            full = full or len(got) >= limit
        key = ORDERS.get(order, "votes")

        def rank(raw):
            if key == "name":
                return str(raw.get("name") or "").strip().lower()
            try:
                return -int(raw.get(key) or 0)
            except (TypeError, ValueError):
                return 0
        raws.sort(key=rank)
        found = []
        seen = set()
        for raw in raws:
            st = clean_station(raw)
            if st is None or st["url"] in seen:
                continue
            seen.add(st["url"])
            found.append(st)
        self._remember(found)
        more = full and offset + limit <= MAX_OFFSET
        return {"stations": found, "next": offset + limit if more else None}

    def map_stations(self, country: str = "", kind: str = "all", genre: str = "") -> list[dict]:
        """Stations the directory knows the position of, for the globe: the
        best liked worldwide, or in one country. `kind` "news" is news and
        talk only; "music" leaves those out, or is one genre's. Kept ten
        minutes, since the globe asks again as it turns."""
        country = _text(country, 2).upper()
        if country and not country.isalpha():
            country = ""
        kind = kind if kind in ("news", "music") else "all"
        tags = genre_tags(genre) if kind == "music" else list(NEWS_TAGS) if kind == "news" else []
        key = (country, kind, tuple(tags))
        with self._lock:
            got = self._maps.get(key)
            if got and time.monotonic() - got[0] < 600:
                return copy.deepcopy(got[1])
        want = MAP_COUNTRY if country else MAP_WORLD
        raws = []
        for tag in tags or [""]:
            raws += self._api("/json/stations/search", {
                "tag": tag, "countrycode": country, "has_geo_info": "true", "order": "votes",
                "reverse": "true", "hidebroken": "true", "limit": str(want // len(tags) if tags else want),
            })
        if kind == "music" and not tags:
            raws = [r for r in raws if isinstance(r, dict) and not _spoken(r)]
        found, seen = [], set()
        for st in sorted(filter(None, map(clean_station, raws)), key=lambda st: -st["votes"]):
            if "lat" not in st or st["url"] in seen:
                continue
            seen.add(st["url"])
            found.append(st)
        self._remember(found)
        with self._lock:
            self._maps[key] = (time.monotonic(), found)
            while len(self._maps) > 40:
                self._maps.popitem(last=False)
        return copy.deepcopy(found)

    def find_logo(self, stream_url: str) -> tuple[bytes, str] | None:
        """The logo Radio Browser has for a stream address, if any."""
        if not stream_url.startswith(("http://", "https://")):
            return None
        for raw in self._api("/json/stations/byurl", {"url": stream_url[:2000]}):
            st = clean_station(raw)
            if st and st["logo"]:
                self._remember([st])
                got = self.logo(st["logo"])
                if got and sniff(got[0]):  # really a picture, whatever the server said
                    return got
        return None

    def listed(self, url: str) -> bool:
        """Whether the directory listed this stream address (so the player
        may relay it for a preview)."""
        with self._lock:
            return url in self._streams

    def _remember(self, found: list[dict]) -> None:
        with self._lock:
            for st in found:
                self._streams[st["url"]] = None
                self._streams.move_to_end(st["url"])
            while len(self._streams) > 2000:
                self._streams.popitem(last=False)
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
        self._remember(found)
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

