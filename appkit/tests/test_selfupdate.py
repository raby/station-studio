"""The self-updater (station_appkit/selfupdate.py)."""
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

from station_appkit import selfupdate
from station_appkit.selfupdate import UpdateError, Updater

NEXT = "99.0.0"


def release(app, name, data, url=None):
    return {"assets": [
        {"name": "other.txt", "browser_download_url": app.download_prefix + "x/other.txt",
         "digest": "sha256:00", "size": 1},
        {"name": name, "browser_download_url": url or app.download_prefix + f"{app.tag_prefix}{NEXT}/{name}",
         "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)},
    ]}


class Opener:
    def __init__(self, data, fail=None):
        self.data, self.fail, self.asked = data, fail, []

    def __call__(self, req, timeout=None):
        self.asked.append((req.full_url, req.get_header("User-agent")))
        if self.fail:
            raise self.fail
        return io.BytesIO(self.data)


def wait_for(updater, stage, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        st = updater.status()
        if st and st["stage"] == stage:
            return st
        time.sleep(0.02)
    raise AssertionError(f"never reached {stage}: {updater.status()}")


def test_asset_names(app):
    assert selfupdate.asset_name(app, "1.2.0", "windows") == "StationTest-1.2.0-windows-setup.exe"
    assert selfupdate.asset_name(app, "1.2.0", "mac", "arm64") == "StationTest-1.2.0-mac-apple-silicon.dmg"
    assert selfupdate.asset_name(app, "1.2.0", "mac", "x86_64") == "StationTest-1.2.0-mac-intel.dmg"


def test_install_kind():
    assert selfupdate.install_kind() is None  # tests aren't the downloaded app
    assert selfupdate.install_kind("darwin", frozen=True) == "mac"
    assert selfupdate.install_kind("win32", frozen=True) == "windows"
    assert selfupdate.install_kind("linux", frozen=True) is None
    assert selfupdate.install_kind("darwin", frozen=False) is None


def test_release_asset_only_from_the_projects_own_releases(app):
    name = f"StationTest-{NEXT}-windows-setup.exe"
    url, sha, size = selfupdate.release_asset(app, NEXT, name, lambda u: release(app, name, b"setup"))
    assert url.startswith("https://github.com/raby/station-studio/releases/download/")
    assert sha == hashlib.sha256(b"setup").hexdigest() and size == 5
    asked = []
    selfupdate.release_asset(app, NEXT, name, lambda u: asked.append(u) or release(app, name, b"setup"))
    assert asked == [f"https://api.github.com/repos/raby/station-studio/releases/tags/test-v{NEXT}"]
    for bad in (release(app, name, b"x", url="https://evil.example/setup.exe"), release(app, "other-name", b"x"),
                {}, [], {"assets": [{"name": name, "browser_download_url": app.download_prefix + "x",
                                     "digest": "md5:00", "size": 1}]}):
        with pytest.raises(UpdateError, match="isn't ready yet"):
            selfupdate.release_asset(app, NEXT, name, lambda u, bad=bad: bad)

    def offline(url):
        raise OSError("no network")
    with pytest.raises(UpdateError, match="Couldn't reach GitHub"):
        selfupdate.release_asset(app, NEXT, name, offline)


def test_download_checks_the_file(tmp_path):
    data = b"x" * 600_000
    sha = hashlib.sha256(data).hexdigest()
    seen = []
    opener = Opener(data)
    selfupdate.download("https://x/f", tmp_path / "f", sha, len(data), progress=lambda d, t: seen.append(d),
                        opener=opener, user_agent="StationTest")
    assert (tmp_path / "f").read_bytes() == data and seen[-1] == len(data)
    assert opener.asked == [("https://x/f", "StationTest")]
    with pytest.raises(UpdateError, match="didn't match"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(data[:-1] + b"y"))
    assert not (tmp_path / "g").exists()
    with pytest.raises(UpdateError, match="didn't match"):  # longer than GitHub says
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(data + b"more"))
    with pytest.raises(UpdateError, match="larger than expected"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, selfupdate.MAX_DOWNLOAD + 1, opener=Opener(data))
    with pytest.raises(UpdateError, match="download stopped"):
        selfupdate.download("https://x/f", tmp_path / "g", sha, len(data), opener=Opener(b"", OSError("reset")))
    assert not (tmp_path / "g").exists()


def test_mac_app_must_be_replaceable(app, tmp_path):
    bundle = tmp_path / "Applications" / "Station Test.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    exe = bundle / "Contents" / "MacOS" / "station-test-server"
    assert selfupdate.mac_app(app, str(exe)) == bundle
    for where in ("/Volumes/Station Test/Station Test.app", "/private/var/x/AppTranslocation/y/Station Test.app"):
        with pytest.raises(UpdateError, match="Move Station Test into your Applications folder first"):
            selfupdate.mac_app(app, f"{where}/Contents/MacOS/station-test-server")
    with pytest.raises(UpdateError, match="Applications folder first"):
        selfupdate.mac_app(app, "/usr/bin/python3")


@pytest.mark.skipif(sys.platform == "win32", reason="the Mac's shell script")
def test_mac_swap_script(tmp_path):
    def bundle(path, text):
        (path / "Contents").mkdir(parents=True)
        (path / "Contents" / "which").write_text(text)

    app, new, tidy = tmp_path / "Station Test.app", tmp_path / ".Station Test 99.0.0.app", tmp_path / "tidy"
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
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Station Test.app", "started"]
    # Nothing to put in its place: the old app stays.
    started.unlink()
    script = selfupdate.mac_swap_script(gone.pid, app, tmp_path / "missing.app", ["touch", str(started)], tidy)
    subprocess.run(["sh", "-c", script], check=True, timeout=30)
    assert (app / "Contents" / "which").read_text() == "new"
    assert not (tmp_path / ".Station Test old.app").exists()


def test_mac_unpack_checks_the_version(app, tmp_path):
    bundle = tmp_path / "Station Test.app"
    bundle.mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ditto":
            plist = Path(cmd[2]) / "Contents" / "Info.plist"
            plist.parent.mkdir(parents=True)
            plist.write_bytes(plistlib.dumps({"CFBundleShortVersionString": run.version}))
        return subprocess.CompletedProcess(cmd, 0)

    run.version = NEXT
    new = selfupdate.mac_unpack(app, tmp_path / "x.dmg", bundle, NEXT, run=run)
    assert new == tmp_path / ".Station Test 99.0.0.app" and new.is_dir()
    assert [c[0] for c in calls] == ["hdiutil", "ditto", "hdiutil"]  # detached again
    assert calls[1][1].endswith("/Station Test.app")  # the app in the disk image
    run.version = "1.0"
    with pytest.raises(UpdateError, match="held version 1.0"):
        selfupdate.mac_unpack(app, tmp_path / "x.dmg", bundle, NEXT, run=run)
    assert not new.exists()

    def broken(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    with pytest.raises(UpdateError, match="Couldn't open the download"):
        selfupdate.mac_unpack(app, tmp_path / "x.dmg", bundle, NEXT, run=broken)


def test_windows_update_runs_the_checked_setup(app, tmp_path):
    name = selfupdate.asset_name(app, NEXT, "windows")
    launched, quits = [], []
    up = Updater(app, lambda: quits.append(1), port=8124, kind="windows",
                 get_json=lambda u: release(app, name, b"setup!"), opener=Opener(b"setup!"), launch=launched.append)
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting" and quits == [1]
    [cmd] = launched
    assert Path(cmd[0]).name == name and Path(cmd[0]).read_bytes() == b"setup!"
    assert "/VERYSILENT" in cmd
    # A file that doesn't match is never run.
    launched.clear()
    up = Updater(app, lambda: quits.append(1), kind="windows", get_json=lambda u: release(app, name, b"setup!"),
                 opener=Opener(b"tampered"), launch=launched.append)
    up.start(NEXT)
    st = wait_for(up, "failed")
    assert "didn't match" in st["error"] and st["page"].endswith(f"test-v{NEXT}") and not launched


def test_mac_update(app, tmp_path, monkeypatch):
    bundle = tmp_path / "Station Test.app"
    bundle.mkdir()
    name = selfupdate.asset_name(app, NEXT, "mac")
    launched, quits = [], []
    up = Updater(app, lambda: quits.append(1), port=8124, kind="mac", get_json=lambda u: release(app, name, b"dmg"),
                 opener=Opener(b"dmg"), launch=launched.append)
    monkeypatch.setattr(up, "mac_app", lambda: bundle)
    monkeypatch.setattr(up, "mac_unpack", lambda dmg, b, v: tmp_path / "new.app")
    up.start(NEXT)
    up.thread.join(5)
    assert up.status()["stage"] == "restarting" and quits == [1]
    [[sh, script]] = launched
    text = Path(script).read_text()
    assert sh == "/bin/sh" and str(os.getpid()) in text and "new.app" in text
    # The new app's helper starts on the same port.
    assert f"{bundle}/Contents/MacOS/station-test-server" in text and "--port 8124" in text


def test_each_app_says_how_its_mac_app_starts_again(app, tmp_path, monkeypatch):
    bundle = tmp_path / "Station Test.app"
    bundle.mkdir()
    name = selfupdate.asset_name(app, NEXT, "mac")
    launched = []
    up = Updater(app, lambda: None, kind="mac", get_json=lambda u: release(app, name, b"dmg"), opener=Opener(b"dmg"),
                 launch=launched.append, mac_relaunch=lambda b, port: [f"{b}/x", "app", "--no-window", str(port)])
    monkeypatch.setattr(up, "mac_app", lambda: bundle)
    monkeypatch.setattr(up, "mac_unpack", lambda dmg, b, v: tmp_path / "new.app")
    up.start(NEXT)
    up.thread.join(5)
    assert "app --no-window 8123" in Path(launched[0][1]).read_text()


def test_a_problem_shows_on_the_page(app):
    up = Updater(app, lambda: None, kind="windows", get_json=lambda u: 1 / 0, launch=lambda c: None)
    up.start(NEXT)
    st = wait_for(up, "failed")
    assert "Couldn't reach GitHub" in st["error"]
    # Anything unexpected too, rather than a page left waiting.
    up = Updater(app, lambda: None, kind="windows", get_json=lambda u: release(app, "x", b"x"),
                 launch=lambda c: None)
    up._fetch = lambda version, folder: (_ for _ in ()).throw(RuntimeError("boom"))
    up.start(NEXT)
    assert "didn't work (boom)" in wait_for(up, "failed")["error"]
    # And it can be tried again.
    up._fetch = lambda version, folder: folder / "setup.exe"
    up.start(NEXT)
    wait_for(up, "restarting")


def test_only_newer_one_at_a_time_and_only_the_downloaded_apps(app):
    with pytest.raises(ValueError, match="Only the downloaded Mac and Windows apps"):
        Updater(app, lambda: None, kind=None).start(NEXT)
    gate = threading.Event()
    up = Updater(app, lambda: None, kind="windows", launch=lambda c: None)
    up._fetch = lambda version, folder: gate.wait(5) and folder / "x"
    with pytest.raises(ValueError, match="Station Test 1.2.0 is already up to date"):
        up.start("1.2.0")
    up.start(NEXT)
    with pytest.raises(ValueError, match="already updating"):
        up.start(NEXT)
    gate.set()
    up.thread.join(5)


def test_another_way_to_update(app):
    class PipUpdater(Updater):
        KINDS = ("mac", "windows", "pip")

        def update_pip(self, version):
            self._set(stage="restarting")
            self._launch(["pip", version])

    launched = []
    up = PipUpdater(app, lambda: None, kind="pip", launch=launched.append)
    up.start(NEXT)
    up.thread.join(5)
    assert launched == [["pip", NEXT]]


def test_what_starts_after_an_update_starts_afresh(monkeypatch):
    seen = {}

    class Popen:
        def __init__(self, cmd, env=None, **kwargs):
            seen.update(cmd=cmd, env=env, kwargs=kwargs)

    monkeypatch.setattr(selfupdate.subprocess, "Popen", Popen)
    monkeypatch.setattr(selfupdate.sys, "frozen", True, raising=False)
    selfupdate.detached(["/new"], env={"STATION_TEST_CHILD": "1"})
    assert seen["cmd"] == ["/new"]
    assert seen["env"]["STATION_TEST_CHILD"] == "1" and seen["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
