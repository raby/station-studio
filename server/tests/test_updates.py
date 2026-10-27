"""Like Station Player and Station Studio: the new-version notice, Check
for updates, Update now, and the station-server: link that starts
Station Server from its Dock window."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from station_server import __main__ as cli
from station_server import __version__, selfupdate, updates
from station_server.config import ConfigStore
from station_server.desktop import Login
from station_server.onair import ServerApp
from station_server.web import STATIC_DIR, make_server


def releases(*tags, draft=()):
    return [{"tag_name": t, "draft": t in draft, "prerelease": False} for t in tags]


def test_newest_release_reads_only_station_server_releases():
    assert updates.newest_release(releases("player-v9.0.0", "studio-v5.0.0", "server-v0.4.0", "server-v0.10.0",
                                           "server-v0.11.0", draft=("server-v0.11.0",))) == "0.10.0"
    assert updates.newest_release(releases("v2.0.0", "player-v1.0.0")) is None


def test_downloads_match_the_installer_names():
    assert selfupdate.asset_name("0.4.0", "windows") == "StationServer-0.4.0-windows-setup.exe"
    assert selfupdate.asset_name("0.4.0", "mac", machine="arm64") == "StationServer-0.4.0-mac-apple-silicon.dmg"
    assert selfupdate.asset_name("0.4.0", "mac", machine="x86_64") == "StationServer-0.4.0-mac-intel.dmg"
    assert selfupdate.RELEASE_PAGE.format(version="0.4.0").endswith("/releases/tag/server-v0.4.0")


def test_the_new_mac_app_starts_in_the_background(tmp_path):
    cmd = selfupdate.mac_relaunch_command(tmp_path / "Station Server.app", 8770)
    assert cmd[0].endswith("Contents/MacOS/station-server-helper")
    assert cmd[1:] == ["app", "--no-window", "--port", "8770"]


def make_app(tmp_path, **config):
    store = ConfigStore(tmp_path / "home")
    for k, v in config.items():
        setattr(store.config, k, v)
    return ServerApp(store, ffmpeg="ffmpeg", login=Login(platform="linux", home=tmp_path / "user"))


def test_checks_daily_unless_turned_off(tmp_path):
    asked = []
    app = make_app(tmp_path)
    assert app.check_for_updates(fetch=lambda: asked.append(1) or "99.0.0") == "99.0.0"
    assert app.check_for_updates(fetch=lambda: asked.append(1) or "99.0.1") == "99.0.0"  # cached for the day
    assert len(asked) == 1
    state = app.state()
    assert state["update"] == "99.0.0"
    assert state["release_page"].endswith("server-v99.0.0")
    assert state["check_updates"] is True

    quiet = make_app(tmp_path / "quiet", check_updates=False)
    assert quiet.check_for_updates(fetch=lambda: pytest.fail("asked GitHub")) is None
    assert quiet.check_for_updates(force=True, fetch=lambda: "99.0.0") == "99.0.0"


def test_no_notice_for_this_version_or_older(tmp_path):
    app = make_app(tmp_path)
    app.latest = __version__
    assert app.state()["update"] is None
    app.latest = "0.0.1"
    assert app.state()["update"] is None


@pytest.fixture
def call(tmp_path, monkeypatch):
    app = make_app(tmp_path)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, data=None):
        req = urllib.request.Request(
            base + path, data=json.dumps(data).encode() if data is not None else None,
            headers={"X-Station-Server": "1", "Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if "json" in resp.headers["Content-Type"] else body)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    call.app = app
    yield call
    app.shutdown()
    server.shutdown()


def test_check_for_updates_asks_now(call, monkeypatch):
    seen = {}

    def latest(cache, fetch=None, now=None, force=False, quiet=True):
        seen["force"], seen["quiet"] = force, quiet
        return "99.0.0"

    monkeypatch.setattr(updates, "latest_version", latest)
    status, state = call("/api/update/check", {})
    assert status == 200 and state["update"] == "99.0.0" and seen["force"] is True and seen["quiet"] is False


def test_a_check_that_cant_reach_github_says_so(call, monkeypatch):
    import ssl
    import urllib.error

    def offline():
        raise urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    monkeypatch.setattr(updates, "_fetch_latest", offline)
    status, body = call("/api/update/check", {})
    assert status == 502 and "Couldn't check for a new version" in body["error"]
    assert "certificate verify failed" in body["error"]


def test_turning_off_new_version_notices_is_saved(call, tmp_path):
    status, state = call("/api/settings", {"check_updates": False})
    assert status == 200 and state["check_updates"] is False
    assert json.loads((tmp_path / "home" / "config.json").read_text())["check_updates"] is False
    assert ConfigStore(tmp_path / "home").config.check_updates is False
    assert ConfigStore(tmp_path / "home").config.open_window is True


def test_update_now_only_in_the_downloaded_app(call):
    status, body = call("/api/update", {"version": "99.0.0"})
    assert status == 400 and "downloaded Mac and Windows apps" in body["error"]
    call.app.updater.kind = "mac"
    status, body = call("/api/update", {"version": __version__})
    assert status == 400 and "already up to date" in body["error"]


def test_update_now_reports_progress(call, monkeypatch):
    updater = call.app.updater
    updater.kind = "windows"
    gate = threading.Event()
    monkeypatch.setattr(updater, "_windows", lambda version: gate.wait(5))
    status, state = call("/api/update", {"version": "99.0.0"})
    assert status == 200
    assert state["updating"]["stage"] == "downloading" and state["updating"]["version"] == "99.0.0"
    assert state["can_update"] is True
    status, body = call("/api/update", {"version": "99.0.0"})
    assert status == 400 and "already updating" in body["error"]
    gate.set()


def test_a_station_server_link_starts_it_in_the_background():
    assert cli._from_link(["station-server://start"]) == ["app", "--no-window"]
    assert cli._from_link(["-psn_0_12345"]) == []
    assert cli._from_link(["play", "x.xml", "--to", "a.mp3"]) == ["play", "x.xml", "--to", "a.mp3"]


def test_the_update_watch_keeps_going_after_an_error():
    calls = []

    class App:
        def check_for_updates(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

    waits = iter([False, True])
    cli._watch_for_updates(App(), every=1, wait=lambda s: next(waits))
    assert len(calls) == 2


def test_the_dock_window_can_start_it(call):
    status, sw = call("/sw.js")
    assert status == 200 and b"/offline.html" in sw
    status, page = call("/offline.html")
    assert status == 200 and b"/offline.js" in page
    assert b"station-server://start" in (STATIC_DIR / "offline.js").read_bytes()
    manifest = json.loads((STATIC_DIR / "manifest.webmanifest").read_text())
    assert [s["url"] for s in manifest["shortcuts"]] == ["/?view=new", "/?view=log"]
    assert manifest["launch_handler"] == {"client_mode": "focus-existing"}


def test_turning_notices_off_clears_the_one_showing(call):
    call.app.latest = "99.0.0"
    assert call("/api/state")[1]["update"] == "99.0.0"
    status, state = call("/api/settings", {"check_updates": False})
    assert status == 200 and state["update"] is None


def test_start_at_login_not_from_the_disk_image(tmp_path):
    login = Login(platform="mac", home=tmp_path, python="/Volumes/Station Server/Station Server.app/Contents/MacOS/x")
    with pytest.raises(ValueError, match="Applications folder"):
        login.set(True)
    login.set(False)
    assert login.enabled() is False
