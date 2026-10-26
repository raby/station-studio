"""Live shows: a presenter's encoder connects, goes on air in place of the music, and hands back."""
import base64
import random
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import date

import pytest

from conftest import FFMPEG, needs_ffmpeg
from station_server.audio import BYTES_PER_SECOND, CHUNK
from station_server.config import Station, clean_station
from station_server.live import LiveInput
from test_app import api, exports, wait_for  # noqa: F401 (fixtures)
from test_crossfades import Recorder, channel_of, samples, steady


def free_ports() -> int:
    """A port P with P+1 free too, below the range the system hands out for
    outgoing connections (so another test's connection can't take it)."""
    for _ in range(200):
        p = random.randrange(20000, 30000)
        try:
            with socket.socket() as a, socket.socket() as b:
                a.bind(("", p))
                b.bind(("", p + 1))
            return p
        except OSError:
            continue
    raise RuntimeError("no free ports")


def mp3(seconds: float, freq: int = 440) -> bytes:
    return subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                           "-ac", "2", "-b:a", "128k", "-f", "mp3", "-"], capture_output=True, check=True).stdout


def drain(source, timeout=10.0) -> int:
    n, end = 0, time.time() + timeout
    while not source.ended.is_set() and time.time() < end:
        chunk = source.read(0.2)
        if chunk:
            n += len(chunk)
    return n


@pytest.fixture
def live():
    sources = []
    li = LiveInput(free_ports(), "letmein", FFMPEG or "ffmpeg", on_source=lambda s: sources.append(s) or True,
                   host="127.0.0.1")
    li.start()
    li.sources = sources
    yield li
    li.stop()


def icecast_source(port, password="letmein", name="DJ Rob"):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    auth = base64.b64encode(f"source:{password}".encode()).decode()
    s.sendall(f"PUT /live HTTP/1.1\r\nAuthorization: Basic {auth}\r\nContent-Type: audio/mpeg\r\n"
              f"ice-name: {name}\r\nExpect: 100-continue\r\n\r\n".encode())
    return s, s.recv(200)


@needs_ffmpeg
def test_an_icecast_encoder_goes_live_with_its_titles(live):
    conn, reply = icecast_source(live.port)
    assert reply.startswith(b"HTTP/1.0 200")
    conn.sendall(mp3(1.5))
    assert wait_for(lambda: live.sources)
    source = live.sources[0]
    assert source.presenter == "DJ Rob" and source.address == "127.0.0.1"
    # Titles from the encoder, as Icecast takes them.
    auth = base64.b64encode(b"source:letmein").decode()
    req = urllib.request.Request(f"http://127.0.0.1:{live.port}/admin/metadata?mount=/live&mode=updinfo&song=Abba%20-%20SOS",
                                 headers={"Authorization": f"Basic {auth}"})
    assert urllib.request.urlopen(req, timeout=5).status == 200 and source.title == "Abba - SOS"
    # Someone else can't barge in.
    other, reply = icecast_source(live.port, name="Intruder")
    assert b"403" in reply.split(b"\r\n")[0]
    other.close()
    conn.close()
    assert drain(source) > BYTES_PER_SECOND  # their audio, decoded
    assert live.source is None


@needs_ffmpeg
def test_a_shoutcast_encoder_goes_live(live):
    s = socket.create_connection(("127.0.0.1", live.port + 1), timeout=5)
    s.sendall(b"letmein:#1\r\n")  # as a SHOUTcast 2 encoder in legacy mode sends it
    assert s.recv(100).startswith(b"OK2")
    s.sendall(b"content-type:audio/mpeg\r\nicy-name:Night Shift\r\n\r\n" + mp3(1.0, 300))
    assert wait_for(lambda: live.sources)
    urllib.request.urlopen(f"http://127.0.0.1:{live.port}/admin.cgi?pass=letmein&mode=updinfo&song=Live%20Mix",
                           timeout=5)
    source = live.sources[0]
    assert source.presenter == "Night Shift" and source.title == "Live Mix"
    s.close()
    assert drain(source) > BYTES_PER_SECOND // 2


def test_wrong_passwords_are_turned_away_then_locked_out(live):
    for _ in range(5):
        s = socket.create_connection(("127.0.0.1", live.port + 1), timeout=5)
        s.sendall(b"guess\r\n")
        assert s.recv(100) == b"invalid password\r\n"
        s.close()
    conn, reply = icecast_source(live.port)  # even the right one, for a while
    assert b"429" in reply
    conn.close()
    assert not live.sources


