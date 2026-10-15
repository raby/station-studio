import http.client
import socket
import threading
import time

import pytest

from conftest import write_wav
from station_player import radio
from station_player.config import ConfigStore
from station_player.server import PlayerApp, make_server, station_id

METAINT = 64


def icy_block(text: str) -> bytes:
    raw = text.encode()
    n = -(-len(raw) // 16)
    return bytes([n]) + raw.ljust(n * 16, b"\x00")


def icy_body(titles: list[str | None], audio: bytes = b"A") -> bytes:
    """Audio in METAINT-byte runs, a metadata block after each run."""
    out = b""
    for t in titles:
        out += audio * METAINT
        out += icy_block(f"StreamTitle='{t}';") if t is not None else b"\x00"
    return out + audio * 10


class FakeStation:
    """A tiny SHOUTcast/Icecast lookalike. `routes` maps a path to raw
    response bytes (status line, headers and body)."""

    def __init__(self, routes: dict[str, bytes]):
        self.routes = routes
        self.requests: list[bytes] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            self.requests.append(data)
            path = data.split(b" ")[1].decode()
            reply = self.routes.get(path, b"HTTP/1.0 404 Not Found\r\n\r\n")
            if callable(reply):
                reply(conn)
            else:
                try:
                    conn.sendall(reply)
                except OSError:
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture
def station():
    titles = ["Artist One - Song One", None, "Artist Two - Song Two"]
    st = FakeStation({
        "/icy": b"ICY 200 OK\r\nicy-name:Test FM\r\ncontent-type:audio/mpeg\r\nicy-metaint:64\r\n\r\n"
                + icy_body(titles),
        "/http": b"HTTP/1.0 200 OK\r\nContent-Type: audio/aac\r\nicy-metaint: 64\r\n\r\n" + icy_body(titles),
        "/bare-lf": b"ICY 200 OK\nicy-metaint:64\n\n" + icy_body(titles),
        "/plain": b"HTTP/1.0 200 OK\r\nContent-Type: audio/ogg\r\n\r\n" + b"O" * 500,
        "/moved": b"HTTP/1.1 302 Found\r\nLocation: /http\r\n\r\n",
        "/loop": b"HTTP/1.1 302 Found\r\nLocation: /loop\r\n\r\n",
        "/file": b"HTTP/1.1 302 Found\r\nLocation: file:///etc/passwd\r\n\r\n",
        "/html": b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n<html>",
        "/chunked": b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nTransfer-Encoding: chunked\r\n\r\n",
        "/garbage": b"SSH-2.0-OpenSSH\r\n\r\n",
    })
    yield st
    st.close()


def drain(stream):
    seen = []
    audio = b"".join(radio.relay(stream, seen.append))
    return audio, seen


def test_icy_response_is_relayed_without_metadata(station):
    stream = radio.open_stream(station.url + "/icy")
    assert stream.content_type == "audio/mpeg"
    audio, titles = drain(stream)
    assert audio == b"A" * (METAINT * 3 + 10)
    assert titles == ["Artist One - Song One", "Artist Two - Song Two"]
    assert b"Icy-MetaData: 1" in station.requests[0]


def test_http_response_and_redirects(station):
    stream = radio.open_stream(station.url + "/moved")
    assert stream.url.endswith("/http")
    assert stream.content_type == "audio/aac"
    assert drain(stream)[1][0] == "Artist One - Song One"


def test_old_servers_with_bare_newlines(station):
    stream = radio.open_stream(station.url + "/bare-lf")
    assert stream.content_type == "audio/mpeg"  # assumed when not stated
    assert drain(stream)[1] == ["Artist One - Song One", "Artist Two - Song Two"]


def test_stream_without_metadata_passes_through(station):
    stream = radio.open_stream(station.url + "/plain")
    assert stream.metaint == 0
    assert drain(stream) == (b"O" * 500, [])


@pytest.mark.parametrize("path", ["/loop", "/file", "/chunked", "/garbage", "/nope"])
def test_bad_streams_are_refused(station, path):
    with pytest.raises(radio.StreamError):
        radio.open_stream(station.url + path)


def test_non_audio_content_type_is_flagged(station):
    assert radio.open_stream(station.url + "/html").content_type is None


def test_connect_deadline_holds_against_a_slow_drip_server():
    def drip(conn):
        try:
            for _ in range(100):
                conn.sendall(b"H")
                time.sleep(0.2)
        except OSError:
            pass

    st = FakeStation({"/": drip})
    started = time.monotonic()
    with pytest.raises(radio.StreamError):
        radio.open_stream(st.url + "/", timeout=1)
    assert time.monotonic() - started < 3
    st.close()


def test_unreachable_station():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here now
    with pytest.raises(radio.StreamError):
        radio.open_stream(f"http://127.0.0.1:{port}/")


@pytest.mark.parametrize("block,want", [
    (b"StreamTitle='A - B';StreamUrl='';\x00\x00", "A - B"),
    (b"StreamTitle='It''s - Rock';", "It''s - Rock"),
    (b"StreamTitle='Don't Stop';StreamUrl='x';", "Don't Stop"),
    (b"StreamTitle='';", ""),
    (b"StreamUrl='http://x';", None),
    ("StreamTitle='Sigur Rós';".encode("latin-1"), "Sigur Rós"),
    ("StreamTitle='Björk';".encode("utf-8"), "Björk"),
])
def test_parse_stream_title(block, want):
    assert radio.parse_stream_title(block) == want


# ---- through the player's server -------------------------------------------------

@pytest.fixture
def player(station, tmp_path):
    write_wav(tmp_path / "m" / "x.wav")
    store = ConfigStore()
    store.config.stations = [
        {"name": "ICY", "url": station.url + "/icy"},
        {"name": "HTML", "url": station.url + "/html"},
        {"name": "Down", "url": "http://127.0.0.1:1/"},
    ]
    app = PlayerApp(store)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, server.server_address[1], station
    server.shutdown()
    server.server_close()


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_relay_through_the_server_records_the_title(player):
    app, port, station = player
    sid = station_id(station.url + "/icy")
    assert {s["id"] for s in app.state()["stations"]} >= {sid}
    resp, body = get(port, f"/radio/{sid}")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "audio/mpeg"
    assert body == b"A" * (METAINT * 3 + 10)
    resp, body = get(port, f"/api/nowplaying?station={sid}")
    assert resp.status == 200 and b"Artist Two - Song Two" in body


def test_relay_refuses_what_it_cant_play(player):
    app, port, station = player
    assert get(port, f"/radio/{station_id(station.url + '/html')}")[0].status == 502
    assert get(port, f"/radio/{station_id('http://127.0.0.1:1/')}")[0].status == 502
    # Only configured stations can be relayed: no fetching arbitrary URLs.
    assert get(port, "/radio/0123456789abcdef")[0].status == 404
    assert get(port, "/api/nowplaying?station=nope")[0].status == 404
