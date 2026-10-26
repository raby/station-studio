"""Streaming from this computer: Station Server as the station's server (stream.py)."""
import json
import socket
import time
import urllib.request

import pytest

from station_server import stream
from station_server.config import Station, clean_station
from station_server.senders import SenderError, ServeSender, StreamInfo, sender_from_url, someone_sending
from station_server.stream import StreamServer


@pytest.fixture
def server():
    srv = StreamServer(0, name="Rebirth Classics", genre="Oldies", bitrate=128, most=2)
    srv.start()
    yield srv
    srv.stop()


def connect(port, path="/", headers=""):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(f"GET {path} HTTP/1.0\r\nHost: radio.example:{port}\r\n{headers}\r\n".encode())
    return sock


def read_head(sock):
    data = b""
    while b"\r\n\r\n" not in data:
        data += sock.recv(1)
    return data.decode()


def read_exactly(sock, n):
    data = b""
    while len(data) < n:
        part = sock.recv(n - len(data))
        assert part, "the stream ended"
        data += part
    return data


def wait_for(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while not cond():
        assert time.monotonic() < end
        time.sleep(0.01)


def test_listeners_get_the_burst_then_the_stream(server):
    server.publish(b"A" * 1000)
    sock = connect(server.port, "/;")
    head = read_head(sock)
    assert head.startswith("HTTP/1.0 200 OK") and "Content-Type: audio/mpeg" in head
    assert "icy-name: Rebirth Classics" in head and "icy-br: 128" in head and "icy-metaint" not in head
    assert read_exactly(sock, 1000) == b"A" * 1000  # what was already sent, so it starts at once
    wait_for(lambda: server.listening == 1)
    server.publish(b"B" * 500)
    assert read_exactly(sock, 500) == b"B" * 500
    assert server.counts() == {"listeners": 1, "peak": 1, "max": 2, "source": True}
    server.stop()  # off air: everyone's stream ends
    sock.settimeout(3)
    assert sock.recv(10) == b""


def test_song_titles_for_players_that_ask(server, monkeypatch):
    monkeypatch.setattr(stream, "METAINT", 100)
    server.set_title("Madness - One Step Beyond")
    sock = connect(server.port, "/stream", "Icy-MetaData: 1\r\n")
    assert "icy-metaint: 100" in read_head(sock)
    wait_for(lambda: server.listening == 1)
    server.publish(b"x" * 250)
    assert read_exactly(sock, 100) == b"x" * 100
    blocks = read_exactly(sock, 1)[0]
    meta = read_exactly(sock, blocks * 16)
    assert meta.rstrip(b"\0") == b"StreamTitle='Madness - One Step Beyond';"
    assert read_exactly(sock, 100) == b"x" * 100
    assert read_exactly(sock, 1) == b"\0"  # unchanged: an empty block
    server.set_title("The Specials - Ghost Town")
    server.publish(b"y" * 100)
    assert read_exactly(sock, 50) == b"x" * 50 and read_exactly(sock, 50) == b"y" * 50
    blocks = read_exactly(sock, 1)[0]
    assert read_exactly(sock, blocks * 16).rstrip(b"\0") == b"StreamTitle='The Specials - Ghost Town';"
    sock.close()


def test_full_means_the_next_listener_is_turned_away(server):
    socks = [connect(server.port) for _ in range(2)]
    for s in socks:
        read_head(s)
    wait_for(lambda: server.listening == 2)
    third = connect(server.port)
    assert read_head(third).startswith("HTTP/1.0 503")
    for s in socks + [third]:
        s.close()
    wait_for(lambda: server.listening == 0)
    assert server.counts()["peak"] == 2


def test_a_listener_who_cant_keep_up_is_let_go(server, monkeypatch):
    monkeypatch.setattr(stream, "BACKLOG", 3)
    slow = stream._Listener("10.0.0.9", False)  # nobody reads its queue
    server._listeners.append(slow)
    for _ in range(5):
        server.publish(b"z")
    assert server.listening == 0


def get(port, path, accept="*/*"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.headers.get("Content-Type"), resp.read().decode()


def test_it_answers_like_shoutcast_and_icecast(server):
    server.set_title("Madness - Baggy Trousers")
    assert get(server.port, "/7.html")[1] == "<html><body>0,1,0,2,0,128,Madness - Baggy Trousers</body></html>"
    assert get(server.port, "/currentsong")[1] == "Madness - Baggy Trousers"
    stats = json.loads(get(server.port, "/stats?sid=1&json=1")[1])
    assert stats["currentlisteners"] == 0 and stats["maxlisteners"] == 2 and stats["servertitle"] == "Rebirth Classics"
    assert "<SONGTITLE>Madness - Baggy Trousers</SONGTITLE>" in get(server.port, "/stats?sid=1")[1]
    ice = json.loads(get(server.port, "/status-json.xsl")[1])["icestats"]["source"]
    assert ice["listeners"] == 0 and ice["title"] == "Madness - Baggy Trousers"
    ctype, pls = get(server.port, "/listen.pls")
    assert ctype == "audio/x-scpls" and f"File1=http://127.0.0.1:{server.port}/;" in pls
    ctype, page = get(server.port, "/", accept="text/html,application/xhtml+xml")
    assert ctype.startswith("text/html") and "<audio" in page and "Rebirth Classics" in page
    # The counts Station Server reads from a DNAS work here too
    from station_server.senders import parse_7html, parse_icestats
    assert parse_7html(get(server.port, "/7.html")[1])["max"] == 2
    assert parse_icestats(json.loads(get(server.port, "/status-json.xsl")[1]), "/stream")["source"]


def test_a_port_in_use_says_so():
    first = StreamServer(0)
    first.start()
    try:
        sender = ServeSender(first.port, StreamInfo(name="Classics"))
        with pytest.raises(SenderError, match=f"Port {first.port} is in use"):
            sender.connect()
        assert not someone_sending(sender)  # not "Winamp is still on the server"
    finally:
        first.stop()


def test_serve_addresses():
    sender = sender_from_url("serve://:8123?max=7", StreamInfo(name="Classics", bitrate=96))
    assert isinstance(sender, ServeSender) and sender.server.most == 7 and sender.server.bitrate == 96
    assert sender.describe == "this computer, port 8123"
    with pytest.raises(ValueError):
        sender_from_url("serve://", StreamInfo())
    st = clean_station({"name": "Classics", "server": "serve://:8000?max=20", "test_server": "serve://:8100"})
    assert st.server == "serve://:8000?max=20" and st.password == ""
    for bad in ("serve://:80", "serve://:8000?max=0", "ftp://x:1"):
        with pytest.raises(ValueError):
            clean_station({"name": "Classics", "server": bad})
    with pytest.raises(ValueError, match="live shows port"):
        clean_station({"name": "Classics", "server": "serve://:8010", "live": True, "live_password": "secret123"})
    assert Station().server == ""


def test_stopping_frees_the_port_at_once():
    first = StreamServer(0)
    first.start()
    port = first.port
    first.stop()  # the Ready check starts and stops it, just before going on air
    again = StreamServer(port)
    again.start()
    again.stop()


def test_connections_holding_back_their_request_are_limited(server, monkeypatch):
    monkeypatch.setattr(stream, "MOST_WAITING", 2)
    idle = [socket.create_connection(("127.0.0.1", server.port), timeout=5) for _ in range(2)]
    wait_for(lambda: server._waiting == 2)
    extra = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    assert extra.recv(10) == b""  # turned away
    for s in idle:
        s.close()
    wait_for(lambda: server._waiting == 0)
    assert get(server.port, "/currentsong")[1] == ""