def test_end_live_keeps_them_off_for_a_while(live):
    live.end(block_seconds=60)
    s = socket.create_connection(("127.0.0.1", live.port + 1), timeout=5)
    s.sendall(b"letmein\r\n")
    assert s.recv(100) == b"Stream In Use\r\n"
    s.close()


def test_a_port_in_use_is_reported():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        with pytest.raises(OSError):
            LiveInput(port - 1, "x", "ffmpeg", on_source=lambda s: True, host="127.0.0.1").start()


class FakeSource:
    """A presenter's decoded audio, made up."""

    def __init__(self, chunks, title=""):
        self.chunks = list(chunks)
        self.title = title
        self.ended = threading.Event()
        self.closed = False

    def read(self, timeout):
        if self.chunks:
            item = self.chunks.pop(0)
            if callable(item):  # something happening between two pieces of audio
                item()
                return self.read(timeout)
            return item
        self.ended.set()
        return None

    def quiet(self):
        return 0.0

    def close(self):
        self.closed = True
        self.ended.set()


def test_the_music_fades_under_the_presenter_then_comes_back():
    ch = channel_of({"A": (1000, 1.0), "B": (10, 1.0)}, crossfade=0.5)
    enc = Recorder()
    ch._play(0, 0.0, enc)  # A's last half second is held, to fade out
    logged, titles = [], []
    voice = steady(50, 1.0)
    source = FakeSource([voice[i:i + CHUNK] for i in range(0, len(voice), CHUNK)], title="")
    source.chunks.insert(3, lambda: setattr(source, "title", "Abba - SOS"))
    real = ch._titles.put
    ch._titles.put = lambda item: (titles.append(item[0]), real(item))
    ch.go_live(source, after=lambda: (ch.queue, 1, 0.0), on_title=lambda *a: logged.append(a),
               title="Rebirth Classics: DJ Rob")
    assert ch._skip.is_set()
    before = len(enc.data)
    assert ch._take_live(enc) == (ch.queue, 1, 0.0)
    live = samples(bytes(enc.data[before:]))
    assert live[0] == 1050 and live[-1] == 50  # A fading under the voice, then the voice alone
    assert len(live) * 2 == BYTES_PER_SECOND
    assert titles == ["Rebirth Classics: DJ Rob", "Abba - SOS"]
    assert [t for t, *_ in logged] == ["Abba - SOS"]  # the presenter's songs, for the play log
    assert ch.live is None and not ch._skip.is_set() and not ch._tail


def test_silence_fills_in_while_the_presenter_is_slow(monkeypatch):
    import station_server.live
    ch = channel_of({"A": (1000, 0.2)}, crossfade=0)
    enc = Recorder()
    quiet = FakeSource([None, None, steady(50, 0.1)])
    quiet.read = lambda timeout, q=quiet: q.chunks.pop(0) if q.chunks else q.ended.set()
    ch.go_live(quiet, after=lambda: (ch.queue, 0, 0.0))
    ch._take_live(enc)
    assert enc.data[:2 * CHUNK] == bytes(2 * CHUNK) and samples(bytes(enc.data[-4:])) == [50, 50]
    # A presenter whose audio stops arriving is taken off air.
    monkeypatch.setattr(station_server.live, "QUIET_SECONDS", 0.0)
    gone = FakeSource([None] * 1000)
    gone.read = lambda timeout: None
    gone.quiet = lambda: 1.0
    ch.go_live(gone, after=lambda: (ch.queue, 0, 0.0))
    ch._take_live(Recorder())
    assert gone.closed


def test_the_settings():
    st = clean_station({"live": True, "live_port": "8010", "live_password": "mic"})
    assert (st.live, st.live_port) == (True, 8010)
    hidden = st.to_json(secrets=False)
    assert hidden["live_password"] == "" and hidden["has_live_password"]
    assert clean_station({"live_password": ""}, st).live_password == "mic"  # blank keeps it
    assert not Station().live
    for bad in ({"live": True, "live_password": ""}, {"live_port": 80}, {"live_port": "x"}):
        with pytest.raises(ValueError):
            clean_station(bad)


