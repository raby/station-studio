"""The app: stations on air from the settings, and the page's API."""
import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from datetime import date

import pytest

from conftest import FFMPEG, needs_ffmpeg, station_export, write_wav
from station_server.config import ConfigStore
from station_server.desktop import Login
from station_server.onair import ServerApp
from station_server.web import make_server


def wait_for(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def exports(tmp_path):
    """A station-studio exports folder holding weeks 11 and 12."""
    files = [write_wav(tmp_path / "music" / f"{i}.wav", seconds=0.6, freq=300 + 100 * i) for i in range(3)]
    folder = tmp_path / "exports" / "rebirth-classics"
    folder.mkdir(parents=True)
    xml = station_export(tmp_path, files, titles=[("Artist", f"Track {i}") for i in range(3)])
    shutil.copy(xml, folder / "011.xml")
    shutil.copy(xml, folder / "012.xml")
    return folder


@pytest.fixture
def api(tmp_path):
    app = ServerApp(ConfigStore(tmp_path / "home"), ffmpeg=FFMPEG or "ffmpeg",
                    login=Login(platform="linux", home=tmp_path / "user"))
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, data=None, headers=None):
        hdrs = {"X-Station-Server": "1", "Content-Type": "application/json"} if data is not None else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if "json" in resp.headers["Content-Type"] else body)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    call.app = app
    yield call
    app.shutdown()
    server.shutdown()


def test_page_and_guards(api):
    status, body = api("/")
    assert status == 200 and b"Station Server" in body
    assert api("/api/state")[1]["stations"] == []
    status, body = api("/api/stations", {"name": "x"}, headers={"X-Station-Server": "0"})
    assert status == 403
    status, body = api("/api/state", headers={"Host": "evil.example"})
    assert status == 403
    assert api("/../config.json")[0] == 404


def test_start_at_login_setting(api):
    assert api("/api/state")[1]["login"] is False
    assert api("/api/settings", {"login": True})[1]["login"] is True
    assert api("/api/settings", {"open_window": False})[1]["login"] is True  # left alone
    assert api("/api/settings", {"login": False})[1]["login"] is False


def test_add_check_and_delete_a_station(api, exports):
    status, body = api("/api/stations", {"name": "Rebirth Classics", "playlist": str(exports),
                                         "server": "icecast://localhost:1/classics", "password": "pw"})
    assert status == 200
    sid = body["id"]
    st = body["stations"][0]["station"]
    assert st["has_password"] and st["password"] == ""  # never sent to the page
    status, found = api("/api/check/playlist", {"playlist": str(exports)})
    assert status == 200 and found["file"].endswith("012.xml") and found["tracks"] == 3
    status, err = api("/api/check/playlist", {"playlist": str(exports / "nope.xml")})
    assert status == 400
    status, err = api("/api/stations", {"bitrate": 5})
    assert status == 400 and "320" in err["error"]
    assert api(f"/api/stations/{sid}/delete", {})[1]["stations"] == []
    assert api("/api/stations/nosuch/skip", {})[0] == 404


