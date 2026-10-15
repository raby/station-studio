"""Opening the player in a window of its own.

Chrome, Edge, Brave and Chromium can show a page as a plain app window
(`--app=URL`: no tabs or address bar). Station Player uses one when it
finds one, with its own browser profile under the player's home folder,
so the window is separate from your browsing: closing the browser leaves
the player open, and closing the player leaves your tabs alone. Without
one of those browsers it opens in a normal browser tab as before.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

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
        if found:
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


def window_command(exe: str, url: str, profile: Path) -> list[str]:
    return [
        exe, f"--app={url}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1200,800",
        # It's a music player: let it start sound without a click first.
        "--autoplay-policy=no-user-gesture-required",
    ]


def open_ui(url: str, home: Path, own_window: bool = True, exe: str | None = None, run=None) -> str:
    """Show the player: in its own window when possible, else a browser
    tab. Returns "window" or "tab"."""
    exe = exe if exe is not None else (find_browser() if own_window else None)
    if own_window and exe:
        cmd = window_command(exe, url, home / "window")
        try:
            (run or _spawn)(cmd)
            return "window"
        except OSError:
            pass
    webbrowser.open(url)
    return "tab"


def _spawn(cmd: list[str]) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
