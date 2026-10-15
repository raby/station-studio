import base64
import http.client
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import write_itunes_xml, write_wav
from station_player import setup
from station_player.config import Config, ConfigStore
from station_player.library import Library, apply_remaps
from station_player.server import PlayerApp, make_server, station_id

H = {"X-Station-Player": "1"}


@pytest.fixture
def running(tmp_path):
    store = ConfigStore()
    store.config.playlist_sources = []
    app = PlayerApp(store)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, server.server_address[1], tmp_path
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, headers=None, timeout=10):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    payload = None
    if body is not None:
        payload = json.dumps(body)
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, json.loads(data) if data else None


class FakeRun:
    """Stands in for subprocess.run: answers from a list, records calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        code, out, err = self.answers.pop(0)
        return subprocess.CompletedProcess(argv, code, out, err)


# ---- pickers -------------------------------------------------------------------

def test_mac_folder_picker_asks_the_frontmost_app():
    run = FakeRun((0, "/Users/rob/Music/\n", ""))
    out = setup.Picker(platform="darwin", run=run).pick("folder")
    assert out == {"path": "/Users/rob/Music"}
    argv = run.calls[0][0]
    assert argv[:2] == ["osascript", "-e"]
    assert "path to frontmost application" in argv[2] and "choose folder" in argv[2]


def test_mac_file_picker_asks_for_xml():
    run = FakeRun((0, "/Users/rob/Music/Library.xml\n", ""))
    assert setup.Picker(platform="darwin", run=run).pick("file") == {"path": "/Users/rob/Music/Library.xml"}
    script = run.calls[0][0][2]
    assert "choose file of type" in script and '"xml"' in script and "iTunes" in script


def test_mac_cancel_is_not_an_error():
    run = FakeRun((1, "", "execution error: User canceled. (-128)\n"))
    assert setup.Picker(platform="darwin", run=run).pick("folder") == {"cancelled": True}
    assert len(run.calls) == 1


def test_mac_falls_back_when_the_frontmost_app_cant_be_asked():
    run = FakeRun((1, "", "Not authorized to send Apple events (-1743)"), (0, "/Volumes/Music/\n", ""))
    assert setup.Picker(platform="darwin", run=run).pick("folder") == {"path": "/Volumes/Music"}
    assert "frontmost" not in run.calls[1][0][2]


def test_mac_failure_is_reported():
    run = FakeRun((1, "", "boom"), (1, "", "boom"))
    with pytest.raises(setup.PickerFailed, match="boom"):
        setup.Picker(platform="darwin", run=run).pick("folder")


def test_windows_picker_runs_hidden_powershell_in_sta():
    path = "C:\\Users\\Zoë\\Music"
    run = FakeRun((0, base64.b64encode(path.encode()).decode() + "\r\n", ""))
    out = setup.Picker(platform="win32", run=run, env={"USERPROFILE": "C:\\Users\\Zoë"}).pick("folder")
    assert out == {"path": path}
    argv, kwargs = run.calls[0]
    assert argv[0] == "powershell" and "-STA" in argv
    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["env"]["SP_KIND"] == "folder" and kwargs["env"]["USERPROFILE"] == "C:\\Users\\Zoë"
    script = argv[-1]
    assert "FolderBrowserDialog" in script and "OpenFileDialog" in script and "TopMost" in script


def test_windows_cancel_and_failure():
    assert setup.Picker(platform="win32", run=FakeRun((0, "", "")), env={}).pick("file") == {"cancelled": True}
    with pytest.raises(setup.PickerFailed):
        setup.Picker(platform="win32", run=FakeRun((1, "", "no forms")), env={}).pick("file")
    with pytest.raises(setup.PickerFailed):
        setup.Picker(platform="win32", run=FakeRun((0, "!!!not base64", "")), env={}).pick("file")


def test_windows_drive_root_keeps_its_backslash():
    run = FakeRun((0, base64.b64encode(b"D:\\").decode(), ""))
    assert setup.Picker(platform="win32", run=run, env={}).pick("folder") == {"path": "D:\\"}


def test_linux_uses_zenity_then_kdialog():
    run = FakeRun((0, "/home/rob/Music\n", ""))
    picker = setup.Picker(platform="linux", run=run, env={"DISPLAY": ":0"},
                          which=lambda n: "/usr/bin/zenity" if n == "zenity" else None)
    assert picker.pick("folder") == {"path": "/home/rob/Music"}
    assert run.calls[0][0][:2] == ["zenity", "--file-selection"] and "--directory" in run.calls[0][0]

    run = FakeRun((0, "/home/rob/lib.xml\n", ""))
    picker = setup.Picker(platform="linux", run=run, env={"WAYLAND_DISPLAY": "wayland-0"},
                          which=lambda n: "/usr/bin/kdialog" if n == "kdialog" else None)
    assert picker.pick("file") == {"path": "/home/rob/lib.xml"}
    assert run.calls[0][0][0] == "kdialog" and "--getopenfilename" in run.calls[0][0]


def test_linux_zenity_file_filter_and_cancel():
    run = FakeRun((1, "", ""))
    picker = setup.Picker(platform="linux", run=run, env={"DISPLAY": ":0"}, which=lambda n: "/x/" + n)
    assert picker.pick("file") == {"cancelled": True}
    assert any(a.startswith("--file-filter=") and "*.xml" in a for a in run.calls[0][0])
    assert "--directory" not in run.calls[0][0]


def test_linux_without_a_picker_or_a_screen_is_unsupported():
    with pytest.raises(setup.PickerUnavailable):
        setup.Picker(platform="linux", run=FakeRun(), env={"DISPLAY": ":0"}, which=lambda n: None).pick("folder")
    with pytest.raises(setup.PickerUnavailable):
        setup.Picker(platform="linux", run=FakeRun(), env={}, which=lambda n: "/x").pick("folder")


def test_picker_missing_program_is_a_failure():
    def run(argv, **kwargs):
        raise FileNotFoundError("osascript")
    with pytest.raises(setup.PickerFailed):
        setup.Picker(platform="darwin", run=run).pick("folder")


def test_picker_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        setup.Picker(platform="darwin", run=FakeRun()).pick("disk")


class BlockingRun:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, argv, **kwargs):
        self.started.set()
        assert self.release.wait(10)
        return subprocess.CompletedProcess(argv, 0, "/picked/folder/\n", "")


def test_pick_api_one_window_at_a_time_without_blocking_the_server(running):
    app, port, _ = running
    run = BlockingRun()
    app.picker = setup.Picker(platform="darwin", run=run)
    first = {}
    t = threading.Thread(target=lambda: first.update(
        zip(("resp", "body"), request(port, "POST", "/api/pick", {"kind": "folder"}, H))))
    t.start()
    assert run.started.wait(5)
    # While the window is open: other requests are answered...
    resp, state = request(port, "GET", "/api/state")
    assert resp.status == 200 and "sources" in state
    # ...but a second window is refused.
    resp, body = request(port, "POST", "/api/pick", {"kind": "file"}, H)
    assert resp.status == 409 and body["error"] == "A folder window is already open"
    run.release.set()
    t.join(5)
    assert first["resp"].status == 200 and first["body"] == {"path": "/picked/folder"}
    # The lock is free again afterwards.
    app.picker = setup.Picker(platform="darwin", run=FakeRun((1, "", "(-128)")))
    assert request(port, "POST", "/api/pick", {"kind": "file"}, H)[1] == {"cancelled": True}


def test_pick_api_errors(running):
    app, port, _ = running
    app.picker = setup.Picker(platform="linux", run=FakeRun(), env={}, which=lambda n: None)
    resp, body = request(port, "POST", "/api/pick", {"kind": "folder"}, H)
    assert resp.status == 501 and body["unsupported"] is True and body["error"]
    resp, body = request(port, "POST", "/api/pick", {"kind": "nope"}, H)
    assert resp.status == 400
    resp, _ = request(port, "POST", "/api/pick", {"kind": "folder"})
    assert resp.status == 403
    app.picker = setup.Picker(platform="darwin", run=FakeRun((1, "", "x"), (1, "", "x")))
    resp, body = request(port, "POST", "/api/pick", {"kind": "folder"}, H)
    assert resp.status == 500 and "couldn't open" in body["error"]


# ---- suggestions ---------------------------------------------------------------

def make_home(tmp_path) -> Path:
    home = tmp_path / "home"
    for i in range(3):
        write_wav(home / "Music" / "Band" / f"{i:02d} song.wav", frames=10)
    (home / "Music" / "notes.txt").write_text("hi")
    write_itunes_xml(home / "Music" / "iTunes" / "iTunes Music Library.xml", [], [])
    write_itunes_xml(home / "Music" / "Music" / "Library.xml", [], [])
    (home / "Music" / "Music" / "other.xml").write_text("<?xml version='1.0'?><rss/>")
    return home


def test_suggestions_find_music_itunes_exports_and_station_studio(tmp_path):
    home = make_home(tmp_path)
    exports = tmp_path / "studio" / "exports"
    exports.mkdir(parents=True)
    (exports / "001.xml").write_text("x")
    out = setup.suggestions(Config(), home=home, exports=exports)
    by_path = {s["path"]: s for s in out}
    music = by_path[str((home / "Music").resolve())]
    assert music == {"kind": "music", "path": str((home / "Music").resolve()), "label": "Music folder",
                     "detail": "3 songs", "added": False}
    itunes = by_path[str((home / "Music" / "iTunes" / "iTunes Music Library.xml").resolve())]
    assert itunes["kind"] == "playlist" and itunes["detail"] == "iTunes library export"
    assert itunes["label"] == "iTunes Music Library.xml"
    assert str((home / "Music" / "Music" / "Library.xml").resolve()) in by_path
    assert not any(p.endswith("other.xml") for p in by_path)
    studio = by_path[str(exports.resolve())]
    assert studio["kind"] == "playlist" and "Station Studio playlists" in studio["detail"]
    assert all(set(s) == {"kind", "path", "label", "detail", "added"} for s in out)


def test_suggestions_mark_what_is_added(tmp_path):
    home = make_home(tmp_path)
    cfg = Config(music_folders=[str((home / "Music").resolve())],
                 playlist_sources=[str((home / "Music" / "iTunes").resolve())])
    out = setup.suggestions(cfg, home=home, exports=tmp_path / "none")
    added = {Path(s["path"]).name: s["added"] for s in out}
    assert added["Music"] is True
    assert added["iTunes Music Library.xml"] is True  # inside an added folder
    assert added["Library.xml"] is False


def test_suggestions_skip_empty_music_and_missing_folders(tmp_path):
    home = tmp_path / "home"
    (home / "Music").mkdir(parents=True)
    (home / "Music" / "readme.txt").write_text("x")
    assert setup.suggestions(Config(), home=home, exports=tmp_path / "none") == []
    assert setup.suggestions(Config(), home=tmp_path / "nobody", exports=tmp_path / "none") == []


def test_song_count_is_bounded(tmp_path):
    for i in range(30):
        (tmp_path / "m").mkdir(exist_ok=True)
        (tmp_path / "m" / f"{i}.mp3").write_bytes(b"")
    assert setup.count_songs(tmp_path / "m") == (30, False)
    n, cut = setup.count_songs(tmp_path / "m", max_files=10)
    assert (n, cut) == (10, True)
    home = tmp_path
    (home / "Music").mkdir()
    for i in range(12):
        (home / "Music" / f"{i}.flac").write_bytes(b"")
    out = setup.suggestions(Config(), home=home, exports=tmp_path / "none", max_files=5)
    assert out[0]["detail"] == "over 5 songs"
    assert setup.suggestions(Config(), home=home, exports=tmp_path / "none")[0]["detail"] == "12 songs"
    (home / "Music" / "solo").mkdir()
    for f in (home / "Music").glob("*.flac"):
        f.unlink()
    (home / "Music" / "solo" / "a.m4a").write_bytes(b"")
    assert setup.suggestions(Config(), home=home, exports=tmp_path / "none")[0]["detail"] == "1 song"


def test_suggestions_api(running):
    app, port, tmp_path = running
    app.suggest_home = make_home(tmp_path)
    resp, body = request(port, "GET", "/api/suggestions")
    assert resp.status == 200
    kinds = sorted(s["kind"] for s in body["suggestions"])
    assert kinds == ["music", "playlist", "playlist"]
    music = next(s for s in body["suggestions"] if s["kind"] == "music")
    resp, state = request(port, "POST", "/api/sources", {"kind": "music", "path": music["path"]}, H)
    assert resp.status == 200
    body = request(port, "GET", "/api/suggestions")[1]
    assert next(s for s in body["suggestions"] if s["kind"] == "music")["added"] is True


# ---- sources and missing in the state ------------------------------------------

def test_state_lists_sources_with_counts_and_missing(running, tmp_path):
    app, port, _ = running
    song = write_wav(tmp_path / "music" / "a.wav")
    write_wav(tmp_path / "music" / "b.wav")
    gone = (tmp_path / "gone" / "x.wav").as_uri()
    tracks = [{"Track ID": 1, "Name": "A", "Persistent ID": "P1", "Location": song.as_uri()},
              {"Track ID": 2, "Name": "Gone", "Persistent ID": "P2", "Location": gone}]
    write_itunes_xml(tmp_path / "exports" / "001.xml", tracks,
                     [{"Name": "W1", "Playlist Items": [{"Track ID": 1}, {"Track ID": 2}]}])
    # A second weekly export with the same missing song under another id.
    write_itunes_xml(tmp_path / "exports" / "002.xml",
                     [{**tracks[1], "Persistent ID": "P9"}], [{"Name": "W2", "Playlist Items": [{"Track ID": 2}]}])
    request(port, "POST", "/api/sources", {"kind": "playlist", "path": str(tmp_path / "exports")}, H)
    request(port, "POST", "/api/sources", {"kind": "music", "path": str(tmp_path / "music")}, H)
    app.store.config.music_folders.append(str(tmp_path / "nowhere"))
    state = request(port, "GET", "/api/state")[1]
    assert state["missing"] == 1
    assert state["sources"] == [
        {"kind": "playlist", "path": str((tmp_path / "exports").resolve()), "name": "exports", "exists": True,
         "tracks": 3, "playlists": 2},
        {"kind": "music", "path": str((tmp_path / "music").resolve()), "name": "music", "exists": True,
         "tracks": 2, "playlists": 1},
        {"kind": "music", "path": str(tmp_path / "nowhere"), "name": "nowhere", "exists": False,
         "tracks": 0, "playlists": 0},
    ]
    # The page never sees where a track used to be.
    detail = request(port, "GET", f"/api/playlists/{state['playlists'][0]['id']}")[1]
    assert all("location" not in t for t in detail["tracks"])


def test_single_xml_source_counts(tmp_path):
    song = write_wav(tmp_path / "a.wav")
    xml = write_itunes_xml(tmp_path / "lib.xml", [{"Track ID": 1, "Name": "A", "Location": song.as_uri()}],
                           [{"Name": "P", "Playlist Items": [{"Track ID": 1}]},
                            {"Name": "Q", "Playlist Items": [{"Track ID": 1}]}])
    store = ConfigStore()
    store.config.playlist_sources = [str(xml)]
    state = PlayerApp(store).state()
    assert state["sources"] == [{"kind": "playlist", "path": str(xml), "name": "lib.xml", "exists": True,
                                 "tracks": 1, "playlists": 2}]
    assert state["missing"] == 0


# ---- finding moved music -------------------------------------------------------

def test_apply_remaps_prefers_the_most_specific_rule():
    remaps = [{"from": "/old", "to": "/nowhere"}, {"from": "/old/Music/iTunes", "to": "/new"}]
    assert apply_remaps(Path("/old/Music/iTunes/a/b.mp3"), remaps) == Path("/new/a/b.mp3")
    assert apply_remaps(Path("/old/other/b.mp3"), remaps) == Path("/nowhere/other/b.mp3")


def test_library_keeps_the_original_location(tmp_path):
    xml = write_itunes_xml(tmp_path / "lib.xml",
                           [{"Track ID": 1, "Name": "A", "Location": "file:///Users/rob/Music/A/a.mp3"},
                            {"Track ID": 2, "Name": "B", "Location": "file:///Users/rob/notes.txt"}],
                           [{"Name": "P", "Playlist Items": [{"Track ID": 1}, {"Track ID": 2}]}])
    lib = Library()
    lib.load([str(xml)], [], [{"from": "/Users/rob", "to": "/elsewhere"}])
    by_name = {t.name: t for t in lib.tracks.values()}
    assert by_name["A"].location == Path("/Users/rob/Music/A/a.mp3") and by_name["A"].path is None
    assert by_name["B"].location is None  # not audio: never looked for
    assert "location" not in by_name["A"].to_json()
    assert lib.missing_count() == 2


def test_find_remap_strips_the_shared_end(tmp_path):
    a = write_wav(tmp_path / "new" / "Band" / "LP" / "01 Intro.wav")
    write_wav(tmp_path / "new" / "Band" / "LP" / "02 Song.wav")
    missing = [Path("/Users/rob/Music/iTunes/Band/LP/01 Intro.wav"),
               Path("/Users/rob/Music/iTunes/Band/LP/02 Song.wav")]
    remap = setup.find_remap(missing, tmp_path / "new")
    assert remap == {"from": "/Users/rob/Music/iTunes", "to": (tmp_path / "new").as_posix()}
    assert apply_remaps(missing[0], [remap]) == a


def test_find_remap_prefers_matching_folders_and_skips_ties(tmp_path):
    write_wav(tmp_path / "lib" / "Other" / "X" / "01 Intro.wav")
    write_wav(tmp_path / "lib" / "Band" / "LP" / "01 Intro.wav")
    remap = setup.find_remap([Path("C:/Users/Rob/Music/Band/LP/01 Intro.wav")], tmp_path / "lib")
    assert remap == {"from": "C:/Users/Rob/Music", "to": (tmp_path / "lib").as_posix()}
    # Two equally good namesakes: no guess.
    write_wav(tmp_path / "two" / "A" / "01 Intro.wav")
    write_wav(tmp_path / "two" / "B" / "01 Intro.wav")
    assert setup.find_remap([Path("/old/C/01 Intro.wav")], tmp_path / "two") is None


def test_find_remap_takes_the_commonest_pair_and_ignores_case(tmp_path):
    for name in ("a.wav", "b.wav", "c.wav"):
        write_wav(tmp_path / "pc" / "Music" / "Band" / name)
    write_wav(tmp_path / "pc" / "Odd" / "d.wav")
    missing = [Path(f"/Users/rob/Music/band/{n}") for n in ("a.wav", "B.WAV", "c.wav")] + [Path("/x/y/d.wav")]
    remap = setup.find_remap(missing, tmp_path / "pc")
    assert remap == {"from": "/Users/rob", "to": (tmp_path / "pc").as_posix()}
    assert setup.find_remap([Path("/x/nothing.wav")], tmp_path / "pc") is None
    assert setup.find_remap([], tmp_path / "pc") is None


def test_locate_api_finds_moved_music(running, tmp_path):
    app, port, _ = running
    old = Path("/Volumes/OldDrive/iTunes Media/Music")
    tracks = []
    for i, (artist, name) in enumerate([("Band", "01 Intro.wav"), ("Band", "02 Song.wav"), ("Solo", "03 Gone.wav")], 1):
        tracks.append({"Track ID": i, "Name": name, "Persistent ID": f"P{i}",
                       "Location": (old / artist / "LP" / name).as_uri()})
        if name != "03 Gone.wav":
            write_wav(tmp_path / "moved" / artist / "LP" / name)
    write_itunes_xml(tmp_path / "exports" / "001.xml", tracks,
                     [{"Name": "W1", "Playlist Items": [{"Track ID": i} for i in (1, 2, 3)]}])
    # An old, broader rewrite that doesn't help mustn't hide the new one.
    app.store.config.path_remaps = [{"from": "/Volumes/OldDrive", "to": "/nowhere"}]
    request(port, "POST", "/api/sources", {"kind": "playlist", "path": str(tmp_path / "exports")}, H)
    assert request(port, "GET", "/api/state")[1]["missing"] == 3

    resp, body = request(port, "POST", "/api/locate", {"folder": str(tmp_path / "moved")}, H)
    assert resp.status == 200
    assert body["found"] == 2 and body["missing"] == 1
    assert body["remap"] == {"from": old.as_posix(), "to": (tmp_path / "moved").resolve().as_posix()}
    assert body["remap"] in body["config"]["path_remaps"]  # the state comes back too
    assert body["sources"][0]["tracks"] == 3
    detail = request(port, "GET", f"/api/playlists/{body['playlists'][0]['id']}")[1]
    assert [t["available"] for t in detail["tracks"]] == [True, True, False]

    # Again: nothing more to find, and the same rule isn't added twice.
    resp, body = request(port, "POST", "/api/locate", {"folder": str(tmp_path / "moved")}, H)
    assert body["found"] == 0 and body["missing"] == 1 and body["remap"] is None
    assert len(body["config"]["path_remaps"]) == 2


def test_locate_replaces_an_older_rule_for_the_same_folder(running, tmp_path):
    app, port, _ = running
    write_wav(tmp_path / "new" / "A" / "a.wav")
    write_itunes_xml(tmp_path / "exports" / "001.xml",
                     [{"Track ID": 1, "Name": "a", "Location": Path("/old/A/a.wav").as_uri()}],
                     [{"Name": "W", "Playlist Items": [{"Track ID": 1}]}])
    app.store.config.path_remaps = [{"from": "/old", "to": "/wrong"}]
    request(port, "POST", "/api/sources", {"kind": "playlist", "path": str(tmp_path / "exports")}, H)
    body = request(port, "POST", "/api/locate", {"folder": str(tmp_path / "new")}, H)[1]
    assert body["found"] == 1 and body["missing"] == 0
    assert body["config"]["path_remaps"] == [{"from": "/old", "to": (tmp_path / "new").resolve().as_posix()}]


def test_locate_api_rejects_a_missing_folder(running, tmp_path):
    _, port, _ = running
    resp, body = request(port, "POST", "/api/locate", {"folder": str(tmp_path / "no")}, H)
    assert resp.status == 400 and "not found" in body["error"]
    resp, body = request(port, "POST", "/api/locate", {"folder": ""}, H)
    assert resp.status == 400
    resp, body = request(port, "POST", "/api/locate", {"folder": str(tmp_path)}, H)
    assert resp.status == 200 and body["found"] == 0 and body["remap"] is None and body["missing"] == 0


# ---- trying a station ----------------------------------------------------------

class FakeStation:
    """A tiny stream server: answers every connection with `head`, then
    `body` (or silence)."""

    def __init__(self, head: bytes, body: bytes = b"", hold: float = 0.0):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/live"
        self.head, self.body, self.hold = head, body, hold
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                conn.recv(4096)
                conn.sendall(self.head + self.body)
                time.sleep(self.hold)
            except OSError:
                pass
            finally:
                conn.close()

    def close(self):
        self.sock.close()


def no_resolve(url):
    return url


def test_check_station_ok_with_name():
    st = FakeStation(b"ICY 200 OK\r\ncontent-type: audio/mpeg\r\nicy-name: Test FM\r\n\r\n", b"\xff" * 2048)
    try:
        out = setup.check_station(st.url, no_resolve)
    finally:
        st.close()
    assert out == {"ok": True, "type": "audio/mpeg", "name": "Test FM", "url": st.url}


def test_check_station_follows_playlist_links():
    st = FakeStation(b"HTTP/1.0 200 OK\r\nContent-Type: audio/aacp\r\n\r\n", b"\x00" * 100)
    try:
        out = setup.check_station("http://example.invalid/listen.pls", lambda u: st.url)
    finally:
        st.close()
    assert out["ok"] is True and out["name"] is None and out["type"] == "audio/aacp" and out["url"] == st.url


def test_check_station_plain_english_failures():
    cases = [
        (FakeStation(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n", b"<html>"), "web page"),
        (FakeStation(b"HTTP/1.0 404 Not Found\r\n\r\n"), "404"),
        (FakeStation(b"HTTP/1.0 503 Busy\r\n\r\n"), "(503)"),
        (FakeStation(b"HTTP/1.0 200 OK\r\nContent-Type: application/vnd.apple.mpegurl\r\n\r\n", b"#EXTM3U"), "HLS"),
        (FakeStation(b"ICY 200 OK\r\ncontent-type: audio/mpeg\r\n\r\n"), "no audio"),
        (FakeStation(b"ICY 200 OK\r\ncontent-type: audio/mpeg\r\n\r\n", hold=3), "didn't answer"),
    ]
    try:
        for st, words in cases:
            out = setup.check_station(st.url, no_resolve, seconds=1)
            assert out["ok"] is False and words in out["message"], (words, out)
    finally:
        for st, _ in cases:
            st.close()


def test_check_station_bad_input_and_unreachable():
    assert setup.check_station("ftp://x", no_resolve)["message"].startswith("Stream URL must start")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    out = setup.check_station(f"http://127.0.0.1:{port}/", no_resolve)
    assert out["ok"] is False and "refused" in out["message"]

    def bad_playlist(url):
        raise ValueError("That playlist link doesn't contain a stream URL")
    out = setup.check_station("http://x/a.m3u", bad_playlist)
    assert out == {"ok": False, "message": "That playlist link doesn't contain a stream URL"}

    def broken(url, **kwargs):
        raise RuntimeError("weird")
    out = setup.check_station("http://x/", no_resolve, open_stream=broken)
    assert out["ok"] is False and "weird" in out["message"]


def test_station_test_api(running):
    _, port, _ = running
    st = FakeStation(b"ICY 200 OK\r\ncontent-type: audio/ogg\r\nicy-name: Ogg Radio\r\n\r\n", b"OggS" * 100)
    try:
        resp, body = request(port, "POST", "/api/stations/test", {"url": st.url}, H)
    finally:
        st.close()
    assert resp.status == 200 and body == {"ok": True, "type": "audio/ogg", "name": "Ogg Radio", "url": st.url}
    resp, body = request(port, "POST", "/api/stations/test", {"url": "nope"}, H)
    assert resp.status == 200 and body["ok"] is False


# ---- editing stations ----------------------------------------------------------

@pytest.fixture
def stations(running):
    app, port, tmp_path = running
    app.store.config.stations = [{"name": "A", "url": "http://a.example/1"},
                                 {"name": "B", "url": "http://b.example/2"},
                                 {"name": "C", "url": "http://c.example/3"}]
    return app, port


def names(state):
    return [s["name"] for s in state["stations"]]


def test_rename_and_readdress_a_station(stations):
    app, port = stations
    app.store.config.alarm = {"on": True, "time": "07:00", "days": [0],
                              "source": {"kind": "station", "id": station_id("http://b.example/2"), "name": "B"}}
    resp, state = request(port, "POST", "/api/stations/update", {"url": "http://b.example/2", "name": "  Bee FM "}, H)
    assert resp.status == 200 and names(state) == ["A", "Bee FM", "C"]
    resp, state = request(port, "POST", "/api/stations/update",
                          {"url": "http://b.example/2", "new_url": "https://b.example/new"}, H)
    assert resp.status == 200
    assert state["stations"][1] == {"name": "Bee FM", "url": "https://b.example/new",
                                    "id": station_id("https://b.example/new")}
    # The alarm still finds the station.
    assert state["config"]["alarm"]["source"] == {"kind": "station", "id": station_id("https://b.example/new"),
                                                  "name": "Bee FM"}
    saved = json.loads(app.store.path.read_text())
    assert saved["stations"][1]["url"] == "https://b.example/new"


def test_station_update_validation(stations):
    app, port = stations
    cases = [
        ({"url": "http://a.example/1", "name": "  "}, 400),
        ({"url": "http://a.example/1", "new_url": "ftp://x"}, 400),
        ({"url": "http://a.example/1", "new_url": "http://c.example/3"}, 400),
        ({"url": "http://nope/", "name": "X"}, 404),
    ]
    for body, status in cases:
        resp, _ = request(port, "POST", "/api/stations/update", body, H)
        assert resp.status == status, body
    assert names(app.state()) == ["A", "B", "C"]
    assert app.store.config.stations[0]["url"] == "http://a.example/1"


def test_station_update_resolves_playlist_links(stations, monkeypatch):
    app, port = stations
    monkeypatch.setattr("station_player.server._fetch_text", lambda url, *a: "File1=http://real.example/s\n")
    resp, state = request(port, "POST", "/api/stations/update",
                          {"url": "http://a.example/1", "new_url": "http://a.example/listen.pls"}, H)
    assert resp.status == 200 and state["stations"][0]["url"] == "http://real.example/s"


def test_move_station(stations):
    app, port = stations
    resp, state = request(port, "POST", "/api/stations/move", {"url": "http://c.example/3", "to": 0}, H)
    assert resp.status == 200 and names(state) == ["C", "A", "B"]
    state = request(port, "POST", "/api/stations/move", {"url": "http://c.example/3", "to": 99}, H)[1]
    assert names(state) == ["A", "B", "C"]
    state = request(port, "POST", "/api/stations/move", {"url": "http://b.example/2", "to": -5}, H)[1]
    assert names(state) == ["B", "A", "C"]
    assert request(port, "POST", "/api/stations/move", {"url": "http://b.example/2", "to": True}, H)[0].status == 400
    assert request(port, "POST", "/api/stations/move", {"url": "http://b.example/2", "to": "1"}, H)[0].status == 400
    assert request(port, "POST", "/api/stations/move", {"url": "http://x/", "to": 1}, H)[0].status == 404
    assert [s["name"] for s in json.loads(app.store.path.read_text())["stations"]] == ["B", "A", "C"]
