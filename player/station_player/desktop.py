"""Make Station Player open like any other app: a shortcut in the apps menu
(and on the desktop where that's usual) and, optionally, starting when
the user logs in.

Shortcuts run `python -m station_player --app` with the Python that
Station Player is installed in, so they keep working across
`pipx upgrade`. `--app` reuses a copy that's already running.

Windows: Start menu and desktop shortcuts (.lnk, written by PowerShell)
         and a per-user "Run" registry value for login.
macOS:   ~/Applications/Station Player.app and a per-user LaunchAgent.
Linux:   a .desktop entry in the apps menu (and on the desktop), and one
         in ~/.config/autostart.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from station_player import __version__

APP_NAME = "Station Player"
APP_ID = "io.github.raby.station-player"
ICON_DIR = Path(__file__).parent / "static"
_CREATE_NO_WINDOW = 0x08000000


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        return "linux"
    return "other"


def launch_command(background: bool = False, platform: str | None = None,
                   python: str | None = None) -> list[str]:
    """The command a shortcut runs. On Windows it uses pythonw.exe, so no
    console window opens."""
    platform = platform or current_platform()
    exe = Path(python or sys.executable)
    if platform == "windows":
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            exe = windowless
    cmd = [str(exe), "-m", "station_player", "--app"]
    if background:
        cmd.append("--no-browser")
    return cmd


def _desktop_exec(args: list[str]) -> str:
    """Quote a command for a freedesktop Exec= line."""
    out = []
    for arg in args:
        arg = arg.replace("%", "%%")
        if any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
            arg = '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'
        out.append(arg)
    # The whole value is a desktop-file string, where "\" is escaped again.
    return " ".join(out).replace("\\", "\\\\")


class Desktop:
    """Create and remove the shortcuts. `platform`, `home`, `env`,
    `run`, `registry` and `folders` exist so tests can stand in for the
    real system."""

    def __init__(self, platform: str | None = None, home: Path | None = None,
                 env: dict | None = None, python: str | None = None,
                 run=None, registry=None, folders: dict | None = None) -> None:
        self.platform = platform or current_platform()
        self.home = home or Path.home()
        self.env = os.environ if env is None else env
        self.python = python
        self._run = run or subprocess.run
        self._registry = registry
        self._folders = folders

    # ---- what the UI shows ---------------------------------------------------

    @property
    def supported(self) -> bool:
        return self.platform in ("windows", "mac", "linux")

    def status(self) -> dict:
        info = {"supported": self.supported, "platform": self.platform,
                "shortcut": False, "login": False}
        if not self.supported:
            return info
        try:
            info["shortcut"] = self._shortcut_exists()
            info["login"] = self._login_exists()
        except OSError:
            pass
        return info

    def set_shortcut(self, on: bool) -> None:
        self._require_support()
        getattr(self, f"_{self.platform}_shortcut")(on)

    def set_login(self, on: bool) -> None:
        self._require_support()
        getattr(self, f"_{self.platform}_login")(on)

    def remove_all(self) -> None:
        self.set_shortcut(False)
        self.set_login(False)

    def _require_support(self) -> None:
        if not self.supported:
            raise OSError("Shortcuts aren't supported on this system")

    def _shortcut_exists(self) -> bool:
        if self.platform == "windows":
            return (self._win_folder("programs") / f"{APP_NAME}.lnk").exists()
        if self.platform == "mac":
            return (self._mac_bundle() / "Contents" / "Info.plist").exists()
        return self._linux_menu_entry().exists()

    def _login_exists(self) -> bool:
        if self.platform == "windows":
            return self._reg().get(APP_NAME) is not None
        if self.platform == "mac":
            return self._mac_agent().exists()
        return self._linux_autostart().exists()

    # ---- Linux -------------------------------------------------------------------

    def _xdg(self, var: str, default: str) -> Path:
        value = self.env.get(var, "")
        return Path(value) if value and Path(value).is_absolute() else self.home / default

    def _linux_menu_entry(self) -> Path:
        return self._xdg("XDG_DATA_HOME", ".local/share") / "applications" / "station-player.desktop"

    def _linux_autostart(self) -> Path:
        return self._xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "station-player.desktop"

    def _linux_desktop_dir(self) -> Path | None:
        dirs = self._xdg("XDG_CONFIG_HOME", ".config") / "user-dirs.dirs"
        try:
            for line in dirs.read_text(encoding="utf-8").splitlines():
                if line.startswith("XDG_DESKTOP_DIR="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    value = value.replace("$HOME", str(self.home))
                    path = Path(value)
                    return path if path.is_absolute() and path.is_dir() else None
        except OSError:
            pass
        default = self.home / "Desktop"
        return default if default.is_dir() else None

    def _linux_icon(self) -> Path:
        # A copy outside site-packages, whose path changes with the
        # Python version pipx installs into.
        return self._xdg("XDG_DATA_HOME", ".local/share") / "icons" / "station-player.png"

    def _linux_install_icon(self) -> None:
        icon = self._linux_icon()
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ICON_DIR / "icon.png", icon)

    def _linux_drop_icon_if_unused(self) -> None:
        if not self._linux_menu_entry().exists() and not self._linux_autostart().exists():
            self._linux_icon().unlink(missing_ok=True)

    def _linux_entry_text(self, background: bool) -> str:
        cmd = launch_command(background, "linux", self.python)
        lines = [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.0",
            f"Name={APP_NAME}",
            "GenericName=Music Player",
            "Comment=Play Station Studio playlists, music folders and internet radio",
            f"Exec={_desktop_exec(cmd)}",
            f"Icon={self._linux_icon()}",
            "Terminal=false",
            "Categories=AudioVideo;Audio;Player;",
            "StartupNotify=false",
        ]
        if background:
            lines.append("X-GNOME-Autostart-enabled=true")
        return "\n".join(lines) + "\n"

    def _linux_shortcut(self, on: bool) -> None:
        menu = self._linux_menu_entry()
        desk_dir = self._linux_desktop_dir()
        on_desktop = desk_dir / "station-player.desktop" if desk_dir else None
        if on:
            self._linux_install_icon()
            menu.parent.mkdir(parents=True, exist_ok=True)
            text = self._linux_entry_text(background=False)
            menu.write_text(text, encoding="utf-8")
            if on_desktop:
                on_desktop.write_text(text, encoding="utf-8")
                on_desktop.chmod(0o755)  # desktops only launch executable entries
        else:
            for path in (menu, on_desktop):
                if path is not None:
                    path.unlink(missing_ok=True)
            self._linux_drop_icon_if_unused()

    def _linux_login(self, on: bool) -> None:
        entry = self._linux_autostart()
        if on:
            self._linux_install_icon()
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(self._linux_entry_text(background=True), encoding="utf-8")
        else:
            entry.unlink(missing_ok=True)
            self._linux_drop_icon_if_unused()

    # ---- macOS -------------------------------------------------------------------

    def _mac_bundle(self) -> Path:
        return self.home / "Applications" / f"{APP_NAME}.app"

    def _mac_agent(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{APP_ID}.plist"

    def _mac_shortcut(self, on: bool) -> None:
        bundle = self._mac_bundle()
        if not on:
            # Only ever delete a bundle this code made.
            try:
                with (bundle / "Contents" / "Info.plist").open("rb") as fh:
                    ours = plistlib.load(fh).get("CFBundleIdentifier") == APP_ID
            except (OSError, plistlib.InvalidFileException, ValueError):
                ours = False
            if ours:
                shutil.rmtree(bundle)
            return
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True, exist_ok=True)
        (contents / "Resources").mkdir(parents=True, exist_ok=True)
        cmd = " ".join(shlex.quote(a) for a in launch_command(False, "mac", self.python))
        script = contents / "MacOS" / "station-player"
        script.write_text(f'#!/bin/sh\nexec {cmd} "$@"\n', encoding="utf-8")
        script.chmod(0o755)
        icon = ICON_DIR / "icon.icns"
        if icon.exists():
            shutil.copyfile(icon, contents / "Resources" / "icon.icns")
        info = {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": APP_ID,
            "CFBundleExecutable": "station-player",
            "CFBundleIconFile": "icon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            # A background helper: the UI is the browser, so no Dock icon.
            "LSUIElement": True,
        }
        with (contents / "Info.plist").open("wb") as fh:
            plistlib.dump(info, fh)

    def _mac_login(self, on: bool) -> None:
        agent = self._mac_agent()
        if not on:
            agent.unlink(missing_ok=True)
            return
        agent.parent.mkdir(parents=True, exist_ok=True)
        with agent.open("wb") as fh:
            plistlib.dump({
                "Label": APP_ID,
                "ProgramArguments": launch_command(True, "mac", self.python),
                "RunAtLoad": True,
                # Quitting from the app ends the job; don't restart it.
                "KeepAlive": False,
            }, fh)

    # ---- Windows ---------------------------------------------------------------

    def _win_folder(self, which: str) -> Path:
        if self._folders is not None:
            return Path(self._folders[which])
        import ctypes  # Windows only
        from ctypes import wintypes
        csidl = {"programs": 0x0002, "desktop": 0x0010}[which]
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) != 0:
            raise OSError(f"Could not find the {which} folder")
        return Path(buf.value)

    def _windows_shortcut(self, on: bool) -> None:
        links = [self._win_folder(w) / f"{APP_NAME}.lnk" for w in ("programs", "desktop")]
        if not on:
            for link in links:
                link.unlink(missing_ok=True)
            return
        cmd = launch_command(False, "windows", self.python)
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SP_LINK); "
            "$s.TargetPath = $env:SP_TARGET; $s.Arguments = $env:SP_ARGS; "
            "$s.WorkingDirectory = $env:SP_HOME; $s.IconLocation = $env:SP_ICON; "
            "$s.Description = 'Play Station Studio playlists, music and radio'; $s.Save()"
        )
        for link in links:
            link.parent.mkdir(parents=True, exist_ok=True)
            # Paths travel in environment variables, never inside the
            # script text, so no quoting can go wrong.
            env = {**self.env, "SP_LINK": str(link), "SP_TARGET": cmd[0],
                   "SP_ARGS": subprocess.list2cmdline(cmd[1:]), "SP_HOME": str(self.home),
                   "SP_ICON": str(ICON_DIR / "icon.ico")}
            result = self._run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", script],
                env=env, capture_output=True, text=True, creationflags=_CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                raise OSError(f"Could not create {link.name}: {(result.stderr or '').strip()}")

    def _windows_login(self, on: bool) -> None:
        reg = self._reg()
        if on:
            reg.set(APP_NAME, subprocess.list2cmdline(launch_command(True, "windows", self.python)))
        else:
            reg.delete(APP_NAME)

    def _reg(self):
        return self._registry or _RunKey()


class _RunKey:
    """HKCU\\...\\CurrentVersion\\Run, the per-user list of login programs."""

    PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def get(self, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.PATH) as key:
                return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
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
        except FileNotFoundError:
            pass