@needs_ffmpeg
def test_on_air_titles_play_log_and_resume(api, exports, icecast, monkeypatch):
    import station_player.playlog
    monkeypatch.setattr(station_player.playlog, "MIN_SECONDS", 0)  # the test tracks are short
    status, body = api("/api/stations", {
        "name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
        "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
    })
    sid = body["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: api("/api/state")[1]["stations"][0]["state"] == "on")
    assert icecast.got_audio.wait(10) and icecast.got_title.wait(10)
    assert icecast.titles[0] == "Artist - Track 0"
    st = api("/api/state")[1]["stations"][0]
    assert st["file"].endswith("012.xml") and st["order"][0]["title"].startswith("Track")
    # The test connection doesn't knock the station off the server.
    status, check = api("/api/check/server", {"id": sid, "server": f"icecast://localhost:{icecast.port}/classics"})
    assert status == 200 and check["on_air"] is True
    runner = api.app.runners[sid]
    assert wait_for(lambda: runner.playlog.entries(date.today(), date.today()))
    status, csv = api(f"/api/stations/{sid}/playlog.csv?from={date.today()}&to={date.today()}")
    assert status == 200 and b"Track 0" in csv
    # On air is remembered, so the next start puts it back on.
    again = ConfigStore(api.app.store.home)
    assert again.config.station(sid).on_air
    api(f"/api/stations/{sid}/on", {"on": False})
    assert api("/api/state")[1]["stations"][0]["state"] == "off"


@needs_ffmpeg
def test_a_new_week_is_picked_up(api, exports, icecast, monkeypatch):
    import station_server.onair as onair
    monkeypatch.setattr(onair, "WATCH_SECONDS", 0.2)
    status, body = api("/api/stations", {
        "name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
        "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
    })
    sid = body["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    runner = api.app.runners[sid]
    assert wait_for(lambda: runner.state == "on")
    shutil.copy(exports / "012.xml", exports / "013.xml")
    assert wait_for(lambda: runner.xml.name == "013.xml")
    # Until the playing track ends the page shows 013 as next.
    st = runner.status()
    if runner.channel.queue is not runner.queue:
        assert st["playlist"]["next"]["name"] == runner.queue.name
    assert wait_for(lambda: runner.channel.queue is runner.queue)
    assert "next" not in runner.status()["playlist"]


def test_bad_server_goes_to_error(api, exports):
    status, body = api("/api/stations", {"name": "X", "playlist": str(exports),
                                         "server": "icecast://127.0.0.1:1/x", "password": "pw"})
    sid = body["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: api("/api/state")[1]["stations"][0]["state"] == "error")
    assert api("/api/state")[1]["stations"][0]["error"]
    # Saving the station clears the old problem.
    api(f"/api/stations/{sid}", {"server": "icecast://127.0.0.1:2/x"})
    st = api("/api/state")[1]["stations"][0]
    assert st["state"] == "off" and st["error"] is None


@needs_ffmpeg
def test_two_stations_on_air_at_once(api, exports, icecast, tmp_path):
    other = tmp_path / "exports" / "rebirth-radio"
    other.mkdir()
    files = [write_wav(tmp_path / "music2" / f"{i}.wav", seconds=0.6, freq=700 + 50 * i) for i in range(2)]
    xml = station_export(tmp_path / "radio", files, name="Rebirth Radio 012",
                         titles=[("Radio", f"Song {i}") for i in range(2)])
    shutil.copy(xml, other / "012.xml")
    ids = []
    for name, folder, mount in (("Rebirth Classics", exports, "classics"), ("Rebirth Radio", other, "radio")):
        status, body = api("/api/stations", {
            "name": name, "playlist": str(folder), "start_mode": "top",
            "server": f"icecast://localhost:{icecast.port}/{mount}", "password": "hackme",
        })
        ids.append(body["id"])
    for sid in ids:
        api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: [s["state"] for s in api("/api/state")[1]["stations"]] == ["on", "on"])
    assert wait_for(lambda: any(t.startswith("Radio - ") for t in icecast.titles)
                    and any(t.startswith("Artist - ") for t in icecast.titles))
    mounts = {target for method, target, _ in icecast.requests if method == "PUT"}
    assert mounts == {"/classics", "/radio"}
    # One off air leaves the other on.
    api(f"/api/stations/{ids[0]}/on", {"on": False})
    assert [s["state"] for s in api("/api/state")[1]["stations"]] == ["off", "on"]


def test_window_route_opens_the_mac_apps_own_window(api, monkeypatch, tmp_path):
    status, body = api("/api/window", {})
    assert status == 409  # not the Mac app
    assert api("/api/state")[1]["mac_app"] is False
    import station_server.window as window
    opened = []
    monkeypatch.setattr(window, "mac_app_bundle", lambda: tmp_path / "Station Server.app")
    monkeypatch.setattr(window, "open_ui", lambda url, home, own, app=None: opened.append(app))
    assert api("/api/window", {})[0] == 200
    assert wait_for(lambda: opened == [tmp_path / "Station Server.app"])


def test_shows_are_saved_and_checked(api, exports):
    show = {"name": "Saturday Night Mix", "source": "/music/mix.mp3", "days": [5], "time": "20:00"}
    status, body = api("/api/stations", {"name": "X", "playlist": str(exports), "shows": [show]})
    assert status == 200
    st = body["stations"][0]
    assert st["station"]["shows"][0]["time"] == "20:00" and st["shows"][0]["label"] == "Sat 20:00"
    status, err = api("/api/stations", {"name": "Y", "shows": [{**show, "days": []}]})
    assert status == 400 and "days" in err["error"]


@needs_ffmpeg
def test_a_show_goes_out_then_the_music_returns(api, exports, icecast, tmp_path):
    from datetime import datetime

    from station_server.schedule import Show
    recording = write_wav(tmp_path / "shows" / "Saturday Night Mix.wav", seconds=5, freq=880)
    status, body = api("/api/stations", {
        "name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
        "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
    })
    sid = body["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    runner = api.app.runners[sid]
    assert wait_for(lambda: runner.state == "on" and runner.channel.now)
    now = datetime.now().astimezone()
    runner._start_show(Show("mix", "Saturday Night Mix", str(recording), (now.weekday(),), now.hour, now.minute), now)
    assert wait_for(lambda: (runner.status()["now"] or {}).get("show") == "Saturday Night Mix")
    order = runner.status()["order"]
    assert order[0]["show"] == "Saturday Night Mix" and order[1]["title"].startswith("Track")
    assert wait_for(lambda: "Saturday Night Mix" in icecast.titles)
    assert wait_for(lambda: not runner.channel.in_show and runner.show_on is None)
    assert wait_for(lambda: icecast.titles[-1].startswith("Artist - Track"))


@needs_ffmpeg
def test_back_on_air_part_way_through_a_show(api, exports, icecast, tmp_path, monkeypatch):
    from datetime import datetime

    from station_server.onair import StationRunner
    recording = write_wav(tmp_path / "shows" / "Long Show.wav", seconds=2.0, freq=660)
    now = datetime.now().astimezone()
    status, body = api("/api/stations", {
        "name": "Rebirth Classics", "playlist": str(exports),
        "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
        "shows": [{"name": "Long Show", "source": str(recording), "days": [now.weekday()],
                   "time": now.strftime("%H:%M")}],
    })
    monkeypatch.setattr(StationRunner, "show_length", lambda self, show: 3600.0)  # an hour-long show
    sid = body["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    runner = api.app.runners[sid]
    assert wait_for(lambda: runner.state == "on" and runner.channel.now)
    assert runner.channel.in_show and runner.status()["now"]["show"] == "Long Show"
    assert wait_for(lambda: "Long Show" in icecast.titles)
