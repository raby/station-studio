"""The app window (station_appkit/window.py)."""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from station_appkit import App, window


def test_finds_a_chromium_browser_on_each_system():
    mac = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    assert window.find_browser("darwin", env={"HOME": "/Users/rob"}, exists=lambda p: p == mac) == mac
    assert window.find_browser("darwin", env={"HOME": "/Users/rob"}, exists=lambda p: False) is None
    edge = os.path.join(r"C:\Program Files (x86)", r"Microsoft\Edge\Application\msedge.exe")
    env = {"ProgramFiles(x86)": r"C:\Program Files (x86)"}
    assert window.find_browser("win32", env=env, exists=lambda p: p == edge) == edge
    assert window.find_browser("win32", env=env, exists=lambda p: False) is None
    assert window.find_browser("linux", which=lambda n: "/usr/bin/chromium" if n == "chromium" else None) \
        == "/usr/bin/chromium"
    assert window.find_browser("linux", which=lambda n: "/snap/bin/chromium" if n == "chromium" else None) is None


def test_browser_names():
    assert window.browser_name(None) is None
    assert window.browser_name(r"C:\x\msedge.exe") == "Microsoft Edge"
    assert window.browser_name("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") == "Google Chrome"
    assert window.browser_name("/usr/bin/brave-browser") == "Brave"
    assert window.browser_name("/opt/other") == "your browser"


def test_opens_an_app_window_with_its_own_profile(app, tmp_path):
    ran, tabs = [], []
    how = window.open_ui(app, app.default_url, tmp_path, exe="/x/chrome", run=ran.append, tab=tabs.append)
    assert how == "window" and not tabs
    [cmd] = ran
    assert cmd[:3] == ["/x/chrome", f"--app={app.default_url}", f"--user-data-dir={tmp_path / 'window'}"]
    assert "--window-size=1280,860" in cmd


def test_each_app_sizes_its_window_and_adds_its_flags(tmp_path):
    player = App(name="P", slug="P", tag_prefix="p-v", version="1", port=1, window_size=(1200, 800),
                 browser_args=("--autoplay-policy=no-user-gesture-required",))
    cmd = window.window_command(player, "/b", "http://u/", tmp_path)
    assert cmd[-2:] == ["--window-size=1200,800", "--autoplay-policy=no-user-gesture-required"]


def test_falls_back_to_a_tab(app, tmp_path):
    tabs = []
    assert window.open_ui(app, "http://u/", tmp_path, exe="", tab=tabs.append, bundle=None, find=lambda: None) == "tab"
    assert window.open_ui(app, "http://u/", tmp_path, own_window=False, exe="/x/chrome", tab=tabs.append) == "tab"

    def broken(cmd):
        raise OSError("gone")

    assert window.open_ui(app, "http://u/", tmp_path, exe="/x/chrome", run=broken, tab=tabs.append) == "tab"
    gave_up = subprocess.Popen(["false"])
    assert window.open_ui(app, "http://u/", tmp_path, exe="/x/chrome", run=lambda c: gave_up, tab=tabs.append) == "tab"
    assert tabs == ["http://u/"] * 4


def _shortcut(folder: Path, name: str, app_id: str, url: str, data: str) -> None:
    contents = folder / f"{name}.app" / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump({"CrAppModeShortcutID": app_id, "CrAppModeShortcutURL": url, "CrAppModeUserDataDir": data}, fh)


def test_installed_app_on_a_mac(app, tmp_path):
    home = tmp_path
    profile = home / ".station-test" / "window"
    url = app.default_url
    assert window.installed_app_id(app, url, profile, home, "darwin") is None
    shims = home / "Applications" / "Chrome Apps.localized"
    # Apps the browser put in that profile itself, or another profile's
    # copy of the app, are never taken for it.
    _shortcut(shims, "Google Chat", "chat", "https://chat.google.com/", str(profile))
    _shortcut(shims, "Other copy", "other", url, "/elsewhere")
    _shortcut(shims, "Station Test", "ours", url, str(profile))
    (shims / "Broken.app" / "Contents").mkdir(parents=True)
    (shims / "Broken.app" / "Contents" / "Info.plist").write_text("not a plist")
    app_id = window.installed_app_id(app, url, profile, home, "darwin")
    assert app_id == "ours"
    assert window.installed_app_id(app, url.rstrip("/"), profile, home, "darwin") == "ours"
    cmd = window.window_command(app, "/b", url, profile, app_id)
    assert "--app-id=ours" in cmd and not any(a.startswith("--app=") for a in cmd)
    # On another port, or a page deeper in, the installed app (which opens
    # its own start page) isn't used.
    assert window.installed_app_id(app, "http://127.0.0.1:9000/", profile, home, "darwin") is None
    assert window.installed_app_id(app, url + "settings", profile, home, "darwin") is None
    assert f"--app={url}settings" in window.window_command(app, "/b", url + "settings", profile, app_id)


