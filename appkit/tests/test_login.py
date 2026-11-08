"""Start at login (station_appkit/login.py)."""
import os
import plistlib
import sys

import pytest

from station_appkit import App
from station_appkit.login import Login, RunKey, current_platform, desktop_exec, not_installed

APP = App(name="Station Test", slug="StationTest", tag_prefix="test-v", version="1.2.0", port=8123)
CMD = ["/Applications/Station Test.app/Contents/MacOS/station-test-server", "--no-browser"]


class FakeRunKey:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def login(tmp_path, platform, command=CMD, **kw):
    return Login(APP, command, agent_id="io.github.raby.station-test", autostart_name="station-test.desktop",
                 platform=platform, home=tmp_path, **kw)


def test_platforms():
    assert [current_platform(p) for p in ("darwin", "mac", "win32", "windows", "linux", "freebsd14", "sunos5")] \
        == ["mac", "mac", "windows", "windows", "linux", "linux", "other"]


def test_mac_agent_quit_ends_it(tmp_path):
    lg = login(tmp_path, "darwin")
    assert lg.supported and lg.enabled() is False
    lg.set(True)
    with lg.agent().open("rb") as fh:
        job = plistlib.load(fh)
    assert lg.agent() == tmp_path / "Library/LaunchAgents/io.github.raby.station-test.plist"
    assert job == {"Label": "io.github.raby.station-test", "ProgramArguments": CMD, "RunAtLoad": True,
                   "KeepAlive": False}
    assert lg.enabled() is True
    lg.set(False)
    assert lg.enabled() is False and not lg.agent().exists()
    lg.set(False)  # already off: fine


def test_mac_agent_that_restarts_after_a_crash(tmp_path):
    lg = login(tmp_path, "mac", restart_after_crash=True, env={"STATION_TEST_CHILD": "1"})
    lg.set(True)
    job = plistlib.loads(lg.agent().read_bytes())
    assert job["KeepAlive"] == {"SuccessfulExit": False} and job["ThrottleInterval"] == 30
    assert job["EnvironmentVariables"] == {"STATION_TEST_CHILD": "1"}


def test_windows_run_key(tmp_path):
    key = FakeRunKey()
    lg = login(tmp_path, "win32", [r"C:\Apps\Station Test.exe", "--no-browser"], registry=key)
    assert lg.enabled() is False
    lg.set(True)
    assert key.values == {"Station Test": '"C:\\Apps\\Station Test.exe" --no-browser'}
    assert lg.enabled() is True
    lg.set(False)
    assert key.values == {} and lg.enabled() is False


def test_linux_autostart_quotes_what_needs_it(tmp_path):
    cmd = ["/home/rob/my apps/station-test", "--title", 'Rob\'s "best" $HOME 100%', "back\\slash"]
    lg = login(tmp_path, "linux", cmd)
    lg.set(True)
    entry = tmp_path / ".config/autostart/station-test.desktop"
    text = entry.read_text()
    assert "Name=Station Test\n" in text and "NoDisplay=true" in text and "X-GNOME-Autostart-enabled=true" in text
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert exec_line == ('Exec="/home/rob/my apps/station-test" --title '
                         '"Rob\'s \\\\"best\\\\" \\\\$HOME 100%%" "back\\\\\\\\slash"')
    assert lg.enabled() is True
    lg.set(False)
    assert not entry.exists()


def test_linux_autostart_follows_xdg_config_home(tmp_path):
    config = tmp_path / "cfg"
    lg = login(tmp_path, "linux", environ={"XDG_CONFIG_HOME": str(config)})
    assert lg.autostart() == config / "autostart/station-test.desktop"
    # A relative one isn't a place to look.
    assert login(tmp_path, "linux", environ={"XDG_CONFIG_HOME": "cfg"}).autostart() \
        == tmp_path / ".config/autostart/station-test.desktop"


