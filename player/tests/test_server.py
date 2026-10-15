import http.client
import shutil
from pathlib import Path
import json
import socket
import threading
import time

import pytest

from conftest import write_itunes_xml, write_wav
from station_player.config import ConfigStore
from station_player.server import PlayerApp, make_server, parse_range, resolve_stream_url


@pytest.fixture
def running(tmp_path):
    song = write_wav(tmp_path / "music" / "song.wav", frames=4000)
    write_itunes_xml(tmp_path / "exports" / "001.xml",
                     [{"Track ID": 1, "Name": "Song", "Persistent ID": "P1", "Location": song.as_uri()}],
                     [{"Name": "Week 1", "Playlist Items": [{"Track ID": 1}]}])
    store = ConfigStore()
    store.config.playlist_sources = [str(tmp_path / "exports")]
    app = PlayerApp(store)
    server = make_server(app, port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield app, server.server_address[1], song, tmp_path
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    payload = None
    if body is not None:
        payload = json.dumps(body)
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def test_parse_range():
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=500-", 1000) == (500, 999)
    assert parse_range("bytes=-100", 1000) == (900, 999)
    assert parse_range("bytes=900-5000", 1000) == (900, 999)
    assert parse_range("items=0-1", 1000) is None
    with pytest.raises(ValueError):
        parse_range("bytes=1000-", 1000)


def test_resolve_stream_url_reads_pls_and_m3u():
    pls = "[playlist]\nNumberOfEntries=1\nFile1=http://radio.example/live\n"
    m3u = "#EXTM3U\n#EXTINF:-1,Station\nhttps://radio.example/aac\n"
    assert resolve_stream_url("http://x/listen.pls", fetch=lambda u: pls) == "http://radio.example/live"
    assert resolve_stream_url("http://x/listen.m3u", fetch=lambda u: m3u) == "https://radio.example/aac"
    assert resolve_stream_url("http://x/stream", fetch=lambda u: 1 / 0) == "http://x/stream"


def test_resolve_stream_url_reports_unusable_playlist_links():
    with pytest.raises(ValueError, match="Couldn't read"):
        resolve_stream_url("http://x/listen.pls", fetch=lambda u: 1 / 0)
    with pytest.raises(ValueError, match="doesn't contain"):
        resolve_stream_url("http://x/listen.m3u", fetch=lambda u: "#EXTM3U\nnothing here\n")


def test_stream_fetch_has_an_overall_deadline():
    import socket as s_
    from station_player import server as srv
    listener = s_.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    conns = []

    def drip():  # accept, then send a byte too slowly to ever finish
        c, _ = listener.accept()
        conns.append(c)
        c.recv(1024)
        c.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 1000\r\n\r\n")
        for _ in range(20):
            time.sleep(0.2)
            try:
                c.sendall(b"x")
            except OSError:
                return

    threading.Thread(target=drip, daemon=True).start()
    t0 = time.monotonic()
    with pytest.raises(Exception):
        srv._fetch_text(f"http://127.0.0.1:{port}/x.pls", deadline=1)
    assert time.monotonic() - t0 < 2.5
    listener.close()


def test_redirects_to_other_schemes_are_refused():
    import urllib.error
    import urllib.request
    from station_player import server as srv
    handler = srv._HttpOnlyRedirects()
    req = urllib.request.Request("http://x/a.pls")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "ftp://evil/x")
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://ok/x") is not None


def test_state_and_playlist_and_ui(running):
    _, port, _, _ = running
    resp, data = request(port, "GET", "/api/state")
    state = json.loads(data)
    assert resp.status == 200
    [pl] = state["playlists"]
    assert pl["name"] == "Week 1"
    assert state["stations"][0]["name"] == "Rebirth Radio"
    resp, data = request(port, "GET", f"/api/playlists/{pl['id']}")
    assert json.loads(data)["tracks"][0]["available"] is True
    resp, data = request(port, "GET", "/")
    assert resp.status == 200 and b"Station Player" in data
    resp, _ = request(port, "GET", "/../server.py")
    assert resp.status == 404


