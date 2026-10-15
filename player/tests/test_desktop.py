import plistlib
import shlex
import subprocess

import pytest

from station_player import desktop as dt
from station_player.desktop import APP_ID, APP_NAME, Desktop, _desktop_exec, launch_command

PY = "/opt/My Tools/venv/bin/python"


def make(platform, tmp_path, **kw):
    return Desktop(platform=platform, home=tmp_path, env={}, python=PY, **kw)


# ---- Linux ------------------------------------------------------------------------

def test_linux_shortcut_menu_desktop_and_login(tmp_path):
    (tmp_path / "Desktop").mkdir()
    d = make("linux", tmp_path)
    assert d.status() == {"supported": True, "platform": "linux", "shortcut": False, "login": False}

    d.set_shortcut(True)
    menu = tmp_path / ".local/share/applications/station-player.desktop"
    text = menu.read_text()
    assert 'Exec="/opt/My Tools/venv/bin/python" -m station_player --app\n' in text
    assert "Terminal=false" in text and f"Name={APP_NAME}" in text
    on_desktop = tmp_path / "Desktop" / "station-player.desktop"
    assert on_desktop.read_text() == text
    assert on_desktop.stat().st_mode & 0o111  # desktops launch executable entries only

    d.set_login(True)
    auto = tmp_path / ".config/autostart/station-player.desktop"
    assert "--app --no-browser" in auto.read_text()
    assert d.status()["shortcut"] and d.status()["login"]

    icon = tmp_path / ".local/share/icons/station-player.png"
    assert f"Icon={icon}\n" in text and icon.exists()

    d.set_shortcut(False)
    assert icon.exists()  # the login entry still uses it
    d.remove_all()
    assert not menu.exists() and not on_desktop.exists() and not auto.exists()
    assert not icon.exists()
    assert d.status()["shortcut"] is False


def test_linux_respects_xdg_dirs(tmp_path):
    data, conf, desk = tmp_path / "data", tmp_path / "conf", tmp_path / "Schreibtisch"
    desk.mkdir()
    conf.mkdir()
    (conf / "user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n')
    d = Desktop(platform="linux", home=tmp_path, python=PY,
                env={"XDG_DATA_HOME": str(data), "XDG_CONFIG_HOME": str(conf)})
    d.set_shortcut(True)
    d.set_login(True)
    assert (data / "applications/station-player.desktop").exists()
    assert (desk / "station-player.desktop").exists()
    assert (conf / "autostart/station-player.desktop").exists()


def test_desktop_exec_quoting():
    assert _desktop_exec(["/usr/bin/python3", "-m", "x"]) == "/usr/bin/python3 -m x"
    # Reserved characters are quoted; ", `, $ and \ are escaped inside the
    # quotes, then every backslash is doubled for the desktop-file string.
    assert _desktop_exec(['/a b/$x"y', "100%"]) == '"/a b/\\\\$x\\\\"y" 100%%'


# ---- macOS --------------------------------------------------------------------------

def test_mac_app_bundle_and_login_agent(tmp_path):
    d = make("mac", tmp_path)
    d.set_shortcut(True)
    bundle = tmp_path / "Applications" / f"{APP_NAME}.app"
    with (bundle / "Contents/Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    assert info["CFBundleIdentifier"] == APP_ID
    assert info["CFBundleExecutable"] == "station-player"
    assert info["LSUIElement"] is True
    script = (bundle / "Contents/MacOS/station-player").read_text()
    assert script.startswith("#!/bin/sh\nexec ")
    assert shlex.split(script.splitlines()[1])[1:] == [PY, "-m", "station_player", "--app", "$@"]
    assert (bundle / "Contents/MacOS/station-player").stat().st_mode & 0o111
    assert (bundle / "Contents/Resources/icon.icns").exists()

    d.set_login(True)
    with (tmp_path / "Library/LaunchAgents" / f"{APP_ID}.plist").open("rb") as fh:
        agent = plistlib.load(fh)
    assert agent["ProgramArguments"] == [PY, "-m", "station_player", "--app", "--no-browser"]
    assert agent["RunAtLoad"] is True
    assert d.status()["shortcut"] and d.status()["login"]

    d.remove_all()
    assert not bundle.exists()
    assert d.status() == {"supported": True, "platform": "mac", "shortcut": False, "login": False}


def test_mac_never_deletes_an_app_it_did_not_make(tmp_path):
    bundle = tmp_path / "Applications" / f"{APP_NAME}.app" / "Contents"
    bundle.mkdir(parents=True)
    with (bundle / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleIdentifier": "com.someone.else"}, fh)
    make("mac", tmp_path).set_shortcut(False)
    assert (bundle / "Info.plist").exists()


# ---- Windows ------------------------------------------------------------------------

class FakeRegistry:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_windows_shortcuts_and_run_key(tmp_path):
    runs = []

    def run(cmd, env, **kw):
        runs.append((cmd, env, kw))
        (tmp_path / "x").mkdir(exist_ok=True)
        open(env["SP_LINK"], "w").close()  # what PowerShell would do
        return subprocess.CompletedProcess(cmd, 0, "", "")

    folders = {"programs": tmp_path / "Programs", "desktop": tmp_path / "OneDrive Desktop"}
    reg = FakeRegistry()
    exe = tmp_path / "venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    (exe.parent / "pythonw.exe").touch()
    d = Desktop(platform="windows", home=tmp_path, env={"PATH": "x"}, python=str(exe),
                run=run, registry=reg, folders=folders)

    d.set_shortcut(True)
    assert [r[1]["SP_LINK"] for r in runs] == [
        str(folders["programs"] / f"{APP_NAME}.lnk"), str(folders["desktop"] / f"{APP_NAME}.lnk")]
    cmd, env, kw = runs[0]
    assert cmd[0] == "powershell" and "$env:SP_LINK" in cmd[-1]
    assert env["SP_TARGET"].endswith("pythonw.exe")  # no console window
    assert env["SP_ARGS"] == "-m station_player --app"
    assert env["SP_LINK"] not in cmd[-1]  # paths never pasted into the script
    assert kw["creationflags"] == dt._CREATE_NO_WINDOW

    d.set_login(True)
    assert reg.values[APP_NAME].endswith("pythonw.exe -m station_player --app --no-browser")
    assert d.status() == {"supported": True, "platform": "windows", "shortcut": True, "login": True}

    d.remove_all()
    assert not list(folders["programs"].glob("*.lnk")) and reg.values == {}


def test_windows_powershell_failure_is_reported(tmp_path):
    def run(cmd, env, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "COM error")

    d = Desktop(platform="windows", home=tmp_path, env={}, python=PY, run=run,
                registry=FakeRegistry(), folders={"programs": tmp_path, "desktop": tmp_path})
    with pytest.raises(OSError, match="COM error"):
        d.set_shortcut(True)


def test_launch_command_falls_back_to_python_when_no_pythonw(tmp_path):
    exe = tmp_path / "python.exe"
    assert launch_command(platform="windows", python=str(exe))[0] == str(exe)


def test_unsupported_platform(tmp_path):
    d = make("other", tmp_path)
    assert d.status()["supported"] is False
    with pytest.raises(OSError):
        d.set_shortcut(True)
