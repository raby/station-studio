"""New-version notice: asks PyPI, at most once a day, for the newest
Station Player release. Only the version number is fetched; nothing is
sent but the request itself. Turned off with "check_updates" in settings.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/station-player/json"
CHECK_EVERY = 24 * 60 * 60
_TIMEOUT = 10
_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")  # final releases only


def parse_version(text) -> tuple[int, ...] | None:
    if not isinstance(text, str) or not _VERSION_RE.match(text.strip()):
        return None
    return tuple(int(p) for p in text.strip().split("."))


def is_newer(latest: str | None, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _fetch_latest(url: str = PYPI_URL) -> str | None:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "StationPlayer"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read(2_000_000))
    version = data.get("info", {}).get("version") if isinstance(data, dict) else None
    return version if parse_version(version) else None


def latest_version(cache: Path, fetch=None, now: float | None = None, force: bool = False) -> str | None:
    """The newest release, from a cache under a day old (unless `force`),
    else from PyPI. Network trouble is quiet: the last known answer (or
    None) is kept."""
    now = time.time() if now is None else now
    fetch = fetch or _fetch_latest
    cached: dict = {}
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            cached = {}
    except (OSError, ValueError):
        pass
    checked = cached.get("checked")
    known = cached.get("latest") if parse_version(cached.get("latest")) else None
    if not force and isinstance(checked, (int, float)) and 0 <= now - checked < CHECK_EVERY:
        return known
    try:
        latest = fetch()
    except Exception as exc:  # offline, proxy, PyPI down: try again tomorrow
        logger.info("Couldn't check for a new version: %s", exc)
        latest = known
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked": now, "latest": latest}), encoding="utf-8")
    except OSError:
        pass
    return latest
