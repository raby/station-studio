import http.client
import json
import threading

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

    request(port, "POST", "/api/stations/remove", {"index": 0}, h)
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
