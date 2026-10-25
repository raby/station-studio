"""New-version notice for the downloaded Station Studio app.

Asks GitHub, at most once a day, for the newest `studio-v*` release
(the releases that carry the Mac and Windows downloads; see
.github/workflows/publish-studio-app.yml). Only release names are
fetched; nothing is sent but the request itself.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REPO = "raby/station-studio"
TAG_PREFIX = "studio-v"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=50"
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


def newest_release(releases) -> str | None:
    """The highest version among published `studio-v*` releases."""
    best: str | None = None
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith(TAG_PREFIX):
            continue
        version = tag[len(TAG_PREFIX):]
        if parse_version(version) and (best is None or is_newer(version, best)):
            best = version
    return best


def _fetch_latest(url: str = RELEASES_API) -> str | None:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "StationStudio"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return newest_release(json.loads(resp.read(5_000_000)))


def latest_version(cache: Path, fetch=None, now: float | None = None, force: bool = False) -> str | None:
    """The newest release, from a cache under a day old (unless `force`),
    else from GitHub. Network trouble is quiet: the last known answer (or
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
    except Exception as exc:  # offline, proxy, GitHub down: try again next time
        logger.info("Couldn't check for a new version: %s", exc)
        return known  # not cached as checked, so the next hourly look retries
    logger.info("Newest Station Studio release: %s", latest)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked": now, "latest": latest}), encoding="utf-8")
    except OSError:
        pass
    return latest
