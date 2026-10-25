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
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from station_studio import __version__, desktop, selfupdate, updates
from station_studio.selfupdate import UpdateError, Updater

NEXT = "99.0.0"


# ---------- updates: which version is newest ----------


def test_versions_compare_numerically():
    assert updates.is_newer("1.10.0", "1.9.0")
    assert updates.is_newer("1.6", "1.5.9")
    assert not updates.is_newer("1.5.0", "1.5")
    assert not updates.is_newer("2.0.0rc1", "1.0.0")  # final releases only
    assert not updates.is_newer(None, "1.0.0")


def test_newest_release_reads_only_studio_releases():
    releases = [
        {"tag_name": "player-v9.0.0"},  # Station Player's own releases share the repo
        {"tag_name": "v3.0.0"},  # PyPI tags carry no downloads
        {"tag_name": "studio-v1.7.0"},
        {"tag_name": "studio-v1.10.0", "draft": True},
        {"tag_name": "studio-v1.9.0", "prerelease": True},
        {"tag_name": "studio-v1.6.0"},
        {"tag_name": "studio-vnext"},
        "junk",
    ]
    assert updates.newest_release(releases) == "1.7.0"
    assert updates.newest_release({"message": "rate limited"}) is None


def test_latest_version_is_asked_once_a_day(tmp_path):
    cache = tmp_path / "update-check.json"
    asked = []

    def fetch():
        asked.append(1)
        return "1.7.0"

    assert updates.latest_version(cache, fetch=fetch, now=1000) == "1.7.0"
    assert updates.latest_version(cache, fetch=fetch, now=2000) == "1.7.0"
    assert len(asked) == 1
    assert updates.latest_version(cache, fetch=fetch, now=1000 + updates.CHECK_EVERY + 1) == "1.7.0"
    assert len(asked) == 2

    def offline():
        raise OSError("no network")

    # Offline: keep what was known, quietly, and try again next time
    # rather than waiting a day.
    assert updates.latest_version(cache, fetch=offline, force=True) == "1.7.0"
    later = 1000 + 2 * updates.CHECK_EVERY + 2  # a day after the last good check
    assert updates.latest_version(cache, fetch=offline, now=later) == "1.7.0"
    assert updates.latest_version(cache, fetch=lambda: "1.8.0", now=later + 3600) == "1.8.0"


# ---------- selfupdate: fetching and checking the download ----------


def release(name, data, url=None):
    return {"assets": [
        {"name": "other.txt", "browser_download_url": selfupdate.DOWNLOAD_PREFIX + "x/other.txt",
         "digest": "sha256:00", "size": 1},
        {"name": name, "browser_download_url": url or selfupdate.DOWNLOAD_PREFIX + f"studio-v{NEXT}/{name}",
         "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)},
    ]}


class Opener:
    def __init__(self, data):
        self.data, self.asked = data, []

    def __call__(self, req, timeout=None):
        self.asked.append(req.full_url)
        return io.BytesIO(self.data)


def wait_for_stage(updater, stage, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        st = updater.status()
        if st and st["stage"] == stage:
            return st
        time.sleep(0.02)
    raise AssertionError(f"never reached {stage}: {updater.status()}")


def test_asset_names():
    assert selfupdate.asset_name("1.6.0", "windows") == "StationStudio-1.6.0-windows-setup.exe"
    assert selfupdate.asset_name("1.6.0", "mac", "arm64") == "StationStudio-1.6.0-mac-apple-silicon.dmg"
    assert selfupdate.asset_name("1.6.0", "mac", "x86_64") == "StationStudio-1.6.0-mac-intel.dmg"
    assert selfupdate.install_kind() is None  # tests aren't the downloaded app


def test_install_kind_in_the_downloaded_app(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert selfupdate.install_kind("darwin") == "mac"
    assert selfupdate.install_kind("win32") == "windows"
    assert selfupdate.install_kind("linux") is None


def test_release_asset_only_from_the_projects_own_releases():
    name = "StationStudio-99.0.0-windows-setup.exe"
    url, sha, size = selfupdate.release_asset(NEXT, name, lambda u: release(name, b"setup"))
    assert url.startswith("https://github.com/raby/station-studio/releases/download/")
    assert sha == hashlib.sha256(b"setup").hexdigest() and size == 5
    for bad in (release(name, b"x", url="https://evil.example/setup.exe"), release("other-name", b"x"), {}, []):
        with pytest.raises(UpdateError, match="isn't ready yet"):
            selfupdate.release_asset(NEXT, name, lambda u, bad=bad: bad)

    def offline(url):
        raise OSError("no network")

    with pytest.raises(UpdateError, match="Couldn't reach GitHub"):
        selfupdate.release_asset(NEXT, name, offline)


def test_download_checks_the_file(tmp_path):
    data = b"x" * 600_000
    sha = hashlib.sha256(data).hexdigest()
    seen = []
    selfupdate.download("https://x/f", tmp_path / "f", sha, len(data), progress=lambda d, t: seen.append(d),
                        opener=Opener(data))
    assert (tmp_path / "f").read_bytes() == data and seen[-1] == len(data)
    with pytest.raises(UpdateError, match="didn't match"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(data[:-1] + b"y"))
    assert not (tmp_path / "g").exists()
    with pytest.raises(UpdateError, match="didn't match"):  # longer than GitHub says
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(data + b"more"))
    with pytest.raises(UpdateError, match="larger than expected"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, selfupdate.MAX_DOWNLOAD + 1, opener=Opener(data))


def test_mac_app_must_be_replaceable(tmp_path):
    app = tmp_path / "Applications" / "Station Studio.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    exe = app / "Contents" / "MacOS" / selfupdate.MAC_HELPER
    assert selfupdate.mac_app(str(exe)) == app
    for where in ("/Volumes/Station Studio/Station Studio.app", "/private/var/x/AppTranslocation/y/Station Studio.app"):
        with pytest.raises(UpdateError, match="Applications folder first"):
            selfupdate.mac_app(f"{where}/Contents/MacOS/{selfupdate.MAC_HELPER}")
    with pytest.raises(UpdateError, match="Applications folder first"):
        selfupdate.mac_app("/usr/bin/python3")


@pytest.mark.skipif(sys.platform == "win32", reason="the Mac's shell script")
def test_mac_swap_script(tmp_path):
    def bundle(path, text):
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "which").write_text(text)

    app, new, tidy = tmp_path / "Station Studio.app", tmp_path / ".Station Studio 99.0.0.app", tmp_path / "tidy"
    bundle(app, "old")
    bundle(new, "new")
    tidy.mkdir()
    started = tmp_path / "started"
    gone = subprocess.Popen(["true"])
    gone.wait()
    script = selfupdate.mac_swap_script(gone.pid, app, new, ["touch", str(started)], tidy)
    subprocess.run(["sh", "-c", script], check=True, timeout=30)
    for _ in range(100):
        if started.exists():
            break
        time.sleep(0.05)
    assert (app / "Contents" / "which").read_text() == "new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Station Studio.app", "started"]
    # Nothing to put in its place: the old app stays.
    started.unlink()
    script = selfupdate.mac_swap_script(gone.pid, app, tmp_path / "missing.app", ["touch", str(started)], tidy)
    subprocess.run(["sh", "-c", script], check=True, timeout=30)
    assert (app / "Contents" / "which").read_text() == "new"
    assert not (tmp_path / ".Station Studio old.app").exists()


