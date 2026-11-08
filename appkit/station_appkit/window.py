"""Opening an app in a window of its own.

Chrome, Edge, Brave and Chromium can show a page as a plain app window
(`--app=URL`: no tabs or address bar). The apps use one when they find
one, with its own browser profile in the app's data folder, so the window
is separate from your browsing: closing the browser leaves the app open,
and closing the app leaves your tabs alone. Without one of those browsers
it opens in a normal browser tab.

Installed as an app from that window, it's a proper app in the browser's
eyes: its own name, icon and menu in the Dock, Alt-Tab and the taskbar.
From then on it's opened that way (`--app-id`).

The downloaded Mac apps need none of that: they have a window of their
own (macwindow.py), so opening the app there means opening the .app.
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

from station_appkit import App

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
        # Snap browsers can't use a profile in a hidden folder like ~/.station-studio.
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


def installed_app_id(app: App, url: str, profile: Path, home: Path | None = None,
                     platform: str | None = None) -> str | None:
    """The id of the app installed from its own window for `url`, or None.

    Found from the shortcut the browser makes for it, which names its id,
    address, profile and name, so another app installed in that profile
    (browsers add some of their own) is never mistaken for it. Only a
    shortcut for this address counts: the installed app always opens its
    own (start) page, so a link deeper in, or the app on another port,
    opens in a plain app window instead."""
    home = home or Path.home()
    platform = platform or sys.platform
    profile_text = str(profile)
    if platform == "darwin":
        # ~/Applications/Chrome Apps.localized/Station Studio.app (or Edge Apps…)
        for info_path in home.glob("Applications/*.localized/*.app/Contents/Info.plist"):
            try:
                with info_path.open("rb") as fh:
                    info = plistlib.load(fh)
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            app_id = info.get("CrAppModeShortcutID")
            shortcut = str(info.get("CrAppModeShortcutURL", ""))
            data_dir = str(info.get("CrAppModeUserDataDir", ""))
            if app_id and same_page(shortcut, url) and profile_text in data_dir:
                return str(app_id)
        return None
    if platform.startswith("linux"):
        # The shortcut doesn't say its address, so only the usual one.
        if not same_page(url, app.default_url):
            return None
        for entry in (home / ".local" / "share" / "applications").glob("*.desktop"):
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found = re.search(r"--app-id=([a-p]{32})", text)
            if found and f"--user-data-dir={profile_text}" in text.replace('"', "") \
                    and re.search(rf"^Name={re.escape(app.name)}$", text, re.M):
                return found.group(1)
    return None


def same_page(a: str, b: str) -> bool:
    """Whether two addresses are the same page (a trailing / aside)."""
    x, y = urlparse(a), urlparse(b)
    try:
        return (x.scheme, x.hostname, x.port or 80, x.path.rstrip("/")) == \
            (y.scheme, y.hostname, y.port or 80, y.path.rstrip("/"))
    except ValueError:  # a port that isn't one
        return False


def window_command(app: App, exe: str, url: str, profile: Path, app_id: str | None = None) -> list[str]:
    # The installed app always opens its own start page, so it's only
    # used for that page (see installed_app_id).
    target = f"--app-id={app_id}" if app_id and urlparse(url).path in ("", "/") else f"--app={url}"
    width, height = app.window_size
    return [
        exe, target, f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", f"--window-size={width},{height}",
        *app.browser_args,
    ]


def mac_app_bundle(platform: str | None = None, executable: str | None = None,
                   frozen: bool | None = None) -> Path | None:
    """The downloaded .app this runs from, whose own window shows the app
    (see macwindow.py); None anywhere else."""
    platform = platform or sys.platform
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if platform != "darwin" or not frozen:
        return None
    exe = Path(executable or sys.executable)
    if len(exe.parents) < 3 or exe.parent.name != "MacOS" or exe.parents[2].suffix != ".app":
        return None
    return exe.parents[2]


def open_ui(app: App, url: str, home: Path, own_window: bool = True, exe: str | None = None, run=None,
            tab=webbrowser.open, bundle: Path | None = None, find=find_browser) -> str:
    """Show the app at `url`: in its own window when possible, else a
    browser tab. `home` is its data folder (the window's browser profile
    is kept there). Returns "window" or "tab"."""
    bundle = bundle if bundle is not None else (mac_app_bundle() if own_window else None)
    if own_window and bundle:
        # Opens the app, or brings its window back if it's running.
        try:
            proc = (run or spawn)(["/usr/bin/open", str(bundle)])
            if not failed(proc, wait=10):
                return "window"
        except OSError:
            pass
    exe = exe if exe is not None else (find() if own_window else None)
    if own_window and exe:
        profile = home / "window"
        cmd = window_command(app, exe, url, profile, installed_app_id(app, url, profile))
        try:
            proc = (run or spawn)(cmd)
            if not failed(proc):
                return "window"
        except OSError:
            pass
    tab(url)
    return "tab"


def failed(proc, wait: float = 1.5) -> bool:
    """Whether the browser gave up straight away (a bad profile folder, say).
    One already running hands the window over and exits cleanly."""
    if proc is None or not hasattr(proc, "wait"):
        return False
    try:
        proc.wait(wait)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode != 0


def spawn(cmd: list[str]) -> subprocess.Popen:
    """Start a browser (or `open`) on its own."""
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    # The downloaded app's own libraries mustn't leak into the browser.
    env = {k: v for k, v in os.environ.items() if k not in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")}
    return subprocess.Popen(cmd, env=env, **kwargs)


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
