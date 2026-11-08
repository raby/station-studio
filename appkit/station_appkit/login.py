"""Start an app when the person logs in: on, off, whether it's on, and
bringing an entry made by another copy (or an older version) up to date.

macOS:   a per-user LaunchAgent (~/Library/LaunchAgents/<id>.plist).
Windows: a value in HKCU\\...\\CurrentVersion\\Run.
Linux:   an entry in ~/.config/autostart (or $XDG_CONFIG_HOME).

Each app says what to run (`command`) and what to call it; the rest is
the same for all three.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from station_appkit import App


def current_platform(platform: str | None = None) -> str:
    """"mac", "windows", "linux" or "other"."""
    platform = platform or sys.platform
    if platform.startswith("win") or platform == "windows":
        return "windows"
    if platform in ("darwin", "mac"):
        return "mac"
    if platform.startswith("linux") or "bsd" in platform:
        return "linux"
    return "other"


def not_installed(program: str) -> bool:
    """Run from the disk image (or from where macOS put a download it
    hasn't been moved out of), which is gone after the next restart."""
    return program.startswith("/Volumes/") or "/AppTranslocation/" in program


def desktop_exec(args: list[str]) -> str:
    """A command quoted for a freedesktop `Exec=` line: % doubled, any
    argument with a space or special character quoted (with ", `, $ and \\
    escaped), and the whole value escaped again as a desktop-file string."""
    out = []
    for arg in args:
        arg = arg.replace("%", "%%")
        if not arg or any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
            arg = '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'
        out.append(arg)
    return " ".join(out).replace("\\", "\\\\")


class RunKey:  # pragma: no cover - Windows only
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


class Login:
    """Start at login for one app.

    `command` is what starts it in the background (None when there's
    nothing to start it with: then it isn't offered). `agent_id` names the
    Mac LaunchAgent and `autostart_name` the Linux entry (kept as each app
    has always named them, so entries made by earlier versions are found).
    `restart_after_crash` keeps it running on the Mac if it stops without
    being asked to (a server that should stay on air); `env` goes in its
    environment there. `linux_entry` writes the Linux entry's text, for an
    app that wants more in it than a name and a command.

    `platform`, `home`, `environ` and `registry` let tests stand in for the
    real system; `environ` (for $XDG_CONFIG_HOME) is only read from the
    real environment when `home` is the real home folder."""

    def __init__(self, app: App, command: list[str] | None, *, agent_id: str, autostart_name: str,
                 restart_after_crash: bool = False, env: dict | None = None, linux_entry=None,
                 platform: str | None = None, home: Path | None = None, environ=None, registry=None) -> None:
        self.app = app
        self.command = command
        self.agent_id = agent_id
        self.autostart_name = autostart_name
        self.restart_after_crash = restart_after_crash
        self.env = dict(env or {})
        self.linux_entry = linux_entry
        self.platform = current_platform(platform)
        self.home = home or Path.home()
        self.environ = environ if environ is not None else (os.environ if home is None else {})
        self._registry = registry

    # ---- where it's kept -----------------------------------------------------

    def agent(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{self.agent_id}.plist"

    def autostart(self) -> Path:
        config = self.environ.get("XDG_CONFIG_HOME", "")
        base = Path(config) if config and Path(config).is_absolute() else self.home / ".config"
        return base / "autostart" / self.autostart_name

    def registry(self):
        return self._registry or RunKey()

    # ---- on, off, and whether it's on ------------------------------------------

    @property
    def supported(self) -> bool:
        return bool(self.command) and self.platform in ("mac", "windows", "linux")

    def enabled(self) -> bool | None:
        """Whether it starts at login; None where it can't."""
        if not self.supported:
            return None
        try:
            if self.platform == "windows":
                return self.registry().get(self.app.name) is not None
            if self.platform == "mac":
                return self.agent().is_file()
            return self.autostart().is_file()
        except OSError:
            return False

    def set(self, on: bool) -> None:
        """Turn it on or off. Raises ValueError when it can't be on (not
        supported here, or run from the disk image)."""
        if not self.supported:
            raise ValueError("Start at login isn't available here")
        if on and not_installed(self.command[0]):
            raise ValueError(f"Move {self.app.name} into your Applications folder first, then turn this on from there")
        if self.platform == "windows":
            if on:
                self.registry().set(self.app.name, subprocess.list2cmdline(self.command))
            else:
                self.registry().delete(self.app.name)
            return
        path = self.agent() if self.platform == "mac" else self.autostart()
        if not on:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.platform == "mac":
            with path.open("wb") as fh:
                plistlib.dump(self.agent_plist(), fh)
        else:
            path.write_text(self.linux_entry() if self.linux_entry else self.entry_text(), encoding="utf-8")

    def agent_plist(self) -> dict:
        plist = {"Label": self.agent_id, "ProgramArguments": list(self.command), "RunAtLoad": True}
        if self.env:
            plist["EnvironmentVariables"] = dict(self.env)
        if self.restart_after_crash:
            # Started again if it crashes; Quit (a clean exit) ends it.
            plist["KeepAlive"] = {"SuccessfulExit": False}
            plist["ThrottleInterval"] = 30
        else:
            plist["KeepAlive"] = False  # Quit ends it for the day
        return plist

    def entry_text(self) -> str:
        return ("[Desktop Entry]\nType=Application\n"
                f"Name={self.app.name}\nExec={desktop_exec(self.command)}\n"
                "Terminal=false\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n")

    def refresh(self) -> bool:
        """Start at login made by another copy (a pipx install, an app since
        moved) or an older version starts what it said then: written again
        to start this one, as this version would. True when it was."""
        if not self.supported or not self.enabled() or not_installed(self.command[0]):
            return False
        try:
            if self.platform == "windows":
                current = self.registry().get(self.app.name) == subprocess.list2cmdline(self.command)
            elif self.platform == "mac":
                with self.agent().open("rb") as fh:
                    current = plistlib.load(fh) == self.agent_plist()
            else:
                want = self.linux_entry() if self.linux_entry else self.entry_text()
                current = self.autostart().read_text(encoding="utf-8") == want
        except (OSError, ValueError, plistlib.InvalidFileException):
            current = False
        if current:
            return False
        try:
            self.set(True)
        except (OSError, ValueError):
            return False
        return True
