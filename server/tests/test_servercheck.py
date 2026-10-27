"""Testing a server step by step (servercheck.py), against the fake servers in conftest."""
import socket
import time

from station_server import servercheck
from station_server.config import ConfigStore
from station_server.onair import ServerApp


def names(result):
    return [(s["name"], s["status"]) for s in result["steps"]]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_icecast_all_good(icecast):
    r = servercheck.diagnose(f"icecast://127.0.0.1:{icecast.port}/radio", "hackme")
    assert r["ok"] and r["kind"] == "icecast"
    assert names(r) == [("Address", "ok"), ("Port", "ok"), ("Server", "ok"), ("Password", "ok"), ("Listeners", "ok")]
    end = time.monotonic() + 3
    while icecast.mounts and time.monotonic() < end:
        time.sleep(0.05)
    assert not icecast.mounts  # left again straight away


def test_icecast_wrong_password_says_which_password(icecast):
    r = servercheck.diagnose(f"icecast://127.0.0.1:{icecast.port}/radio", "nope")
    assert not r["ok"]
    step = r["steps"][-2]
    assert step["name"] == "Password" and step["status"] == "fail" and "source password" in step["detail"]


def test_a_source_already_on_the_mount_is_left_alone(icecast):
    icecast.mounts.add("/radio")  # Winamp, say
    before = len([q for q in icecast.requests if q[0] == "PUT"])
    r = servercheck.diagnose(f"icecast://127.0.0.1:{icecast.port}/radio", "hackme")
    assert ("Password", "warn") in names(r) and r["ok"]
    assert len([q for q in icecast.requests if q[0] == "PUT"]) == before  # never tried


def test_shoutcast_checks_the_source_port_and_password(shoutcast):
    r = servercheck.diagnose(f"shoutcast://127.0.0.1:{shoutcast.port}", "hackme")
    assert ("Source port", "ok") in names(r) and ("Password", "ok") in names(r) and r["ok"]
    bad = servercheck.diagnose(f"shoutcast://127.0.0.1:{shoutcast.port}", "nope")
    assert ("Password", "fail") in names(bad) and "DNAS source password" in bad["steps"][-2]["detail"]


def test_shoutcast_with_its_source_port_closed():
    http = socket.socket()
    http.bind(("127.0.0.1", 0))
    http.listen(1)
    port = http.getsockname()[1]
    try:
        r = servercheck.diagnose(f"shoutcast://127.0.0.1:{port}", "x",
                                 fetch=lambda url, timeout=6: (200, {"Server": "Shoutcast Server v2.6"}, ""))
    finally:
        http.close()
    assert names(r)[-1][0] == "Source port" and names(r)[-1][1] == "fail"
    assert str(port + 1) in r["steps"][-1]["detail"]


def test_nothing_on_the_port():
    r = servercheck.diagnose(f"icecast://127.0.0.1:{free_port()}/radio", "x")
    assert names(r)[-1] == ("Port", "fail") and "isn't running" in r["steps"][-1]["detail"]


def test_a_name_that_doesnt_exist():
    def nowhere(*a, **k):
        raise socket.gaierror("nodename nor servname provided")
    r = servercheck.diagnose("icecast://strem.example.com:8000/radio", "x", resolve=nowhere)
    assert names(r) == [("Address", "fail")] and "spelling" in r["steps"][0]["detail"]


def test_icecast_chosen_for_a_shoutcast_server_says_so(shoutcast):
    r = servercheck.diagnose(f"icecast://127.0.0.1:{shoutcast.port}/radio", "hackme",
                             fetch=lambda url, timeout=6: (200, {"Server": "Shoutcast Server v2.6.1"}, ""))
    assert names(r)[-1] == ("Server", "fail") and "choose SHOUTcast" in r["steps"][-1]["detail"]
    assert r["kind"] == "shoutcast"


def test_which_server_answered():
    assert servercheck.identify(200, {"Server": "Icecast 2.4.4"}, "") == ("icecast", "Icecast 2.4.4")
    assert servercheck.identify(200, {}, "<title>SHOUTcast D.N.A.S. Status</title>")[0] == "shoutcast"
    assert servercheck.identify(200, {}, "SHOUTcast Server v2.6") == ("shoutcast", "SHOUTcast DNAS 2")
    assert servercheck.identify(401, {}, "", {"icestats": {"server_id": "Icecast 2.5.0"}}) == ("icecast", "Icecast 2.5.0")
    assert servercheck.identify(200, {"Server": "nginx"}, "<html>") == (None, "nginx")


def test_missing_parts_of_the_address():
    assert names(servercheck.diagnose("", None)) == [("Address", "fail")]
    r = servercheck.diagnose("icecast://h:8000", "x")
    assert "mount" in r["steps"][0]["detail"]


def test_a_station_uses_its_saved_password_and_isnt_disturbed_on_air(tmp_path, icecast):
    app = ServerApp(ConfigStore(tmp_path))
    addr = f"icecast://127.0.0.1:{icecast.port}/radio"
    st = app.save_station({"name": "Rebirth Radio", "server": addr, "password": "hackme"})
    r = app.diagnose_server({"server": addr, "id": st.id})  # no password typed: the saved one
    assert ("Password", "ok") in names(r)
    assert app.diagnose_server({"server": addr, "password": "nope"})["ok"] is False


def test_the_saved_password_only_goes_to_its_own_server(tmp_path, icecast):
    app = ServerApp(ConfigStore(tmp_path))
    addr = f"icecast://127.0.0.1:{icecast.port}/radio"
    st = app.save_station({"name": "Rebirth Radio", "server": addr, "password": "hackme"})
    other = f"icecast://127.0.0.1:{icecast.port}/other"  # typed in place of the saved address
    r = app.diagnose_server({"server": other, "id": st.id})
    assert ("Password", "fail") in names(r) and "Enter the source password" in r["steps"][-1]["detail"]


def test_a_mistyped_port_or_a_non_web_service():
    assert names(servercheck.diagnose("icecast://h:80o0/radio", "x")) == [("Address", "fail")]
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def ssh_like():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
            conn.close()
    import threading
    threading.Thread(target=ssh_like, daemon=True).start()
    try:
        r = servercheck.diagnose(f"icecast://127.0.0.1:{port}/radio", "x")
    finally:
        srv.close()
    assert ("Server", "warn") in names(r)  # reported, not a crash
