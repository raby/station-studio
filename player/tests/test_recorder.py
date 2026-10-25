import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from station_player import radio, recorder
from station_player.recorder import Recorder, file_stem
from test_radio import METAINT, icy_body
from test_server import request


class FakeStream:
    """What radio.open_stream returns: `data` then either the end of the
    stream or (live=True) a trickle of b"L" until closed."""

    def __init__(self, data: bytes, metaint: int = 0, ctype: str | None = "audio/mpeg", live: bool = False):
        self._data = data
        self._live = live
        self._closed = threading.Event()
        self.metaint = metaint
        self.content_type = ctype
        self.body = self

    def read1(self, n):
        if self._data:
            out, self._data = self._data[:n], self._data[n:]
            return out
        if self._live and not self._closed.wait(0.02):
            return b"L" * min(n, 10)
        if self._closed.is_set():
            raise OSError("closed")
        return b""

    def read(self, n):
        return self.read1(n)

    def close(self):
        self._closed.set()


class Opener:
    def __init__(self, *streams):
        self.streams = list(streams)
        self.calls = 0

    def __call__(self, url, user_agent=""):
        self.calls += 1
        if not self.streams:
            raise radio.StreamError("refused")
        return self.streams.pop(0)


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(recorder, "RETRY_SECONDS", 0.01)


def wait_until(check, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def only(rec):
    [status] = rec.status()
    return status


def test_records_audio_and_song_titles(tmp_path):
    live = FakeStream(icy_body(["Artist - Song", "Other - Tune"]), metaint=METAINT, live=True)
    rec = Recorder(tmp_path, opener=Opener(live))
    rec.start("http://radio/x", "Test FM")
    wait_until(lambda: only(rec)["bytes"] > METAINT * 2 + 10)
    rec.stop("http://radio/x")
    status = only(rec)
    assert status["done"] and status["error"] is None
    path = Path(status["file"])
    assert path.parent == tmp_path
    assert path.suffix == ".mp3" and path.name.startswith("Test FM ")
    audio = path.read_bytes()
    assert audio.startswith(b"A" * (METAINT * 2 + 10))  # the titles taken out
    assert set(audio[METAINT * 2 + 10:]) <= {ord("L")}
    titles = path.with_suffix(".txt").read_text().splitlines()
    assert [t.split("  ", 1)[1] for t in titles] == ["Artist - Song", "Other - Tune"]
    assert titles[0].startswith("00:00:0")


def test_reconnects_into_the_same_file(tmp_path):
    opener = Opener(FakeStream(b"first"), FakeStream(b"second", live=True))
    rec = Recorder(tmp_path, opener=opener)
    rec.start("http://radio/x", "Test FM")
    wait_until(lambda: only(rec)["bytes"] > 20)
    rec.stop_all()
    files = list(tmp_path.glob("*.mp3"))
    assert len(files) == 1
    assert files[0].read_bytes().startswith(b"firstsecond")
    assert opener.calls == 2


def test_gives_up_on_a_station_that_wont_connect(tmp_path):
    rec = Recorder(tmp_path, opener=Opener())
    rec.start("http://radio/x", "Dead FM")
    wait_until(lambda: only(rec)["done"])
    assert "Couldn't connect" in only(rec)["error"]
    assert list(tmp_path.iterdir()) == []


def test_refuses_streams_that_arent_audio(tmp_path):
    rec = Recorder(tmp_path, opener=Opener(FakeStream(b"<html>", ctype=None)))
    rec.start("http://radio/x", "Web page")
    wait_until(lambda: only(rec)["done"])
    assert "isn't a plain audio stream" in only(rec)["error"]


def test_never_overwrites_an_earlier_recording(tmp_path, monkeypatch):
    rec = Recorder(tmp_path, opener=Opener(FakeStream(b"new", ctype="audio/aacp", live=True)))
    rec.start("http://radio/x", "Test FM")
    wait_until(lambda: only(rec)["file"])
    first = Path(only(rec)["file"])
    rec.stop_all()
    assert first.suffix == ".aac"
    again = Recorder(tmp_path, opener=Opener(FakeStream(b"newer", ctype="audio/aacp", live=True)))
    # The same minute: the second recording gets "(2)".
    monkeypatch.setattr(recorder.time, "time", lambda: datetime.fromtimestamp(first.stat().st_mtime).replace(second=1).timestamp())
    again.start("http://radio/x", "Test FM")
    wait_until(lambda: only(again)["file"] is not None)
    again.stop_all()
    assert first.read_bytes().startswith(b"new")
    names = sorted(p.name for p in tmp_path.glob("*.aac"))
    assert len(names) == 2 and any(n.endswith(" (2).aac") for n in names)


def test_stops_by_itself_after_the_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "MAX_HOURS", 0.3 / 3600)
    rec = Recorder(tmp_path, opener=Opener(FakeStream(b"x", live=True)))
    rec.start("http://radio/x", "Test FM")
    wait_until(lambda: only(rec)["done"], 3)
    assert only(rec)["error"] is None


