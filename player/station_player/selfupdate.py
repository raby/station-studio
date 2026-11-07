"""Update Station Player from inside the app (Settings › App, or the
new-version notice), so nobody needs a terminal or a fresh download.

The downloaded Mac and Windows apps fetch the new version's installer
from its GitHub release, and use it only when its SHA-256 matches the one
GitHub publishes for that file (station_appkit.selfupdate, shared with
Station Studio and Station Server). A pipx (or pip) install upgrades with
pip. Then the new version starts and the open page reloads into it.
"""
from __future__ import annotations

import sys

from station_appkit import selfupdate as _selfupdate
from station_appkit.selfupdate import UpdateError  # noqa: F401

from station_player.appinfo import APP
from station_player.desktop import app_executable, bundled

CHILD_ENV = "STATION_PLAYER_CHILD"


def install_kind(platform_name: str | None = None) -> str:
    """"mac" or "windows" for the downloaded apps, "pip" otherwise."""
    return _selfupdate.install_kind(platform_name, frozen=bundled()) or "pip"


def pip_command(version: str, python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
            f"station-player=={version}"]


def relaunch_command(port: int, kind: str) -> list[str]:
    me = [str(app_executable())] if kind != "pip" else [sys.executable, "-P", "-m", "station_player"]
    return [*me, "--app", "--no-browser", "--port", str(port), "--after-update"]


class Updater(_selfupdate.Updater):
    """Update now: the downloaded apps from their GitHub release, a pipx
    install with pip. `quit` stops this player (recordings closed first)."""

    KINDS = ("mac", "windows", "pip")

    def __init__(self, quit, port: int = APP.port, kind: str | None = None, **kwargs) -> None:
        super().__init__(APP, quit, port=port, kind=kind or install_kind(), child_env={CHILD_ENV: "1"},
                         mac_relaunch=lambda bundle, port: relaunch_command(port, "mac"), **kwargs)

    def update_pip(self, version: str) -> None:
        self._set(stage="installing", percent=None)
        result = self._run(pip_command(version), capture_output=True, text=True, timeout=600)
        if result.returncode:
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or [""]
            raise UpdateError(f"pip couldn't install {version}: {tail[0]} "
                              "Try `pipx upgrade station-player` in a terminal.")
        found = self._run([sys.executable, "-c", "import importlib.metadata as m; print(m.version('station-player'))"],
                          capture_output=True, text=True, timeout=60).stdout.strip()
        if found != version:
            raise UpdateError(f"pip installed {found or 'nothing'}, not {version}. "
                              "Try `pipx upgrade station-player` in a terminal.")
        self._set(stage="restarting")
        # The new version asks this one to quit and takes over (see __main__).
        self._launch(relaunch_command(self.port, "pip"))