def test_installed_app_on_another_port_on_a_mac(app, tmp_path):
    profile = tmp_path / "window"
    _shortcut(tmp_path / "Applications" / "Edge Apps.localized", "Station Test", "moved",
              "http://127.0.0.1:9000/", str(profile))
    assert window.installed_app_id(app, "http://127.0.0.1:9000/", profile, tmp_path, "darwin") == "moved"
    assert window.installed_app_id(app, app.default_url, profile, tmp_path, "darwin") is None


def test_installed_app_on_linux(app, tmp_path):
    profile = tmp_path / ".station-test" / "window"
    apps = tmp_path / ".local/share/applications"
    apps.mkdir(parents=True)
    app_id = "ndclgahegldohknijikipmikgfefalhe"
    url = app.default_url
    (apps / "chrome-other-Default.desktop").write_text(
        f"[Desktop Entry]\nName=Gmail\nExec=chrome --user-data-dir={profile} --app-id={'a' * 32}\n")
    assert window.installed_app_id(app, url, profile, tmp_path, "linux") is None
    (apps / f"chrome-{app_id}-Default.desktop").write_text(
        f"[Desktop Entry]\nName=Station Test\nExec=/opt/chrome --user-data-dir={profile} "
        f"--profile-directory=Default --app-id={app_id}\n")
    assert window.installed_app_id(app, url, profile, tmp_path, "linux") == app_id
    # The shortcut doesn't say its address, so only the usual one uses it.
    assert window.installed_app_id(app, "http://127.0.0.1:9000/", profile, tmp_path, "linux") is None
    assert window.installed_app_id(app, url, profile, tmp_path, "win32") is None


def test_same_page():
    assert window.same_page("http://127.0.0.1:8001/", "http://127.0.0.1:8001")
    assert not window.same_page("http://127.0.0.1:8001/", "http://127.0.0.1:80/")
    assert not window.same_page("http://127.0.0.1:8001/", "http://127.0.0.1:8001/x")
    assert not window.same_page("http://127.0.0.1:bad/", "http://127.0.0.1:8001/")


def test_the_mac_app_bundle_is_found_only_in_the_mac_app():
    exe = "/Applications/Station Test.app/Contents/MacOS/station-test-server"
    assert window.mac_app_bundle("darwin", exe, True) == Path("/Applications/Station Test.app")
    assert window.mac_app_bundle("darwin", exe, False) is None  # pipx
    assert window.mac_app_bundle("win32", exe, True) is None
    assert window.mac_app_bundle("darwin", "/usr/local/bin/python3", True) is None


def test_in_the_mac_app_its_own_window_opens(app, tmp_path):
    ran = []
    bundle = Path("/Applications/Station Test.app")
    assert window.open_ui(app, app.default_url, tmp_path, exe="/chrome", run=ran.append, bundle=bundle) == "window"
    assert ran == [["/usr/bin/open", str(bundle)]]


def test_the_apps_libraries_stay_out_of_the_browser(monkeypatch):
    seen = {}

    class Popen:
        def __init__(self, cmd, env=None, **kwargs):
            seen.update(cmd=cmd, env=env)

    monkeypatch.setattr(window.subprocess, "Popen", Popen)
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/Applications/X.app/Contents/Frameworks")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEI123")
    window.spawn(["/b"])
    assert seen["cmd"] == ["/b"]
    assert "DYLD_LIBRARY_PATH" not in seen["env"] and "LD_LIBRARY_PATH" not in seen["env"]
    assert seen["env"].get("PATH") == os.environ.get("PATH")


def test_download_names_and_free_paths(tmp_path):
    assert window.download_name('attachment; filename="Week 1.xml"', "http://x/api/export") == "Week 1.xml"
    assert window.download_name("attachment; filename=../../evil.sh", "http://x/a") == "evil.sh"
    assert window.download_name(None, "http://x/api/traces/abc.json?x=1") == "abc.json"
    assert window.download_name(None, "http://x/") == "download"
    (tmp_path / "a.xml").write_text("")
    (tmp_path / "a 2.xml").write_text("")
    assert window.free_path(tmp_path, "a.xml") == tmp_path / "a 3.xml"
    assert window.free_path(tmp_path, "b.xml") == tmp_path / "b.xml"