def test_audio_full_and_ranged(running):
    app, port, song, _ = running
    tid = next(iter(app.library.tracks))
    full = song.read_bytes()
    resp, data = request(port, "GET", f"/audio/{tid}")
    assert resp.status == 200 and data == full
    assert resp.getheader("Content-Type") == "audio/wav"
    assert resp.getheader("Accept-Ranges") == "bytes"
    resp, data = request(port, "GET", f"/audio/{tid}", headers={"Range": "bytes=10-19"})
    assert resp.status == 206 and data == full[10:20]
    assert resp.getheader("Content-Range") == f"bytes 10-19/{len(full)}"
    resp, _ = request(port, "GET", f"/audio/{tid}", headers={"Range": f"bytes={len(full)}-"})
    assert resp.status == 416


def test_audio_only_serves_indexed_tracks(running):
    _, port, _, _ = running
    resp, _ = request(port, "GET", "/audio/0123456789abcdef")
    assert resp.status == 404
    resp, _ = request(port, "GET", "/audio/..%2F..%2Fetc%2Fpasswd")
    assert resp.status == 404


def test_post_requires_header_and_local_host(running):
    _, port, _, _ = running
    resp, _ = request(port, "POST", "/api/rescan", body={})
    assert resp.status == 403
    resp, _ = request(port, "GET", "/api/state", headers={"Host": "evil.example"})
    assert resp.status == 403
    resp, _ = request(port, "POST", "/api/rescan", body={}, headers={"X-Station-Player": "1"})
    assert resp.status == 200


def test_add_and_remove_sources_and_stations_persist(running):
    app, port, _, tmp = running
    h = {"X-Station-Player": "1"}
    write_wav(tmp / "folder" / "a.wav")
    resp, data = request(port, "POST", "/api/sources", {"kind": "music", "path": str(tmp / "folder")}, h)
    assert resp.status == 200
    assert any(p["kind"] == "folder" for p in json.loads(data)["playlists"])
    resp, data = request(port, "POST", "/api/sources", {"kind": "music", "path": str(tmp / "nope")}, h)
    assert resp.status == 400 and "Not found" in json.loads(data)["error"]

    resp, data = request(port, "POST", "/api/stations", {"name": "Test FM", "url": "https://test.example/live"}, h)
    assert [s["name"] for s in json.loads(data)["stations"]] == ["Rebirth Radio", "Test FM"]
    resp, data = request(port, "POST", "/api/stations", {"name": "Bad", "url": "ftp://x"}, h)
    assert resp.status == 400

    saved = json.loads(app.store.path.read_text())
    assert saved["music_folders"] == [str(tmp / "folder")]
    assert saved["stations"][1]["url"] == "https://test.example/live"

    request(port, "POST", "/api/stations/remove",
            {"url": "https://sh.uk.audio-stream.com/proxy/adgerdes?mp=/stream"}, h)
    request(port, "POST", "/api/sources/remove", {"kind": "music", "path": str(tmp / "folder")}, h)
    resp, data = request(port, "POST", "/api/remaps", {"from": "/a", "to": "/b"}, h)
    state = json.loads(data)
    assert [s["name"] for s in state["stations"]] == ["Test FM"]
    assert state["config"]["music_folders"] == []
    assert state["config"]["path_remaps"] == [{"from": "/a", "to": "/b"}]


def test_first_run_picks_up_station_studio_exports(tmp_path):
    exports = tmp_path / "studio-home" / "exports"
    exports.mkdir(parents=True)
    store = ConfigStore()
    assert store.config.playlist_sources == [str(exports)]


def test_remove_remap_by_identity(running):
    _, port, _, _ = running
    h = {"X-Station-Player": "1"}
    request(port, "POST", "/api/remaps", {"from": "/a", "to": "/b"}, h)
    request(port, "POST", "/api/remaps", {"from": "/c", "to": "/d"}, h)
    resp, data = request(port, "POST", "/api/remaps/remove", {"from": "/c", "to": "/d"}, h)
    assert json.loads(data)["config"]["path_remaps"] == [{"from": "/a", "to": "/b"}]


