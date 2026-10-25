"""Opening Station Server in a window of its own, the way station-player does.

Chrome, Edge or Brave show the page as a plain app window (no tabs or
address bar) with its own browser profile under `~/.station-server`.
Installed as an app from that window (Install as app, in Settings), it
gets its own name and icon in the Dock and taskbar, and is opened that way
from then on. Without one of those browsers it opens in a browser tab.
"""
from __future__ import annotations

import plistlib
import re
import sys
import webbrowser
from pathlib import Path

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


def open_ui(url: str, home: Path, own_window: bool = True, exe: str | None = None, run=None) -> str:
    """Show the page: in its own window when possible, else a browser tab.
    Returns "window" or "tab"."""
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
