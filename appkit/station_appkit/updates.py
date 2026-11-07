"""The new-version notice: asks GitHub, at most once a day, for an app's
newest release (`studio-v*`, `player-v*` or `server-v*`: the releases
that carry its Mac and Windows downloads). Only release names are
fetched; nothing is sent but the request itself.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path

from station_appkit import App

logger = logging.getLogger(__name__)

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


def newest_release(releases, tag_prefix: str) -> str | None:
    """The highest version among published releases tagged `tag_prefix`."""
    best: str | None = None
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith(tag_prefix):
            continue
        version = tag[len(tag_prefix):]
        if parse_version(version) and (best is None or is_newer(version, best)):
            best = version
    return best


def fetch_latest(app: App, opener=urllib.request.urlopen) -> str | None:
    """The app's newest release, from GitHub."""
    req = urllib.request.Request(app.releases_api, headers={"Accept": "application/vnd.github+json",
                                                            "User-Agent": app.slug})
    with opener(req, timeout=_TIMEOUT) as resp:
        return newest_release(json.loads(resp.read(5_000_000)), app.tag_prefix)


def latest_version(app: App, cache: Path, fetch=None, now: float | None = None, force: bool = False,
                   quiet: bool = True) -> str | None:
    """The newest release, from a cache under a day old (unless `force`),
    else from GitHub.

    Network trouble is quiet: the last known answer (or None) is kept, and
    the failed look isn't cached, so the next one (an hour on) tries again
    rather than waiting a day. With `quiet` off it raises instead, so a
    check asked for by hand can say it couldn't, rather than that all is up
    to date."""
    now = time.time() if now is None else now
    fetch = fetch or (lambda: fetch_latest(app))
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
        logger.info("Couldn't check for a new version of %s: %s", app.name, exc)
        if not quiet:
            raise
        return known
    logger.info("Newest %s release: %s", app.name, latest)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked": now, "latest": latest}), encoding="utf-8")
    except OSError:
        pass
    return latest
