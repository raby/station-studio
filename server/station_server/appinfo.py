"""Station Server as the shared desktop code (station_appkit) knows it:
its name, releases, port and Mac helper. updates.py, selfupdate.py,
window.py and macwindow.py hand this to station_appkit."""
from __future__ import annotations

from station_appkit import App

from station_server import __version__
from station_server.desktop import APP_NAME, MAC_HELPER

APP = App(
    name=APP_NAME,
    slug="StationServer",
    tag_prefix="server-v",  # .github/workflows/publish-server.yml
    version=__version__,
    port=8770,
    mac_helper=MAC_HELPER,
    env_prefix="STATION_SERVER",
    window_size=(1180, 820),
)
