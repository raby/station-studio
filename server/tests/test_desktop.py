import plistlib
import sys

from station_server import audio, desktop
from station_server.desktop import APP_NAME, Login, launch_command


class FakeRunKey:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_windows_login_uses_the_run_key(tmp_path):
    python = tmp_path / "Python" / "python.exe"
    python.parent.mkdir()
    (python.parent / "pythonw.exe").write_text("")
    reg = FakeRunKey()
    login = Login(platform="windows", registry=reg, python=str(python))
    assert not login.enabled()
    login.set(True)
    assert login.enabled()
    cmd = reg.values[APP_NAME]
    assert "pythonw.exe" in cmd and cmd.endswith("-m station_server app --no-window")
    login.set(False)
    assert not login.enabled() and reg.values == {}


def test_mac_login_restarts_after_a_crash_only(tmp_path):
    login = Login(platform="mac", home=tmp_path, python="/usr/bin/python3")
    login.set(True)
    agent = tmp_path / "Library" / "LaunchAgents" / "io.github.raby.station-server.plist"
    data = plistlib.loads(agent.read_bytes())
    assert data["ProgramArguments"][-2:] == ["app", "--no-window"]
    assert data["RunAtLoad"] and data["KeepAlive"] == {"SuccessfulExit": False}
    assert login.enabled()
    login.set(False)
    assert not agent.exists() and not login.enabled()


def test_linux_autostart(tmp_path):
    login = Login(platform="linux", home=tmp_path, python="/usr/bin/python3")
    login.set(True)
    entry = tmp_path / ".config" / "autostart" / "io.github.raby.station-server.desktop"
    assert "Exec=/usr/bin/python3 -P -m station_server app --no-window" in entry.read_text()
    login.set(False)
    assert not entry.exists()


def test_downloaded_app_runs_itself(monkeypatch, tmp_path):
    exe = tmp_path / "Station Server"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    if sys.platform == "darwin":
        (tmp_path / desktop.MAC_HELPER).write_text("")
    cmd = launch_command()
    assert cmd[1:] == ["app", "--no-window"]


def test_ffmpeg_inside_the_app_comes_first(monkeypatch, tmp_path):
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    inside = tmp_path / "ffmpeg" / name
    inside.parent.mkdir()
    inside.write_text("")
    monkeypatch.delenv(audio.ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert audio.find_ffmpeg() == str(inside)
