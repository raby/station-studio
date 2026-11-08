"""New-version notice for the downloaded Station Server app: asks GitHub,
at most once a day, for the newest `server-v*` release (the releases that
carry the Mac and Windows downloads). Only release names are fetched;
nothing is sent but the request itself. The work is
station_appkit.updates, shared with Station Player and Station Studio."""
from __future__ import annotations

from pathlib import Path

from station_appkit import updates as _updates
from station_appkit.updates import CHECK_EVERY, is_newer, parse_version  # noqa: F401

from station_server.appinfo import APP

TAG_PREFIX = APP.tag_prefix


def newest_release(releases) -> str | None:
    return _updates.newest_release(releases, TAG_PREFIX)


def _fetch_latest() -> str | None:
    return _updates.fetch_latest(APP)


def latest_version(cache: Path, fetch=None, now: float | None = None, force: bool = False,
                   quiet: bool = True) -> str | None:
    """The newest release, from a cache under a day old (unless `force`),
    else from GitHub. Network trouble is quiet unless `quiet` is off, when
    it raises, so a check asked for by hand can say it couldn't."""
    return _updates.latest_version(APP, cache, fetch=fetch or (lambda: _fetch_latest()), now=now,
                                   force=force, quiet=quiet)
