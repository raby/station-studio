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


def test_a_browser_that_quits_at_once_falls_back_to_a_tab(tmp_path, monkeypatch):
    import subprocess
    import sys
    tabs = []
    monkeypatch.setattr(window.webbrowser, "open", tabs.append)
    failing = lambda cmd: subprocess.Popen([sys.executable, "-c", "raise SystemExit(1)"])  # noqa: E731
    assert window.open_ui("http://x/", tmp_path, exe="/b", run=failing) == "tab"
    handed_over = lambda cmd: subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: E731
    assert window.open_ui("http://y/", tmp_path, exe="/b", run=handed_over) == "window"
    assert tabs == ["http://x/"]


def test_snap_browsers_are_skipped():
    which = {"chromium": "/snap/bin/chromium", "brave": "/usr/bin/brave"}.get
    assert window.find_browser("linux", {}, which=which) == "/usr/bin/brave"


def test_an_installed_app_opens_as_that_app(tmp_path):
    import plistlib
    profile = tmp_path / "home" / ".station-player" / "window"
    home = tmp_path / "home"
    assert window.installed_app_id(profile, home, "darwin") is None
    shims = home / "Applications" / "Chrome Apps.localized"
    # Apps the browser put in that profile itself, or another profile's
    # Station Player, are never taken for it.
    for name, url, data in (("Google Chat", "https://chat.google.com/", str(profile)),
                            ("Station Player", window.DEFAULT_URL, "/elsewhere"),
                            ("Station Player 2", window.DEFAULT_URL, str(profile))):
        contents = shims / f"{name}.app" / "Contents"
        contents.mkdir(parents=True)
        with (contents / "Info.plist").open("wb") as fh:
            plistlib.dump({"CrAppModeShortcutID": ("x" if "Chat" in name else "y" if data == "/elsewhere" else "ours") * 1,
                           "CrAppModeShortcutURL": url, "CrAppModeUserDataDir": data}, fh)
    app_id = window.installed_app_id(profile, home, "darwin")
    assert app_id == "ours"
    cmd = window.window_command("/b", window.DEFAULT_URL, profile, app_id)
    assert "--app-id=ours" in cmd and not any(a.startswith("--app=") for a in cmd)
    # On another port the installed app (which opens the usual one) isn't used.
    assert "--app=http://127.0.0.1:9000/" in window.window_command("/b", "http://127.0.0.1:9000/", profile, app_id)


def test_installed_app_on_linux(tmp_path):
    profile = tmp_path / ".station-player" / "window"
    apps = tmp_path / ".local/share/applications"
    apps.mkdir(parents=True)
    app_id = "ndclgahegldohknijikipmikgfefalhe"
    (apps / "chrome-other-Default.desktop").write_text(
        f"[Desktop Entry]\nName=Gmail\nExec=chrome --user-data-dir={profile} --app-id={'a' * 32}\n")
    assert window.installed_app_id(profile, tmp_path, "linux") is None
    (apps / f"chrome-{app_id}-Default.desktop").write_text(
        f"[Desktop Entry]\nName=Station Player\nExec=/opt/chrome --user-data-dir={profile} --profile-directory=Default --app-id={app_id}\n")
    assert window.installed_app_id(profile, tmp_path, "linux") == app_id


def test_the_mac_app_bundle_is_found_only_in_the_mac_app():
    exe = "/Applications/Station Player.app/Contents/MacOS/station-player-server"
    assert window.mac_app_bundle("darwin", exe, True) == Path("/Applications/Station Player.app")
    assert window.mac_app_bundle("darwin", exe, False) is None  # pipx
    assert window.mac_app_bundle("win32", exe, True) is None
    assert window.mac_app_bundle("darwin", "/usr/local/bin/python3", True) is None


def test_in_the_mac_app_its_own_window_opens(tmp_path):
    ran = []
    app = Path("/Applications/Station Player.app")
    assert window.open_ui("http://127.0.0.1:8765/", tmp_path, exe="/chrome", run=ran.append, app=app) == "window"
    assert ran == [["/usr/bin/open", str(app)]]


def test_download_names_and_free_paths(tmp_path):
    assert window.download_name('attachment; filename="play log.csv"', "http://x/api/log.csv") == "play log.csv"
    assert window.download_name("attachment; filename=../../evil.sh", "http://x/a") == "evil.sh"
    assert window.download_name(None, "http://x/api/playlists/abc.m3u?x=1") == "abc.m3u"
    assert window.download_name(None, "http://x/") == "download"
    (tmp_path / "a.csv").write_text("")
    (tmp_path / "a 2.csv").write_text("")
    assert window.free_path(tmp_path, "a.csv") == tmp_path / "a 3.csv"
    assert window.free_path(tmp_path, "b.csv") == tmp_path / "b.csv"
