"""Opening Station Server in a window of its own, the way station-player does.

Chrome, Edge or Brave show the page as a plain app window (no tabs or
address bar) with its own browser profile under `~/.station-server`.
Installed as an app from that window (Install as app, in Settings), it
gets its own name and icon in the Dock and taskbar, and is opened that way
from then on. Without one of those browsers it opens in a browser tab.

The downloaded Mac app needs none of that: it has a window of its own
(macwindow.py), so opening Station Server there means opening the app.
"""
from __future__ import annotations

import email.message
import plistlib
import re
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from station_player.window import _failed, _spawn, find_browser

APP_NAME = "Station Server"


def installed_app_id(url: str, profile: Path, home: Path | None = None, platform: str | None = None) -> str | None:
    """The id of Station Server installed as an app from its own window, or
    None. Found from the shortcut the browser made for it, which names its
    id, address and profile."""
    home = home or Path.home()
    platform = platform or sys.platform
    if platform == "darwin":
        for info_path in home.glob("Applications/*.localized/*.app/Contents/Info.plist"):
            try:
                with info_path.open("rb") as fh:
                    info = plistlib.load(fh)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            app_id = info.get("CrAppModeShortcutID")
            if app_id and str(info.get("CrAppModeShortcutURL", "")).startswith(url) \
                    and str(profile) in str(info.get("CrAppModeUserDataDir", "")):
                return str(app_id)
        return None
    if platform.startswith("linux"):
        for entry in (home / ".local" / "share" / "applications").glob("*.desktop"):
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = re.search(r"--app-id=([a-p]{32})", text)
            if found and f"--user-data-dir={profile}" in text.replace('"', "") \
                    and re.search(rf"^Name={APP_NAME}$", text, re.M):
                return found.group(1)
    return None


def mac_app_bundle(platform: str | None = None, executable: str | None = None,
                   frozen: bool | None = None) -> Path | None:
    """The downloaded Station Server.app this runs from, whose own window
    shows Station Server (see macwindow.py); None anywhere else."""
    platform = platform or sys.platform
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if platform != "darwin" or not frozen:
        return None
    exe = Path(executable or sys.executable)
    if len(exe.parents) < 3 or exe.parent.name != "MacOS" or exe.parents[2].suffix != ".app":
        return None
    return exe.parents[2]


def open_ui(url: str, home: Path, own_window: bool = True, exe: str | None = None, run=None,
            app: Path | None = None) -> str:
    """Show the page: in its own window when possible, else a browser tab.
    Returns "window" or "tab"."""
    app = app if app is not None else (mac_app_bundle() if own_window else None)
    if own_window and app:
        # Opens the app, or brings its window back if it's running.
        try:
            proc = (run or _spawn)(["/usr/bin/open", str(app)])
            if not _failed(proc, wait=10):
                return "window"
        except OSError:
            pass
    exe = exe if exe is not None else (find_browser() if own_window else None)
    if own_window and exe:
        profile = home / "window"
        app_id = installed_app_id(url, profile)
        cmd = [exe, f"--app-id={app_id}" if app_id else f"--app={url}", f"--user-data-dir={profile}",
               "--no-first-run", "--no-default-browser-check", "--window-size=1180,820"]
        try:
            proc = (run or _spawn)(cmd)
            if not _failed(proc):
                return "window"
        except OSError:
            pass
    webbrowser.open(url)
    return "tab"


# ---- for the Mac app's window (macwindow.py), which saves downloads itself ----

def download_name(disposition: str | None, url: str) -> str:
    """The file name a download is saved under: the server's, else the
    last part of the address."""
    if disposition:
        msg = email.message.Message()
        msg["Content-Disposition"] = disposition
        name = msg.get_filename()
        if name:
            return Path(name).name
    return Path(urlparse(url).path).name or "download"


def free_path(folder: Path, name: str) -> Path:
    """`folder/name`, or `name 2`, `name 3`… when that's taken."""
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists():
        target = folder / f"{stem} {n}{suffix}"
        n += 1
    return target
