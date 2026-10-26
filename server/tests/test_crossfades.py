"""Crossfades between songs, jingles every few songs, and the clock allowing for both."""
import struct
from array import array
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import FFMPEG, Unpaced, needs_ffmpeg, write_wav
from station_player.library import Track
from station_server.audio import BYTES_PER_SECOND, CHUNK, mix
from station_server.channel import Channel
from station_server.config import Station, clean_station
from station_server.onair import JingleBag, StationRunner
from station_server.playlist import Queue
from station_server.schedule import WeekStart, step_seconds, where_now
from station_server.senders import FileSender
from test_app import api, exports, wait_for  # noqa: F401 (fixtures)


def samples(data: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def steady(value: int, seconds: float) -> bytes:
    return (array("h", [value]) * (int(seconds * BYTES_PER_SECOND) // 2)).tobytes()


def test_mix_fades_the_tail_under_the_head():
    tail, head = struct.pack("<4h", 1000, 1000, 1000, 1000), struct.pack("<4h", 10, 10, 32767, -5)
    # The tail fades on a curve: halfway through it's at a quarter.
    assert samples(mix(tail, head, 0, 8)) == [1010, 1010, 32767, 245]  # full scale, not wrapped
    # The second half of the same fade, and a head longer than the tail.
    assert samples(mix(tail, head + struct.pack("<2h", 7, 7), 4, 8)) == [260, 260, 32767, -5, 7, 7]
    # A head shorter than the tail: the tail fades out over silence.
    assert samples(mix(tail, b"", 0, 8)) == [1000, 1000, 250, 250]


def test_two_loud_songs_are_eased_not_clipped():
    loud = struct.pack("<4h", 20000, -20000, 30000, -30000)
    out = samples(mix(loud, loud, 0, 16))
    assert out[0] == -out[1] and 24576 < out[0] < 32767  # 40000 rounded off below full scale
    assert out[2] < 32767 and out[2] >= 30000  # never quieter than the incoming song itself
    quiet = struct.pack("<2h", 30000, -30000)
    assert samples(mix(struct.pack("<2h", 0, 0), quiet, 0, 4)) == [30000, -30000]  # untouched


class Recorder:
    def __init__(self):
        self.data = bytearray()

    def write(self, pcm):
        self.data += pcm


def channel_of(sounds: dict, crossfade: float, **kw) -> Channel:
    """A channel whose tracks are steady tones: name -> (value, seconds)."""
    tracks = [Track(id=n, name=n, path=Path(f"/music/{n}.wav")) for n in sounds]

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        value, seconds = sounds[path.stem]
        data = steady(value, seconds)
        for i in range(0, len(data), CHUNK):
            yield data[i:i + CHUNK]

    sender = type("S", (), {"set_title": lambda self, t: None, "send": lambda self, b: None})()
    return Channel(Queue("Week", tracks), sender, "ffmpeg", pacer=Unpaced(), decoder=decoder,
                   level=False, crossfade=crossfade, **kw)


def test_a_crossfade_overlaps_the_songs():
    ch = channel_of({"A": (1000, 1.0), "B": (10, 1.0)}, crossfade=0.5)
    enc = Recorder()
    assert ch._play(0, 0.0, enc) and ch._play(1, 0.0, enc)
    ch._flush(enc)
    out = samples(bytes(enc.data))
    half = BYTES_PER_SECOND // 4  # samples in half a second
    assert len(out) == 3 * half  # 1.5 s: the two songs overlap by half a second
    assert set(out[:half]) == {1000}  # A alone
    assert out[half] == 1010 and abs(out[half + half // 2] - 260) <= 2  # A fading out under B
    assert out[2 * half] == 10 and abs(out[-1]) <= 1  # B alone, faded out at the very end


def test_without_a_crossfade_songs_play_end_to_end():
    ch = channel_of({"A": (1000, 1.0), "B": (10, 1.0)}, crossfade=0)
    enc = Recorder()
    ch._play(0, 0.0, enc)
    ch._play(1, 0.0, enc)
    assert len(enc.data) == 2 * BYTES_PER_SECOND and not ch._tail


def test_a_song_shorter_than_the_crossfade_and_one_that_cant_be_read():
    ch = channel_of({"A": (1000, 1.0), "B": (10, 0.2), "C": (20, 1.0)}, crossfade=0.5)
    enc = Recorder()
    ch._play(0, 0.0, enc)
    ch._play(1, 0.0, enc)  # B is over before A has faded: the rest of A fades over silence
    assert len(enc.data) + len(ch._tail) == BYTES_PER_SECOND  # B takes the crossfade's time
    tail = ch._tail
    ch._decode = lambda *a, **k: iter(())
    assert not ch._play(2, 0.0, enc)
    assert ch._tail == tail  # still waiting for a track that plays


def test_skipping_fades_out_quickly_whatever_the_crossfade():
    ch = channel_of({"A": (1000, 6.0)}, crossfade=5)
    decode = ch._decode

    def skip_soon(*a, **k):
        for i, chunk in enumerate(decode(*a, **k)):
            if i == 55:
                ch.skip()
            yield chunk
    ch._decode = skip_soon
    ch._play(0, 0.0, Recorder())
    assert len(ch._tail) == 2 * BYTES_PER_SECOND  # two seconds, not five


def test_skipping_fades_out_and_stopping_doesnt():
    ch = channel_of({"A": (1000, 3.0), "B": (10, 1.0)}, crossfade=0.5)
    enc = Recorder()
    decode = ch._decode

    def skip_soon(*a, **k):
        for i, chunk in enumerate(decode(*a, **k)):
            if i == 10:
                ch.skip()
            yield chunk
    ch._decode = skip_soon
    ch._play(0, 0.0, enc)
    assert len(ch._tail) == BYTES_PER_SECOND // 2  # the half second before the skip, to fade out
    ch._decode = decode
    ch.stop()
    ch._play(1, 0.0, enc)
    assert not ch._tail


@needs_ffmpeg
def test_a_jingle_every_few_songs_not_logged(tmp_path):
    played, logged, titles = [], [], []
    music = Queue("Week", [Track(id=f"A{i}", name=f"A{i}", path=tmp_path / f"A{i}.wav") for i in range(5)])
    jingle = Track(id="J", name="Ident", path=tmp_path / "J.wav")

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        played.append(path.stem)
        assert (channel.jingle is jingle) == (path.stem == "J")
        if len(played) >= 8:
            channel.stop()
        yield bytes(CHUNK)

    channel = Channel(music, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder,
                      level=False, crossfade=0.05, jingle=lambda: jingle, jingle_every=2,
                      jingle_title="Rebirth Classics", on_played=lambda now, s: logged.append(now.track.name))
    real = channel._titles.put
    channel._titles.put = lambda item: (titles.append(item[0]), real(item))
    channel.run()
    assert played == ["A0", "A1", "J", "A2", "A3", "J", "A4", "A0"]
    assert "J" not in logged and logged[:4] == ["A0", "A1", "A2", "A3"]
    assert titles[2] == "Rebirth Classics" and channel.jingle is None


@needs_ffmpeg
def test_no_jingle_in_a_show_or_after_a_skip(tmp_path):
    played = []
    music = Queue("Week", [Track(id=f"A{i}", name=f"A{i}", path=tmp_path / f"A{i}.wav") for i in range(4)])
    show = Queue("Show", [Track(id=f"S{i}", name=f"S{i}", path=tmp_path / f"S{i}.wav") for i in range(3)])

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        played.append(path.stem)
        if path.stem == "A0":
            channel.insert(show, after=lambda: (music, 1, 0.0), now=False)  # a show after this song
        if path.stem == "A1":
            channel.skip()
        if len(played) >= 7:
            channel.stop()
        yield bytes(CHUNK)

    channel = Channel(music, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder,
                      level=False, jingle=lambda: Track(id="J", name="J", path=tmp_path / "J.wav"),
                      jingle_every=1)
    channel.run()
    assert played == ["A0", "S0", "S1", "S2", "A1", "A2", "J"]


def test_no_jingle_while_the_fallback_plays(tmp_path):
    write_wav(tmp_path / "jingles" / "a.wav", seconds=0.2)
    runner = StationRunner(Station(name="R", jingles=str(tmp_path / "jingles")), None, tmp_path)
    runner.jingles = JingleBag(runner.station)
    assert runner._jingle().path.stem == "a"
    runner.playing_fallback = lambda: True
    assert runner._jingle() is None


def test_a_show_is_timed_allowing_for_the_crossfade(tmp_path):
    from station_server.schedule import Show
    for n in "ab":
        write_wav(tmp_path / "show" / f"{n}.wav", seconds=20.0)
    show = Show.parse({"name": "Mix", "days": [5], "time": "20:00", "source": str(tmp_path / "show")})
    runner = StationRunner(Station(name="R"), None, tmp_path)
    assert runner.show_length(show) == pytest.approx(40, abs=0.5)
    runner.station.crossfade = 3.0
    assert runner.show_length(show) == pytest.approx(34, abs=0.5)  # each song's end overlaps what follows


def test_jingles_are_shuffled_and_none_repeats_until_all_have_played(tmp_path):
    for n in "abcd":
        write_wav(tmp_path / "jingles" / f"{n}.wav", seconds=0.2)
    st = Station(name="R", jingles=str(tmp_path / "jingles"))
    order = iter([[0, 1, 2, 3], [3, 2, 1, 0]])

    def shuffle(bag):
        bag[:] = [bag[i] for i in next(order)]
    bag = JingleBag(st, shuffle=shuffle)
    first = [bag().path.stem for _ in range(4)]
    second = [bag().path.stem for _ in range(4)]
    assert sorted(first) == sorted(second) == list("abcd")
    assert first[-1] != second[0]  # the last of one round isn't the first of the next
    assert bag.seconds == pytest.approx(0.2, abs=0.05)
    assert JingleBag(Station(name="R", jingles=str(tmp_path / "none")))() is None


def test_the_clock_allows_for_the_crossfade_and_jingles():
    tracks = [Track(id=str(i), name=str(i), duration_ms=200_000) for i in range(3)]
    week = WeekStart()
    monday = week.latest(datetime(2026, 10, 28, 12, 0)).astimezone()
    at = monday + timedelta(seconds=390)
    assert where_now(tracks, week, at) == (1, 190.0)
    for t in tracks:
        t.step_adjust = -5.0
    assert step_seconds(tracks[0]) == 195.0
    assert where_now(tracks, week, at) == (2, 0.0)


def test_the_runner_tells_the_clock(tmp_path):
    for n in "ab":
        write_wav(tmp_path / "jingles" / f"{n}.wav", seconds=8.0)
    st = Station(name="R", crossfade=3.0, jingles=str(tmp_path / "jingles"), jingle_every=4)
    runner = StationRunner(st, None, tmp_path)
    runner.jingles = JingleBag(st)
    queue = Queue("Week", [Track(id="1", name="1", duration_ms=200_000)])
    runner._step(queue)
    # Each song starts the next 3 s early; every 4 songs an 8 s jingle comes between.
    assert queue.tracks[0].step_adjust == pytest.approx(-3.0 + 8.0 / 4, abs=0.05)
    runner.jingles = None
    runner._step(queue)
    assert queue.tracks[0].step_adjust == -3.0


def test_the_settings():
    st = clean_station({"crossfade": "4", "jingles": " ~/Jingles ", "jingle_every": "3"})
    assert (st.crossfade, st.jingles, st.jingle_every) == (4.0, "~/Jingles", 3)
    assert Station().crossfade == 0 and not Station().jingles
    for bad in ({"crossfade": 11}, {"crossfade": -1}, {"jingle_every": 0}, {"jingle_every": "x"}):
        with pytest.raises(ValueError):
            clean_station(bad)


def test_the_ready_check_looks_at_the_jingles(api, exports, tmp_path):  # noqa: F811
    write_wav(tmp_path / "jingles" / "ident.wav", seconds=0.5)
    sid = api("/api/stations", {"name": "R", "playlist": str(exports), "jingles": str(tmp_path / "jingles")})[1]["id"]
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Jingles"]["status"] == "ok" and "1 jingles" in checks["Jingles"]["detail"]
    api(f"/api/stations/{sid}", {"jingles": str(tmp_path / "gone")})
    checks = {c["name"]: c for c in api(f"/api/stations/{sid}/check", {})[1]["checks"]}
    assert checks["Jingles"]["status"] == "fail"


@needs_ffmpeg
def test_crossfaded_on_air_with_jingles(api, exports, icecast, tmp_path, monkeypatch):  # noqa: F811
    import station_player.playlog
    monkeypatch.setattr(station_player.playlog, "MIN_SECONDS", 0)
    write_wav(tmp_path / "jingles" / "ident.wav", seconds=0.3)
    sid = api("/api/stations", {"name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
                                "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
                                "crossfade": 0.2, "jingles": str(tmp_path / "jingles"), "jingle_every": 1})[1]["id"]
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: "Rebirth Classics" in icecast.titles, timeout=20)  # a jingle went out
    runner = api.app.runners[sid]
    assert wait_for(lambda: len(runner.playlog.entries(datetime.now().date(), datetime.now().date())) >= 2)
    names = [e.get("title") for e in runner.playlog.entries(datetime.now().date(), datetime.now().date())]
    assert "ident" not in names
    api(f"/api/stations/{sid}/on", {"on": False})
