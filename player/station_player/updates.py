"""New-version notice: asks GitHub, at most once a day, for the newest
`player-v*` release (the releases that carry the Mac and Windows
downloads), as Station Server and Station Studio do. Only release names
are fetched; nothing is sent but the request itself. Turned off with
"check_updates" in settings. The work is station_appkit.updates."""
from __future__ import annotations

from pathlib import Path

from station_appkit import updates as _updates
from station_appkit.updates import CHECK_EVERY, is_newer, parse_version  # noqa: F401

from station_player.appinfo import APP

TAG_PREFIX = APP.tag_prefix


def newest_release(releases) -> str | None:
    return _updates.newest_release(releases, TAG_PREFIX)


def _fetch_latest() -> str | None:
    return _updates.fetch_latest(APP)


def latest_version(cache: Path, fetch=None, now: float | None = None, force: bool = False) -> str | None:
    """The newest release, from a cache under a day old (unless `force`),
    else from GitHub; quiet about network trouble (and tried again on the
    next look, not a day later)."""
    return _updates.latest_version(APP, cache, fetch=fetch or (lambda: _fetch_latest()), now=now, force=force)