def test_presenters_have_their_own_passwords():
    st = clean_station({"live": True, "presenters": [{"name": "DJ Rob", "password": "rob1"},
                                                     {"name": " ", "password": ""}]})  # an empty row is dropped
    assert [p["name"] for p in st.presenters] == ["DJ Rob"] and st.presenters[0]["id"]
    rob = st.presenters[0]["id"]
    hidden = st.to_json(secrets=False)["presenters"]
    assert hidden == [{"id": rob, "name": "DJ Rob", "password": "", "has_password": True}]
    # Saved from the page: a blank password keeps theirs; a new presenter needs one.
    again = clean_station({"live": True, "presenters": hidden + [{"name": "Sue", "password": "sue2"}]}, st)
    assert [(p["name"], p["password"]) for p in again.presenters] == [("DJ Rob", "rob1"), ("Sue", "sue2")]
    for bad in ([{"name": "Sue", "password": ""}], [{"name": "", "password": "x"}],
                [{"name": "A", "password": "same"}, {"name": "B", "password": "same"}]):
        with pytest.raises(ValueError):
            clean_station({"live": True, "presenters": bad})
    with pytest.raises(ValueError):  # not the shared password either
        clean_station({"live": True, "live_password": "same", "presenters": [{"name": "A", "password": "same"}]})


@needs_ffmpeg
def test_a_presenters_own_password_names_them():
    sources = []
    li = LiveInput(free_ports(), "", FFMPEG, on_source=lambda s: sources.append(s) or True, host="127.0.0.1",
                   presenters={"rob1": "DJ Rob", "sue2": "Sue"})
    li.start()
    try:
        conn, reply = icecast_source(li.port, password="", name="Anyone")  # no shared password: no way in
        assert b"401" in reply.split(b"\r\n")[0]
        conn.close()
        s = socket.create_connection(("127.0.0.1", li.port + 1), timeout=5)
        s.sendall(b"sue2\r\n")
        assert s.recv(100).startswith(b"OK2")
        s.sendall(b"icy-name:Whatever BUTT says\r\n\r\n" + mp3(0.5))
        assert wait_for(lambda: sources)
        assert sources[0].presenter == "Sue"  # by her password, not her encoder's name
        s.close()
        drain(sources[0])
    finally:
        li.stop()


