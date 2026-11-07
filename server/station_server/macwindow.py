"""Station Server's own window on the Mac (the downloaded app), as
Station Player and Station Studio have: station_appkit.macwindow with
Station Server's menus.

Closing the window leaves Station Server running, and the Dock icon
brings it back, with the stations still on air; Quit (⌘Q) takes them off
air and stops it. Station Server itself runs in the app's helper process
(see packaging/station-server.spec), which is what starts at login; this
window only shows it. Only the Mac app imports this module, since it
needs PyObjC.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

import Foundation
import objc
from PyObjCTools import AppHelper
from station_appkit import macwindow as _mac
from station_appkit.macwindow import CMD, item

from station_server.appinfo import APP

logger = logging.getLogger(__name__)


def on_air(url: str) -> int:
    """How many stations are on air (0 when Station Server doesn't answer)."""
    state = _mac.get_json(url + "api/state") or {}
    return sum(1 for st in state.get("stations") or [] if st.get("state") == "on")


class ServerAppWindow(_mac.StationAppWindow):
    APP = APP
    STATUS = "api/state"

    @objc.python_method
    def launched(self):
        # Off the main thread: finding the router can take seconds, and the
        # window shouldn't wait for it.
        threading.Thread(target=self.askAboutNetwork, name="local-network", daemon=True).start()

    @objc.python_method
    def askAboutNetwork(self):
        try:
            with objc.autorelease_pool():
                ask_for_local_network()
        except Exception as exc:  # noqa: BLE001 - only a nudge for macOS
            logger.info("Could not ask about the local network: %s", exc)

    # The page's views (app.js `go`), as its sidebar has them.
    @objc.python_method
    def appMenus(self) -> list:
        return [("Go", [
            item("Stations", "goStations:", "1", CMD, self),
            item("Play log", "goLog:", "2", CMD, self),
            item("Add a station", "goNewStation:", "n", CMD, self),
        ])]

    @objc.python_method
    def dockItems(self) -> list:
        return [item("Add a station", "goNewStation:", "", None, self),
                item("Play log", "goLog:", "", None, self)]

    def goStations_(self, sender):
        self.goTo("station")

    def goLog_(self, sender):
        self.goTo("log")

    def goNewStation_(self, sender):
        self.goTo("new")

    @objc.python_method
    def beforeQuit(self, proceed):
        def ask(live):
            if live and not self.confirm(
                    f"Quit {APP.name}?", "The station goes off air." if live == 1 else f"All {live} stations go off air."):
                proceed(False)
                return
            proceed()

        # Asked off the main thread, so a slow answer doesn't freeze the menus.
        threading.Thread(target=lambda: AppHelper.callAfter(ask, on_air(self.url)), daemon=True).start()


def ask_for_local_network() -> None:
    """Get macOS to ask whether Station Server may find devices on the local
    network. It asks only for some kinds of connection (not the plain ones
    the helper makes), so the window makes one of those, to the router, and
    ignores the answer: once allowed, the helper can reach the router too."""
    from station_server import upnp
    routers = {a.rsplit(".", 1)[0] + ".1" for a in upnp.lan_addresses()}
    try:
        out = subprocess.run(["/sbin/route", "-n", "get", "default"], capture_output=True, text=True,
                             timeout=5).stdout
        routers |= {line.split(":", 1)[1].strip() for line in out.splitlines()
                    if line.strip().startswith("gateway:") and upnp._private(line.split(":", 1)[1].strip())}
    except (OSError, subprocess.SubprocessError):
        pass
    session = Foundation.NSURLSession.sharedSession()
    for router in routers:
        url = Foundation.NSURL.URLWithString_(f"http://{router}/")
        req = Foundation.NSMutableURLRequest.requestWithURL_cachePolicy_timeoutInterval_(url, 1, 5.0)
        req.setHTTPMethod_("HEAD")
        session.dataTaskWithRequest_completionHandler_(req, lambda data, resp, err: None).resume()


def run(url: str, quit_server, bundle: Path | None = None) -> int:
    """Show Station Server at `url` in the app's own window until it
    quits. `quit_server()` stops Station Server."""
    return _mac.run(ServerAppWindow, url, quit_server, bundle=bundle)
