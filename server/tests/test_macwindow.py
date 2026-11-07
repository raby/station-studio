"""Station Server's Mac window (station_server/macwindow.py), with PyObjC
stood in for: its menus and what Quit asks while stations are on air.
What it draws is checked on a Mac by server-installers.yml."""
import importlib

from station_appkit.testing import fake_pyobjc


def test_menus_take_the_page_to_its_views():
    with fake_pyobjc():
        mac = importlib.import_module("station_server.macwindow")
        win = mac.ServerAppWindow.alloc().initWithURL_options_("http://127.0.0.1:8770/", {})
        ran = []
        win.js = lambda code, done=None: ran.append(code.split("stationApp.")[-1])
        win.buildMenus()
        for action in (win.goStations_, win.goLog_, win.goNewStation_, win.openUpdates_):
            action(None)
        assert ran == ['go("station")', 'go("log")', 'go("new")', 'go("settings", "updates")']
        assert win.STATUS == "api/state"


def test_quit_asks_while_stations_are_on_air(monkeypatch):
    with fake_pyobjc() as fake:
        mac = importlib.import_module("station_server.macwindow")
        monkeypatch.setattr(mac._mac, "get_json", lambda url: {"stations": [{"state": "on"}, {"state": "on"},
                                                                            {"state": "off"}]})
        assert mac.on_air("http://127.0.0.1:8770/") == 2
        monkeypatch.setattr(mac._mac, "get_json", lambda url: None)
        assert mac.on_air("http://127.0.0.1:8770/") == 0
        win = mac.ServerAppWindow.alloc().initWithURL_options_("http://127.0.0.1:8770/", {})
        asked, got = [], []
        win.confirm = lambda message, detail: asked.append(detail) or False
        fake.AppHelper.callAfter.side_effect = lambda f, *a: f(*a)
        monkeypatch.setattr(mac, "on_air", lambda url: 2)
        win.beforeQuit(lambda go=True: got.append(go))
        import time
        for _ in range(100):
            if got:
                break
            time.sleep(0.01)
        assert asked == ["All 2 stations go off air."] and got == [False]
