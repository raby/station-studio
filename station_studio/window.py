"""Opening Station Studio in a window of its own, like Station Player:
a Chrome, Edge or Brave app window with its own profile in the data
folder, else a browser tab; the downloaded Mac app opens its own window
(macwindow.py). The work is station_appkit.window, shared with Station
Player and Station Server."""
from __future__ import annotations

import webbrowser
from pathlib import Path

from station_appkit import window as _window
from station_appkit.window import browser_name, find_browser, mac_app_bundle  # noqa: F401

from station_studio.appinfo import APP

DEFAULT_URL = APP.default_url


def open_ui(url: str, home: Path, own_window: bool = True, tab=webbrowser.open, **kwargs) -> str:
    """Show Station Studio: in its own window when possible, else a
    browser tab. Returns "window" or "tab"."""
    return _window.open_ui(APP, url, home, own_window=own_window, tab=tab, **kwargs)
