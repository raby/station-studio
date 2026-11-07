"""Update the downloaded Station Server app from inside it (the Update now
button), so nobody needs a terminal or a fresh download.

The app fetches the new version's installer from its GitHub release and
uses it only when its SHA-256 matches the one GitHub publishes for that
file (station_appkit.selfupdate, shared with Station Player and Station
Studio). On the Mac the new app replaces the old one once Station Server
has quit; on Windows the setup runs silently and starts the new version
itself.

Nothing here touches the data folder (~/.station-server): stations, play
logs and settings stay where they are. Stations that were on air go back
on air when the new version starts.
"""
from __future__ import annotations

from pathlib import Path

from station_appkit import selfupdate as _selfupdate
from station_appkit.selfupdate import UpdateError  # noqa: F401

from station_server.appinfo import APP
from station_server.desktop import CHILD_ENV, MAC_HELPER, bundled


def install_kind(platform_name: str | None = None) -> str | None:
    """"mac" or "windows" for the downloaded apps, None otherwise."""
    return _selfupdate.install_kind(platform_name, frozen=bundled())


def release_page(version: str) -> str:
    return APP.release_page(version)


def mac_relaunch_command(bundle: Path, port: int) -> list[str]:
    # The new app's helper, in the background; the open page reloads into it.
    return [f"{bundle}/Contents/MacOS/{MAC_HELPER}", "app", "--no-window", "--port", str(port)]


class Updater(_selfupdate.Updater):
    """The Update now button's work."""

    def __init__(self, quit, port: int = APP.port, kind: str | None = None, **kwargs) -> None:
        super().__init__(APP, quit, port=port, kind=kind or install_kind(), child_env={CHILD_ENV: "1"},
                         mac_relaunch=mac_relaunch_command, **kwargs)
