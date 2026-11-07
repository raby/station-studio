"""Opening the player in a window of its own: a Chrome, Edge or Brave app
window with its own profile under the player's home folder, else a
normal browser tab; the downloaded Mac app opens its own window
(macwindow.py). The work is station_appkit.window, shared with Station
Studio and Station Server."""
from __future__ import annotations

from pathlib import Path

from station_appkit import window as _window
from station_appkit.window import (  # noqa: F401
    browser_name, download_name, find_browser, free_path, mac_app_bundle,
)

from station_player.appinfo import APP

DEFAULT_URL = APP.default_url


def open_ui(url: str, home: Path, own_window: bool = True, **kwargs) -> str:
    """Show the player: in its own window when possible, else a browser
    tab. Returns "window" or "tab"."""
    return _window.open_ui(APP, url, home, own_window=own_window, **kwargs)
