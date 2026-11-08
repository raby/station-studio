"""The downloaded Station Studio app: launcher, Quit, new-version check
and Update now (station_studio/desktop.py, updates.py, selfupdate.py)."""
from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from station_studio import __version__, appsettings, desktop, selfupdate, updates
from station_studio.appinfo import APP
from station_studio.selfupdate import Updater

NEXT = "99.0.0"


# ---------- new versions and Update now: Station Studio's own ----------
# The work is station_appkit's (appkit/tests); these check Studio's part.


def release(name, data):
    return {"assets": [{"name": name, "browser_download_url": f"{APP.download_prefix}studio-v{NEXT}/{name}",
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}]}


class Opener:
    def __init__(self, data):
        self.data = data

    def __call__(self, req, timeout=None):
        return io.BytesIO(self.data)


def test_newest_release_reads_only_studio_releases():
    releases = [
        {"tag_name": "player-v9.0.0"},  # Station Player's own releases share the repo
        {"tag_name": "v3.0.0"},  # PyPI tags carry no downloads
        {"tag_name": "studio-v1.7.0"},
        {"tag_name": "studio-v1.10.0", "draft": True},
        {"tag_name": "studio-v1.6.0"},
    ]
    assert updates.newest_release(releases) == "1.7.0"
    assert updates.is_newer("1.10.0", "1.9.0")


def test_latest_version_is_asked_once_a_day(tmp_path):
    cache = tmp_path / "update-check.json"
    asked = []
    assert updates.latest_version(cache, fetch=lambda: asked.append(1) or "1.7.0", now=1000) == "1.7.0"
    assert updates.latest_version(cache, fetch=lambda: asked.append(1) or "1.8.0", now=2000) == "1.7.0"
    assert asked == [1]


def test_studio_downloads_and_release_page():
    from station_appkit.selfupdate import asset_name

    assert APP.name == "Station Studio" and APP.port == 8001 and APP.version == __version__
    assert asset_name(APP, "1.6.0", "windows") == "StationStudio-1.6.0-windows-setup.exe"
    assert asset_name(APP, "1.6.0", "mac", "arm64") == "StationStudio-1.6.0-mac-apple-silicon.dmg"
    assert selfupdate.release_page("1.6.0") == "https://github.com/raby/station-studio/releases/tag/studio-v1.6.0"
    assert selfupdate.install_kind() is None  # tests aren't the downloaded app


def test_mac_update_restarts_the_helper(tmp_path, monkeypatch):
    app = tmp_path / "Station Studio.app"
    app.mkdir()
    name = f"StationStudio-{NEXT}-mac-apple-silicon.dmg"
    monkeypatch.setattr(selfupdate._selfupdate.platform, "machine", lambda: "arm64")
    launched, quits = [], []
    up = Updater(lambda: quits.append(1), port=8070, kind="mac", get_json=lambda u: release(name, b"dmg"),
                 opener=Opener(b"dmg"), launch=launched.append)
    monkeypatch.setattr(up, "mac_app", lambda: app)
    monkeypatch.setattr(up, "mac_unpack", lambda dmg, a, v: tmp_path / "new.app")
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting" and quits == [1]
    [[sh, script]] = launched
    text = Path(script).read_text()
    assert sh == "/bin/sh" and str(os.getpid()) in text and "new.app" in text
    assert f"Contents/MacOS/{selfupdate.MAC_HELPER}" in text and "--no-browser" in text and "8070" in text


def test_only_the_downloaded_apps_update():
    with pytest.raises(ValueError, match="Mac and Windows"):
        Updater(lambda: None, kind="none").start(NEXT)
    with pytest.raises(ValueError, match="Station Studio .* is already up to date"):
        Updater(lambda: None, kind="windows").start(__version__)


# ---------- the app's own routes ----------


def make_app(kind="mac", latest=None, port=8001, home=None, login=None):
    stops = []
    home = home or Path(tempfile.mkdtemp())
    login = login or appsettings.Login(home, "linux", command=["station-studio", "app", "--no-browser"])
    desk = desktop.DesktopState(port, lambda: stops.append(1), kind=kind,
                                cache=Path(os.devnull), fetch_latest=lambda: latest,
                                home=home, login=login, browser="Google Chrome")
    desk.latest = latest
    app = FastAPI()

    @app.get("/{spa_path:path}")
    def catch_all(spa_path: str):  # stands in for the page's catch-all
        return {"page": spa_path}

    desktop.attach_routes(app, desk)
    client = TestClient(app, base_url=f"http://127.0.0.1:{port}")
    return client, desk, stops


