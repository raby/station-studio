"""Starting Station Server when you log in, so a station computer that
restarts (a power cut, Windows updates) goes back on air by itself.

Windows: a per-user "Run" registry value.
macOS:   a per-user LaunchAgent, which also starts Station Server again
         if it ever crashes (Quit ends it for good).
Linux:   an entry in ~/.config/autostart.

Each starts `station-server app --no-window`: the stations that were on
air go back on air, with no window until you open one.
"""
from __future__ import annotations

import sys
from pathlib import Path

from station_appkit.login import Login as _Login

APP_NAME = "Station Server"
APP_ID = "io.github.raby.station-server"
MAC_HELPER = "station-server-helper"  # see packaging/station-server.spec
CHILD_ENV = "STATION_SERVER_CHILD"


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def bundled() -> bool:
    """Whether this is the downloaded app, which carries its own Python."""
    return bool(getattr(sys, "frozen", False))


def app_executable() -> Path:
    """What runs Station Server in the downloaded app: on the Mac its helper
    (the app the Finder opens only hands over to it), elsewhere the app."""
    exe = Path(sys.executable)
    helper = exe.with_name(MAC_HELPER)
    return helper if sys.platform == "darwin" and helper.exists() else exe


def launch_command(background: bool = True, platform: str | None = None,
                   python: str | None = None) -> list[str]:
    tail = ["app", *(["--no-window"] if background else [])]
    if python is None and bundled():
        return [str(app_executable()), *tail]
    exe = python or sys.executable
    if (platform or current_platform()) == "windows" and Path(exe).with_name("pythonw.exe").exists():
        exe = str(Path(exe).with_name("pythonw.exe"))  # no console window
    return [exe, "-P", "-m", "station_server", *tail]


class Login(_Login):
    """Start at login: on, off, or whether it's on (station_appkit.login).
    On the Mac it's started again if it stops without being asked to, so
    the stations stay on air."""

    def __init__(self, platform: str | None = None, home: Path | None = None,
                 registry=None, python: str | None = None) -> None:
        from station_server.appinfo import APP
        self.python = python
        platform = platform or current_platform()
        super().__init__(APP, launch_command(True, platform, python), agent_id=APP_ID,
                         autostart_name=f"{APP_ID}.desktop", restart_after_crash=True, env={CHILD_ENV: "1"},
                         platform=platform, home=home, registry=registry)
