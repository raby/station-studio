"""The new-version notice: asks GitHub, at most once a day, for an app's
newest release (`studio-v*`, `player-v*` or `server-v*`: the releases
that carry its Mac and Windows downloads), and keeps what each recent one
says it changes (the "What's new" at the top of its notes), for Update
now to show. One request, the same one as ever: nothing is sent but the
request itself.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from station_appkit import App
from station_appkit.changelog import whats_new


def urlopen(*args, **kwargs):
    """urllib's urlopen, but an HTTP error it raises is closed already: it's
    a response too, and the callers here log one and carry on, which would
    leave its connection to the garbage collector (a leak 3.13+ warns of)."""
    try:
        return urllib.request.urlopen(*args, **kwargs)
    except urllib.error.HTTPError as exc:
        if exc.fp is not None:
            exc.close()
        raise

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


# How many recent releases' "What's new" the cache keeps: enough for a
# copy a few releases behind.
NOTES_KEPT = 10


def release_notes(releases, tag_prefix: str) -> dict[str, dict]:
    """Each recent published release's "What's new", by version (plain text;
    see station_appkit.changelog). Releases from before notes said what they
    changed have none, and are left out."""
    found: list[tuple[str, dict]] = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith(tag_prefix) or not parse_version(tag[len(tag_prefix):]):
            continue
        notes = whats_new(release.get("body"))
        if notes is not None:
            notes.version = tag[len(tag_prefix):]
            published = release.get("published_at")
            notes.date = published[:10] if isinstance(published, str) and published else None
            found.append((notes.version, notes.as_dict()))
    found.sort(key=lambda pair: parse_version(pair[0]), reverse=True)
    return dict(found[:NOTES_KEPT])


def _releases(app: App, opener=urlopen):
    req = urllib.request.Request(app.releases_api, headers={"Accept": "application/vnd.github+json",
                                                            "User-Agent": app.slug})
    with opener(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read(5_000_000))


def fetch_latest(app: App, opener=urlopen) -> str | None:
    """The app's newest release, from GitHub."""
    return newest_release(_releases(app, opener), app.tag_prefix)


def fetch_latest_with_notes(app: App, opener=urlopen) -> tuple[str | None, dict[str, dict]]:
    """The app's newest release, and what recent ones changed: one request."""
    releases = _releases(app, opener)
    return newest_release(releases, app.tag_prefix), release_notes(releases, app.tag_prefix)


# The cache's notes, read again only when the file changes: Player's page
# asks once a second while a new version is on offer.
_notes_read: dict[Path, tuple[int, dict]] = {}


def _cached_notes(cache: Path) -> dict:
    try:
        changed = cache.stat().st_mtime_ns
        seen = _notes_read.get(cache)
        if seen and seen[0] == changed:
            return seen[1]
        notes = json.loads(cache.read_text(encoding="utf-8")).get("notes")
    except (OSError, ValueError, AttributeError):
        return {}
    notes = notes if isinstance(notes, dict) else {}
    _notes_read[cache] = (changed, notes)
    return notes


def notes_since(cache: Path, current: str, latest: str | None) -> list[dict]:
    """What the releases after `current`, up to `latest`, say they change,
    newest first, from the cache latest_version keeps. Empty when there's
    no newer version, or GitHub's notes didn't say (older releases)."""
    if not is_newer(latest, current):
        return []
    notes = _cached_notes(cache)
    return [notes[v] for v in sorted(notes, key=lambda v: parse_version(v) or (), reverse=True)
            if isinstance(notes[v], dict) and is_newer(v, current) and not is_newer(v, latest)]


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
    fetch = fetch or (lambda: fetch_latest_with_notes(app))
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
        latest = fetch()  # a version, or (version, notes) from fetch_latest_with_notes
    except Exception as exc:  # offline, proxy, GitHub down: try again next time
        logger.info("Couldn't check for a new version of %s: %s", app.name, exc)
        if not quiet:
            raise
        return known
    notes = None
    if isinstance(latest, tuple):
        latest, notes = latest
    logger.info("Newest %s release: %s", app.name, latest)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        record = {"checked": now, "latest": latest}
        if notes is not None:
            record["notes"] = notes
        cache.write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass
    return latest
