"""Opening the player in a window of its own.

Chrome, Edge, Brave and Chromium can show a page as a plain app window
(`--app=URL`: no tabs or address bar). Station Player uses one when it
finds one, with its own browser profile under the player's home folder,
so the window is separate from your browsing: closing the browser leaves
the player open, and closing the player leaves your tabs alone. Without
one of those browsers it opens in a normal browser tab as before.

Installed as an app from that window (Settings › App), it's a proper app
in the browser's eyes: its own name and icon in the Dock, Alt-Tab and the
taskbar. From then on it's opened that way (`--app-id`).

The downloaded Mac app needs none of that: it has a window of its own
(macwindow.py), so opening the player there means opening the app.
"""
from __future__ import annotations

import email.message
import os
import plistlib
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

_MAC_APPS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Vivaldi")
_LINUX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                "microsoft-edge", "microsoft-edge-stable", "brave-browser", "brave")


def find_browser(platform: str | None = None, env=None, exists=None, which=None) -> str | None:
    """A Chromium-family browser that can open app windows, or None."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    exists = exists or (lambda p: Path(p).is_file())
    which = which or shutil.which
    if platform == "darwin":
        for root in ("/Applications", str(Path(env.get("HOME", "~")).expanduser() / "Applications")):
            for app in _MAC_APPS:
                exe = f"{root}/{app}.app/Contents/MacOS/{app}"
                if exists(exe):
                    return exe
        return None
    if platform.startswith("win"):
        bases = [env.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        rel = (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
               r"BraveSoftware\Brave-Browser\Application\brave.exe")
        for r in rel:  # Edge first: every Windows 10/11 has it
            for base in bases:
                if base and exists(os.path.join(base, r)):
                    return os.path.join(base, r)
        return None
    for name in _LINUX_NAMES:
        found = which(name)
        # Snap browsers can't use a profile in the hidden ~/.station-player.
        if found and not os.path.realpath(found).startswith("/snap/") and not found.startswith("/snap/"):
            return found
    return None


def browser_name(exe: str | None) -> str | None:
    if not exe:
        return None
    low = exe.lower()
    for key, name in (("edge", "Microsoft Edge"), ("brave", "Brave"), ("vivaldi", "Vivaldi"),
                      ("chromium", "Chromium"), ("chrome", "Google Chrome")):
        if key in low:
            return name
    return "your browser"


DEFAULT_URL = "http://127.0.0.1:8765/"


def installed_app_id(profile: Path, home: Path | None = None, platform: str | None = None) -> str | None:
    """The id of Station Player installed as an app from its own window,
    or None. Found from the shortcut the browser makes for it (which names
    its id, address and profile), so another app installed in that
    profile (browsers add some of their own) is never mistaken for it."""
    home = home or Path.home()
    platform = platform or sys.platform
    profile_text = str(profile)
    if platform == "darwin":
        # ~/Applications/Chrome Apps.localized/Station Player.app (or Edge Apps…)
        for info_path in home.glob("Applications/*.localized/*.app/Contents/Info.plist"):
            try:
                with info_path.open("rb") as fh:
                    info = plistlib.load(fh)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            app_id = info.get("CrAppModeShortcutID")
            url = str(info.get("CrAppModeShortcutURL", ""))
            data_dir = str(info.get("CrAppModeUserDataDir", ""))
            if app_id and url.startswith(DEFAULT_URL) and profile_text in data_dir:
                return str(app_id)
        return None
    if platform.startswith("linux"):
        for entry in (home / ".local" / "share" / "applications").glob("*.desktop"):
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = re.search(r"--app-id=([a-p]{32})", text)
            if found and f"--user-data-dir={profile_text}" in text.replace('"', "") \
                    and re.search(r"^Name=Station Player$", text, re.M):
                return found.group(1)
    return None


def window_command(exe: str, url: str, profile: Path, app_id: str | None = None) -> list[str]:
    # The installed app always opens the usual address, so it's only
    # used when the player is on it.
    target = f"--app-id={app_id}" if app_id and url == DEFAULT_URL else f"--app={url}"
    return [
        exe, target, f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1200,800",
        # It's a music player: let it start sound without a click first.
        "--autoplay-policy=no-user-gesture-required",
    ]


def mac_app_bundle(platform: str | None = None, executable: str | None = None,
                   frozen: bool | None = None) -> Path | None:
    """The downloaded Station Player.app this runs from, whose own window
    shows the player (see macwindow.py); None anywhere else."""
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
    """Show the player: in its own window when possible, else a browser
    tab. Returns "window" or "tab"."""
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
        cmd = window_command(exe, url, profile, installed_app_id(profile))
        try:
            proc = (run or _spawn)(cmd)
            if not _failed(proc):
                return "window"
        except OSError:
            pass
    webbrowser.open(url)
    return "tab"


def _failed(proc, wait: float = 1.5) -> bool:
    """Whether the browser gave up straight away (a bad profile folder, say).
    One already running hands the window over and exits cleanly."""
    if proc is None or not hasattr(proc, "wait"):
        return False
    try:
        proc.wait(wait)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode != 0


def _spawn(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


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
