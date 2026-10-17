import plistlib
import shlex
import struct
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
            (out / "Contents/Resources/Assets.car").write_text("script icon")
            with (out / "Contents/Info.plist").open("wb") as fh:
                plistlib.dump({"CFBundleExecutable": "applet", "CFBundleIconFile": "applet",
                               "CFBundleIconName": "applet"}, fh)
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
    # The player's icon, not the script icon newer macOS takes from Assets.car.
    assert (bundle / "Contents/Resources/applet.icns").exists()
    assert not (bundle / "Contents/Resources/Assets.car").exists() and "CFBundleIconName" not in info
    # station-player://start starts it without a window.
    assert info["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["station-player"]
    assert "on open location theURL" in script
    assert "station_player --app --no-browser >/dev/null" in script.split("on open location")[1]
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
    monkeypatch.setattr(dt, "MAC_SHORTCUT_REVISION", 99)  # same version, app gained something
    assert d.refresh() is True
    assert d.refresh() is False
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
    # station-player://start starts it without a window.
    assert keys.values[(r"Software\Classes\station-player", "URL Protocol")] == ""
    assert keys.values[(r"Software\Classes\station-player\shell\open\command", "")].endswith(
        "pythonw.exe -P -m station_player --app --no-browser")
    assert d.refresh() is False
    del keys.values[(r"Software\Classes\station-player\shell\open\command", "")]  # made by 0.4.0
    assert d.refresh() is True and d.refresh() is False

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


# ---- a desktop icon for one station ----------------------------------------------

URL = "https://stream.example/live?x=1&y=2"


def test_linux_station_icon(tmp_path):
    (tmp_path / "Desktop").mkdir()
    logo = tmp_path / "abc.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    d = make("linux", tmp_path)
    path = d.station_shortcut("Late / Night FM", URL, tmp_path / "icons", logo)
    assert path == tmp_path / "Desktop" / "Late Night FM.desktop"
    text = path.read_text()
    assert "Name=Late / Night FM\n" in text
    assert f'Exec="{PY}" -P -m station_player --app --station "{URL}"\n' in text
    icon = next((tmp_path / "icons").glob("*.png"))
    assert f"Icon={icon}\n" in text
    assert path.stat().st_mode & 0o111
    # Made again: the same icon, not a second one.
    assert d.station_shortcut("Late / Night FM", URL, tmp_path / "icons", logo) == path
    # Someone else's file with that name is left alone.
    other = make("linux", tmp_path).station_shortcut("Late / Night FM", URL + "&other", tmp_path / "icons")
    assert other.name == "Late Night FM 2.desktop" and path.read_text() == text
    assert "Icon=" in other.read_text() and (tmp_path / ".local/share/icons/station-player.png").exists()


def test_mac_station_icon_is_an_applet_with_the_logo(tmp_path):
    (tmp_path / "Desktop").mkdir()
    calls = []
    run = fake_osacompile(calls)

    def with_sips(cmd, **kw):
        if cmd[0] == "sips":
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"icns")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return run(cmd, **kw)

    logo = tmp_path / "abc.png"
    logo.write_bytes(b"png")
    d = make("mac", tmp_path, run=with_sips)
    bundle = d.station_shortcut("Rebirth Radio", URL, tmp_path / "icons", logo)
    assert bundle == tmp_path / "Desktop" / "Rebirth Radio.app"
    with (bundle / "Contents/Info.plist").open("rb") as fh:
        info = plistlib.load(fh)
    assert info["CFBundleIdentifier"].startswith(APP_ID + ".station.")
    assert info["CFBundleName"] == "Rebirth Radio" and "CFBundleDocumentTypes" not in info
    script = next(c for c in calls if isinstance(c, str))
    assert f"station_player --app --station '{URL}' >/dev/null" in script
    assert (bundle / "Contents/Resources/applet.icns").read_bytes() == b"icns"  # the station's logo
    assert not any(isinstance(c, list) and c[0].endswith("lsregister") for c in calls)
    assert d.station_shortcut("Rebirth Radio", URL, tmp_path / "icons", logo) == bundle


def test_mac_station_icon_without_osacompile(tmp_path):
    (tmp_path / "Desktop").mkdir()
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    bundle = make("mac", tmp_path, run=missing).station_shortcut("Jazz", URL, tmp_path / "icons")
    script = (bundle / "Contents/MacOS/station").read_text()
    assert shlex.split(script.splitlines()[1])[1:] == [PY, "-P", "-m", "station_player", "--app", "--station", URL]
    assert (bundle / "Contents/Resources/icon.icns").exists()


def test_windows_station_icon(tmp_path):
    calls = []

    def run(cmd, env=None, **kw):
        calls.append(env)
        args = env["SP_ARGS"].encode("utf-16-le")  # as a .lnk keeps them: a count, then the text
        Path(env["SP_LINK"]).write_bytes(b"head" + struct.pack("<H", len(args) // 2) + args + b"tail")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    desk = tmp_path / "desk"
    desk.mkdir()
    logo = tmp_path / "abc.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (48).to_bytes(4, "big") * 2 + b"rest")
    d = make("windows", tmp_path, run=run, folders={"desktop": str(desk), "programs": str(tmp_path / "p")})
    link = d.station_shortcut("Soul: 24/7", URL, tmp_path / "icons", logo)
    assert link == desk / "Soul 24 7.lnk"
    env = calls[0]
    assert env["SP_ARGS"] == f"-P -m station_player --app --station {URL}"
    ico = Path(env["SP_ICON"])
    assert ico.suffix == ".ico" and ico.read_bytes()[:6] == b"\0\0\1\0\1\0" and ico.read_bytes()[6] == 48
    assert d.station_shortcut("Soul: 24/7", URL, tmp_path / "icons", logo) == link
    # A station whose link only starts the same gets an icon of its own.
    assert d.station_shortcut("Soul: 24/7", URL[:-1], tmp_path / "icons", logo).name == "Soul 24 7 2.lnk"
    # Too big for an icon: the player's own icon instead.
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (512).to_bytes(4, "big") * 2)
    d.station_shortcut("Big", URL + "big", tmp_path / "icons", big)
    assert calls[-1]["SP_ICON"].endswith("icon.ico")
    assert dt._file_name("con") == "con station" and dt._file_name("Con FM") == "Con FM"


def test_station_icon_needs_a_desktop(tmp_path):
    with pytest.raises(OSError, match="desktop"):
        make("linux", tmp_path).station_shortcut("X", URL, tmp_path / "icons")
    with pytest.raises(OSError):
        make("other", tmp_path).station_shortcut("X", URL, tmp_path / "icons")
