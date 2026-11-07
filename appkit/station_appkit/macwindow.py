"""An app's own window on the Mac (the downloaded apps).

The app shows its page in a window of its own, drawn by WebKit (the engine
Safari uses), with its own Dock icon and menu bar, so no Chrome is needed.
Closing the window leaves the app running, and the Dock icon brings it
back; Quit (⌘Q) stops it.

The app itself still runs in the .app's helper process (see each app's
PyInstaller spec), which is what starts at login; this window only shows
it. Each app subclasses `StationAppWindow` for its own menus and what Quit asks,
then calls `run`. Only the Mac apps import this module, since it needs
PyObjC.
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

from station_appkit import App
from station_appkit.window import download_name, free_path

BRIDGE = "stationApp"  # the page's window.webkit.messageHandlers.stationApp
# How long the app may be gone before the window gives up and quits (an
# update restarts it, which takes a while on a slow Mac).
GONE_FOR = 120

logger = logging.getLogger(__name__)
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# WebKit's enum values (named in PyObjC only on newer versions).
_ACTION_CANCEL, _ACTION_ALLOW = 0, 1
_RESPONSE_CANCEL, _RESPONSE_ALLOW = 0, 1
_REPLY_SUCCESS = 0  # NSApplicationDelegateReplySuccess
_TERMINATE_CANCEL, _TERMINATE_NOW, _TERMINATE_LATER = 0, 1, 2

# What the build's own check (each app's *-installers.yml) reads of the page.
SELF_TEST_RESULT = """JSON.stringify({
  native: Boolean(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.stationApp),
  bridge: typeof window.stationApp === "object", title: document.title,
  sidebar: Boolean(document.querySelector("nav, aside, .sidebar, #sidebar")), agent: navigator.userAgent,
  serviceWorker: Boolean(navigator.serviceWorker && navigator.serviceWorker.controller),
  autoplay: window.__selfTest,
})"""


def get_json(url: str):
    """The JSON at `url` (the app's own server), or None."""
    try:
        with _local.open(url, timeout=3) as resp:
            return json.load(resp)
    except Exception:
        return None


def app():
    return AppKit.NSApplication.sharedApplication()


def item(title: str, action: str | None, key: str = "", mods: int | None = None, target=None):
    """A menu item."""
    entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    if mods is not None:
        entry.setKeyEquivalentModifierMask_(mods)
    if target is not None:
        entry.setTarget_(target)
    return entry


def submenu(bar, title: str, items) -> AppKit.NSMenu:
    """A menu in the menu bar; None in `items` is a separator."""
    menu = AppKit.NSMenu.alloc().initWithTitle_(title)
    for entry in items:
        menu.addItem_(entry if entry is not None else AppKit.NSMenuItem.separatorItem())
    holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    holder.setSubmenu_(menu)
    bar.addItem_(holder)
    return menu


CMD = AppKit.NSEventModifierFlagCommand
OPT = AppKit.NSEventModifierFlagOption
SHIFT = AppKit.NSEventModifierFlagShift
CTRL = AppKit.NSEventModifierFlagControl


class StationAppWindow(AppKit.NSObject):
    """The app, its window and the page in it, all in one.

    A subclass sets `APP` and may override the @objc.python_method hooks
    (marked "for a subclass") and add its own menu actions."""

    APP: App = None  # for a subclass
    MIN_SIZE = (420, 480)
    STATUS = "api/app"  # what answers {"version": …} while the app runs
    AUTOPLAY = False  # sound may start without a click first (a music player)

    def initWithURL_options_(self, url, options):
        self = objc.super(StationAppWindow, self).init()
        if self is None:
            return None
        self.url = url
        self.opts = options  # quit_server, bundle, and whatever a subclass wants
        self.window = None
        self.webview = None
        self.leaving = False  # quitting without stopping the app (a restart)
        self.stopped = False  # the app has been stopped already
        self.version = None
        self.gone_since = None
        self.tested = False
        return self

    @objc.python_method
    def env(self, what: str) -> str | None:
        """STATION_STUDIO_<what>, say."""
        return os.environ.get(f"{self.APP.env_prefix}_{what}") if self.APP.env_prefix else None

    # ---- starting up ----------------------------------------------------------

    def applicationDidFinishLaunching_(self, note):
        self.buildMenus()
        self.buildWindow()
        self.show()
        threading.Thread(target=self.watch, name="watch-app", daemon=True).start()
        self.launched()

    @objc.python_method
    def launched(self):
        """For a subclass: once the window is up."""

    @objc.python_method
    def buildWindow(self):
        mask = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskMiniaturizable | AppKit.NSWindowStyleMaskResizable)
        frame = Foundation.NSMakeRect(0, 0, *self.APP.window_size)
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, mask, AppKit.NSBackingStoreBuffered, False)
        win.setTitle_(self.APP.name)
        win.setMinSize_(Foundation.NSMakeSize(*self.MIN_SIZE))
        win.setReleasedWhenClosed_(False)  # closing only hides it (see windowShouldClose_)
        win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorFullScreenPrimary)
        win.center()
        # Where it was left last time, and how big.
        win.setFrameAutosaveName_(self.autosaveName())
        win.setDelegate_(self)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        if self.AUTOPLAY:
            config.setMediaTypesRequiringUserActionForPlayback_(0)
        # Lets the page tell it's in the app (an install button, say, has no place here).
        config.setApplicationNameForUserAgent_(f"{self.APP.slug}App/{self.APP.version}")
        config.userContentController().addScriptMessageHandler_name_(self, BRIDGE)
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(win.contentView().bounds(), config)
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        view.setNavigationDelegate_(self)
        view.setUIDelegate_(self)
        if self.env("INSPECT") and view.respondsToSelector_("setInspectable:"):
            view.setInspectable_(True)  # Safari › Develop menu
        view.addObserver_forKeyPath_options_context_(self, "title", 0, None)
        win.setContentView_(view)
        self.window, self.webview = win, view
        view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(self.url)))

    @objc.python_method
    def autosaveName(self) -> str:
        return f"{self.APP.name} window"

    @objc.python_method
    def buildMenus(self):
        name = self.APP.name
        bar = AppKit.NSMenu.alloc().init()
        submenu(bar, name, [
            item(f"About {name}", "orderFrontStandardAboutPanel:"),
            None,
            item("Settings…", "openSettings:", ",", CMD, self),
            item("Check for Updates…", "openUpdates:", "", None, self),
            None,
            item(f"Hide {name}", "hide:", "h", CMD),
            item("Hide Others", "hideOtherApplications:", "h", CMD | OPT),
            item("Show All", "unhideAllApplications:"),
            None,
            item(f"Quit {name}", "terminate:", "q", CMD),
        ])
        submenu(bar, "File", [
            item("Close Window", "performClose:", "w", CMD),
        ])
        # Without these, ⌘C and ⌘V do nothing in the page's text boxes.
        submenu(bar, "Edit", [
            item("Undo", "undo:", "z", CMD),
            item("Redo", "redo:", "z", CMD | SHIFT),
            None,
            item("Cut", "cut:", "x", CMD),
            item("Copy", "copy:", "c", CMD),
            item("Paste", "paste:", "v", CMD),
            item("Select All", "selectAll:", "a", CMD),
        ])
        for title, items in self.appMenus():
            submenu(bar, title, items)
        submenu(bar, "View", [
            *self.viewItems(),
            item("Enter Full Screen", "toggleFullScreen:", "f", CMD | CTRL),
        ])
        windows = submenu(bar, "Window", [
            item("Minimize", "performMiniaturize:", "m", CMD),
            item("Zoom", "performZoom:", ""),
            None,
            item(name, "showWindow:", "0", CMD, self),
            None,
            item("Bring All to Front", "arrangeInFront:"),
        ])
        helps = submenu(bar, "Help", [
            item(f"{name} Help", "openHelp:", "?", CMD, self),
        ])
        app().setMainMenu_(bar)
        app().setWindowsMenu_(windows)
        app().setHelpMenu_(helps)

    @objc.python_method
    def appMenus(self) -> list:
        """For a subclass: [(title, items)], between Edit and View."""
        return []

    @objc.python_method
    def viewItems(self) -> list:
        """For a subclass: items at the top of View."""
        return []

    @objc.python_method
    def dockItems(self) -> list:
        """For a subclass: the Dock icon's menu."""
        return []

    def applicationDockMenu_(self, application):
        menu = AppKit.NSMenu.alloc().init()
        for entry in self.dockItems():
            menu.addItem_(entry)
        return menu

    # ---- the window --------------------------------------------------------------

    @objc.python_method
    def show(self):
        if self.window is None:
            return
        if self.window.isMiniaturized():
            self.window.deminiaturize_(None)
        self.window.makeKeyAndOrderFront_(None)
        app().activateIgnoringOtherApps_(True)

    def showWindow_(self, sender):
        self.show()

    def windowShouldClose_(self, window):
        # Closing hides the window, and the Dock icon (or Window › <the
        # app>) brings it back.
        window.orderOut_(None)
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
        self.show()
        return True

    def observeValueForKeyPath_ofObject_change_context_(self, path, obj, change, context):
        if path == "title" and self.window is not None:
            self.window.setTitle_(str(self.webview.title() or "") or self.APP.name)

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
        """Call window.stationApp.<call> in the page."""
        self.js(f"window.stationApp && window.stationApp.{call}")

    @objc.python_method
    def goTo(self, place: str, section: str | None = None):
        """For a subclass: show one of the page's views ("settings",
        "help"), and a section of it ("updates")."""
        self.show()
        self.tell(f"go({json.dumps(place)}{', ' + json.dumps(section) if section else ''})")

    def openSettings_(self, sender):
        self.goTo("settings")

    def openUpdates_(self, sender):
        self.goTo("settings", "updates")

    def openHelp_(self, sender):
        self.goTo("help")

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        if body == "show":
            self.show()
        elif body == "quit":  # Quit on the page, which has stopped the app
            self.stopped = True
            app().terminate_(None)
        else:
            self.message(body)

    @objc.python_method
    def message(self, body):
        """For a subclass: anything else the page tells the app."""

    # ---- links and files from elsewhere ------------------------------------------------

    def application_openURLs_(self, application, urls):
        self.openURLs(urls)
        self.show()  # a station-studio: link, say

    @objc.python_method
    def openURLs(self, urls):
        """For a subclass: files (Open With) or links handed to the app."""

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
            # An exported playlist or the play log, say: saved to Downloads.
            handler(_ACTION_CANCEL)
            self.download(str(url.absoluteString()))
        elif main and not self.ours(url):
            # A link to a website opens in the browser, not in place of the app.
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
        self.pageLoaded()
        report = self.env("SELFTEST")
        if report and not self.tested:
            self.tested = True
            AppHelper.callLater(6, self.selfTest, report)

    @objc.python_method
    def pageLoaded(self):
        """For a subclass: each time the page has loaded."""

    @objc.python_method
    def selfTest(self, report: str):
        """For the build's own check: what the page sees of the app."""
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

    # ---- the app behind the window -------------------------------------------

    @objc.python_method
    def serverVersion(self) -> str | None:
        """The version of the app answering at the window's address, or None."""
        state = get_json(self.url + self.STATUS)
        return str(state["version"]) if isinstance(state, dict) and "version" in state else None

    @objc.python_method
    def watch(self):
        """Quit along with the app; after an update, reopen as the new
        version."""
        while True:
            time.sleep(3)
            found = self.serverVersion()
            if found is None:
                self.gone_since = self.gone_since or time.monotonic()
                if time.monotonic() - self.gone_since > GONE_FOR:
                    logger.info("%s has stopped, so its window closes", self.APP.name)
                    self.stopped = True
                    AppHelper.callAfter(app().terminate_, None)
                    return
                continue
            self.gone_since = None
            if self.version and found != self.version and found != self.APP.version:
                logger.info("%s %s is running now: reopening its window", self.APP.name, found)
                AppHelper.callAfter(self.restart)
                return
            self.version = self.version or found

    @objc.python_method
    def restart(self):
        bundle = self.opts.get("bundle")
        if bundle:
            # Once this one has gone, as a new app (-n), so macOS opens the
            # new version rather than bringing back this one.
            subprocess.Popen(["/bin/sh", "-c", 'sleep 1; /usr/bin/open -n "$0"', str(bundle)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.leaving = True
        app().terminate_(None)

    # ---- quitting ----------------------------------------------------------------------

    def applicationShouldTerminate_(self, application):
        if self.leaving or self.stopped:
            return _TERMINATE_NOW

        def proceed(go: bool = True):
            if go:
                threading.Thread(target=self.stopServer, daemon=True).start()
            else:
                application.replyToApplicationShouldTerminate_(False)

        # After this returns, so the answer always comes later, as promised.
        AppHelper.callAfter(self.beforeQuit, proceed)
        return _TERMINATE_LATER

    @objc.python_method
    def beforeQuit(self, proceed):
        """For a subclass: ask first (stations on air, say), then
        `proceed()`, or `proceed(False)` to stay open. Runs on the main
        thread."""
        proceed()

    @objc.python_method
    def confirm(self, message: str, detail: str) -> bool:
        """Quit or Cancel, asked in a dialog."""
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(message)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("Quit")
        alert.addButtonWithTitle_("Cancel")
        return alert.runModal() == AppKit.NSAlertFirstButtonReturn

    @objc.python_method
    def stopServer(self):
        try:
            self.opts["quit_server"]()
        except Exception as exc:
            logger.warning("Could not stop %s: %s", self.APP.name, exc)
        self.stopped = True
        AppHelper.callAfter(app().replyToApplicationShouldTerminate_, True)


def run(window_class, url: str, quit_server, bundle: Path | None = None, **options) -> int:
    """Show the app at `url` in its own window until it quits.
    `quit_server()` stops the app; `options` are for the subclass."""
    application = AppKit.NSApplication.sharedApplication()
    application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    delegate = window_class.alloc().initWithURL_options_(
        url, {"quit_server": quit_server, "bundle": bundle, **options})
    application.setDelegate_(delegate)
    AppHelper.runEventLoop()
    return 0
