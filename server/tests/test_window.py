"""The window: the Mac app's own, else a browser's."""
import sys

from station_server import __main__ as cli
from station_server.window import download_name, free_path, mac_app_bundle, open_ui

APP = "/Applications/Station Server.app"


def test_only_the_downloaded_mac_app_has_a_window_of_its_own():
    exe = f"{APP}/Contents/MacOS/Station Server"
    assert str(mac_app_bundle("darwin", exe, True)) == APP
    assert mac_app_bundle("darwin", exe, False) is None  # pip install
    assert mac_app_bundle("win32", exe, True) is None
    assert mac_app_bundle("darwin", "/usr/local/bin/python3", True) is None


def test_open_ui_opens_the_mac_app(tmp_path):
    ran = []
    how = open_ui("http://127.0.0.1:8770/", tmp_path, True, exe="", run=lambda cmd: ran.append(cmd),
                  app=tmp_path / "Station Server.app")
    assert how == "window"
    assert ran == [["/usr/bin/open", str(tmp_path / "Station Server.app")]]


def test_downloads_keep_their_name_and_never_overwrite(tmp_path):
    assert download_name('attachment; filename="Rebirth play log.csv"', "http://x/a.csv") == "Rebirth play log.csv"
    assert download_name("attachment; filename*=UTF-8''Caf%C3%A9.csv", "http://x/a.csv") == "Café.csv"
    assert download_name('attachment; filename="../../evil.csv"', "http://x/a") == "evil.csv"
    assert download_name(None, "http://x/api/stations/a/playlog.csv?from=1") == "playlog.csv"
    (tmp_path / "log.csv").write_text("")
    assert free_path(tmp_path, "log.csv").name == "log 2.csv"


def _mac_app(monkeypatch, tmp_path, open_window=True):
    """Pretend to be the downloaded Mac app (not its helper)."""
    from station_server import desktop
    from station_server.config import ConfigStore
    store = ConfigStore(tmp_path / "home")
    store.config.open_window = open_window
    store.save()
    monkeypatch.setenv("STATION_SERVER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(desktop, "app_executable", lambda: tmp_path / "station-server-helper")
    monkeypatch.setattr(cli, "_log_to_file", lambda home: None)
    monkeypatch.setattr(cli, "_hand_over", lambda port, url: 0)
    shown, tabs = [], []
    monkeypatch.setattr(cli, "_own_mac_window", lambda url: shown.append(url) or 0)
    import station_server.window as window
    monkeypatch.setattr(window, "open_ui", lambda url, home, own=True, **kw: tabs.append((url, own)) or "tab")
    return shown, tabs


def test_mac_app_is_station_servers_own_window(monkeypatch, tmp_path):
    shown, tabs = _mac_app(monkeypatch, tmp_path)
    assert cli.main(["app", "--port", "8799"]) == 0
    assert shown == ["http://127.0.0.1:8799/"] and tabs == []


def test_mac_app_with_its_window_off_opens_a_browser_tab(monkeypatch, tmp_path):
    shown, tabs = _mac_app(monkeypatch, tmp_path, open_window=False)
    assert cli.main(["app", "--port", "8799"]) == 0
    assert shown == [] and tabs == [("http://127.0.0.1:8799/", False)]


def test_mac_app_started_at_login_shows_nothing(monkeypatch, tmp_path):
    shown, tabs = _mac_app(monkeypatch, tmp_path)
    assert cli.main(["station-server://start"]) == 0
    assert shown == [] and tabs == []