def test_starting_twice_records_once(tmp_path):
    opener = Opener(FakeStream(b"x", live=True), FakeStream(b"y", live=True))
    rec = Recorder(tmp_path, opener=opener)
    rec.start("http://radio/x", "Test FM")
    rec.start("http://radio/x", "Test FM")
    wait_until(lambda: only(rec)["bytes"] > 0)
    rec.stop_all()
    assert opener.calls == 1


def test_file_names_are_safe():
    when = datetime(2026, 10, 15, 9, 5)
    assert file_stem('Rock/Pop: "Live"', when) == "Rock Pop Live 2026-10-15 09.05"
    assert file_stem("...", when) == "Radio 2026-10-15 09.05"


def test_record_endpoints(tmp_path, monkeypatch):
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp, make_server, station_id

    store = ConfigStore(tmp_path / "home")
    store.config.studio = True
    store.config.stations = [{"name": "Test FM", "url": "http://radio/x"}]
    app = PlayerApp(store, recorder=Recorder(tmp_path / "rec", opener=Opener(FakeStream(b"x", live=True))))
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    h = {"X-Station-Player": "1"}
    try:
        sid = station_id("http://radio/x")
        resp, body = request(port, "POST", "/api/record", {"station": sid, "on": True}, h)
        assert resp.status == 200
        assert json.loads(body)["recordings"][0]["name"] == "Test FM"
        wait_until(lambda: json.loads(request(port, "GET", "/api/recordings")[1])["recordings"][0]["bytes"] > 0)
        resp, body = request(port, "POST", "/api/record", {"station": sid, "on": False}, h)
        assert json.loads(body)["recordings"][0]["done"]
        assert request(port, "POST", "/api/record", {"station": "nope", "on": True}, h)[0].status == 404
        assert request(port, "POST", "/api/record", {"station": sid, "on": "yes"}, h)[0].status == 400
        opened = []
        monkeypatch.setattr("station_player.server.subprocess.Popen", lambda cmd, **k: opened.append(cmd))
        monkeypatch.setattr("station_player.server.sys.platform", "linux")
        assert request(port, "POST", "/api/recordings/open", {}, h)[0].status == 200
        assert opened == [["xdg-open", str(tmp_path / "rec")]]
    finally:
        server.shutdown()
        server.server_close()


def test_record_a_station_only_played_lately(tmp_path):
    """One tried from Find stations can be recorded without saving it first."""
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp, station_id

    store = ConfigStore(tmp_path / "home")
    store.config.studio = True
    store.config.recent_stations = [{"name": "Found FM", "url": "http://radio/found"}]
    app = PlayerApp(store, recorder=Recorder(tmp_path / "rec", opener=Opener(FakeStream(b"x", live=True))))
    sid = station_id("http://radio/found")
    assert next(s for s in app.state()["recent_stations"])["id"] == sid
    app.record(sid, True)
    assert only(app.recorder)["name"] == "Found FM"
    # Dropping off Recently played doesn't stop it being stopped.
    store.config.recent_stations.clear()
    app.record(sid, False)
    wait_until(lambda: only(app.recorder)["done"])
    assert "http://radio/found" not in [s["url"] for s in store.config.stations]  # not saved
    with pytest.raises(LookupError):
        app.record(station_id("http://radio/never"), True)

def test_playlist_links_are_not_recorded(tmp_path):
    rec = Recorder(tmp_path, opener=Opener(FakeStream(b"#EXTM3U\n", ctype="audio/x-mpegurl")))
    rec.start("http://radio/x", "HLS FM")
    wait_until(lambda: only(rec)["done"])
    assert "isn't a plain audio stream" in only(rec)["error"]


def test_a_station_that_keeps_hanging_up_is_given_up_on(tmp_path):
    opener = Opener(*[FakeStream(b"bit") for _ in range(20)])
    rec = Recorder(tmp_path, opener=opener)
    rec.start("http://radio/x", "Flaky FM")
    wait_until(lambda: only(rec)["done"])
    assert "kept dropping" in only(rec)["error"]
    assert opener.calls == recorder.RETRIES + 1


def test_record_again_while_the_last_one_is_still_stopping(tmp_path):
    rec = Recorder(tmp_path, opener=Opener(FakeStream(b"a", live=True), FakeStream(b"b", live=True)))
    rec.start("http://radio/x", "Test FM")
    wait_until(lambda: only(rec)["bytes"] > 0)
    first = rec._recordings["http://radio/x"]
    first._stop.set()  # stopping, but the thread hasn't finished yet
    rec.start("http://radio/x", "Test FM")
    assert rec._recordings["http://radio/x"] is not first
    rec.stop_all()