def test_head_requests_send_no_body(running):
    _, port, _, _ = running
    for path in ("/api/state", "/api/playlists/nope", "/"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(f"HEAD {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n".encode())
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        head, _, body = raw.partition(b"\r\n\r\n")
        assert b"Content-Length" in head
        assert body == b"", path


def test_negative_content_length_is_rejected(running):
    _, port, _, _ = running
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest("POST", "/api/rescan")
    conn.putheader("X-Station-Player", "1")
    conn.putheader("Content-Length", "-1")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400
    conn.close()


def test_failed_save_rolls_back_and_reports(running, monkeypatch):
    app, port, _, tmp = running
    before = json.loads(json.dumps(app.store.config.to_json()))

    def boom():
        raise PermissionError("read-only")

    monkeypatch.setattr(app.store, "save", boom)
    resp, data = request(port, "POST", "/api/remaps", {"from": "/x", "to": "/y"},
                         {"X-Station-Player": "1"})
    assert resp.status == 500 and "read-only" in json.loads(data)["error"]
    assert app.store.config.to_json() == before


def test_concurrent_changes_end_with_the_latest_settings(running):
    app, port, _, tmp = running
    h = {"X-Station-Player": "1"}
    folders = []
    for i in range(6):
        folder = tmp / f"f{i}"
        write_wav(folder / "a.wav")
        folders.append(str(folder))

    def add(path):
        request(port, "POST", "/api/sources", {"kind": "music", "path": path}, h)

    threads = [threading.Thread(target=add, args=(f,)) for f in folders]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(app.store.config.music_folders) == sorted(folders)
    names = {p["name"] for p in app.library.playlist_summaries() if p["kind"] == "folder"}
    assert names == {f"f{i}" for i in range(6)}


def test_parse_range_on_empty_file_is_unsatisfiable():
    with pytest.raises(ValueError):
        parse_range("bytes=-500", 0)
    with pytest.raises(ValueError):
        parse_range("bytes=0-", 0)


def test_added_paths_are_normalised(running):
    app, port, _, tmp = running
    write_wav(tmp / "folder" / "a.wav")
    roundabout = str(tmp / "folder" / ".." / "folder")
    request(port, "POST", "/api/sources", {"kind": "music", "path": roundabout}, {"X-Station-Player": "1"})
    request(port, "POST", "/api/sources", {"kind": "music", "path": str(tmp / "folder")}, {"X-Station-Player": "1"})
    assert app.store.config.music_folders == [str((tmp / "folder").resolve())]


def test_state_stays_responsive_during_a_rebuild(running, monkeypatch):
    app, port, _, _ = running
    from station_player import server as srv
    started, release = threading.Event(), threading.Event()
    real_load = srv.Library.load

    def slow_load(self, *a, **kw):
        started.set()
        release.wait(5)
        return real_load(self, *a, **kw)

    monkeypatch.setattr(srv.Library, "load", slow_load)
    t = threading.Thread(target=app.rescan)
    t.start()
    assert started.wait(5)
    t0 = time.monotonic()
    resp, data = request(port, "GET", "/api/state")  # must not wait for the scan
    assert resp.status == 200
    assert time.monotonic() - t0 < 1
    release.set()
    t.join()


def test_stale_rebuild_does_not_replace_newer_library(running, monkeypatch):
    app, _, _, tmp = running
    from station_player import server as srv
    write_wav(tmp / "newer" / "a.wav")
    gate = threading.Event()
    real_load = srv.Library.load
    calls = []

    def load(self, sources, folders, *rest):
        calls.append(list(folders))
        if len(calls) == 1:  # the first (older) rebuild is slow
            gate.wait(5)
        return real_load(self, sources, folders, *rest)

    monkeypatch.setattr(srv.Library, "load", load)
    slow = threading.Thread(target=app.rescan)
    slow.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        pass
    app.add_source("music", str(tmp / "newer"))  # newer change finishes first
    gate.set()
    slow.join()
    assert any(p["name"] == "newer" for p in app.library.playlist_summaries())


def test_corrupt_settings_file_is_set_aside(tmp_path):
    home = tmp_path / "player-home"
    home.mkdir()
    (home / "config.json").write_text('{"music_folders": ["a",], }')
    store = ConfigStore()
    assert store.config.music_folders == []
    assert store.warnings and "could not be read" in store.warnings[0]
    assert not (home / "config.json").exists()
    assert list(home.glob("config.broken-*.json"))
    app = PlayerApp(store)
    assert app.state()["errors"][0] == store.warnings[0]


def test_wrongly_typed_settings_are_dropped(tmp_path):
    home = tmp_path / "player-home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({
        "playlist_sources": None,
        "music_folders": ["/ok", 3, ""],
        "path_remaps": [{"from": "/a", "to": "/b"}, {"from": "/a"}, "x"],
        "stations": [{"name": "S", "url": "https://s"}, {"url": None}],
        "unknown": 1,
    }))
    cfg = ConfigStore().config
    assert cfg.playlist_sources == []
    assert cfg.music_folders == ["/ok"]
    assert cfg.path_remaps == [{"from": "/a", "to": "/b"}]
    assert cfg.stations == [{"name": "S", "url": "https://s"}]


