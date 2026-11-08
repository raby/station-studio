"""Station Studio's own window on the Mac (the downloaded app), as
Station Player and Station Server have: station_appkit.macwindow with
Station Studio's menus. Closing the window leaves Station Studio running,
and the Dock icon brings it back; Quit (⌘Q) stops it. Only the Mac app
imports this module, since it needs PyObjC."""
from __future__ import annotations

import json
from pathlib import Path

import objc
from station_appkit import macwindow as _mac
from station_appkit.macwindow import CMD, item

from station_studio.appinfo import APP


class StudioAppWindow(_mac.StationAppWindow):
    APP = APP
    STATUS = "api/app"

    @objc.python_method
    def goTo(self, place: str, section: str | None = None):
        # The page's own addresses ("/settings"), for window.stationApp.go.
        self.show()
        self.tell(f"go({json.dumps('/' + place)})")

    @objc.python_method
    def appMenus(self) -> list:
        return [("Go", [
            item("Stations", "goStations:", "1", CMD, self),
            item("Source pools", "goPools:", "2", CMD, self),
            item("New station", "goNewStation:", "n", CMD, self),
        ])]

    @objc.python_method
    def dockItems(self) -> list:
        return [item("New station", "goNewStation:", "", None, self),
                item("Source pools", "goPools:", "", None, self)]

    def goStations_(self, sender):
        self.show()
        self.tell('go("/")')

    def goPools_(self, sender):
        self.goTo("sources")

    def goNewStation_(self, sender):
        self.goTo("stations/new")


def run(url: str, quit_server, bundle: Path | None = None) -> int:
    """Show Station Studio at `url` in the app's own window until it
    quits. `quit_server()` stops Station Studio."""
    return _mac.run(StudioAppWindow, url, quit_server, bundle=bundle)
