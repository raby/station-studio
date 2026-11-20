"""The Mac window's Python side (station_appkit/macwindow.py), with
PyObjC stood in for (station_appkit.testing). What it draws is checked on
a Mac, by each app's installers workflow."""
import importlib

import pytest

from station_appkit import App
from station_appkit.testing import fake_pyobjc, page_call

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
    assert [page_call(code) for code in win.ran] == ['go("settings")', 'go("settings", "updates")', 'go("help")']
    assert page_call("1 + 1") == "1 + 1"


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


# ---- what the window shows, and what it saves ------------------------------------


class URL:
    def __init__(self, text):
        from urllib.parse import urlparse
        self.text, self.parts = text, urlparse(text)

    def scheme(self):
        return self.parts.scheme

    def host(self):
        return self.parts.hostname

    def port(self):
        return self.parts.port

    def absoluteString(self):
        return self.text


class Reply:
    """An NSHTTPURLResponse, as much as the window asks of one."""

    def __init__(self, url, headers=None):
        self.url, self.headers = URL(url), {k.lower(): v for k, v in (headers or {}).items()}

    def URL(self):
        return self.url

    def valueForHTTPHeaderField_(self, name):
        return self.headers.get(name.lower())


class NavigationResponse:
    def __init__(self, reply, can_show=True, main=True):
        self.reply, self.can_show, self.main = reply, can_show, main

    def response(self):
        return self.reply

    def canShowMIMEType(self):
        return self.can_show

    def isForMainFrame(self):
        return self.main


def decide(win, response):
    answers = []
    win.webView_decidePolicyForNavigationResponse_decisionHandler_(None, response, answers.append)
    return answers


def test_attachments_and_unshowable_files_are_saved_not_shown(mac):
    win = window(mac)
    saved = []
    win.download = saved.append
    export = "http://127.0.0.1:8123/api/playlists/7/export"
    xml = {"Content-Type": "application/xml", "Content-Disposition": 'attachment; filename="020.xml"'}
    # An exported playlist: WebKit could show XML, but it's handed over to
    # save, so it goes to Downloads instead of taking the app's place.
    assert decide(win, NavigationResponse(Reply(export, xml))) == [mac.module._RESPONSE_CANCEL]
    assert saved == [export]
    # As before: a type WebKit can't show is saved too.
    assert decide(win, NavigationResponse(Reply("http://127.0.0.1:8123/log.zip"), can_show=False)) \
        == [mac.module._RESPONSE_CANCEL]
    # The app's pages, and anything in a frame, are shown.
    saved.clear()
    assert decide(win, NavigationResponse(Reply("http://127.0.0.1:8123/", {"Content-Type": "text/html"}))) \
        == [mac.module._RESPONSE_ALLOW]
    assert decide(win, NavigationResponse(Reply(export, xml), main=False)) == [mac.module._RESPONSE_ALLOW]
    assert decide(win, NavigationResponse(Reply(export, {"Content-Disposition": "inline"}))) \
        == [mac.module._RESPONSE_ALLOW]
    # Only the app's own server's files are fetched to save.
    assert decide(win, NavigationResponse(Reply("http://127.0.0.1:9999/x.xml", xml))) \
        == [mac.module._RESPONSE_ALLOW]
    assert saved == []


def test_attachment_and_header(mac):
    is_attachment, header = mac.module.is_attachment, mac.module.header
    assert is_attachment('attachment; filename="020.xml"') and is_attachment("  Attachment")
    assert not is_attachment("inline") and not is_attachment("") and not is_attachment(None)
    assert header(Reply("http://x/", {"Content-Disposition": "attachment"}), "content-disposition") == "attachment"
    assert header(Reply("http://x/"), "Content-Disposition") == ""
    assert header(object(), "Content-Disposition") == ""  # not an HTTP response


def answering(win, result):
    """The page answers each call with `result` (True: done; None: a page
    without the bridge yet; False: not a page at all, a file)."""
    win.ran = []
    win.js = lambda code, done=None: (win.ran.append(code), done and done(result))
    win.loaded = []
    win.home = lambda: win.loaded.append(win.url)


def test_a_menu_on_a_page_that_isnt_the_apps_brings_the_app_back(mac):
    win = window(mac)
    answering(win, False)  # the window is showing a file, say
    win.openSettings_(None)
    assert win.loaded == ["http://127.0.0.1:8123/"] and win.pending[0] == 'go("settings")'
    # Once the app's page has loaded, the call is made: once, with no
    # second trip home if the page still doesn't answer.
    win.webView_didFinishNavigation_(None, None)
    assert win.pending is None
    _, (delay, tell, call, bring_back), _ = mac.AppHelper.callLater.mock_calls[-1]
    assert (delay, call, bring_back) == (1.0, 'go("settings")', False)
    win.loaded.clear()
    tell(call, bring_back)
    assert win.loaded == []


def test_a_menu_while_the_page_loads_or_once_it_has_does_only_what_it_says(mac):
    for result in (True, None):
        win = window(mac)
        answering(win, result)
        win.openHelp_(None)
        assert win.loaded == [] and win.pending is None


def test_a_menu_whose_page_took_too_long_to_come_back_is_dropped(mac, monkeypatch):
    # The app's page couldn't load (the app restarting, say): the next page
    # to load much later doesn't get a Play/Pause chosen back then.
    win = window(mac)
    answering(win, False)
    clock = [1000.0]
    monkeypatch.setattr(mac.module.time, "monotonic", lambda: clock[0])
    win.openHelp_(None)
    assert win.pending == ('go("help")', 1000.0)
    clock[0] += mac.module.PENDING_FOR + 1
    calls = len(mac.AppHelper.callLater.mock_calls)
    win.webView_didFinishNavigation_(None, None)
    assert win.pending is None and len(mac.AppHelper.callLater.mock_calls) == calls
