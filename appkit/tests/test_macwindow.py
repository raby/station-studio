"""The Mac window's Python side (station_appkit/macwindow.py), with
PyObjC stood in for (station_appkit.testing). What it draws is checked on
a Mac, by each app's installers workflow."""
import importlib

import pytest

from station_appkit import App
from station_appkit.testing import fake_pyobjc

APP = App(name="Station Test", slug="StationTest", tag_prefix="test-v", version="1.2.0", port=8123,
          mac_helper="station-test-server", env_prefix="STATION_TEST")


@pytest.fixture
def mac():
    with fake_pyobjc() as fake:
        mod = importlib.import_module("station_appkit.macwindow")

        class TestAppWindow(mod.StationAppWindow):
            APP = APP

        fake.module, fake.Window = mod, TestAppWindow
        yield fake


def window(mac, **opts):
    win = mac.Window.alloc().initWithURL_options_("http://127.0.0.1:8123/", {"quit_server": lambda: None, **opts})
    win.ran = []
    win.js = lambda code, done=None: win.ran.append(code)
    return win


def test_the_page_is_told_where_to_go(mac):
    win = window(mac)
    win.openSettings_(None)
    win.openUpdates_(None)
    win.openHelp_(None)
    assert win.ran == ['window.stationApp && window.stationApp.go("settings")',
                       'window.stationApp && window.stationApp.go("settings", "updates")',
                       'window.stationApp && window.stationApp.go("help")']


def test_menus_and_dock_build(mac):
    win = window(mac)
    win.buildMenus()
    assert win.appMenus() == [] and win.viewItems() == [] and win.dockItems() == []
    win.applicationDockMenu_(None)
    mac.AppKit.NSApplication.sharedApplication().setMainMenu_.assert_called()


def test_the_window_has_the_apps_size_user_agent_and_title(mac):
    win = window(mac)
    win.buildWindow()
    mac.Foundation.NSMakeRect.assert_any_call(0, 0, 1280, 860)
    config = mac.WebKit.WKWebViewConfiguration.alloc().init()
    config.setApplicationNameForUserAgent_.assert_called_with("StationTestApp/1.2.0")
    config.setMediaTypesRequiringUserActionForPlayback_.assert_not_called()  # only for a music player
    assert win.autosaveName() == "Station Test window"


def test_only_its_own_address_stays_in_the_window(mac):
    win = window(mac)

    class URL:
        def __init__(self, scheme, host="", port=None):
            self._s, self._h, self._p = scheme, host, port

        def scheme(self):
            return self._s

        def host(self):
            return self._h

        def port(self):
            return self._p

    assert win.ours(None) and win.ours(URL("about")) and win.ours(URL("blob"))
    assert win.ours(URL("http", "127.0.0.1", 8123))
    assert not win.ours(URL("http", "127.0.0.1", 8124)) and not win.ours(URL("https", "github.com"))


def test_quit_on_the_page_quits_the_window(mac):
    win = window(mac)
    win.userContentController_didReceiveScriptMessage_(None, type("M", (), {"body": lambda self: "quit"})())
    assert win.stopped
    mac.AppKit.NSApplication.sharedApplication().terminate_.assert_called()


def test_quit_asks_first_then_stops_the_app(mac):
    stopped = []
    win = window(mac, quit_server=lambda: stopped.append(1))
    win.leaving = True
    assert win.applicationShouldTerminate_(None) == mac.module._TERMINATE_NOW
    win.leaving = False
    application = mac.AppKit.NSApplication.sharedApplication()
    assert win.applicationShouldTerminate_(application) == mac.module._TERMINATE_LATER
    # The question comes after the answer "later" has gone back.
    func, proceed = mac.AppHelper.callAfter.call_args.args
    assert func == win.beforeQuit
    proceed(False)
    application.replyToApplicationShouldTerminate_.assert_called_with(False)
    win.stopServer()
    assert stopped == [1] and win.stopped


def test_a_new_version_answering_reopens_the_window(mac, monkeypatch):
    win = window(mac)
    answers = iter(["1.1.0", "1.1.0", "1.3.0"])
    monkeypatch.setattr(mac.module.time, "sleep", lambda s: None)
    monkeypatch.setattr(win, "serverVersion", lambda: next(answers))
    win.watch()
    mac.AppHelper.callAfter.assert_called_with(win.restart)


def test_the_app_gone_for_good_closes_the_window(mac, monkeypatch):
    win = window(mac)
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(mac.module.time, "sleep", lambda s: None)
    monkeypatch.setattr(mac.module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(win, "serverVersion", lambda: None)
    win.watch()
    assert win.stopped


def test_its_server_version_comes_from_its_status(mac, monkeypatch):
    win = window(mac)
    asked = []
    monkeypatch.setattr(mac.module, "get_json", lambda url: asked.append(url) or {"version": "1.2.0"})
    assert win.serverVersion() == "1.2.0" and asked == ["http://127.0.0.1:8123/api/app"]
    monkeypatch.setattr(mac.module, "get_json", lambda url: None)
    assert win.serverVersion() is None
