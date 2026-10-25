"""Station Studio's own window on the Mac (the downloaded app), as
Station Player has.

The app shows Station Studio in a window of its own, drawn by WebKit (the
engine Safari uses), with its own Dock icon and menu bar, so no Chrome is
needed. Closing the window leaves Station Studio running, and the Dock
icon brings it back; Quit (⌘Q) stops it.

Station Studio itself still runs in the app's helper process (see
packaging/station-studio.spec), which is what starts at login; this
window only shows it. Only the Mac app imports this module, since it
needs PyObjC.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import AppKit
import Foundation
import objc
import WebKit
from PyObjCTools import AppHelper

from station_studio import __version__
from station_studio.window import download_name, free_path

NAME = "Station Studio"
BRIDGE = "stationApp"  # the page's window.webkit.messageHandlers.stationApp
# How long Station Studio may be gone before the window gives up and quits
# (an update restarts it, which takes a while on a slow Mac).
GONE_FOR = 120

logger = logging.getLogger(__name__)
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# WebKit's enum values (named in PyObjC only on newer versions).
_ACTION_CANCEL, _ACTION_ALLOW = 0, 1
_RESPONSE_CANCEL, _RESPONSE_ALLOW = 0, 1
_LINK_ACTIVATED = 0  # WKNavigationTypeLinkActivated
_REPLY_SUCCESS = 0  # NSApplicationDelegateReplySuccess
_TERMINATE_CANCEL, _TERMINATE_NOW, _TERMINATE_LATER = 0, 1, 2


def server_version(url: str) -> str | None:
    """The version of Station Studio answering at `url`, or None."""
    try:
        with _local.open(url + "api/app", timeout=3) as resp:
            return str(json.load(resp)["version"])
    except Exception:
        return None


SELF_TEST_RESULT = """JSON.stringify({
  native: Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.stationApp),
  bridge: typeof window.stationApp === "object", title: document.title,
  sidebar: Boolean(document.querySelector("nav, aside, .sidebar")), agent: navigator.userAgent,
  serviceWorker: Boolean(navigator.serviceWorker && navigator.serviceWorker.controller),
})"""


def _app():
    return AppKit.NSApplication.sharedApplication()


def _item(title: str, action: str | None, key: str = "", mods: int | None = None, target=None):
    item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    if mods is not None:
        item.setKeyEquivalentModifierMask_(mods)
    if target is not None:
        item.setTarget_(target)
    return item


def _submenu(bar, title: str, items) -> AppKit.NSMenu:
    menu = AppKit.NSMenu.alloc().initWithTitle_(title)
    for item in items:
        menu.addItem_(item if item is not None else AppKit.NSMenuItem.separatorItem())
    holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    holder.setSubmenu_(menu)
    bar.addItem_(holder)
    return menu


class AppDelegate(AppKit.NSObject):
    """The app, its window and the page in it, all in one."""

    def initWithURL_options_(self, url, options):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self.url = url
        self.opts = options  # quit_server, bundle
        self.window = None
        self.webview = None
        self.leaving = False  # quitting without stopping Station Studio (a restart)
        self.stopped = False  # Station Studio has been stopped already
        self.version = None
        self.gone_since = None
        return self

    # ---- starting up ----------------------------------------------------------

    def applicationDidFinishLaunching_(self, note):
        self.buildMenus()
        self.buildWindow()
        self.show()
        threading.Thread(target=self.watch, name="watch-studio", daemon=True).start()

    @objc.python_method
    def buildWindow(self):
        mask = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        frame = Foundation.NSMakeRect(0, 0, 1280, 860)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, mask, AppKit.NSBackingStoreBuffered, False)
        win.setTitle_(NAME)
        win.setMinSize_(Foundation.NSMakeSize(420, 480))
        win.setReleasedWhenClosed_(False)  # closing only hides it (see windowShouldClose_)
        win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorFullScreenPrimary)
        win.center()
        # Where it was left last time, and how big.
        win.setFrameAutosaveName_("Station Studio window")
        win.setDelegate_(self)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        # Lets the page tell it's in the app (an install button, say, has no place here).
        config.setApplicationNameForUserAgent_(f"StationStudioApp/{__version__}")
        config.userContentController().addScriptMessageHandler_name_(self, BRIDGE)
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(win.contentView().bounds(), config)
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        view.setNavigationDelegate_(self)
        view.setUIDelegate_(self)
        if os.environ.get("STATION_STUDIO_INSPECT") and view.respondsToSelector_("setInspectable:"):
            view.setInspectable_(True)  # Safari › Develop menu
        view.addObserver_forKeyPath_options_context_(self, "title", 0, None)
        win.setContentView_(view)
        self.window, self.webview = win, view
        view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(self.url)))

    @objc.python_method
    def buildMenus(self):
        cmd = AppKit.NSEventModifierFlagCommand
        opt = AppKit.NSEventModifierFlagOption
        shift = AppKit.NSEventModifierFlagShift
        ctrl = AppKit.NSEventModifierFlagControl
        bar = AppKit.NSMenu.alloc().init()
        _submenu(bar, NAME, [
            _item(f"About {NAME}", "orderFrontStandardAboutPanel:"),
            None,
            _item("Settings…", "openSettings:", ",", cmd, self),
            _item("Check for Updates…", "openSettings:", "", None, self),
            None,
            _item(f"Hide {NAME}", "hide:", "h", cmd),
            _item("Hide Others", "hideOtherApplications:", "h", cmd | opt),
            _item("Show All", "unhideAllApplications:"),
            None,
            _item(f"Quit {NAME}", "terminate:", "q", cmd),
        ])
        _submenu(bar, "File", [
            _item("Close Window", "performClose:", "w", cmd),
        ])
        # Without these, ⌘C and ⌘V do nothing in the page's text boxes.
        _submenu(bar, "Edit", [
            _item("Undo", "undo:", "z", cmd),
            _item("Redo", "redo:", "z", cmd | shift),
            None,
            _item("Cut", "cut:", "x", cmd),
            _item("Copy", "copy:", "c", cmd),
            _item("Paste", "paste:", "v", cmd),
            _item("Select All", "selectAll:", "a", cmd),
        ])
        _submenu(bar, "Go", [
            _item("Stations", "goStations:", "1", cmd, self),
            _item("Source pools", "goPools:", "2", cmd, self),
            _item("New station", "goNewStation:", "n", cmd, self),
        ])
        _submenu(bar, "View", [
            _item("Enter Full Screen", "toggleFullScreen:", "f", cmd | ctrl),
        ])
        windows = _submenu(bar, "Window", [
            _item("Minimize", "performMiniaturize:", "m", cmd),
            _item("Zoom", "performZoom:", ""),
            None,
            _item(NAME, "showWindow:", "0", cmd, self),
            None,
            _item("Bring All to Front", "arrangeInFront:"),
        ])
        helps = _submenu(bar, "Help", [
            _item(f"{NAME} Help", "openHelp:", "?", cmd, self),
        ])
        _app().setMainMenu_(bar)
        _app().setWindowsMenu_(windows)
        _app().setHelpMenu_(helps)

    # ---- the window --------------------------------------------------------------

    @objc.python_method
    def show(self):
        if self.window is None:
            return
        if self.window.isMiniaturized():
            self.window.deminiaturize_(None)
        self.window.makeKeyAndOrderFront_(None)
        _app().activateIgnoringOtherApps_(True)

    def showWindow_(self, sender):
        self.show()

    def windowShouldClose_(self, window):
        # Closing hides the window, and the Dock icon (or Window ›
        # Station Studio) brings it back.
        window.orderOut_(None)
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.show()
        return True

    def observeValueForKeyPath_ofObject_change_context_(self, path, obj, change, context):
        if path == "title" and self.window is not None:
            self.window.setTitle_(str(self.webview.title() or "") or NAME)

    # ---- the page -------------------------------------------------------------------

    @objc.python_method
    def js(self, code: str, done=None):
        """Run some of the page's script; `done` gets its result (None if
        the page couldn't)."""
        if self.webview is None:
            if done:
                done(None)
            return

        def handler(result, error):
            if done:
                done(None if error is not None else result)

        self.webview.evaluateJavaScript_completionHandler_(code, handler)

    @objc.python_method
    def tell(self, call: str):
        self.js(f"window.stationApp && window.stationApp.{call}")

    @objc.python_method
    def go(self, path: str):
        self.show()
        self.tell(f"go({json.dumps(path)})")

    def openSettings_(self, sender):
        self.go("/settings")

    def openHelp_(self, sender):
        self.go("/help")

    def goStations_(self, sender):
        self.go("/")

    def goPools_(self, sender):
        self.go("/sources")

    def goNewStation_(self, sender):
        self.go("/stations/new")

    def applicationDockMenu_(self, app):
        menu = AppKit.NSMenu.alloc().init()
        menu.addItem_(_item("New station", "goNewStation:", "", None, self))
        menu.addItem_(_item("Source pools", "goPools:", "", None, self))
        return menu

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        if body == "show":
            self.show()
        elif body == "quit":  # Quit on the page, which has stopped Station Studio
            self.stopped = True
            _app().terminate_(None)

    # ---- links from elsewhere ----------------------------------------------------------

    def application_openURLs_(self, app, urls):
        self.show()  # a station-studio: link

    # ---- what the page asks of the app ---------------------------------------------

    def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(
            self, view, message, frame, handler):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(str(message))
        alert.beginSheetModalForWindow_completionHandler_(self.window, lambda response: handler())

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
            self, view, message, frame, handler):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(str(message))
        alert.addButtonWithTitle_("OK")
        alert.addButtonWithTitle_("Cancel")
        alert.beginSheetModalForWindow_completionHandler_(
            self.window, lambda response: handler(response == AppKit.NSAlertFirstButtonReturn))

    def webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_(
            self, view, prompt, default, frame, handler):
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(str(prompt))
        alert.addButtonWithTitle_("OK")
        alert.addButtonWithTitle_("Cancel")
        field = AppKit.NSTextField.alloc().initWithFrame_(Foundation.NSMakeRect(0, 0, 300, 24))
        field.setStringValue_(str(default or ""))
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        alert.beginSheetModalForWindow_completionHandler_(
            self.window, lambda response: handler(
                str(field.stringValue()) if response == AppKit.NSAlertFirstButtonReturn else None))

    def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(
            self, view, params, frame, handler):
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(bool(params.allowsMultipleSelection()))
        panel.beginSheetModalForWindow_completionHandler_(
            self.window, lambda response: handler(
                panel.URLs() if response == AppKit.NSModalResponseOK else None))

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, view, config, action, features):
        # Links that open a new window (the release page, say) open in the browser.
        url = action.request().URL()
        if url is not None:
            AppKit.NSWorkspace.sharedWorkspace().openURL_(url)
        return None

    @objc.python_method
    def ours(self, url) -> bool:
        if url is None:
            return True
        scheme = str(url.scheme() or "")
        if scheme in ("about", "data", "blob"):
            return True
        here = urlparse(self.url)
        return scheme == here.scheme and str(url.host() or "") == here.hostname and (url.port() or 80) == (here.port or 80)

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, handler):
        url = action.request().URL()
        frame = action.targetFrame()
        main = frame is None or frame.isMainFrame()
        wants_download = action.respondsToSelector_("shouldPerformDownload") and action.shouldPerformDownload()
        if wants_download and url is not None and self.ours(url):
            # An exported playlist, say: saved to Downloads.
            handler(_ACTION_CANCEL)
            self.download(str(url.absoluteString()))
        elif main and not self.ours(url):
            # A link to a website opens in the browser, not in place of Station Studio.
            handler(_ACTION_CANCEL)
            AppKit.NSWorkspace.sharedWorkspace().openURL_(url)
        else:
            handler(_ACTION_ALLOW)

    def webView_decidePolicyForNavigationResponse_decisionHandler_(self, view, response, handler):
        if response.canShowMIMEType() or not response.isForMainFrame():
            handler(_RESPONSE_ALLOW)
        else:
            handler(_RESPONSE_CANCEL)
            self.download(str(response.response().URL().absoluteString()))

    def webView_didFinishNavigation_(self, view, navigation):
        report = os.environ.get("STATION_STUDIO_SELFTEST")
        if report and not getattr(self, "tested", False):
            self.tested = True
            AppHelper.callLater(6, self.selfTest, report)

    @objc.python_method
    def selfTest(self, report: str):
        """For the build's own check (studio-installers.yml): what the page
        sees of the app."""
        self.js(SELF_TEST_RESULT, lambda result: Path(report).write_text(str(result), encoding="utf-8"))

    def webViewWebContentProcessDidTerminate_(self, view):
        view.reload()

    @objc.python_method
    def download(self, url: str):
        def work():
            folder = Path.home() / "Downloads"
            try:
                with _local.open(url, timeout=120) as resp:
                    target = free_path(folder, download_name(resp.headers.get("Content-Disposition"), url))
                    folder.mkdir(parents=True, exist_ok=True)
                    with open(target, "wb") as out:
                        while chunk := resp.read(65536):
                            out.write(chunk)
            except Exception as exc:
                logger.warning("Could not download %s: %s", url, exc)
                return
            AppHelper.callAfter(self.reveal, target)

        threading.Thread(target=work, name="download", daemon=True).start()

    @objc.python_method
    def reveal(self, path: Path):
        AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(
            [Foundation.NSURL.fileURLWithPath_(str(path))])

    # ---- Station Studio behind the window -------------------------------------------

    @objc.python_method
    def watch(self):
        """Quit along with Station Studio; after an update, reopen as the
        new version."""
        while True:
            time.sleep(3)
            found = server_version(self.url)
            if found is None:
                self.gone_since = self.gone_since or time.monotonic()
                if time.monotonic() - self.gone_since > GONE_FOR:
                    logger.info("Station Studio has stopped, so its window closes")
                    self.stopped = True
                    AppHelper.callAfter(_app().terminate_, None)
                    return
                continue
            self.gone_since = None
            if self.version and found != self.version and found != __version__:
                logger.info("Station Studio %s is running now: reopening its window", found)
                AppHelper.callAfter(self.restart)
                return
            self.version = self.version or found

    @objc.python_method
    def restart(self):
        bundle = self.opts.get("bundle")
        if bundle:
            # Once this one has gone, so macOS opens the new app rather than this.
            subprocess.Popen(["/bin/sh", "-c", 'sleep 1; /usr/bin/open "$0"', str(bundle)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.leaving = True
        _app().terminate_(None)

    # ---- quitting ----------------------------------------------------------------------

    def applicationShouldTerminate_(self, app):
        if self.leaving or self.stopped:
            return _TERMINATE_NOW
        threading.Thread(target=self.stopStudio, daemon=True).start()
        return _TERMINATE_LATER

    @objc.python_method
    def stopStudio(self):
        try:
            self.opts["quit_server"]()
        except Exception as exc:
            logger.warning("Could not stop Station Studio: %s", exc)
        self.stopped = True
        AppHelper.callAfter(_app().replyToApplicationShouldTerminate_, True)


def run(url: str, quit_server, bundle: Path | None = None) -> int:
    """Show Station Studio at `url` in the app's own window until it
    quits. `quit_server()` stops Station Studio."""
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().initWithURL_options_(url, {"quit_server": quit_server, "bundle": bundle})
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
    return 0
