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

from station_appkit.selfupdate import asset_name

from station_player import __version__, selfupdate
from station_player.appinfo import APP
from station_player.selfupdate import Updater

NEXT = "99.0.0"


def release(name, data, url=None):
    return {"assets": [
        {"name": "other.txt", "browser_download_url": APP.download_prefix + "x/other.txt",
         "digest": "sha256:00", "size": 1},
        {"name": name, "browser_download_url": url or APP.download_prefix + f"player-v{NEXT}/{name}",
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
    assert asset_name(APP, "1.2.0", "windows") == "StationPlayer-1.2.0-windows-setup.exe"
    assert asset_name(APP, "1.2.0", "mac", "arm64") == "StationPlayer-1.2.0-mac-apple-silicon.dmg"
    assert selfupdate.install_kind() == "pip"  # tests aren't the downloaded app







def test_windows_update_runs_the_checked_setup(tmp_path):
    name = asset_name(APP, NEXT, "windows")
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
    name = asset_name(APP, NEXT, "mac")
    launched, quits = [], []
    up = Updater(lambda: quits.append(1), port=8770, kind="mac", get_json=lambda u: release(name, b"dmg"),
                 opener=Opener(b"dmg"), launch=launched.append)
    monkeypatch.setattr(up, "mac_app", lambda: app)
    monkeypatch.setattr(up, "mac_unpack", lambda dmg, a, v: tmp_path / "new.app")
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
