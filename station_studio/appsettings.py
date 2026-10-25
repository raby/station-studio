"""The app's own settings (Settings › App on the page), as Station Player
has them: whether it opens in a window of its own, whether it looks for
new versions, and whether it starts when you log in.

The first two are kept in app-settings.json in the data folder. Start at
login is whatever the computer says: a LaunchAgent on a Mac, the Run key
on Windows, an autostart entry on Linux.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from station_studio import selfupdate

DEFAULTS = {"own_window": True, "check_updates": True}
LOGIN_ID = "io.github.raby.station-studio"
LOGIN_NAME = "Station Studio"


def load(home: Path) -> dict:
    settings = dict(DEFAULTS)
    try:
        saved = json.loads((home / "app-settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if isinstance(saved, dict):
        settings.update({k: v for k, v in saved.items() if k in DEFAULTS and isinstance(v, bool)})
    return settings


def save(home: Path, changes: dict) -> dict:
    settings = load(home)
    settings.update({k: bool(v) for k, v in changes.items() if k in DEFAULTS and v is not None})
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / "app-settings.json.tmp"
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(tmp, home / "app-settings.json")
    return settings


def launch_command(frozen: bool | None = None, executable: str | None = None,
                   platform: str | None = None, which=shutil.which) -> list[str] | None:
    """What starts Station Studio in the background at login, or None
    when there's nothing to start it with."""
    frozen = selfupdate.bundled() if frozen is None else frozen
    exe = Path(executable or sys.executable)
    platform = platform or sys.platform
    if frozen:
        if platform == "darwin":  # the helper runs the server (see packaging/)
            exe = exe.with_name(selfupdate.MAC_HELPER)
        return [str(exe), "--no-browser"]
    cli = which("station-studio")
    return [cli, "app", "--no-browser"] if cli else None


class Login:
    """Start Station Studio when you log in, without opening a window."""

    def __init__(self, home: Path | None = None, platform: str | None = None,
                 command: list[str] | None = None, run_key=None) -> None:
        self.home = home or Path.home()
        self.platform = platform or sys.platform
        self.command = command if command is not None else launch_command(platform=self.platform)
        self._run_key = run_key

    @property
    def supported(self) -> bool:
        return bool(self.command) and (self.platform in ("darwin", "win32") or self.platform.startswith("linux"))

    def enabled(self) -> bool | None:
        if not self.supported:
            return None
        if self.platform == "darwin":
            return self._mac_agent().exists()
        if self.platform == "win32":
            return self._key().get(LOGIN_NAME) is not None
        return self._linux_entry().exists()

    def set(self, on: bool) -> None:
        if not self.supported:
            raise ValueError("Start at login isn't available here")
        if on and _not_installed(self.command[0]):
            raise ValueError("move Station Studio into your Applications folder first, then turn this on from there")
        if self.platform == "darwin":
            agent = self._mac_agent()
            if not on:
                agent.unlink(missing_ok=True)
                return
            agent.parent.mkdir(parents=True, exist_ok=True)
            with agent.open("wb") as fh:
                plistlib.dump({
                    "Label": LOGIN_ID,
                    "ProgramArguments": self.command,
                    "RunAtLoad": True,
                    "KeepAlive": False,  # Quit on the page ends it for the day
                }, fh)
        elif self.platform == "win32":
            if on:
                self._key().set(LOGIN_NAME, subprocess.list2cmdline(self.command))
            else:
                self._key().delete(LOGIN_NAME)
        else:
            entry = self._linux_entry()
            if not on:
                entry.unlink(missing_ok=True)
                return
            entry.parent.mkdir(parents=True, exist_ok=True)
            exec_line = " ".join(_desktop_quote(a) for a in self.command)
            entry.write_text(
                "[Desktop Entry]\nType=Application\nName=Station Studio\n"
                f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
                encoding="utf-8")

    def _mac_agent(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{LOGIN_ID}.plist"

    def _linux_entry(self) -> Path:
        config = os.environ.get("XDG_CONFIG_HOME") or str(self.home / ".config")
        return Path(config) / "autostart" / "station-studio.desktop"

    def _key(self):
        return self._run_key or _RunKey()


def _not_installed(program: str) -> bool:
    """Run from the disk image (or from where macOS put a download it
    hasn't been moved out of), which is gone after the next restart."""
    return program.startswith("/Volumes/") or "/AppTranslocation/" in program


def _desktop_quote(arg: str) -> str:
    if any(c in arg for c in ' \t"\'\\$`'):
        return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'
    return arg


class _RunKey:  # pragma: no cover - Windows only
    """HKCU\\...\\CurrentVersion\\Run, the per-user list of login programs."""

    PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.PATH) as key:
                return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    def set(self, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.PATH) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass
