"""Station Player's own window on the Mac (the downloaded app):
station_appkit.macwindow, shared with Station Studio and Station Server,
with the player's menus, its mini player and Open With.

Like Apple Music, closing the window keeps the music playing and the Dock
icon brings it back; Quit (⌘Q) stops Station Player. The player itself
runs in the app's helper process (see station-player.spec), which is what
starts at login and keeps alarms and recordings going; this window only
shows it. Only the Mac app imports this module, since it needs PyObjC.
"""
from __future__ import annotations

import threading
from pathlib import Path

import AppKit
import Foundation
import objc
from PyObjCTools import AppHelper
from station_appkit import macwindow as _mac
from station_appkit.macwindow import CMD, SHIFT, item

from station_player.appinfo import APP

AUTOSAVE = "Station Player window"  # where the window's place and size are kept
FULL_MIN = (420, 480)
# The mini player: the window shrunk to the player bar, floating on top.
# Its first size; after that, wherever it was last left.
MINI_SIZE = (380, 110)
MINI_MIN = (300, 130)  # with the title bar
MINI_KEY = "Station Player mini player"

# Plays the first track in the library (the build's check adds a folder
# with one), without a click (see selfTest).
SELF_TEST = """(async () => {
  window.__selfTest = "pending";
  try {
    let st = {};
    for (let i = 0; i < 40; i++) {
      st = await (await fetch("/api/state")).json();
      if (!st.scanning && (st.playlists || []).length) break;
      await new Promise((r) => setTimeout(r, 500));
    }
    const pl = (st.playlists || [])[0];
    if (!pl) { window.__selfTest = "no music"; return; }
    const d = await (await fetch("/api/playlists/" + encodeURIComponent(pl.id))).json();
    const a = new Audio("/audio/" + d.tracks[0].id);
    await a.play();
    window.__selfTest = "played";
  } catch (e) {
    window.__selfTest = "blocked: " + e.name;
  }
})(), 1"""


class PlayerAppWindow(_mac.StationAppWindow):
    APP = APP
    MIN_SIZE = FULL_MIN
    STATUS = "api/ping"
    AUTOPLAY = True  # alarms, and resuming play, without a click first

    def initWithURL_options_(self, url, options):
        self = objc.super(PlayerAppWindow, self).initWithURL_options_(url, options)
        if self is None:
            return None
        self.full_frame = None  # while it's the mini player, the frame to go back to
        return self

    @objc.python_method
    def autosaveName(self) -> str:
        return AUTOSAVE

    # ---- menus ---------------------------------------------------------------

    @objc.python_method
    def appMenus(self) -> list:
        return [("Controls", [
            item("Play / Pause", "playPause:", "", None, self),
            item("Next", "playNext:", "", None, self),
            item("Previous", "playPrevious:", "", None, self),
        ])]

    @objc.python_method
    def viewItems(self) -> list:
        return [item("Mini Player", "toggleMini:", "m", CMD | SHIFT, self), None]

    @objc.python_method
    def dockItems(self) -> list:
        return [item("Play / Pause", "playPause:", "", None, self),
                item("Next", "playNext:", "", None, self)]

    @objc.python_method
    def goTo(self, place: str, section: str | None = None):
        self.show()
        self.tell(f"show('{place}')")

    def playPause_(self, sender):
        self.tell("command('toggle')")

    def playNext_(self, sender):
        self.tell("command('next')")

    def playPrevious_(self, sender):
        self.tell("command('prev')")

    # ---- the mini player ------------------------------------------------------

    def toggleMini_(self, sender):
        # The page moves its bar about, then says "mini" or "full".
        self.show()
        self.tell("mini()")

    @objc.python_method
    def message(self, body):
        if body in ("mini", "full"):  # the mini player, or back from it
            self.setMini(body == "mini")

    @objc.python_method
    def setMini(self, on: bool):
        """Shrink the window to the player bar, floating over other
        windows (and on every desktop), or grow it back to where it was."""
        win = self.window
        if win is None or (self.full_frame is not None) == on:
            return
        defaults = Foundation.NSUserDefaults.standardUserDefaults()
        if not on:
            defaults.setObject_forKey_(Foundation.NSStringFromRect(win.frame()), MINI_KEY)
            win.setLevel_(AppKit.NSNormalWindowLevel)
            win.setTitleVisibility_(AppKit.NSWindowTitleVisible)
            win.setTitlebarAppearsTransparent_(False)
            win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorFullScreenPrimary)
            win.setMinSize_(Foundation.NSMakeSize(*FULL_MIN))
            win.setFrame_display_animate_(self.full_frame, True, True)
            self.full_frame = None
            win.setFrameAutosaveName_(AUTOSAVE)
            win.saveFrameUsingName_(AUTOSAVE)
            return
        if win.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
            win.toggleFullScreen_(None)  # out of full screen first, which takes a moment
            AppHelper.callLater(1.0, self.setMini, True)
            return
        self.full_frame = win.frame()
        win.setFrameAutosaveName_("")  # the mini size isn't the window's own
        win.setMinSize_(Foundation.NSMakeSize(*MINI_MIN))
        win.setLevel_(AppKit.NSFloatingWindowLevel)
        # Just the close / minimise / zoom buttons above the bar, no title.
        win.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        win.setTitlebarAppearsTransparent_(True)
        win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                   | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        saved = defaults.stringForKey_(MINI_KEY)
        frame = Foundation.NSRectFromString(saved) if saved else None
        if frame is None or not any(Foundation.NSIntersectsRect(frame, s.visibleFrame()) for s in AppKit.NSScreen.screens()):
            # Bottom right of the screen the window is on.
            screen = (win.screen() or AppKit.NSScreen.mainScreen()).visibleFrame()
            size = win.frameRectForContentRect_(Foundation.NSMakeRect(0, 0, *MINI_SIZE)).size
            frame = Foundation.NSMakeRect(screen.origin.x + screen.size.width - size.width - 20,
                                          screen.origin.y + 20, size.width, size.height)
        win.setFrame_display_animate_(frame, True, True)
        self.show()

    @objc.python_method
    def pageLoaded(self):
        # A page loaded afresh shows everything, so the window does too.
        self.setMini(False)

    # ---- files from the Finder -------------------------------------------------

    @objc.python_method
    def openURLs(self, urls):
        files = [str(u.path()) for u in urls if u.isFileURL()]
        if files:
            # Open With: the player plays them (it may take a moment for a big folder).
            threading.Thread(target=self.opts["open_files"], args=(files,), daemon=True).start()

    # ---- the build's own check ---------------------------------------------------

    @objc.python_method
    def selfTest(self, report: str):
        """What the page sees of the app, and whether it may play sound
        unasked (player-installers.yml)."""
        self.js(SELF_TEST, lambda first: AppHelper.callLater(12, self.js, _mac.SELF_TEST_RESULT, lambda result: Path(
            report).write_text(str(result), encoding="utf-8")))

    # ---- quitting ----------------------------------------------------------------

    @objc.python_method
    def beforeQuit(self, proceed):
        decided = {"done": False}

        def go(recordings):
            if decided["done"]:
                return
            decided["done"] = True
            if recordings and int(recordings) > 0 and not self.confirm(
                    f"Quit {APP.name}?", "Music and radio will stop, and so will recording."):
                proceed(False)
                return
            proceed()

        # The page saves where it was (to carry on next time) and says
        # whether anything is recording; a page that doesn't answer is no
        # reason to stay open.
        self.js("window.stationApp ? window.stationApp.beforeQuit() : 0", go)
        AppHelper.callLater(3, go, 0)


def run(url: str, quit_server, open_files, bundle: Path | None = None) -> int:
    """Show the player at `url` in the app's own window until it quits.
    `quit_server()` stops the player; `open_files(paths)` hands it files
    from Open With."""
    return _mac.run(PlayerAppWindow, url, quit_server, bundle=bundle, open_files=open_files)
