"""Opening Station Studio in a window of its own, like Station Player.

Chrome, Edge, Brave and Chromium can show a page as a plain app window
(`--app=URL`: no tabs or address bar). Station Studio uses one when it
finds one, with its own browser profile in the data folder, so the
window is separate from your browsing: closing the browser leaves Station
Studio open, and closing Station Studio leaves your tabs alone. Without
one of those browsers it opens in a normal browser tab.
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
        # Snap browsers can't use a profile in the hidden ~/.station-studio.
        if found and not os.path.realpath(found).startswith("/snap/") and not found.startswith("/snap/"):
            return found
    return None


def window_command(exe: str, url: str, profile: Path) -> list[str]:
    return [
        exe, f"--app={url}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1280,860",
    ]


def open_ui(url: str, home: Path, own_window: bool = True, exe: str | None = None, run=None,
            tab=webbrowser.open) -> str:
    """Show Station Studio: in its own window when possible, else a
    browser tab. Returns "window" or "tab"."""
    exe = exe if exe is not None else (find_browser() if own_window else None)
    if own_window and exe:
        cmd = window_command(exe, url, home / "window")
        try:
            proc = (run or _spawn)(cmd)
            if not _failed(proc):
                return "window"
        except OSError:
            pass
    tab(url)
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
    # The downloaded app's own libraries mustn't leak into the browser.
    env = {k: v for k, v in os.environ.items() if k not in ("DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")}
    return subprocess.Popen(cmd, env=env, **kwargs)
