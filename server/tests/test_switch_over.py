"""Switching over: rehearsing on a test stream, going live, and the Ready check."""
from datetime import date
from types import SimpleNamespace

import pytest

from conftest import FakeIcecast, needs_ffmpeg
from station_server.config import Station, clean_station
from station_server.senders import someone_sending
from station_server.updates import is_newer
from test_app import api, exports, wait_for  # noqa: F401 (fixtures)


def test_the_test_stream_setting():
    st = clean_station({"server": "shoutcast://81.150.147.206:8002", "password": "live",
                        "test_server": "shoutcast://81.150.147.206:8002?sid=2"})
    assert st.test_server.endswith("sid=2")
    kept = clean_station({"test_password": "spare"}, st)
    assert clean_station({"test_password": ""}, kept).test_password == "spare"  # blank keeps it
    hidden = kept.to_json(secrets=False)
    assert hidden["test_password"] == "" and hidden["has_test_password"]
    with pytest.raises(ValueError):
        clean_station({"server": "shoutcast://h:8000", "test_server": "shoutcast://h:8000"})
    with pytest.raises(ValueError):
        clean_station({"test_server": "h:8002"})
    assert is_newer("0.10.0", "0.9.0")


def test_someone_else_on_the_server_is_spotted():
    assert someone_sending(SimpleNamespace(count_listeners=lambda: {"source": True}))
    assert not someone_sending(SimpleNamespace(count_listeners=lambda: {"source": False}))

    def fails():
        raise OSError("no 7.html")
    assert not someone_sending(SimpleNamespace(count_listeners=fails))


@pytest.fixture
def spare():
    server = FakeIcecast()
    yield server
    server.close()


def add_station(api, exports, live, spare=None):  # noqa: F811
    data = {"name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
            "server": f"icecast://localhost:{live.port}/classics", "password": "hackme"}
    if spare:
        data["test_server"] = f"icecast://localhost:{spare.port}/test"
    return api("/api/stations", data)[1]["id"]


def station(api):  # noqa: F811
    return api("/api/state")[1]["stations"][0]


@needs_ffmpeg
def test_rehearse_on_the_test_stream_then_go_live(api, exports, icecast, spare, monkeypatch):  # noqa: F811
    import station_player.playlog
    monkeypatch.setattr(station_player.playlog, "MIN_SECONDS", 0)
    sid = add_station(api, exports, icecast)
    status, body = api(f"/api/stations/{sid}/on", {"on": True, "rehearse": True})
    assert status == 400 and "test stream" in body["error"]
    sid = add_station(api, exports, icecast, spare)
    api(f"/api/stations/{api('/api/state')[1]['stations'][0]['station']['id']}/delete", {})
    api(f"/api/stations/{sid}/on", {"on": True, "rehearse": True})
    assert wait_for(lambda: station(api)["state"] == "on")
    assert station(api)["rehearsal"] and station(api)["server"].endswith("/test")
    assert spare.got_audio.wait(10) and not icecast.got_audio.is_set()
    runner = api.app.runners[sid]
    assert not runner.station.on_air  # not put back on air after a restart, no alerts
    assert wait_for(lambda: len(spare.titles) >= 2)
    assert not runner.playlog.entries(date.today(), date.today())  # not in the play log
    # Go live: the station moves to the live server.
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: station(api)["state"] == "on" and not station(api)["rehearsal"])
    assert icecast.got_audio.wait(10) and runner.station.on_air
    assert wait_for(lambda: "/test" not in spare.mounts)
    assert wait_for(lambda: runner.playlog.entries(date.today(), date.today()))
    api(f"/api/stations/{sid}/on", {"on": False})


@needs_ffmpeg
def test_winamp_still_on_the_server_is_reported_not_knocked_off(api, exports, icecast):  # noqa: F811
    sid = add_station(api, exports, icecast)
    icecast.mounts.add("/classics")  # Winamp is sending
    status, body = api("/api/check/server", {"id": sid})
    assert status == 502 and "already sending" in body["error"]
    assert not [r for r in icecast.requests if r[0] == "PUT"]  # never tried
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: station(api)["state"] == "waiting")
    assert "already sending" in station(api)["error"] and "Winamp" in station(api)["error"]
    api(f"/api/stations/{sid}/on", {"on": False})


def test_ready_check(api, exports, icecast, spare, tmp_path):  # noqa: F811
    sid = add_station(api, exports, icecast, spare)
    status, body = api(f"/api/stations/{sid}/check", {})
    assert status == 200
    checks = {c["name"]: c for c in body["checks"]}
    assert checks["Playlist"]["status"] == "ok" and "all 3 tracks" in checks["Playlist"]["detail"]
    assert checks["Live server"]["status"] == "ok" and "accepted the password" in checks["Live server"]["detail"]
    assert checks["Test stream"]["status"] == "ok"
    assert checks["Fallback"]["status"] == "warn"
    assert checks["Start at login"]["status"] == "warn" and checks["Alerts"]["status"] == "warn"
    # A problem shows up as one to fix.
    api(f"/api/stations/{sid}", {"fallback": str(tmp_path / "nowhere"), "password": "wrong"})
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Fallback"]["status"] == "fail" and checks["Live server"]["status"] == "fail"
    assert api("/api/stations/nope/check", {})[0] == 404


def test_a_station_with_nothing_set_fails_the_check(api):  # noqa: F811
    sid = api("/api/stations", {"name": "New"})[1]["id"]
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Playlist"]["status"] == "fail" and checks["Live server"]["status"] == "fail"
    assert "Test stream" not in checks


def test_rehearsal_isnt_put_back_on_air_at_start_up(tmp_path):
    from station_server.config import ConfigStore
    from station_server.onair import ServerApp
    store = ConfigStore(tmp_path)
    store.config.stations.append(Station(id="a", name="A", on_air=False, test_server="icecast://h:1/t"))
    store.save()
    app = ServerApp(ConfigStore(tmp_path), ffmpeg="ffmpeg")
    app.resume()
    assert app.runners["a"].state == "off" and not app.runners["a"].rehearsal
    app.shutdown()


def test_a_fallback_folder_includes_its_subfolders(tmp_path):
    from conftest import write_wav
    from station_server.onair import source_queue
    write_wav(tmp_path / "music" / "B Artist" / "Album" / "1.wav", seconds=0.3)
    write_wav(tmp_path / "music" / "A Artist" / "2.wav", seconds=0.3)
    (tmp_path / "music" / "notes.txt").write_text("not music")
    (tmp_path / "music" / "A Artist" / "._2.wav").write_bytes(b"Mac metadata, not audio")
    queue = source_queue("Fallback", str(tmp_path / "music"), Station(), "fallback")
    assert [t.path.name for t in queue.tracks] == ["2.wav", "1.wav"]


def test_winamp_live_is_a_note_in_the_ready_check_not_a_failure(api, exports, icecast, spare):  # noqa: F811
    sid = add_station(api, exports, icecast, spare)
    icecast.mounts.add("/classics")  # Winamp on the live server while rehearsing
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Live server"]["status"] == "warn" and "already sending" in checks["Live server"]["detail"]
    assert checks["Test stream"]["status"] == "ok"
    assert not [r for r in icecast.requests if r[0] == "PUT"]