def test_security_headers_and_fixed_static_types(running):
    _, port, _, _ = running
    resp, _ = request(port, "GET", "/")
    assert resp.getheader("Content-Type") == "text/html; charset=utf-8"
    assert "default-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    resp, _ = request(port, "GET", "/app.js")
    assert resp.getheader("Content-Type") == "text/javascript; charset=utf-8"
    resp, _ = request(port, "GET", "/style.css")
    assert resp.getheader("Content-Type") == "text/css; charset=utf-8"
    resp, _ = request(port, "GET", "/api/state")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"


def test_audio_endpoint_refuses_non_audio_even_if_indexed(running, tmp_path):
    app, port, _, _ = running
    from station_player.library import Track
    page = tmp_path / "evil.html"
    page.write_text("<script>alert(1)</script>")
    app.library.tracks["deadbeefdeadbeef"] = Track(id="deadbeefdeadbeef", name="x", path=page)
    resp, _ = request(port, "GET", "/audio/deadbeefdeadbeef")
    assert resp.status == 404


def test_unreadable_playlist_link_is_rejected_not_saved(running, monkeypatch):
    app, port, _, _ = running
    from station_player import server as srv
    monkeypatch.setattr(srv, "_fetch_text", lambda url: (_ for _ in ()).throw(OSError("unreachable")))
    resp, data = request(port, "POST", "/api/stations", {"name": "X", "url": "http://x/listen.pls"},
                         {"X-Station-Player": "1"})
    assert resp.status == 400 and "Couldn't read" in json.loads(data)["error"]
    assert all(s["name"] != "X" for s in app.store.config.stations)


def test_piled_up_rescans_share_one_scan(running, monkeypatch):
    app, _, _, _ = running
    from station_player import server as srv
    gate = threading.Event()
    real_load = srv.Library.load
    calls = []

    def load(self, *a, **kw):
        calls.append(1)
        gate.wait(5)
        return real_load(self, *a, **kw)

    monkeypatch.setattr(srv.Library, "load", load)
    threads = [threading.Thread(target=app.rescan) for _ in range(5)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # let the others queue up behind the first scan
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) <= 2  # the running scan, plus at most one for the rest


def test_dropped_connections_are_not_errors(running, monkeypatch):
    app, port, _, _ = running
    from http.server import BaseHTTPRequestHandler
    from station_player import server as srv
    handler_cls = srv.make_handler(app)

    def aborted(self):
        raise ConnectionAbortedError(10053, "aborted by the software in your host machine")

    monkeypatch.setattr(BaseHTTPRequestHandler, "handle", aborted)
    h = handler_cls.__new__(handler_cls)
    handler_cls.handle(h)  # must not raise


# ---- 0.2.0: tags, artwork, shortcuts, quit, background scan ----------------------

FIXTURES = Path(__file__).parent / "fixtures"


def test_folder_tracks_use_file_tags_and_cache_them(tmp_path):
    music = tmp_path / "Music" / "Album Dir"
    music.mkdir(parents=True)
    shutil.copy(FIXTURES / "tagged.flac", music / "01 - Name From File.flac")
    write_wav(music / "02 - Plain Artist - Plain Song.wav")
    store = ConfigStore()
    store.config.music_folders = [str(tmp_path / "Music")]
    app = PlayerApp(store)
    pl = next(p for p in app.library.playlist_summaries() if p["kind"] == "folder")
    tracks = app.library.playlist_detail(pl["id"])["tracks"]
    tagged, plain = tracks
    assert (tagged["name"], tagged["artist"], tagged["album"], tagged["genre"]) == \
        ("Tëst Title", "The Artist", "An Album", "Dub")
    assert 950 <= tagged["duration_ms"] <= 1100
    # No tags: the file name and folder fill in, as before.
    assert (plain["name"], plain["artist"], plain["album"]) == ("Plain Song", "Plain Artist", "Album Dir")
    assert plain["duration_ms"] == 1000
    assert (tmp_path / "player-home" / "tag-cache.json").exists()


