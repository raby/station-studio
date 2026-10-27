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

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from station_player.desktop import _RunKey

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
    exe = Path(python or sys.executable)
    if (platform or current_platform()) == "windows" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")  # no console window
    return [str(exe), "-P", "-m", "station_server", *tail]


class Login:
    """Start at login: on, off, or whether it's on."""

    def __init__(self, platform: str | None = None, home: Path | None = None,
                 registry=None, python: str | None = None) -> None:
        self.platform = platform or current_platform()
        self.home = home or Path.home()
        self._registry = registry
        self.python = python

    def _agent(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{APP_ID}.plist"

    def _autostart(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") if self.home == Path.home() else None
        return Path(base or self.home / ".config") / "autostart" / f"{APP_ID}.desktop"

    def _reg(self):
        return self._registry or _RunKey()

    def enabled(self) -> bool:
        try:
            if self.platform == "windows":
                return self._reg().get(APP_NAME) is not None
            if self.platform == "mac":
                return self._agent().is_file()
            return self._autostart().is_file()
        except OSError:
            return False

    def refresh(self) -> None:
        """Start at login made by an older version (0.16.4 to 0.16.6 started
        the Mac app's main program): written again as this version would."""
        if self.platform != "mac" or not self.enabled():
            return
        try:
            with self._agent().open("rb") as fh:
                current = plistlib.load(fh).get("ProgramArguments")
        except (OSError, ValueError, plistlib.InvalidFileException):
            return
        if current != launch_command(True, self.platform, self.python):
            try:
                self.set(True)
            except (OSError, ValueError):
                pass

    def set(self, on: bool) -> None:
        cmd = launch_command(True, self.platform, self.python)
        if on and (cmd[0].startswith("/Volumes/") or "/AppTranslocation/" in cmd[0]):
            # The disk image, or where macOS put a download not yet moved
            # out of it: gone after the next restart.
            raise ValueError("Move Station Server into your Applications folder first, then turn this on from there")
        if self.platform == "windows":
            if on:
                self._reg().set(APP_NAME, subprocess.list2cmdline(cmd))
            else:
                self._reg().delete(APP_NAME)
            return
        path = self._agent() if self.platform == "mac" else self._autostart()
        if not on:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.platform == "mac":
            with path.open("wb") as fh:
                plistlib.dump({
                    "Label": APP_ID,
                    "ProgramArguments": cmd,
                    "EnvironmentVariables": {CHILD_ENV: "1"},
                    "RunAtLoad": True,
                    # Started again if it crashes; Quit (a clean exit) ends it.
                    "KeepAlive": {"SuccessfulExit": False},
                    "ThrottleInterval": 30,
                }, fh)
        else:
            exec_line = " ".join(f'"{c}"' if " " in c else c for c in cmd)
            path.write_text(
                f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nExec={exec_line}\n"
                "X-GNOME-Autostart-enabled=true\nNoDisplay=true\n", encoding="utf-8")
