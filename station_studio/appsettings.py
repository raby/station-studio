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
import shutil
import sys
from pathlib import Path

from station_appkit.login import Login as _Login

from station_studio import selfupdate
from station_studio.appinfo import APP

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


class Login(_Login):
    """Start Station Studio when you log in, without opening a window
    (station_appkit.login, as Station Player and Station Server do)."""

    def __init__(self, home: Path | None = None, platform: str | None = None,
                 command: list[str] | None = None, run_key=None) -> None:
        platform = platform or sys.platform
        command = command if command is not None else launch_command(platform=platform)
        super().__init__(APP, command, agent_id=LOGIN_ID, autostart_name="station-studio.desktop",
                         platform=platform, home=home, registry=run_key)
