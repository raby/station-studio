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
    app = ServerApp(ConfigStore(tmp_path / "home"), ffmpeg=FFMPEG or "ffmpeg")
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
    assert st["playlist"]["file"].endswith("012.xml") and st["order"][0]["title"].startswith("Track")
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
    assert wait_for(lambda: runner.channel.queue is runner.queue and runner.xml.name == "013.xml")


def test_bad_server_goes_to_error(api, exports):
    status, body = api("/api/stations", {"name": "X", "playlist": str(exports),
                                         "server": "icecast://127.0.0.1:1/x", "password": "pw"})
    sid = body["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: api("/api/state")[1]["stations"][0]["state"] == "error")
    assert api("/api/state")[1]["stations"][0]["error"]