def test_play_recording_only_takes_files_from_the_recordings_folder(tmp_path):
    from conftest import write_wav
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp

    app = PlayerApp(ConfigStore(tmp_path / "home"), recorder=Recorder(tmp_path / "rec", opener=Opener()))
    made = write_wav(tmp_path / "rec" / "Test FM 2026-10-15 09.00.wav")
    found = app.play_recording(str(made))
    assert found["track"] in app.library.tracks
    assert app.store.config.music_folders == [str((tmp_path / "rec").resolve())]
    # A second one is picked up without adding the folder again.
    second = write_wav(tmp_path / "rec" / "Test FM 2026-10-15 10.00.wav")
    assert app.play_recording(str(second))["playlist"] == found["playlist"]
    assert len(app.store.config.music_folders) == 1
    outside = write_wav(tmp_path / "elsewhere.wav")
    for bad in (str(outside), str(tmp_path / "rec" / ".." / "elsewhere.wav"), "", str(tmp_path / "rec" / "nope.wav")):
        with pytest.raises(ValueError):
            app.play_recording(bad)


def test_stopping_while_still_connecting_shows_as_stopped(tmp_path):
    # A station slow to answer: Stop returns at once and the recording
    # reads as stopping (not running), so Record works again straight away.
    gate = threading.Event()

    def slow_open(url, user_agent=None):
        gate.wait(10)
        raise radio.StreamError("timed out")
    rec = Recorder(tmp_path, opener=slow_open)
    rec.start("http://x/s", "Slow FM")
    started = time.monotonic()
    rec.stop("http://x/s")
    assert time.monotonic() - started < 2
    status = rec.status()[0]
    assert status["stopping"] and not status["done"]
    gate.set()
    deadline = time.monotonic() + 5
    while not rec.status()[0]["done"] and time.monotonic() < deadline:
        time.sleep(0.05)
    assert rec.status()[0]["done"] and not rec.status()[0]["stopping"]


def test_recordings_list_labels_and_tags(tmp_path):
    from station_player.config import ConfigStore
    from station_player.server import PlayerApp, make_server, station_id

    store = ConfigStore(tmp_path / "home")
    store.config.studio = True
    store.config.stations = [{"name": "Test FM", "url": "http://radio/x"}]
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    # A recording from before labels existed: station and time from its name.
    (rec_dir / "Old FM 2026-10-01 07.30.mp3").write_bytes(b"x" * 1000)
    (rec_dir / "Old FM 2026-10-01 07.30.txt").write_text("00:00:05  Song A\n00:03:10  Song B\n")
    (rec_dir / "notes.txt").write_text("not a recording")
    app = PlayerApp(store, recorder=Recorder(rec_dir, opener=Opener(FakeStream(b"abc"))))
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    h = {"X-Station-Player": "1"}
    try:
        request(port, "POST", "/api/record", {"station": station_id("http://radio/x"), "on": True}, h)
        wait_until(lambda: only(app.recorder)["done"])
        items = json.loads(request(port, "GET", "/api/recordings/list")[1])["recordings"]
        assert [r["station"] for r in items] == ["Test FM", "Old FM"]  # newest first
        new, old = items
        assert new["seconds"] is not None and new["label"] == "" and new["tags"] == []
        assert old["started"] == "2026-10-01T07:30:00" and old["songs"] == ["00:00:05  Song A", "00:03:10  Song B"]

        resp, body = request(port, "POST", "/api/recordings/label",
                             {"file": old["file"], "label": "  Breakfast  show ", "tags": ["Interview", "interview", " live "]}, h)
        assert resp.status == 200
        old = next(r for r in json.loads(body)["recordings"] if r["file"] == old["file"])
        assert old["label"] == "Breakfast show" and old["tags"] == ["Interview", "live"]
        saved = json.loads((rec_dir / "recordings.json").read_text())["items"]
        assert saved["Old FM 2026-10-01 07.30.mp3"]["tags"] == ["Interview", "live"]

        # Played, it shows under its label, with the station as the artist.
        resp, body = request(port, "POST", "/api/recordings/play", {"file": old["file"]}, h)
        track = app.library.tracks[json.loads(body)["track"]]
        assert track.name == "Breakfast show" and track.artist == "Old FM"

        for bad in ({"file": str(tmp_path / "home" / "config.json"), "label": "x", "tags": []},
                    {"file": old["file"], "label": "x", "tags": "not a list"},
                    {"file": old["file"], "label": 5, "tags": []},
                    {"file": old["file"], "label": "x", "tags": [f"t{i}" for i in range(30)]}):
            assert request(port, "POST", "/api/recordings/label", bad, h)[0].status == 400
    finally:
        server.shutdown()
        server.server_close()
