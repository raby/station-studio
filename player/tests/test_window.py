from pathlib import Path

from station_player import window


def test_finds_chrome_on_a_mac():
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert window.find_browser("darwin", {"HOME": "/Users/rob"}, exists=lambda p: p == chrome) == chrome


def test_finds_a_browser_in_the_users_own_applications_folder():
    brave = "/Users/rob/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
    assert window.find_browser("darwin", {"HOME": "/Users/rob"}, exists=lambda p: p == brave) == brave


def test_mac_with_only_safari_has_no_window_browser():
    assert window.find_browser("darwin", {"HOME": "/Users/rob"}, exists=lambda p: False) is None


def test_prefers_edge_on_windows():
    env = {"ProgramFiles(x86)": r"C:\Program Files (x86)", "ProgramFiles": r"C:\Program Files",
           "LOCALAPPDATA": r"C:\Users\rob\AppData\Local"}
    found = window.find_browser("win32", env, exists=lambda p: "Edge" in p or "Chrome" in p)
    assert found.endswith("msedge.exe")


def test_linux_uses_the_path():
    found = window.find_browser("linux", {}, which=lambda n: "/usr/bin/chromium" if n == "chromium" else None)
    assert found == "/usr/bin/chromium"


def test_browser_names():
    assert window.browser_name("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") == "Google Chrome"
    assert window.browser_name(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") == "Microsoft Edge"
    assert window.browser_name("/usr/bin/chromium") == "Chromium"
    assert window.browser_name(None) is None


def test_opens_an_app_window_with_its_own_profile(tmp_path):
    ran = []
    how = window.open_ui("http://127.0.0.1:8765/", tmp_path, exe="/usr/bin/chromium", run=ran.append)
    assert how == "window"
    cmd = ran[0]
    assert cmd[0] == "/usr/bin/chromium"
    assert "--app=http://127.0.0.1:8765/" in cmd
    assert f"--user-data-dir={tmp_path / 'window'}" in cmd


def test_falls_back_to_a_tab(tmp_path, monkeypatch):
    tabs = []
    monkeypatch.setattr(window.webbrowser, "open", tabs.append)

    def broken(cmd):
        raise OSError("gone")

    assert window.open_ui("http://x/", tmp_path, exe="/nope", run=broken) == "tab"
    assert window.open_ui("http://y/", tmp_path, own_window=False, exe="/usr/bin/chromium",
                          run=lambda c: tabs.append("window!")) == "tab"
    monkeypatch.setattr(window, "find_browser", lambda: None)
    assert window.open_ui("http://z/", Path(tmp_path)) == "tab"
    assert tabs == ["http://x/", "http://y/", "http://z/"]
