"""Report a problem: a link to a new GitHub issue with the app, its version
and the computer already filled in, and where the app's log file is.

The issue gets what helps and nothing personal: the app and version, how
it was installed, the system and Python. Not the log file's path (it holds
the user's name on most computers): that's shown in the app instead, so
the person can look at the log and attach it if they choose.
"""
from __future__ import annotations

import logging
import platform
import sys
import urllib.parse
from pathlib import Path

ISSUES_NEW = "https://github.com/raby/station-studio/issues/new"

# How each install kind reads in a report. None (pip, pipx, Docker, a
# checkout) is "installed with pip": the commonest of those.
INSTALLED = {"mac": "downloaded Mac app", "windows": "downloaded Windows app", None: "installed with pip"}


def system(platform_name: str | None = None) -> str:
    """The operating system, as people know it: "macOS 15.1 (Apple Silicon)",
    "Windows 11 (10.0.22631)", "Ubuntu 24.04.1 LTS"."""
    platform_name = platform_name or sys.platform
    machine = platform.machine()
    if platform_name == "darwin":
        chip = "Apple Silicon" if machine == "arm64" else "Intel"
        return f"macOS {platform.mac_ver()[0] or platform.release()} ({chip})"
    if platform_name == "win32":
        return f"Windows {platform.release()} ({platform.version()})"
    try:
        name = platform.freedesktop_os_release().get("PRETTY_NAME")
    except (OSError, AttributeError):
        name = None
    return f"{name or 'Linux ' + platform.release()} ({machine})"


def log_file() -> Path | None:
    """The file this app is logging to (station_appkit.applog), or None when
    it logs to the terminal it was started from."""
    for handler in logging.getLogger().handlers:
        name = getattr(handler, "baseFilename", None)
        if name:
            return Path(name)
    return None


def details(app_name: str, version: str, kind: str | None) -> dict:
    """What a report says about this copy, and what the app shows beside it."""
    log = log_file()
    about = {
        "app": app_name,
        "version": version,
        "installed": INSTALLED.get(kind, INSTALLED[None]),
        "system": system(),
        "python": platform.python_version(),
    }
    return {**about, "log_file": str(log) if log else None, "issue_url": issue_url(about)}


def issue_url(about: dict) -> str:
    """A new issue, titled with the app and with its details in the body."""
    body = "\n".join([
        "**What happened**",
        "",
        "",
        "**What I expected**",
        "",
        "",
        "**Steps to make it happen again**",
        "1. ",
        "",
        "---",
        f"- App: {about['app']} {about['version']} ({about['installed']})",
        f"- System: {about['system']}",
        f"- Python: {about['python']}",
    ])
    return ISSUES_NEW + "?" + urllib.parse.urlencode({"title": f"[{about['app']}] ", "body": body})