def test_api_app_state_wins_over_the_page_catch_all():
    client, desk, _ = make_app(latest=NEXT)
    state = client.get("/api/app").json()
    assert state["app"] is True and state["version"] == __version__
    assert state["can_update"] is True and state["latest"] == NEXT
    assert state["release_page"].endswith(f"/releases/tag/studio-v{NEXT}")
    assert state["update"] is None


def test_api_app_hides_versions_that_arent_newer():
    client, _, _ = make_app(latest=__version__)
    assert client.get("/api/app").json()["latest"] is None
    client, _, _ = make_app(kind=None, latest=NEXT)
    assert client.get("/api/app").json()["can_update"] is False


def test_quit_needs_the_header_and_this_computer():
    client, desk, stops = make_app()
    assert client.post("/api/app/quit").status_code == 403
    assert client.post("/api/app/quit", headers={desktop.API_HEADER: "1", "Host": "evil.example:8001"}).status_code == 403
    assert client.post("/api/app/quit", headers={desktop.API_HEADER: "1"}).json() == {"ok": True}
    for _ in range(50):
        if stops:
            break
        time.sleep(0.02)
    assert stops == [1]


def test_update_route(monkeypatch):
    client, desk, _ = make_app(latest=NEXT)
    started = []
    monkeypatch.setattr(desk.updater, "start", lambda v: started.append(v))
    assert client.post("/api/app/update", json={"version": NEXT}).status_code == 403
    assert client.post("/api/app/update", json={"version": NEXT}, headers={desktop.API_HEADER: "1"}).status_code == 200
    assert started == [NEXT]

    def refuse(v):
        raise ValueError("Station Studio is already updating")

    monkeypatch.setattr(desk.updater, "start", refuse)
    r = client.post("/api/app/update", json={"version": NEXT}, headers={desktop.API_HEADER: "1"})
    assert r.status_code == 400 and r.json()["detail"] == "Station Studio is already updating"


def test_plain_server_says_it_isnt_the_app():
    """`serve` / Docker answer /api/app too, so the page shows no app bar."""
    from api.main import app

    assert TestClient(app).get("/api/app").json() == {"service": "station-studio", "app": False}


def test_check_for_updates_only_in_the_downloaded_apps(tmp_path):
    asked = []
    desk = desktop.DesktopState(8001, lambda: None, kind="", cache=tmp_path / "c.json",
                                fetch_latest=lambda: asked.append(1) or NEXT)
    assert desk.check_for_updates() is None and not asked
    desk = desktop.DesktopState(8001, lambda: None, kind="windows", cache=tmp_path / "c.json",
                                fetch_latest=lambda: asked.append(1) or NEXT)
    assert desk.check_for_updates() == NEXT and asked == [1]


# ---------- the launcher ----------


def test_outdated_only_for_older_apps():
    assert desktop.outdated({"app": True, "version": "0.1.0"})
    assert not desktop.outdated({"app": True, "version": __version__})
    assert not desktop.outdated({"app": False})  # `serve` or Docker: left alone


def test_second_open_just_shows_the_running_copy(monkeypatch):
    shown = []
    monkeypatch.setattr(desktop, "running_instance", lambda base: {"service": "station-studio", "app": True,
                                                                   "version": __version__})
    monkeypatch.setattr(desktop, "open_browser", shown.append)
    monkeypatch.setattr(desktop, "serve", lambda *a: pytest.fail("must not start a second server"))
    assert desktop.main(["--port", "8123"]) == 0
    assert shown == ["http://127.0.0.1:8123/"]
    shown.clear()
    assert desktop.main(["--port", "8123", "--no-browser"]) == 0 and shown == []


def test_starts_when_nothing_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "running_instance", lambda base: None)
    monkeypatch.setattr(desktop, "port_free", lambda port: True)
    monkeypatch.setattr(desktop, "serve", lambda port, show: calls.append((port, show)) or 0)
    assert desktop.main(["--port", "8124", "--no-browser", "-psn_0_12345"]) == 0
    assert calls == [(8124, False)]


