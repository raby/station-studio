import json
import threading

import pytest

from conftest import write_wav
from station_player import myplaylists
from station_player.config import ConfigStore
from station_player.myplaylists import PlaylistStore
from station_player.server import PlayerApp, make_server
from test_server import request

H = {"X-Station-Player": "1"}


@pytest.fixture
def running(tmp_path):
    music = tmp_path / "Music"
    for name in ("01 One", "02 Two", "03 Three"):
        write_wav(music / f"{name}.wav")
    store = ConfigStore(tmp_path / "home")
    store.config.music_folders = [str(music)]
    app = PlayerApp(store)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, server.server_address[1], tmp_path
    server.shutdown()
    server.server_close()


def post(port, path, body):
    resp, data = request(port, "POST", path, body, H)
    return resp.status, json.loads(data)


def get(port, path):
    resp, data = request(port, "GET", path)
    return resp, data


def folder_ids(app):
    pl = next(p for p in app.library.playlist_summaries() if p["kind"] == "folder")
    return [t["id"] for t in app.library.playlist_detail(pl["id"])["tracks"]]


def test_make_fill_reorder_rename_and_delete(running):
    app, port, _ = running
    one, two, three = folder_ids(app)
    status, state = post(port, "/api/my-playlists", {"name": "  Drive   time ", "ids": [two]})
    assert status == 200
    pid = state["created"]
    assert pid.startswith("mine-")
    mine = [p for p in state["playlists"] if p["kind"] == "mine"]
    assert mine == [{"id": pid, "name": "Drive time", "kind": "mine", "source": "", "count": 1, "missing": 0}]

    assert post(port, f"/api/my-playlists/{pid}/add", {"ids": [one, three, "nonsense"]})[0] == 200
    detail = json.loads(get(port, f"/api/playlists/{pid}")[1])
    assert [t["name"] for t in detail["tracks"]] == ["Two", "One", "Three"]

    assert post(port, f"/api/my-playlists/{pid}/set", {"ids": [three, two]})[0] == 200
    detail = json.loads(get(port, f"/api/playlists/{pid}")[1])
    assert [t["name"] for t in detail["tracks"]] == ["Three", "Two"]

    assert post(port, f"/api/my-playlists/{pid}/rename", {"name": "Evenings"})[0] == 200
    assert app.my_playlists.get(pid)["name"] == "Evenings"
    # Saved to disk, so they're there next time.
    assert PlaylistStore(app.store.home / "my-playlists.json").get(pid)["name"] == "Evenings"

    assert post(port, f"/api/my-playlists/{pid}/delete", {})[0] == 200
    assert app.my_playlists.all() == []
    assert get(port, f"/api/playlists/{pid}")[0].status == 404


def test_bad_requests(running):
    app, port, _ = running
    assert post(port, "/api/my-playlists", {"name": "   ", "ids": []})[0] == 400
    assert post(port, "/api/my-playlists", {"name": "x", "ids": "abc"})[0] == 400
    assert post(port, "/api/my-playlists/mine-000000000000/rename", {"name": "x"})[0] == 404
    assert post(port, "/api/my-playlists/mine-000000000000/explode", {})[0] == 404
    assert app.my_playlists.all() == []


def test_tracks_are_found_again_after_a_rescan_and_missing_ones_kept(running):
    app, port, tmp = running
    one, two, three = folder_ids(app)
    pid = app.create_playlist("Mix", [one, two, three])
    (tmp / "Music" / "02 Two.wav").unlink()
    app.rescan()
    detail = app.playlist_detail(pid)
    assert [(t["name"], t["available"]) for t in detail["tracks"]] == [("One", True), ("Two", False), ("Three", True)]
    assert detail["missing"] == 1
    # Reordering keeps the missing track (by its remembered details).
    app.set_playlist_tracks(pid, [t["id"] for t in reversed(detail["tracks"])])
    assert [e["name"] for e in app.my_playlists.get(pid)["entries"]] == ["Three", "Two", "One"]


def test_found_by_path_when_the_id_changed(running):
    app, _, tmp = running
    one = folder_ids(app)[0]
    pid = app.create_playlist("Mix", [one])
    entry = app.my_playlists.get(pid)["entries"][0]
    app.my_playlists.set_entries(pid, [{**entry, "id": "gone-id"}])
    track = app.playlist_detail(pid)["tracks"][0]
    assert track["available"] and track["id"] == one


def test_m3u_export(running):
    app, port, tmp = running
    one, two, _ = folder_ids(app)
    pid = app.create_playlist("Late / night", [two, one])
    resp, body = get(port, f"/api/playlists/{pid}.m3u")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "audio/x-mpegurl"
    assert 'filename="Late _ night.m3u"' in resp.getheader("Content-Disposition")
    lines = body.decode().splitlines()
    assert lines[:2] == ["#EXTM3U", "#PLAYLIST:Late / night"]
    assert lines[2].startswith("#EXTINF:") and lines[2].endswith(",Two")
    assert lines[3] == str(tmp / "Music" / "02 Two.wav")
    assert lines[5] == str(tmp / "Music" / "01 One.wav")
    # Library playlists export too.
    folder = next(p for p in app.library.playlist_summaries() if p["kind"] == "folder")
    resp, body = get(port, f"/api/playlists/{folder['id']}.m3u")
    assert resp.status == 200 and body.decode().count("#EXTINF") == 3
    assert get(port, "/api/playlists/nope.m3u")[0].status == 404


def test_m3u_filename_with_non_latin_name(running):
    app, port, _ = running
    pid = app.create_playlist("日本の歌", folder_ids(app)[:1])
    resp, _ = get(port, f"/api/playlists/{pid}.m3u")
    assert resp.status == 200
    assert "filename*=UTF-8''%E6%97%A5" in resp.getheader("Content-Disposition")


def test_m3u_skips_tracks_without_files():
    text = myplaylists.m3u("x", [{"name": "A", "artist": "B", "duration_ms": 61000}, {"name": "Gone"}],
                           ["/m/a.mp3", None])
    assert text == "#EXTM3U\n#PLAYLIST:x\n#EXTINF:61,B - A\n/m/a.mp3\n"


def test_broken_playlist_file_is_kept_aside(tmp_path):
    path = tmp_path / "my-playlists.json"
    path.write_text("{not json")
    store = PlaylistStore(path)
    assert store.all() == [] and "couldn't be read" in store.warning
    assert (tmp_path / "my-playlists.broken.json").read_text() == "{not json"
    store.create("New", [])
    assert len(json.loads(path.read_text())["playlists"]) == 1


def test_failed_save_changes_nothing(tmp_path, monkeypatch):
    store = PlaylistStore(tmp_path / "my-playlists.json")
    pid = store.create("A", [])

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save", boom)
    with pytest.raises(OSError):
        store.rename(pid, "B")
    with pytest.raises(OSError):
        store.create("C", [])
    assert [p["name"] for p in store.all()] == ["A"]


def test_full_playlists_refuse_more(tmp_path, monkeypatch):
    monkeypatch.setattr(myplaylists, "MAX_ENTRIES", 2)
    store = PlaylistStore(None)
    pid = store.create("A", [{"id": "1"}, {"id": "2"}])
    with pytest.raises(ValueError):
        store.add(pid, [{"id": "3"}])


def test_own_window_setting(running):
    app, port, _ = running
    status, state = post(port, "/api/settings", {"own_window": False})
    assert status == 200 and state["config"]["own_window"] is False
    assert ConfigStore(app.store.home).config.own_window is False
    assert post(port, "/api/settings", {"own_window": "yes"})[0] == 400
    assert "window_browser" in state
