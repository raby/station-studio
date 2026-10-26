"""Settings › Internet: can this computer stream to listeners itself?

- how fast its internet connection uploads (sending test data to
  Cloudflare's free speed test), and so how many listeners it can take
- the address listeners use from outside (Cloudflare says what it sees)
- a DuckDNS name kept pointing at that address when it changes

Nothing is sent but the test data and, for DuckDNS, the name and token.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from station_server import __version__

logger = logging.getLogger(__name__)

UP_URL = "https://speed.cloudflare.com/__up"
META_URL = "https://speed.cloudflare.com/meta"
DUCKDNS_URL = "https://www.duckdns.org/update"
USER_AGENT = f"station-server/{__version__}"
# Upload sizes tried in turn, bigger while each is quick, for about 10 s at most.
SIZES = (250_000, 1_000_000, 4_000_000, 10_000_000)
ENOUGH_SECONDS = 3.0  # a send this long gives a good measure
MOST_SECONDS = 12.0
# Listeners get this share of the upload; the rest is for everything else.
SHARE = 0.7
DUCKDNS_EVERY = 5 * 60


class CheckError(Exception):
    pass


def _open(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def upload_mbps(opener=_open, clock=time.monotonic, sizes=SIZES) -> float:
    """Upload speed in Mbit/s, from the largest test sent in good time."""
    best = None
    spent = 0.0
    for size in sizes:
        req = urllib.request.Request(UP_URL, data=os.urandom(size), method="POST",
                                     headers={"User-Agent": USER_AGENT, "Content-Type": "application/octet-stream"})
        began = clock()
        try:
            with opener(req, timeout=30) as resp:
                resp.read(1024)
        except OSError as exc:
            if best is None:
                raise CheckError(f"The upload test couldn't reach Cloudflare: {exc}") from None
            break
        took = max(clock() - began, 0.001)
        spent += took
        best = size * 8 / took / 1_000_000
        if took >= ENOUGH_SECONDS or spent + took * 4 > MOST_SECONDS:
            break
    return round(best, 1)


def public_address(opener=_open) -> str | None:
    """The address the internet sees this computer at (its router's)."""
    req = urllib.request.Request(META_URL, headers={"User-Agent": USER_AGENT})
    try:
        with opener(req, timeout=10) as resp:
            data = json.loads(resp.read(65536))
    except (OSError, ValueError):
        return None
    ip = data.get("clientIp") if isinstance(data, dict) else None
    return ip if isinstance(ip, str) and re.fullmatch(r"[0-9a-fA-F.:]{3,45}", ip) else None


def listeners_for(mbps: float, kbps: int) -> int:
    """How many listeners at `kbps` an upload of `mbps` takes comfortably."""
    return max(0, int(mbps * 1000 * SHARE // max(kbps, 8)))


def suggest(mbps: float, stations: list[dict]) -> list[dict]:
    """Each station streaming from this computer ({id, name, port, bitrate,
    max}) with a suggested cap, the upload shared in proportion to bitrate."""
    total_kbps = sum(s["bitrate"] for s in stations) or 1
    out = []
    for s in stations:
        share = mbps * s["bitrate"] / total_kbps
        out.append({**s, "suggested": listeners_for(share, s["bitrate"])})
    return out


# ---- DuckDNS -------------------------------------------------------------

def clean_duckdns(name: str) -> str:
    """'rebirth' from 'rebirth', 'rebirth.duckdns.org' or its https link."""
    name = str(name or "").strip().lower()
    name = re.sub(r"^https?://", "", name).split("/")[0]
    name = re.sub(r"\.duckdns\.org$", "", name)
    if name and not re.fullmatch(r"[a-z0-9-]{1,63}", name):
        raise ValueError("Enter the DuckDNS name, like rebirth (for rebirth.duckdns.org)")
    return name


def duckdns_update(name: str, token: str, opener=_open) -> None:
    """Point name.duckdns.org at this connection's address (DuckDNS sees it)."""
    query = urllib.parse.urlencode({"domains": name, "token": token, "ip": ""})
    req = urllib.request.Request(f"{DUCKDNS_URL}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with opener(req, timeout=15) as resp:
            answer = resp.read(100).decode("ascii", "replace").strip()
    except OSError as exc:
        raise CheckError(f"DuckDNS couldn't be reached: {exc}") from None
    if answer != "OK":
        raise CheckError("DuckDNS said no: check the name and the token (duckdns.org, once signed in)")


class DuckDns:
    """Keeps a DuckDNS name up to date, every few minutes while it's set."""

    def __init__(self, app, update=duckdns_update) -> None:
        self.app = app
        self._update = update
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: dict | None = None  # {"at", "ok", "error"}

    def apply(self) -> None:
        cfg = self.app.config
        if cfg.duckdns_name and cfg.duckdns_token:
            self._stop.clear()  # also keeps a thread that was told to stop
            if not (self._thread and self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="duckdns", daemon=True)
                self._thread.start()
        else:
            self._stop.set()
            self.last = None

    def stop(self) -> None:
        self._stop.set()

    def update_now(self) -> dict:
        cfg = self.app.config
        try:
            self._update(cfg.duckdns_name, cfg.duckdns_token)
            self.last = {"at": time.time(), "ok": True, "error": None}
        except CheckError as exc:
            logger.warning("DuckDNS: %s", exc)
            self.last = {"at": time.time(), "ok": False, "error": str(exc)}
        return self.last

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.app.config.duckdns_name and self.app.config.duckdns_token:
                self.update_now()
            if self._stop.wait(DUCKDNS_EVERY):
                return
