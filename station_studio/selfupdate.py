"""Update the downloaded Station Studio app from inside it (the Update now
button), so nobody needs a terminal or a fresh download.

The app fetches the new version's installer from its GitHub release and
uses it only when its SHA-256 matches the one GitHub publishes for that
file. On the Mac the new app replaces the old one once Station Studio has
quit; on Windows the setup runs silently and starts the new version
itself. The work is station_appkit.selfupdate, shared with Station Player
and Station Server.

Nothing here touches the data folder (~/.station-studio): stations,
pools, playlists and exports stay where they are.
"""
from __future__ import annotations

from station_appkit import selfupdate as _selfupdate
from station_appkit.selfupdate import UpdateError, bundled, install_kind  # noqa: F401

from station_studio.appinfo import APP

MAC_HELPER = APP.mac_helper


def release_page(version: str) -> str:
    return APP.release_page(version)


class Updater(_selfupdate.Updater):
    """The Update now button's work. On the Mac the new app's helper is
    started on the same port (the page that's open reloads into it)."""

    def __init__(self, quit, port: int = APP.port, kind: str | None = None, **kwargs) -> None:
        super().__init__(APP, quit, port=port, kind=kind, **kwargs)
