"""Also send to: the live stream going to a second server at the same time."""
import threading
import time

import pytest
from conftest import FakeIcecast, needs_ffmpeg
from test_app import api, exports, wait_for  # noqa: F401 (fixtures)

from station_server.config import clean_station
from station_server.senders import Alongside, Both, ShoutcastSender, StreamInfo


def test_the_second_server_setting():
    st = clean_station({"server": "serve://:8000?max=20", "also_server": " shoutcast://sh.uk.audio-stream.com:8012 ",
                        "also_password": "zoe"})
    assert st.also_server == "shoutcast://sh.uk.audio-stream.com:8012"
    assert clean_station({"also_password": ""}, st).also_password == "zoe"  # blank keeps it
    hidden = st.to_json(secrets=False)
    assert hidden["also_password"] == "" and hidden["has_also_password"]
    with pytest.raises(ValueError, match="SHOUTcast or Icecast"):
        clean_station({"also_server": "serve://:8100", "also_password": "x"})
    with pytest.raises(ValueError, match="different stream"):
        clean_station({"server": "shoutcast://h:8000", "password": "x",
                       "also_server": "shoutcast://H:8000?sid=1", "also_password": "y"})
    with pytest.raises(ValueError, match="password"):
        clean_station({"also_server": "shoutcast://h:8000"})
    assert clean_station({"also_server": ""}).also_server == ""


class Slow:
    """A second server that answers slowly, then not at all."""
    describe = "slow server"
    connected = True
    problem = ""

    def __init__(self):
        self.got: list[bytes] = []
        self.go = threading.Event()
        self.closed = False

    def send(self, data):
        self.go.wait(5)
        if data == b"boom":
            raise RuntimeError("unexpected")
        self.got.append(data)

    def set_title(self, title):
        self.go.wait(5)

    def close(self):
        self.closed = True


def test_a_slow_second_server_never_holds_up_the_main_one(monkeypatch):
    monkeypatch.setattr(Alongside, "WAITING", 3)  # bytes
    slow = Slow()
    main: list[bytes] = []
    both = Both(type("Main", (), {"send": lambda self, d: main.append(d), "set_title": lambda self, t: None,
                                  "close": lambda self: None, "describe": "main"})(), Alongside(slow))
    started = time.monotonic()
    for n in range(10):
        both.send(b"%d" % n)
    both.set_title("Song")
    assert time.monotonic() - started < 1  # didn't wait for it
    assert len(main) == 10 and both.describe == "main"
    slow.go.set()
    # It fell behind: the oldest waiting audio was dropped, the newest kept.
    assert wait_for(lambda: slow.got and slow.got[-1] == b"9")
    assert len(slow.got) <= 4
    both.also.send(b"boom")  # a surprise from it doesn't stop it
    both.also.send(b"after")
    assert wait_for(lambda: slow.got[-1] == b"after")
    both.close()
    assert wait_for(lambda: slow.closed)


def test_closing_doesnt_wait_for_a_stuck_second_server():
    slow = Slow()
    also = Alongside(slow)
    also.send(b"stuck")
    started = time.monotonic()
    also.close()
    assert time.monotonic() - started < 3
    also.send(b"too late")  # ignored once closed
    slow.go.set()
    assert wait_for(lambda: slow.closed)
    assert b"too late" not in slow.got


def test_why_it_cant_connect_is_kept_for_the_page():
    s = ShoutcastSender("127.0.0.1", 1, "pw", StreamInfo(name="x"))
    s.send(b"x")
    assert not s.connected and s.problem


@pytest.fixture
def second():
    server = FakeIcecast(password="zoe")
    yield server
    server.close()


def station(api):  # noqa: F811
    return api("/api/state")[1]["stations"][0]


@needs_ffmpeg
def test_live_goes_to_both_servers_but_rehearsals_dont(api, exports, icecast, second):  # noqa: F811
    spare = FakeIcecast()
    try:
        sid = api("/api/stations", {
            "name": "Rebirth Radio", "playlist": str(exports), "start_mode": "top",
            "server": f"icecast://localhost:{icecast.port}/radio", "password": "hackme",
            "test_server": f"icecast://localhost:{spare.port}/test",
            "also_server": f"icecast://localhost:{second.port}/zoe", "also_password": "zoe"})[1]["id"]
        checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
        assert checks["Second server"]["status"] == "ok"
        api(f"/api/stations/{sid}/on", {"on": True, "rehearse": True})
        assert wait_for(lambda: station(api)["state"] == "on")
        assert spare.got_audio.wait(10)
        assert station(api)["also"] is None and not second.got_audio.is_set()
        api(f"/api/stations/{sid}/on", {"on": True})
        assert wait_for(lambda: station(api)["state"] == "on" and not station(api)["rehearsal"])
        assert icecast.got_audio.wait(10) and second.got_audio.wait(10)
        assert wait_for(lambda: second.titles and icecast.titles)
        assert wait_for(lambda: (station(api)["also"] or {}).get("connected"))
        assert station(api)["also"]["server"].endswith("/zoe")
        checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
        assert checks["Second server"]["status"] == "ok" and "too" in checks["Second server"]["detail"]
        # Test the second server while on air to it: the saved password, and not "in use".
        r = api("/api/check/diagnose", {"server": f"icecast://localhost:{second.port}/zoe", "id": sid,
                                        "also": True})[1]
        assert r["ok"], r
        api(f"/api/stations/{sid}/on", {"on": False})
        assert wait_for(lambda: "/zoe" not in second.mounts)
    finally:
        spare.close()


@needs_ffmpeg
def test_a_second_server_that_cant_be_reached_doesnt_stop_the_station(api, exports, icecast):  # noqa: F811
    sid = api("/api/stations", {
        "name": "Rebirth Radio", "playlist": str(exports), "start_mode": "top",
        "server": f"icecast://localhost:{icecast.port}/radio", "password": "hackme",
        "also_server": "shoutcast://127.0.0.1:1", "also_password": "zoe"})[1]["id"]
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Second server"]["status"] == "fail"
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: station(api)["state"] == "on") and icecast.got_audio.wait(10)
    assert wait_for(lambda: (station(api)["also"] or {}).get("problem"))
    assert not station(api)["also"]["connected"] and station(api)["connected"]
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Second server"]["status"] == "fail" and "keeps trying" in checks["Second server"]["detail"]
    api(f"/api/stations/{sid}/on", {"on": False})


def test_the_saved_second_password_only_goes_to_the_second_server(api, second):  # noqa: F811
    sid = api("/api/stations", {"name": "R", "server": "serve://:8123?max=5",
                                "also_server": f"icecast://localhost:{second.port}/zoe",
                                "also_password": "zoe"})[1]["id"]
    ok = api("/api/check/diagnose", {"server": f"icecast://localhost:{second.port}/zoe", "id": sid, "also": True})[1]
    assert ok["ok"], ok
    other = api("/api/check/diagnose", {"server": f"icecast://localhost:{second.port}/other", "id": sid,
                                        "also": True})[1]
    assert not other["ok"]
