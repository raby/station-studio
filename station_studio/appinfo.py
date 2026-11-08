"""Station Studio as the shared desktop code (station_appkit) knows it:
its name, releases, port and Mac helper. updates.py, selfupdate.py,
window.py and macwindow.py hand this to station_appkit."""
from __future__ import annotations

from station_appkit import App

from station_studio import __version__

APP = App(
    name="Station Studio",
    slug="StationStudio",
    tag_prefix="studio-v",  # .github/workflows/publish-studio-app.yml
    version=__version__,
    port=8001,
    mac_helper="station-studio-server",  # keep in step with packaging/station-studio.spec
    env_prefix="STATION_STUDIO",
    window_size=(1280, 860),
)
