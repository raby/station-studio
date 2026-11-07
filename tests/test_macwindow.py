"""Station Studio's Mac window (station_studio/macwindow.py), with PyObjC
stood in for: its menus and where they take the page. What it draws is
checked on a Mac by studio-installers.yml."""
import importlib

from station_appkit.testing import fake_pyobjc


def test_menus_take_the_page_to_studios_own_addresses():
    with fake_pyobjc():
        mac = importlib.import_module("station_studio.macwindow")
        win = mac.StudioAppWindow.alloc().initWithURL_options_("http://127.0.0.1:8001/", {})
        ran = []
        win.js = lambda code, done=None: ran.append(code.split("stationApp.")[-1])
        win.buildMenus()
        assert [title for title, _ in win.appMenus()] == ["Go"] and len(win.dockItems()) == 2
        for action in (win.goStations_, win.goPools_, win.goNewStation_, win.openSettings_, win.openUpdates_,
                       win.openHelp_):
            action(None)
        assert ran == ['go("/")', 'go("/sources")', 'go("/stations/new")', 'go("/settings")', 'go("/settings")',
                       'go("/help")']
        assert win.APP.name == "Station Studio" and win.STATUS == "api/app"
