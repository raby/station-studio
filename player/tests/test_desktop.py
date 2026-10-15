import plistlib
import shlex
import subprocess
from pathlib import Path

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
    assert 'Exec="/opt/My Tools/venv/bin/python" -P -m station_player --app %F\n' in text
    assert "MimeType=audio/mpeg;" in text  # offered in Open With for audio
    assert "Terminal=false" in text and f"Name={APP_NAME}" in text
    on_desktop = tmp_path / "Desktop" / "station-player.desktop"
    assert on_desktop.read_text() == text
    assert on_desktop.stat().st_mode & 0o111  # desktops launch executable entries only

    d.set_login(True)
    auto = tmp_path / ".config/autostart/station-player.desktop"
    assert "--app --no-browser\n" in auto.read_text() and "MimeType" not in auto.read_text()
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
    assert shlex.split(script.splitlines()[1])[1:] == [PY, "-P", "-m", "station_player", "--app", "$@"]
    assert (bundle / "Contents/MacOS/station-player").stat().st_mode & 0o111
    assert (bundle / "Contents/Resources/icon.icns").exists()

    d.set_login(True)
    with (tmp_path / "Library/LaunchAgents" / f"{APP_ID}.plist").open("rb") as fh:
        agent = plistlib.load(fh)
    assert agent["ProgramArguments"] == [PY, "-P", "-m", "station_player", "--app", "--no-browser"]
    assert agent["RunAtLoad"] is True
    assert d.status()["shortcut"] and d.status()["login"]

    d.remove_all()
    assert not bundle.exists()
    assert d.status() == {"supported": True, "platform": "mac", "shortcut": False, "login": False}


def fake_osacompile(calls):
    """Stands in for macOS's osacompile, codesign and lsregister."""
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "osacompile":
            out = Path(cmd[2])
            calls.append(Path(cmd[3]).read_text())
            (out / "Contents/MacOS").mkdir(parents=True)
            (out / "Contents/Resources").mkdir(parents=True)
            (out / "Contents/MacOS/applet").write_text("stub")
            with (out / "Contents/Info.plist").open("wb") as fh:
                plistlib.dump({"CFBundleExecutable": "applet", "CFBundleIconFile": "applet"}, fh)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_mac_app_is_an_applet_that_takes_open_with_files(tmp_path):
    calls = []
    d = make("mac", tmp_path, run=fake_osacompile(calls))
    d.set_shortcut(True)
    bundle = tmp_path / "Applications" / f"{APP_NAME}.app"
    with (bundle / "Contents/Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    assert info["CFBundleIdentifier"] == APP_ID and info["CFBundleExecutable"] == "applet"
    [doc] = info["CFBundleDocumentTypes"]
    assert doc["LSItemContentTypes"] == ["public.audio"] and doc["LSHandlerRank"] == "Alternate"
    script = calls[1]
    assert "on open theFiles" in script and "quoted form of POSIX path of f" in script
    assert "'/opt/My Tools/venv/bin/python' -P -m station_player --app" in script
    assert (bundle / "Contents/Resources/applet.icns").exists()
    assert calls[2][:3] == ["codesign", "--force", "--sign"]
    assert calls[3][0].endswith("lsregister")
    assert not list((tmp_path / "Applications").glob(".*"))  # nothing left behind
    # Made again (an upgrade), it replaces the old app.
    d.set_shortcut(True)
    assert bundle.exists()
    d.set_shortcut(False)
    assert not bundle.exists()


def test_old_shortcuts_are_brought_up_to_date(tmp_path, monkeypatch):
    calls = []
    d = make("mac", tmp_path, run=fake_osacompile(calls))
    assert d.refresh() is False  # no shortcut: nothing to do
    d.set_shortcut(True)
    assert d.refresh() is False  # already current
    monkeypatch.setattr(dt, "__version__", "99.0")
    assert d.refresh() is True
    linux = make("linux", tmp_path / "l")
    linux.set_shortcut(True)
    assert linux.refresh() is False
    entry = tmp_path / "l/.local/share/applications/station-player.desktop"
    entry.write_text(entry.read_text().replace(" %F", ""))  # made by 0.3
    assert linux.refresh() is True and " %F" in entry.read_text()


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


class FakeKeys:
    def __init__(self):
        self.values = {}

    def get(self, path, name):
        return self.values.get((path, name))

    def set(self, path, name, value):
        self.values[(path, name)] = value

    def delete(self, path, name):
        self.values.pop((path, name), None)

    def delete_tree(self, path):
        for key in [k for k in self.values if k[0] == path or k[0].startswith(path + "\\")]:
            del self.values[key]


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
    keys = FakeKeys()
    d = Desktop(platform="windows", home=tmp_path, env={"PATH": "x"}, python=str(exe),
                run=run, registry=reg, folders=folders, classes=keys)

    d.set_shortcut(True)
    assert [r[1]["SP_LINK"] for r in runs] == [
        str(folders["programs"] / f"{APP_NAME}.lnk"), str(folders["desktop"] / f"{APP_NAME}.lnk")]
    cmd, env, kw = runs[0]
    assert cmd[0] == "powershell" and "$env:SP_LINK" in cmd[-1]
    assert env["SP_TARGET"].endswith("pythonw.exe")  # no console window
    assert env["SP_ARGS"] == "-P -m station_player --app"
    assert env["SP_LINK"] not in cmd[-1]  # paths never pasted into the script
    assert kw["creationflags"] == dt._CREATE_NO_WINDOW

    # Open With for audio files, never the default.
    command = keys.values[(r"Software\Classes\StationPlayer.AudioFile\shell\open\command", "")]
    assert command.endswith('pythonw.exe -P -m station_player --app "%1"')
    assert keys.values[(r"Software\Classes\StationPlayer.AudioFile\shell\open", "FriendlyAppName")] == APP_NAME
    assert keys.values[(r"Software\Classes\.mp3\OpenWithProgids", "StationPlayer.AudioFile")] == ""
    assert not any(k[0] == r"Software\Classes\.mp3" for k in keys.values)

    d.set_login(True)
    assert reg.values[APP_NAME].endswith("pythonw.exe -P -m station_player --app --no-browser")
    assert d.status() == {"supported": True, "platform": "windows", "shortcut": True, "login": True}

    d.remove_all()
    assert not list(folders["programs"].glob("*.lnk")) and reg.values == {} and keys.values == {}


def test_windows_powershell_failure_is_reported(tmp_path):
    def run(cmd, env, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "COM error")

    d = Desktop(platform="windows", home=tmp_path, env={}, python=PY, run=run,
                registry=FakeRegistry(), folders={"programs": tmp_path, "desktop": tmp_path}, classes=FakeKeys())
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
