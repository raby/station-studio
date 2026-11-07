"""Station Player as the shared desktop code (station_appkit) knows it:
its name, releases, port and Mac helper. updates.py, selfupdate.py,
window.py and macwindow.py hand this to station_appkit."""
from __future__ import annotations

from station_appkit import App

from station_player import __version__

APP = App(
    name="Station Player",
    slug="StationPlayer",
    tag_prefix="player-v",  # .github/workflows/publish-player.yml
    version=__version__,
    port=8765,
    mac_helper="station-player-server",  # keep in step with packaging/station-player.spec
    env_prefix="STATION_PLAYER",
    window_size=(1200, 800),
    # It's a music player: let it start sound without a click first.
    browser_args=("--autoplay-policy=no-user-gesture-required",),
)
