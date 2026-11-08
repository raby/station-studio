"""The player in a window of its own (station_player/window.py).

The work is station_appkit.window's (appkit/tests/test_window.py); these
check the player's part."""
import subprocess
import sys
from pathlib import Path

from station_player import window


def test_opens_an_app_window_with_its_own_profile_that_may_play_sound(tmp_path):
    ran = []
    how = window.open_ui("http://127.0.0.1:8765/", tmp_path, exe="/usr/bin/chromium", run=ran.append)
    assert how == "window"
    cmd = ran[0]
    assert cmd[0] == "/usr/bin/chromium"
    assert "--app=http://127.0.0.1:8765/" in cmd
    assert f"--user-data-dir={tmp_path / 'window'}" in cmd
    assert "--window-size=1200,800" in cmd and "--autoplay-policy=no-user-gesture-required" in cmd


def test_falls_back_to_a_tab(tmp_path, monkeypatch):
    tabs = []
    monkeypatch.setattr(window._window.webbrowser, "open", tabs.append)
    failing = lambda cmd: subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])  # noqa: E731
    assert window.open_ui("http://x/", tmp_path, exe="/b", run=failing, tab=tabs.append) == "tab"
    assert window.open_ui("http://y/", tmp_path, own_window=False, tab=tabs.append) == "tab"
    assert tabs == ["http://x/", "http://y/"]


def test_in_the_mac_app_its_own_window_opens(tmp_path):
    ran = []
    app = Path("/Applications/Station Player.app")
    assert window.open_ui("http://127.0.0.1:8765/", tmp_path, exe="/chrome", run=ran.append, bundle=app) == "window"
    assert ran == [["/usr/bin/open", str(app)]]
    assert window.DEFAULT_URL == "http://127.0.0.1:8765/"
