"""Station Studio in a window of its own (station_studio/window.py).

The work is station_appkit.window's (appkit/tests/test_window.py); these
check Station Studio's part."""
from __future__ import annotations

from pathlib import Path

from station_studio import window


def test_opens_an_app_window_with_its_own_profile(tmp_path):
    ran, tabs = [], []
    how = window.open_ui("http://127.0.0.1:8001/", tmp_path, exe="/x/chrome", run=ran.append, tab=tabs.append)
    assert how == "window" and not tabs
    [cmd] = ran
    assert cmd[:3] == ["/x/chrome", "--app=http://127.0.0.1:8001/", f"--user-data-dir={tmp_path / 'window'}"]
    assert "--window-size=1280,860" in cmd
    assert window.DEFAULT_URL == "http://127.0.0.1:8001/"


def test_falls_back_to_a_tab(tmp_path):
    tabs = []
    assert window.open_ui("http://u/", tmp_path, own_window=False, tab=tabs.append) == "tab"
    assert tabs == ["http://u/"]


def test_desktop_uses_the_window(monkeypatch, tmp_path):
    from station_studio import desktop

    seen = []
    monkeypatch.setattr("station_studio.window.open_ui", lambda url, home, own_window, tab: seen.append(own_window) or "tab")
    monkeypatch.delenv("STATION_STUDIO_TAB", raising=False)
    desktop.open_browser("http://127.0.0.1:8001/")
    monkeypatch.setenv("STATION_STUDIO_TAB", "1")
    desktop.open_browser("http://127.0.0.1:8001/")
    assert seen == [True, False]


def test_in_the_mac_app_its_own_window_opens(tmp_path):
    ran = []
    app = Path("/Applications/Station Studio.app")
    assert window.open_ui("http://127.0.0.1:8001/", tmp_path, exe="/chrome", run=ran.append, bundle=app) == "window"
    assert ran == [["/usr/bin/open", str(app)]]
