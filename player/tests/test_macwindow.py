"""Station Player's Mac window (station_player/macwindow.py), with PyObjC
stood in for: its menus, the mini player's messages, Open With and what
Quit asks. What it draws is checked on a Mac by player-installers.yml."""
import importlib

from station_appkit.testing import fake_pyobjc


def make(mac, **opts):
    win = mac.PlayerAppWindow.alloc().initWithURL_options_("http://127.0.0.1:8765/", opts)
    win.ran = []
    win.js = lambda code, done=None: win.ran.append(code)
    return win


def test_menus_and_controls():
    with fake_pyobjc():
        mac = importlib.import_module("station_player.macwindow")
        win = make(mac)
        win.buildMenus()
        assert [title for title, _ in win.appMenus()] == ["Controls"] and len(win.viewItems()) == 2
        win.playPause_(None)
        win.playNext_(None)
        win.openSettings_(None)
        assert [c.split("stationApp.")[-1] for c in win.ran] == ["command('toggle')", "command('next')",
                                                                  "show('settings')"]
        assert win.STATUS == "api/ping" and win.AUTOPLAY and win.autosaveName() == "Station Player window"


def test_mini_player_messages_and_open_with(monkeypatch):
    with fake_pyobjc():
        mac = importlib.import_module("station_player.macwindow")
        opened = []
        win = make(mac, open_files=opened.append)
        minis = []
        monkeypatch.setattr(win, "setMini", minis.append)
        win.message("mini")
        win.message("full")
        win.message("other")
        win.pageLoaded()
        assert minis == [True, False, False]

        class URL:
            def __init__(self, path, file=True):
                self._p, self._f = path, file

            def isFileURL(self):
                return self._f

            def path(self):
                return self._p

        win.openURLs([URL("/Music/a.mp3"), URL("x", file=False)])
        import time
        for _ in range(100):
            if opened:
                break
            time.sleep(0.01)
        assert opened == [["/Music/a.mp3"]]


def test_quit_asks_only_while_recording():
    with fake_pyobjc() as fake:
        mac = importlib.import_module("station_player.macwindow")
        answers = []
        win = make(mac)
        callbacks = []
        win.js = lambda code, done=None: callbacks.append(done)
        win.confirm = lambda message, detail: answers.append(detail) or False
        got = []
        win.beforeQuit(lambda go=True: got.append(go))
        callbacks[0](0)  # nothing recording: quits without asking
        callbacks[0](0)  # (answered once only, the page or the 3 s wait)
        assert got == [True] and answers == []
        win.beforeQuit(lambda go=True: got.append(go))
        callbacks[1](2)  # recording: asked, and Cancel keeps it open
        assert answers == ["Music and radio will stop, and so will recording."] and got[-1] is False
        fake.AppHelper.callLater.assert_called()  # a page that doesn't answer is no reason to stay open
