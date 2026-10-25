import hashlib
import io
import os
import plistlib
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from station_player import __version__, selfupdate
from station_player.selfupdate import UpdateError, Updater

NEXT = "99.0.0"


def release(name, data, url=None):
    return {"assets": [
        {"name": "other.txt", "browser_download_url": selfupdate.DOWNLOAD_PREFIX + "x/other.txt",
         "digest": "sha256:00", "size": 1},
        {"name": name, "browser_download_url": url or selfupdate.DOWNLOAD_PREFIX + f"player-v{NEXT}/{name}",
         "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)},
    ]}


class Opener:
    def __init__(self, data):
        self.data, self.asked = data, []

    def __call__(self, req, timeout=None):
        self.asked.append(req.full_url)
        return io.BytesIO(self.data)


def wait_for(updater, stage, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        st = updater.status()
        if st and st["stage"] == stage:
            return st
        time.sleep(0.02)
    raise AssertionError(f"never reached {stage}: {updater.status()}")


def test_asset_names():
    assert selfupdate.asset_name("1.2.0", "windows") == "StationPlayer-1.2.0-windows-setup.exe"
    assert selfupdate.asset_name("1.2.0", "mac", "arm64") == "StationPlayer-1.2.0-mac-apple-silicon.dmg"
    assert selfupdate.asset_name("1.2.0", "mac", "x86_64") == "StationPlayer-1.2.0-mac-intel.dmg"
    assert selfupdate.install_kind() == "pip"  # tests aren't the downloaded app


def test_release_asset_only_from_the_projects_own_releases():
    name = "StationPlayer-99.0.0-windows-setup.exe"
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
    selfupdate.download("https://x/f", tmp_path / "f", sha, len(data), progress=lambda d, t: seen.append(d), opener=Opener(data))
    assert (tmp_path / "f").read_bytes() == data and seen[-1] == len(data)
    with pytest.raises(UpdateError, match="didn't match"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(data[:-1] + b"y"))
    assert not (tmp_path / "g").exists()
    with pytest.raises(UpdateError, match="didn't match"):  # longer than GitHub says
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(data + b"more"))
    with pytest.raises(UpdateError, match="larger than expected"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, selfupdate.MAX_DOWNLOAD + 1, opener=Opener(data))


def test_mac_app_must_be_replaceable(tmp_path):
    app = tmp_path / "Applications" / "Station Player.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    exe = app / "Contents" / "MacOS" / "station-player-server"
    assert selfupdate.mac_app(str(exe)) == app
    for where in ("/Volumes/Station Player/Station Player.app", "/private/var/x/AppTranslocation/y/Station Player.app"):
        with pytest.raises(UpdateError, match="Applications folder first"):
            selfupdate.mac_app(f"{where}/Contents/MacOS/station-player-server")
    with pytest.raises(UpdateError, match="Applications folder first"):
        selfupdate.mac_app("/usr/bin/python3")


@pytest.mark.skipif(sys.platform == "win32", reason="the Mac's shell script")
def test_mac_swap_script(tmp_path):
    def bundle(path, text):
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "which").write_text(text)

    app, new, tidy = tmp_path / "Station Player.app", tmp_path / ".Station Player 99.0.0.app", tmp_path / "tidy"
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
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Station Player.app", "started"]
    # Nothing to put in its place: the old app stays.
    started.unlink()
    script = selfupdate.mac_swap_script(gone.pid, app, tmp_path / "missing.app", ["touch", str(started)], tidy)
    subprocess.run(["sh", "-c", script], check=True, timeout=30)
    assert (app / "Contents" / "which").read_text() == "new"
    assert not (tmp_path / ".Station Player old.app").exists()


def test_mac_unpack_checks_the_version(tmp_path):
    app = tmp_path / "Station Player.app"
    app.mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd[0])
        if cmd[0] == "ditto":
            plist = Path(cmd[2]) / "Contents" / "Info.plist"
            plist.parent.mkdir(parents=True)
            plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": run.version}))
        return subprocess.CompletedProcess(cmd, 0)

    run.version = NEXT
    new = selfupdate.mac_unpack(tmp_path / "x.dmg", app, NEXT, run=run)
    assert new == tmp_path / ".Station Player 99.0.0.app" and new.is_dir()
    assert calls == ["hdiutil", "ditto", "hdiutil"]  # detached again
    run.version = "1.0"
    with pytest.raises(UpdateError, match="held version 1.0"):
        selfupdate.mac_unpack(tmp_path / "x.dmg", app, NEXT, run=run)
    assert not new.exists()


def test_windows_update_runs_the_checked_setup(tmp_path):
    name = selfupdate.asset_name(NEXT, "windows")
    launched, quits = [], []
    up = Updater(lambda: quits.append(1), port=8765, kind="windows", get_json=lambda u: release(name, b"setup!"),
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
    st = wait_for(up, "failed")
    assert "didn't match" in st["error"] and st["page"].endswith(f"player-v{NEXT}") and not launched


def test_mac_update(tmp_path, monkeypatch):
    app = tmp_path / "Station Player.app"
    app.mkdir()
    monkeypatch.setattr(selfupdate, "mac_app", lambda: app)
    monkeypatch.setattr(selfupdate, "mac_unpack", lambda dmg, a, v, run: tmp_path / "new.app")
    name = selfupdate.asset_name(NEXT, "mac")
    launched, quits = [], []
    up = Updater(lambda: quits.append(1), port=8770, kind="mac", get_json=lambda u: release(name, b"dmg"),
                 opener=Opener(b"dmg"), launch=launched.append)
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting" and quits == [1]
    [[sh, script]] = launched
    text = Path(script).read_text()
    assert sh == "/bin/sh" and str(os.getpid()) in text and "new.app" in text
    assert "--after-update" in text and "8770" in text


def test_pip_update(tmp_path):
    runs, launched = [], []

    def run(cmd, **kwargs):
        runs.append(cmd)
        if "pip" in cmd:
            return subprocess.CompletedProcess(cmd, run.code, "", "ERROR: no network\n")
        return subprocess.CompletedProcess(cmd, 0, f"{NEXT}\n", "")

    run.code = 0
    up = Updater(lambda: None, port=8765, kind="pip", run=run, launch=launched.append)
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting"
    assert runs[0][-1] == f"station-player=={NEXT}"
    assert launched == [[sys.executable, "-P", "-m", "station_player", "--app", "--no-browser",
                         "--port", "8765", "--after-update"]]
    run.code = 1
    launched.clear()
    up = Updater(lambda: None, kind="pip", run=run, launch=launched.append)
    up.start(NEXT)
    st = wait_for(up, "failed")
    assert "no network" in st["error"] and not launched


def test_only_newer_and_one_at_a_time():
    gate = threading.Event()
    up = Updater(lambda: None, kind="pip", run=lambda cmd, **k: gate.wait(5) and None, launch=lambda c: None)
    with pytest.raises(ValueError, match="up to date"):
        up.start(__version__)
    up.start(NEXT)
    with pytest.raises(ValueError, match="already updating"):
        up.start(NEXT)
    gate.set()
    up.thread.join(5)