@needs_ffmpeg
def test_presenters_volume_is_evened_out(tmp_path):
    from station_server.live import LEVEL_FILTER, LiveSource
    seen = []

    def decoder(args):
        seen.append(args)
        return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    quiet = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                            "-af", "volume=-30dB", "-ac", "2", "-b:a", "128k", "-f", "mp3", "-"],
                           capture_output=True, check=True).stdout
    levels = {}
    for level in (False, True):
        source = LiveSource(FFMPEG, "audio/mpeg", decoder=decoder, level=level)
        source.feed(quiet)
        source.finish()
        pcm = bytearray()
        end = time.time() + 15
        while not source.ended.is_set() and time.time() < end:
            pcm += source.read(0.2) or b""
        tail = samples(bytes(pcm[len(pcm) // 2:]))  # once the leveller has settled
        levels[level] = max(abs(x) for x in tail)
    assert LEVEL_FILTER in seen[1] and LEVEL_FILTER not in seen[0]
    assert levels[False] < 1500 and levels[True] > 4 * levels[False]  # a quiet presenter brought up


def test_two_stations_cant_share_a_live_port(api):  # noqa: F811
    api("/api/stations", {"name": "A", "live": True, "live_port": 8010, "live_password": "x"})
    status, body = api("/api/stations", {"name": "B", "live": True, "live_port": 8011, "live_password": "x"})
    assert status == 400 and "8011" in body["error"]
    assert api("/api/stations", {"name": "B", "live": True, "live_port": 8012, "live_password": "x"})[0] == 200


@needs_ffmpeg
def test_a_presenter_goes_live_on_air_and_hands_back(api, exports, icecast, monkeypatch):  # noqa: F811
    import station_player.playlog
    monkeypatch.setattr(station_player.playlog, "MIN_SECONDS", 0)
    port = free_ports()
    sid = api("/api/stations", {"name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
                                "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
                                "live": True, "live_port": port, "live_password": "letmein"})[1]["id"]
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Live shows"]["status"] == "ok" and "are free" in checks["Live shows"]["detail"]
    api(f"/api/stations/{sid}/on", {"on": True})
    station = lambda: api("/api/state")[1]["stations"][0]  # noqa: E731
    assert wait_for(lambda: station().get("live_input", {}).get("listening"))
    assert wait_for(lambda: icecast.titles)
    # A presenter added while on air can go live straight away, named by their password.
    api(f"/api/stations/{sid}", {"presenters": [{"name": "DJ Rob", "password": "rob1"}]})
    encoder = subprocess.Popen([FFMPEG, "-v", "error", "-re", "-f", "lavfi", "-i", "sine=frequency=500:duration=8",
                                "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k", "-content_type", "audio/mpeg",
                                "-ice_name", "BUTT", "-f", "mp3", f"icecast://source:rob1@127.0.0.1:{port}/live"])
    try:
        assert wait_for(lambda: station().get("live"))
        assert station()["live"]["presenter"] == "DJ Rob"
        assert wait_for(lambda: "Rebirth Classics: DJ Rob" in icecast.titles)
        auth = base64.b64encode(b"source:letmein").decode()
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{port}/admin/metadata?mode=updinfo&song=Abba%20-%20SOS",
            headers={"Authorization": f"Basic {auth}"}), timeout=5)
        assert wait_for(lambda: "Abba - SOS" in icecast.titles)
        assert encoder.wait(15) == 0
    finally:
        encoder.kill()
    # They've gone: the music comes back.
    assert wait_for(lambda: not station().get("live"))
    n = len(icecast.titles)
    assert wait_for(lambda: any(t.startswith("Artist - Track") for t in icecast.titles[n:]))
    runner = api.app.runners[sid]
    entries = runner.playlog.entries(date.today(), date.today())
    assert any(e.get("title") == "SOS" and e.get("artist") == "Abba" and e.get("album") == "Live" for e in entries)
    recorded = list(runner.recordings_folder().glob("* DJ Rob.mp3"))
    assert runner.recordings_folder().name == "Rebirth Classics" and len(recorded) == 1
    assert recorded[0].stat().st_size > 30_000  # about 3 s at 128 kbps
    # End live show: off, and kept off.
    api(f"/api/stations/{sid}/endlive", {})
    assert runner.live_input.blocked_until > time.monotonic()
    api(f"/api/stations/{sid}/on", {"on": False})


def test_shoutcast_headers_however_they_end():
    from station_server.live import _read_headers

    class Conn:
        def __init__(self, parts):
            self.parts = list(parts)

        def recv(self, n):
            return self.parts.pop(0) if self.parts else b""
    assert _read_headers(Conn([]), b"icy-name:A\r\nicy-br:128\r\n\r\nAUDIO") == (b"icy-name:A\r\nicy-br:128", b"AUDIO")
    assert _read_headers(Conn([b"icy-br:128\n\nAUDIO"]), b"icy-name:A\n") == (b"icy-name:A\nicy-br:128", b"AUDIO")
    assert _read_headers(Conn([]), b"\r\nAUDIO") == (b"", b"AUDIO")


@needs_ffmpeg
def test_each_show_is_recorded(tmp_path):
    li = LiveInput(free_ports(), "letmein", FFMPEG, on_source=lambda s: True, host="127.0.0.1",
                   record_dir=tmp_path / "recordings")
    li.start()
    try:
        conn, reply = icecast_source(li.port, name="DJ Rob/../x")
        audio = mp3(1.0)
        conn.sendall(audio)
        assert wait_for(lambda: li.source is not None)
        source = li.source
        conn.close()
        drain(source)
        assert source.recording.parent == tmp_path / "recordings" and source.recording.suffix == ".mp3"
        assert "/" not in source.recording.name[17:] and source.recording.read_bytes() == audio
    finally:
        li.stop()
    # A presenter turned away leaves no recording behind.
    refused = LiveInput(free_ports(), "letmein", FFMPEG, on_source=lambda s: False, host="127.0.0.1",
                        record_dir=tmp_path / "refused")
    refused.start()
    try:
        conn, reply = icecast_source(refused.port)
        assert b"403" in reply
        conn.close()
        assert not list((tmp_path / "refused").glob("*"))
    finally:
        refused.stop()


def test_titles_from_old_encoders_in_latin_1():
    from station_server.live import _query
    assert _query("song=Beyonc%C3%A9")["song"] == "Beyoncé"  # UTF-8
    assert _query("song=Beyonc%E9&pass=x")["song"] == "Beyoncé"  # Latin-1


def test_a_show_without_titles_is_logged_as_a_whole():
    ch = channel_of({"A": (1000, 0.2)}, crossfade=0)
    logged = []
    voice = steady(50, 0.5)
    ch.go_live(FakeSource([voice[i:i + CHUNK] for i in range(0, len(voice), CHUNK)]),
               after=lambda: (ch.queue, 0, 0.0), on_title=lambda *a: logged.append(a), title="R: DJ")
    ch._take_live(Recorder())
    assert len(logged) == 1 and logged[0][0] is None and logged[0][2] >= 0