def test_station_studio_link_starts_it_without_a_window(monkeypatch):
    # The installed app's window, opened from the Dock while Station
    # Studio wasn't running, asks for station-studio://start.
    calls = []
    monkeypatch.setattr(desktop, "running_instance", lambda base: None)
    monkeypatch.setattr(desktop, "port_free", lambda port: True)
    monkeypatch.setattr(desktop, "serve", lambda port, show: calls.append((port, show)) or 0)
    assert desktop.main(["station-studio://start"]) == 0
    assert desktop.main(["--port", "8126", "STATION-STUDIO://start"]) == 0
    assert calls == [(desktop.DEFAULT_PORT, False), (8126, False)]


def test_port_taken_by_something_else(monkeypatch, capsys):
    monkeypatch.setattr(desktop, "running_instance", lambda base: None)
    monkeypatch.setattr(desktop, "port_free", lambda port: False)
    monkeypatch.setattr(desktop, "wait_for", lambda base, s: False)
    assert desktop.main(["--port", "8125", "--no-browser"]) == 1
    assert "port 8125" in capsys.readouterr().err


def test_downloaded_app_replaces_an_older_copy(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop, "_log_to_file", lambda: None)
    monkeypatch.setattr(desktop, "_mac_app_itself", lambda: False)
    monkeypatch.setattr(desktop, "running_instance", lambda base: {"service": "station-studio", "app": True,
                                                                   "version": "0.0.1"})
    quits, served = [], []
    monkeypatch.setattr(desktop, "quit_instance", lambda base: quits.append(base) or True)
    monkeypatch.setattr(desktop, "port_free", lambda port: True)
    monkeypatch.setattr(desktop, "serve", lambda port, show: served.append(port) or 0)
    assert desktop.main(["--no-browser"]) == 0
    assert quits == ["http://127.0.0.1:8001/"] and served == [8001]


def _mac_app(monkeypatch, tmp_path, running):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop, "_log_to_file", lambda: None)
    monkeypatch.setattr(desktop, "_mac_app_itself", lambda: True)
    monkeypatch.setattr(desktop, "get_home", lambda: tmp_path)
    monkeypatch.delenv("STATION_STUDIO_TAB", raising=False)
    monkeypatch.setattr(desktop, "running_instance", lambda base: running)
    monkeypatch.setattr(desktop, "serve", lambda *a: pytest.fail("the app itself must not serve"))
    windows, helpers = [], []
    monkeypatch.setattr(desktop, "_own_mac_window", lambda base: windows.append(base) or 0)
    monkeypatch.setattr(desktop, "_start_mac_helper", helpers.append)
    monkeypatch.setattr(desktop, "wait_for", lambda base, s: True)
    return windows, helpers


def test_mac_app_is_station_studios_own_window(monkeypatch, tmp_path):
    windows, helpers = _mac_app(monkeypatch, tmp_path, None)
    assert desktop.main([]) == 0
    assert helpers == [desktop.DEFAULT_PORT] and windows == ["http://127.0.0.1:8001/"]


def test_mac_app_window_shows_the_copy_already_running(monkeypatch, tmp_path):
    windows, helpers = _mac_app(monkeypatch, tmp_path, {"service": "station-studio", "app": True,
                                                        "version": __version__})
    assert desktop.main([]) == 0
    assert helpers == [] and windows == ["http://127.0.0.1:8001/"]


def test_mac_app_without_its_own_window_opens_the_browser(monkeypatch, tmp_path):
    windows, helpers = _mac_app(monkeypatch, tmp_path, {"service": "station-studio", "app": True,
                                                        "version": __version__})
    appsettings.save(tmp_path, {"own_window": False})
    shown = []
    monkeypatch.setattr(desktop, "open_browser", shown.append)
    assert desktop.main([]) == 0
    assert windows == [] and shown == ["http://127.0.0.1:8001/"]
    # A station-studio: link starts it with no window at all.
    appsettings.save(tmp_path, {"own_window": True})
    assert desktop.main(["station-studio://start"]) == 0 and windows == []


