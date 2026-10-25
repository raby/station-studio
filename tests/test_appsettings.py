"""Settings › App: the app's own settings and start at login
(station_studio/appsettings.py)."""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from station_studio import appsettings


def test_settings_default_and_keep_only_what_they_know(tmp_path):
    assert appsettings.load(tmp_path) == {"own_window": True, "check_updates": True}
    (tmp_path / "app-settings.json").write_text("not json")
    assert appsettings.load(tmp_path) == appsettings.DEFAULTS
    (tmp_path / "app-settings.json").write_text('{"own_window": "no", "check_updates": false, "x": true}')
    assert appsettings.load(tmp_path) == {"own_window": True, "check_updates": False}
    assert appsettings.save(tmp_path / "new", {"own_window": False, "check_updates": None, "x": 1}) \
        == {"own_window": False, "check_updates": True}
    assert appsettings.load(tmp_path / "new")["own_window"] is False


def test_what_starts_it_at_login():
    assert appsettings.launch_command(True, "/Applications/Station Studio.app/Contents/MacOS/Station Studio",
                                      "darwin") == [
        "/Applications/Station Studio.app/Contents/MacOS/station-studio-server", "--no-browser"]
    assert appsettings.launch_command(True, r"C:\Apps\Station Studio.exe", "win32") == [
        r"C:\Apps\Station Studio.exe", "--no-browser"]
    assert appsettings.launch_command(False, which=lambda n: "/home/rob/.local/bin/station-studio") == [
        "/home/rob/.local/bin/station-studio", "app", "--no-browser"]
    assert appsettings.launch_command(False, which=lambda n: None) is None


def test_login_on_a_mac(tmp_path):
    login = appsettings.Login(tmp_path, "darwin", command=["/x/station-studio-server", "--no-browser"])
    assert login.supported and login.enabled() is False
    login.set(True)
    agent = tmp_path / "Library/LaunchAgents/io.github.raby.station-studio.plist"
    with agent.open("rb") as fh:
        job = plistlib.load(fh)
    assert job["ProgramArguments"] == ["/x/station-studio-server", "--no-browser"]
    assert job["RunAtLoad"] is True and job["KeepAlive"] is False
    assert login.enabled() is True
    login.set(False)
    assert login.enabled() is False and not agent.exists()


def test_login_on_windows(tmp_path):
    class Key:
        def __init__(self):
            self.values = {}

        def get(self, name):
            return self.values.get(name)

        def set(self, name, value):
            self.values[name] = value

        def delete(self, name):
            self.values.pop(name, None)

    key = Key()
    login = appsettings.Login(tmp_path, "win32", command=[r"C:\Apps\Station Studio.exe", "--no-browser"], run_key=key)
    assert login.enabled() is False
    login.set(True)
    assert key.values == {"Station Studio": '"C:\\Apps\\Station Studio.exe" --no-browser'}
    assert login.enabled() is True
    login.set(False)
    assert key.values == {}


def test_login_on_linux(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    login = appsettings.Login(tmp_path, "linux", command=["/home/rob/my apps/station-studio", "app", "--no-browser"])
    login.set(True)
    entry = tmp_path / ".config/autostart/station-studio.desktop"
    assert 'Exec="/home/rob/my apps/station-studio" app --no-browser' in entry.read_text()
    assert login.enabled() is True
    login.set(False)
    assert login.enabled() is False


def test_login_where_it_cant(tmp_path):
    login = appsettings.Login(tmp_path, "linux", command=[])
    assert not login.supported and login.enabled() is None
    with pytest.raises(ValueError):
        login.set(True)
    assert appsettings.Login(tmp_path, "sunos5", command=["x"]).enabled() is None


def test_login_not_from_the_disk_image(tmp_path):
    login = appsettings.Login(tmp_path, "darwin",
                              command=["/Volumes/Station Studio/Station Studio.app/Contents/MacOS/station-studio-server"])
    with pytest.raises(ValueError, match="Applications folder"):
        login.set(True)
    login.set(False)  # turning it off always works
    assert login.enabled() is False