def test_mac_unpack_checks_the_version(tmp_path):
    app = tmp_path / "Station Studio.app"
    app.mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ditto":
            plist = Path(cmd[2]) / "Contents" / "Info.plist"
            plist.parent.mkdir(parents=True)
            plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": run.version}))
        return subprocess.CompletedProcess(cmd, 0)

    run.version = NEXT
    new = selfupdate.mac_unpack(tmp_path / "x.dmg", app, NEXT, run=run)
    assert new == tmp_path / ".Station Studio 99.0.0.app" and new.is_dir()
    assert [c[0] for c in calls] == ["hdiutil", "ditto", "hdiutil"]  # detached again
    assert calls[1][1].endswith("/Station Studio.app")
    run.version = "1.0"
    with pytest.raises(UpdateError, match="held version 1.0"):
        selfupdate.mac_unpack(tmp_path / "x.dmg", app, NEXT, run=run)
    assert not new.exists()


def test_windows_update_runs_the_checked_setup():
    name = selfupdate.asset_name(NEXT, "windows")
    launched, quits = [], []
    up = Updater(lambda: quits.append(1), kind="windows", get_json=lambda u: release(name, b"setup!"),
                 opener=Opener(b"setup!"), launch=launched.append)
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting" and quits == [1]
    [cmd] = launched
    assert Path(cmd[0]).name == name and Path(cmd[0]).read_bytes() == b"setup!"
    assert "/VERYSILENT" in cmd
    # A file that doesn't match is never run.
    launched.clear()
    up = Updater(lambda: quits.append(1), kind="windows", get_json=lambda u: release(name, b"setup!"),
                 opener=Opener(b"tampered"), launch=launched.append)
    up.start(NEXT)
    st = wait_for_stage(up, "failed")
    assert "didn't match" in st["error"] and st["page"].endswith(f"studio-v{NEXT}") and not launched


def test_mac_update_restarts_the_helper(tmp_path, monkeypatch):
    app = tmp_path / "Station Studio.app"
    app.mkdir()
    monkeypatch.setattr(selfupdate, "mac_app", lambda: app)
    monkeypatch.setattr(selfupdate, "mac_unpack", lambda dmg, a, v, run: tmp_path / "new.app")
    name = selfupdate.asset_name(NEXT, "mac")
    launched, quits = [], []
    up = Updater(lambda: quits.append(1), port=8070, kind="mac", get_json=lambda u: release(name, b"dmg"),
                 opener=Opener(b"dmg"), launch=launched.append)
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting" and quits == [1]
    [[sh, script]] = launched
    text = Path(script).read_text()
    assert sh == "/bin/sh" and str(os.getpid()) in text and "new.app" in text
    assert f"Contents/MacOS/{selfupdate.MAC_HELPER}" in text and "--no-browser" in text and "8070" in text


def test_only_the_downloaded_apps_update_and_only_to_newer():
    with pytest.raises(ValueError, match="Mac and Windows"):
        Updater(lambda: None, kind="none").start(NEXT)
    gate = threading.Event()
    up = Updater(lambda: None, kind="windows", get_json=lambda u: gate.wait(5) and {}, launch=lambda c: None)
    with pytest.raises(ValueError, match="up to date"):
        up.start(__version__)
    up.start(NEXT)
    with pytest.raises(ValueError, match="already updating"):
        up.start(NEXT)
    gate.set()
    up.thread.join(5)


# ---------- the app's own routes ----------


def make_app(kind="mac", latest=None, port=8001):
    stops = []
    desk = desktop.DesktopState(port, lambda: stops.append(1), kind=kind,
                                cache=Path(os.devnull), fetch_latest=lambda: latest)
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