def test_artwork_endpoint(running):
    app, port, song, tmp = running
    sid = next(iter(app.library.tracks))
    # No embedded art and no cover file yet.
    assert request(port, "GET", f"/art/{sid}")[0].status == 404
    cover = tmp / "music" / "Cover.JPG"
    cover.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    resp, body = request(port, "GET", f"/art/{sid}")
    assert resp.status == 200 and resp.getheader("Content-Type") == "image/jpeg"
    assert body == cover.read_bytes()
    # A "cover" that isn't really an image is never served.
    cover.write_bytes(b"<script>alert(1)</script>")
    assert request(port, "GET", f"/art/{sid}")[0].status == 404
    assert request(port, "GET", "/art/not-a-track")[0].status == 404


def test_embedded_artwork_wins(tmp_path):
    music = tmp_path / "m"
    music.mkdir()
    shutil.copy(FIXTURES / "tagged-id3v24.mp3", music / "a.mp3")
    (music / "cover.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
    store = ConfigStore()
    store.config.music_folders = [str(music)]
    app = PlayerApp(store)
    data, mime = app.artwork(next(iter(app.library.tracks)))
    assert mime == "image/png"


class FakeDesktop:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def status(self):
        return {"supported": True, "platform": "linux", "shortcut": ("shortcut", True) in self.calls,
                "login": ("login", True) in self.calls}

    def set_shortcut(self, on):
        if self.fail:
            raise OSError("no permission")
        self.calls.append(("shortcut", on))

    def set_login(self, on):
        self.calls.append(("login", on))


def serve(app):
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_desktop_endpoint(tmp_path):
    fake = FakeDesktop()
    server, port = serve(PlayerApp(ConfigStore(), desktop=fake))
    try:
        resp, body = request(port, "POST", "/api/desktop", {"shortcut": True},
                             headers={"X-Station-Player": "1"})
        assert resp.status == 200 and json.loads(body)["desktop"]["shortcut"] is True
        request(port, "POST", "/api/desktop", {"login": True}, headers={"X-Station-Player": "1"})
        assert fake.calls == [("shortcut", True), ("login", True)]
        # Without the API header another website can't flip these.
        assert request(port, "POST", "/api/desktop", {"login": False})[0].status == 403
        fake.fail = True
        resp, body = request(port, "POST", "/api/desktop", {"shortcut": False},
                             headers={"X-Station-Player": "1"})
        assert resp.status == 500 and "no permission" in json.loads(body)["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_quit_stops_the_server(tmp_path):
    server = make_server(PlayerApp(ConfigStore()), port=0)
    port = server.server_address[1]
    loop = threading.Thread(target=server.serve_forever, daemon=True)
    loop.start()
    try:
        assert request(port, "POST", "/api/quit", {})[0].status == 403  # header required
        assert loop.is_alive()
        resp, _ = request(port, "POST", "/api/quit", {}, headers={"X-Station-Player": "1"})
        assert resp.status == 200
        loop.join(5)
        assert not loop.is_alive()  # serve_forever returned, so main() exits
    finally:
        server.server_close()


def test_background_scan_reports_progress(tmp_path, monkeypatch):
    from station_player import server as srv
    write_wav(tmp_path / "m" / "a.wav")
    store = ConfigStore()
    store.config.music_folders = [str(tmp_path / "m")]
    gate = threading.Event()
    real_load = srv.Library.load

    def slow(self, *a):
        gate.wait(5)
        return real_load(self, *a)

    monkeypatch.setattr(srv.Library, "load", slow)
    app = PlayerApp(store, scan=False)
    assert app.state()["scanning"] is True
    worker = app.scan_in_background()
    assert app.state()["scanning"] is True
    gate.set()
    worker.join(5)
    state = app.state()
    assert state["scanning"] is False and state["playlists"]


def test_a_failed_scan_reports_an_error_instead_of_scanning_forever(tmp_path, monkeypatch):
    from station_player import server as srv

    def boom(self, *a):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(srv.Library, "load", boom)
    app = PlayerApp(ConfigStore(), scan=False)
    app.scan_in_background().join(5)
    state = app.state()
    assert state["scanning"] is False
    assert any("disk on fire" in e for e in state["errors"])
