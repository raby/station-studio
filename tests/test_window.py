"""Station Studio in a window of its own (station_studio/window.py)."""
from __future__ import annotations

import plistlib
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


def test_installed_app_on_a_mac(tmp_path):
    home = tmp_path
    profile = home / ".station-studio" / "window"
    assert window.installed_app_id(profile, home, "darwin") is None
    shims = home / "Applications" / "Chrome Apps.localized"
    # Apps the browser put in that profile itself, or another profile's
    # Station Studio, are never taken for it.
    for name, app_id, url, data in (("Google Chat", "chat", "https://chat.google.com/", str(profile)),
                                    ("Other Studio", "other", window.DEFAULT_URL, "/elsewhere"),
                                    ("Station Studio", "ours", window.DEFAULT_URL, str(profile))):
        contents = shims / f"{name}.app" / "Contents"
        contents.mkdir(parents=True)
        with (contents / "Info.plist").open("wb") as fh:
            plistlib.dump({"CrAppModeShortcutID": app_id, "CrAppModeShortcutURL": url,
                           "CrAppModeUserDataDir": data}, fh)
    (shims / "Broken.app" / "Contents").mkdir(parents=True)
    (shims / "Broken.app" / "Contents" / "Info.plist").write_text("not a plist")
    app_id = window.installed_app_id(profile, home, "darwin")
    assert app_id == "ours"
    cmd = window.window_command("/b", window.DEFAULT_URL, profile, app_id)
    assert "--app-id=ours" in cmd and not any(a.startswith("--app=") for a in cmd)
    # On another port the installed app (which opens the usual one) isn't used.
    assert "--app=http://127.0.0.1:9000/" in window.window_command("/b", "http://127.0.0.1:9000/", profile, app_id)


def test_installed_app_on_linux(tmp_path):
    profile = tmp_path / ".station-studio" / "window"
    apps = tmp_path / ".local/share/applications"
    apps.mkdir(parents=True)
    app_id = "ndclgahegldohknijikipmikgfefalhe"
    (apps / "chrome-other-Default.desktop").write_text(
        f"[Desktop Entry]\nName=Gmail\nExec=chrome --user-data-dir={profile} --app-id={'a' * 32}\n")
    assert window.installed_app_id(profile, tmp_path, "linux") is None
    (apps / f"chrome-{app_id}-Default.desktop").write_text(
        f"[Desktop Entry]\nName=Station Studio\nExec=/opt/chrome --user-data-dir={profile} "
        f"--profile-directory=Default --app-id={app_id}\n")
    assert window.installed_app_id(profile, tmp_path, "linux") == app_id
    assert window.installed_app_id(profile, tmp_path, "win32") is None
