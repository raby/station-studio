"""Opening Station Server in a window of its own, as Station Player and
Station Studio do: a Chrome, Edge or Brave app window with its own
profile under `~/.station-server`, else a browser tab; the downloaded Mac
app opens its own window (macwindow.py). The work is
station_appkit.window."""
from __future__ import annotations

from pathlib import Path

from station_appkit import window as _window
from station_appkit.window import download_name, free_path, mac_app_bundle  # noqa: F401

from station_server.appinfo import APP

APP_NAME = APP.name


def open_ui(url: str, home: Path, own_window: bool = True, **kwargs) -> str:
    """Show the page: in its own window when possible, else a browser tab.
    Returns "window" or "tab"."""
    return _window.open_ui(APP, url, home, own_window=own_window, **kwargs)