def test_an_app_can_write_its_own_linux_entry(tmp_path):
    lg = login(tmp_path, "linux", linux_entry=lambda: "[Desktop Entry]\nName=Mine\n")
    lg.set(True)
    assert lg.autostart().read_text() == "[Desktop Entry]\nName=Mine\n"
    assert lg.refresh() is False


def test_never_from_the_disk_image(tmp_path):
    assert not_installed("/Volumes/Station Test/Station Test.app/Contents/MacOS/x")
    assert not_installed("/private/var/folders/x/AppTranslocation/y/Station Test.app/Contents/MacOS/x")
    assert not not_installed(CMD[0])
    lg = login(tmp_path, "darwin", ["/Volumes/Station Test/Station Test.app/Contents/MacOS/x"])
    with pytest.raises(ValueError, match="Move Station Test into your Applications folder first"):
        lg.set(True)
    lg.set(False)  # turning it off always works
    assert lg.enabled() is False


def test_where_it_cant(tmp_path):
    for lg in (login(tmp_path, "linux", None), login(tmp_path, "linux", []), login(tmp_path, "sunos5")):
        assert not lg.supported and lg.enabled() is None
        with pytest.raises(ValueError, match="isn't available"):
            lg.set(True)


def test_refresh_brings_an_older_or_other_copys_entry_up_to_date(tmp_path):
    old = login(tmp_path, "darwin", ["/Users/rob/.local/bin/python3", "-m", "station_test"])
    new = login(tmp_path, "darwin")
    assert new.refresh() is False  # off: left off
    old.set(True)
    assert new.refresh() is True
    assert plistlib.loads(new.agent().read_bytes())["ProgramArguments"] == CMD
    assert new.refresh() is False  # already right
    # A job written before it said KeepAlive is brought up to date too.
    new.agent().write_bytes(plistlib.dumps({"Label": "io.github.raby.station-test", "ProgramArguments": CMD,
                                            "RunAtLoad": True}))
    assert new.refresh() is True
    new.agent().write_bytes(b"not a plist")
    assert new.refresh() is True and plistlib.loads(new.agent().read_bytes())["KeepAlive"] is False
    # From the disk image it leaves things as they are.
    assert login(tmp_path, "darwin", ["/Volumes/X/x"]).refresh() is False

    key = FakeRunKey()
    login(tmp_path, "win32", [r"C:\old\x.exe"], registry=key).set(True)
    assert login(tmp_path, "win32", [r"C:\Apps\x.exe"], registry=key).refresh() is True
    assert key.values["Station Test"] == r"C:\Apps\x.exe"

    login(tmp_path, "linux", ["/old/x"]).set(True)
    assert login(tmp_path, "linux", ["/new/x"]).refresh() is True
    assert "Exec=/new/x" in login(tmp_path, "linux").autostart().read_text()


def test_desktop_exec_leaves_plain_words_alone():
    assert desktop_exec(["/usr/bin/python3", "-P", "-m", "station_test", "--app"]) == \
        "/usr/bin/python3 -P -m station_test --app"
    assert desktop_exec(["a", ""]) == 'a ""'


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own registry")
def test_the_real_run_key(tmp_path):
    """Against HKCU\\...\\Run itself (the Windows CI runner's), under a
    name nothing else uses, removed again."""
    name = f"Station Test {os.getpid()}"
    app = App(name=name, slug="StationTest", tag_prefix="test-v", version="1.2.0", port=8123)
    lg = Login(app, [r"C:\Apps\Station Test.exe", "--no-browser"], agent_id="x", autostart_name="x",
               platform="win32", home=tmp_path)
    try:
        assert lg.enabled() is False
        lg.set(True)
        assert lg.enabled() is True
        assert RunKey().get(name) == '"C:\\Apps\\Station Test.exe" --no-browser'
        assert lg.refresh() is False
        RunKey().set(name, r"C:\old\x.exe")
        assert lg.refresh() is True
    finally:
        lg.set(False)
    assert lg.enabled() is False and RunKey().get(name) is None
    RunKey().delete(name)  # already gone: fine
