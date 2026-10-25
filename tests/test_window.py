"""Station Studio in a window of its own (station_studio/window.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from station_studio import window


def test_finds_a_chromium_browser_on_each_system():
    mac = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    assert window.find_browser("darwin", env={"HOME": "/Users/rob"}, exists=lambda p: p == mac) == mac
    assert window.find_browser("darwin", env={"HOME": "/Users/rob"}, exists=lambda p: False) is None
    import os

    edge = os.path.join(r"C:\Program Files (x86)", r"Microsoft\Edge\Application\msedge.exe")
    env = {"ProgramFiles(x86)": r"C:\Program Files (x86)"}
    assert window.find_browser("win32", env=env, exists=lambda p: p == edge) == edge
    assert window.find_browser("linux", which=lambda n: "/usr/bin/chromium" if n == "chromium" else None) \
        == "/usr/bin/chromium"
    assert window.find_browser("linux", which=lambda n: "/snap/bin/chromium" if n == "chromium" else None) is None


def test_opens_an_app_window_with_its_own_profile(tmp_path):
    ran, tabs = [], []
    how = window.open_ui("http://127.0.0.1:8001/", tmp_path, exe="/x/chrome", run=ran.append, tab=tabs.append)
    assert how == "window" and not tabs
    [cmd] = ran
    assert cmd[:3] == ["/x/chrome", "--app=http://127.0.0.1:8001/", f"--user-data-dir={tmp_path / 'window'}"]


def test_falls_back_to_a_tab(tmp_path):
    tabs = []
    assert window.open_ui("http://u/", tmp_path, exe="", tab=tabs.append) == "tab"
    assert window.open_ui("http://u/", tmp_path, own_window=False, exe="/x/chrome", tab=tabs.append) == "tab"

    def broken(cmd):
        raise OSError("gone")

    assert window.open_ui("http://u/", tmp_path, exe="/x/chrome", run=broken, tab=tabs.append) == "tab"
    gave_up = subprocess.Popen(["false"])
    assert window.open_ui("http://u/", tmp_path, exe="/x/chrome", run=lambda c: gave_up, tab=tabs.append) == "tab"
    assert tabs == ["http://u/"] * 4


def test_desktop_uses_the_window(monkeypatch, tmp_path):
    from station_studio import desktop

    seen = []
    monkeypatch.setattr("station_studio.window.open_ui", lambda url, home, own_window, tab: seen.append(own_window) or "tab")
    monkeypatch.delenv("STATION_STUDIO_TAB", raising=False)
    desktop.open_browser("http://127.0.0.1:8001/")
    monkeypatch.setenv("STATION_STUDIO_TAB", "1")
    desktop.open_browser("http://127.0.0.1:8001/")
    assert seen == [True, False]