def test_window_route_opens_the_mac_apps_own_window(monkeypatch):
    client, desk, _ = make_app()
    h = {desktop.API_HEADER: "1"}
    assert client.get("/api/app").json()["mac_app"] is False
    assert client.post("/api/app/window", headers=h).status_code == 409
    opened = []
    monkeypatch.setattr(desktop, "open_browser", opened.append)
    desk.mac_app = True
    assert client.post("/api/app/window").status_code == 403
    assert client.post("/api/app/window", headers=h).json() == {"ok": True}
    for _ in range(50):
        if opened:
            break
        time.sleep(0.02)
    assert opened == ["http://127.0.0.1:8001/"]


def test_cli_app_subcommand_hands_over(monkeypatch):
    from click.testing import CliRunner

    from station_studio.cli import main

    seen = []
    monkeypatch.setattr(desktop, "main", lambda argv: seen.append(argv) or 0)
    result = CliRunner().invoke(main, ["app", "--port", "8126", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert seen == [["--port", "8126", "--no-browser"]]


@pytest.mark.slow
def test_app_serves_and_quits_from_the_page(tmp_path):
    """The real thing: `station-studio app` starts on a free port with its
    data in STATION_STUDIO_HOME, answers /api/app, and Quit stops it."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "STATION_STUDIO_HOME": str(tmp_path / "home")}
    env.pop("STATION_STUDIO_DB_URL", None)
    env.pop("STATION_STUDIO_STATIONS_DIR", None)
    proc = subprocess.Popen([sys.executable, "-m", "station_studio.desktop", "--no-browser", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}/"
    try:
        assert desktop.wait_for(base, 60), proc.stdout.read() if proc.poll() is not None else "no answer"
        state = desktop.running_instance(base)
        assert state["app"] is True and state["version"] == __version__
        assert (tmp_path / "home" / "station_studio.db").is_file()
        assert desktop.quit_instance(base)
        assert proc.wait(15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_settings_route_changes_the_app_settings(tmp_path, monkeypatch):
    client, desk, _ = make_app(home=tmp_path)
    state = client.get("/api/app").json()
    assert state["settings"] == {"own_window": True, "check_updates": True}
    assert state["login"] is False and state["browser"] == "Google Chrome"
    assert client.post("/api/app/settings", json={"login": True}).status_code == 403
    h = {desktop.API_HEADER: "1"}
    state = client.post("/api/app/settings", json={"own_window": False, "login": True}, headers=h).json()
    assert state["settings"] == {"own_window": False, "check_updates": True} and state["login"] is True
    assert (tmp_path / ".config" / "autostart" / "station-studio.desktop").exists()
    state = client.post("/api/app/settings", json={"check_updates": False, "login": False}, headers=h).json()
    assert state["settings"] == {"own_window": False, "check_updates": False} and state["login"] is False

    def broken(on):
        raise OSError("read-only")

    monkeypatch.setattr(desk.login, "set", broken)
    r = client.post("/api/app/settings", json={"login": True}, headers=h)
    assert r.status_code == 400 and "read-only" in r.json()["detail"]


def test_check_now_route_asks_github_even_with_daily_checks_off(tmp_path):
    asked = []
    client, desk, _ = make_app(home=tmp_path)
    desk._fetch_latest = lambda: asked.append(1) or NEXT
    desk.cache = tmp_path / "c.json"
    appsettings.save(tmp_path, {"check_updates": False})
    assert desk.check_for_updates() is None and not asked  # the daily look is off
    assert client.post("/api/app/check").status_code == 403
    state = client.post("/api/app/check", headers={desktop.API_HEADER: "1"}).json()
    assert state["latest"] == NEXT and asked == [1]


def test_turning_notices_off_clears_the_one_showing(tmp_path):
    client, desk, _ = make_app(home=tmp_path)
    desk.latest = NEXT
    h = {desktop.API_HEADER: "1"}
    assert client.get("/api/app").json()["latest"] == NEXT
    state = client.post("/api/app/settings", json={"check_updates": False}, headers=h).json()
    assert state["latest"] is None


def test_open_browser_follows_the_own_window_setting(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(desktop, "get_home", lambda: tmp_path)
    monkeypatch.setattr("station_studio.window.open_ui", lambda url, home, own_window, tab: seen.append(own_window) or "tab")
    monkeypatch.delenv("STATION_STUDIO_TAB", raising=False)
    desktop.open_browser("http://127.0.0.1:8001/")
    appsettings.save(tmp_path, {"own_window": False})
    desktop.open_browser("http://127.0.0.1:8001/")
    assert seen == [True, False]
