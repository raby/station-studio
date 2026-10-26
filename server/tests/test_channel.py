import subprocess
import threading
import time

import pytest

from conftest import FFMPEG, Unpaced, needs_ffmpeg, station_export, write_wav
from station_server.audio import BYTES_PER_SECOND, CHUNK, decode
from station_server.channel import Channel, Pacer, level_gain
from station_server.playlist import load_queue
from station_server.senders import FileSender, ShoutcastSender, StreamInfo


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, secs):
        self.slept.append(round(secs, 3))
        self.now += secs


def test_pacer_sends_a_head_start_then_real_time():
    clock = FakeClock()
    pacer = Pacer(lead=2.0, clock=clock, sleep=clock.sleep)
    second = BYTES_PER_SECOND
    for _ in range(3):
        pacer.wait(second)  # 0s, 1s, 2s of audio ahead: no waiting yet
    assert clock.slept == []
    pacer.wait(second)  # 3s ahead with 2s allowed: wait 1s
    assert clock.slept == [1.0]


def test_pacer_does_not_burst_after_falling_behind():
    clock = FakeClock()
    pacer = Pacer(lead=0.0, clock=clock, sleep=clock.sleep)
    pacer.wait(BYTES_PER_SECOND)
    clock.now += 10  # the computer stalled for 10 seconds
    pacer.wait(BYTES_PER_SECOND)  # catches up instead of racing
    pacer.wait(BYTES_PER_SECOND)
    assert clock.slept == [1.0]


def test_pacer_knows_how_far_ahead_it_is():
    clock = FakeClock()
    pacer = Pacer(lead=2.0, clock=clock, sleep=clock.sleep)
    assert pacer.ahead() == 0.0
    pacer.wait(BYTES_PER_SECOND * 2)
    assert pacer.ahead() == 2.0  # two seconds sent, none heard yet
    clock.now += 1.5
    assert pacer.ahead() == 0.5


def test_titles_wait_until_heard_and_go_in_order(tmp_path):
    class Sender:
        titles = []

        def set_title(self, title):
            self.titles.append(title)

    queue = load_queue(station_export(tmp_path, [write_wav(tmp_path / "a.wav")]))
    channel = Channel(queue, Sender(), "ffmpeg", pacer=Unpaced())
    now = time.monotonic()
    channel._titles.put(("A - B", now + 0.3))
    channel._titles.put(("C - D", now + 0.1))  # skipped straight on: still after A - B
    worker = threading.Thread(target=channel._send_titles, daemon=True)
    worker.start()
    time.sleep(0.15)
    assert Sender.titles == []  # A - B isn't heard yet
    time.sleep(0.4)
    assert Sender.titles == ["A - B", "C - D"]
    channel._titles.put(("E - F", time.monotonic() + 5))
    channel.stop()  # stopped while waiting: never sent
    worker.join(2)
    assert not worker.is_alive() and Sender.titles == ["A - B", "C - D"]


def _duration(path) -> float:
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    t = out.rsplit("time=", 1)[1].split()[0]
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@needs_ffmpeg
def test_decode_gives_cd_audio_in_chunks(tmp_path):
    f = write_wav(tmp_path / "a.wav", seconds=0.5, rate=22050)  # resampled to 44.1k stereo
    chunks = list(decode(FFMPEG, f))
    assert all(len(c) == CHUNK for c in chunks[:-1])
    assert abs(sum(map(len, chunks)) / BYTES_PER_SECOND - 0.5) < 0.01


@needs_ffmpeg
def test_decode_of_a_broken_file_just_ends(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio at all")
    assert list(decode(FFMPEG, bad)) == []


@needs_ffmpeg
def test_plays_the_week_gaplessly_to_a_file(tmp_path):
    files = [write_wav(tmp_path / f"{i}.wav", seconds=1.0, freq=330 + 110 * i) for i in range(3)]
    queue = load_queue(station_export(tmp_path, files))
    out = tmp_path / "air.mp3"
    sender = FileSender(out)
    sender.connect()
    seen = []
    channel = Channel(queue, sender, FFMPEG, loop=False, pacer=Unpaced(),
                      on_track=lambda now: seen.append(now.index))
    channel.run()
    sender.close()
    assert seen == [0, 1, 2]
    # One continuous MP3, as long as the three tracks together (no gaps).
    assert abs(_duration(out) - 3.0) < 0.1
    titles = out.with_suffix(".txt").read_text().splitlines()
    assert [line.split("  ", 1)[1] for line in titles] == ["Artist 1 - Song 1", "Artist 2 - Song 2", "Artist 3 - Song 3"]


@needs_ffmpeg
def test_loops_and_starts_where_asked(tmp_path):
    files = [write_wav(tmp_path / f"{i}.wav", seconds=0.2) for i in range(3)]
    queue = load_queue(station_export(tmp_path, files))
    sender = FileSender(tmp_path / "air.mp3")
    sender.connect()
    seen = []

    def on_track(now):
        seen.append(now.index)
        if len(seen) == 5:
            channel.stop()

    channel = Channel(queue, sender, FFMPEG, start=2, pacer=Unpaced(), on_track=on_track)
    channel.run()
    sender.close()
    assert seen == [2, 0, 1, 2, 0]


@needs_ffmpeg
def test_skips_unreadable_tracks_and_gives_up_if_none_play(tmp_path):
    good = write_wav(tmp_path / "good.wav", seconds=0.3)
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    sender = FileSender(tmp_path / "air.mp3")
    sender.connect()
    queue = load_queue(station_export(tmp_path, [bad, good]))
    Channel(queue, sender, FFMPEG, loop=False, pacer=Unpaced()).run()
    assert abs(_duration(tmp_path / "air.mp3") - 0.3) < 0.1

    queue = load_queue(station_export(tmp_path, [bad]))
    with pytest.raises(RuntimeError, match="No track"):
        Channel(queue, sender, FFMPEG, pacer=Unpaced()).run()
    sender.close()


@needs_ffmpeg
def test_on_air_to_shoutcast(tmp_path, shoutcast):
    files = [write_wav(tmp_path / f"{i}.wav", seconds=0.5) for i in range(2)]
    queue = load_queue(station_export(tmp_path, files, titles=[("Inner City", "Good Life"), ("Ce Ce Peniston", "Finally")]))
    sender = ShoutcastSender("127.0.0.1", shoutcast.port, "hackme", StreamInfo(name=queue.name))
    sender.connect()
    Channel(queue, sender, FFMPEG, loop=False, pacer=Unpaced()).run()
    sender.close()
    assert shoutcast.audio[:2] in (b"\xff\xfb", b"\xff\xfa", b"ID")  # an MP3 stream
    for _ in range(50):  # titles are sent in the background
        if len(shoutcast.titles) == 2:
            break
        shoutcast.got_title.wait(0.1)
    assert sorted(shoutcast.titles) == ["Ce Ce Peniston - Finally", "Inner City - Good Life"]


def test_level_gain_follows_tags_and_peak(tmp_path, monkeypatch):
    from station_player.tags import Tags

    from station_server import channel as mod

    track = load_queue(station_export(tmp_path, [write_wav(tmp_path / "a.wav")])).tracks[0]
    cases = [
        (Tags(), 0.0),
        (Tags(gain_db=-6.0, peak=1.0), -6.0),
        (Tags(gain_db=15.0), 10.0),  # boost capped
        (Tags(gain_db=8.0, peak=0.5), 6.02),  # held below full scale
    ]
    for tags, want in cases:
        monkeypatch.setattr(mod, "read_tags", lambda _p, t=tags: t)
        assert level_gain(track) == pytest.approx(want, abs=0.01)


def _fake_queue(name, n, tmp_path):
    from station_player.library import Track
    from station_server.playlist import Queue
    return Queue(name, [Track(id=f"{name}{i}", name=f"{name} {i}", path=tmp_path / f"{name}{i}.wav")
                        for i in range(n)])


@needs_ffmpeg
def test_load_switches_at_the_next_track(tmp_path):
    played = []
    a, b = _fake_queue("A", 3, tmp_path), _fake_queue("B", 3, tmp_path)

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        played.append(path.stem)
        if path.stem == "A0":
            channel.load(b, where=lambda q: (2, 0.0))
        if len(played) >= 3:
            channel.stop()
        yield bytes(CHUNK)

    channel = Channel(a, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder, level=False)
    channel.run()
    assert played == ["A0", "B2", "B0"]


@needs_ffmpeg
def test_keep_going_sends_silence_when_nothing_plays(tmp_path):
    import threading
    queue = _fake_queue("A", 2, tmp_path)
    tries = []

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        tries.append(path.stem)
        return iter(())

    out = tmp_path / "out.mp3"
    sender = FileSender(out)
    sender.connect()
    channel = Channel(queue, sender, FFMPEG, pacer=Unpaced(), decoder=decoder, keep_going=True, level=False)
    t = threading.Thread(target=channel.run)
    t.start()
    for _ in range(200):
        if len(tries) > 2:
            break
        threading.Event().wait(0.05)
    channel.stop()
    t.join(10)
    sender.close()
    assert channel.stuck
    assert len(tries) > 2  # went round again after the silence
    assert out.stat().st_size > 0


@needs_ffmpeg
def test_without_keep_going_a_dead_playlist_stops(tmp_path):
    queue = _fake_queue("A", 2, tmp_path)
    channel = Channel(queue, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(),
                      decoder=lambda *a, **k: iter(()), level=False)
    with pytest.raises(RuntimeError):
        channel.run()


@needs_ffmpeg
def test_a_show_plays_once_then_the_music_comes_back(tmp_path):
    played = []
    music, show = _fake_queue("A", 4, tmp_path), _fake_queue("S", 2, tmp_path)

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        played.append(path.stem)
        if path.stem == "A0":
            channel.insert(show, after=lambda: (music, 2, 0.0), now=True)
        if path.stem == "S0":
            assert channel.in_show
        if len(played) >= 5:
            channel.stop()
        yield bytes(CHUNK)

    channel = Channel(music, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder, level=False)
    channel.run()
    assert played == ["A0", "S0", "S1", "A2", "A3"]
    assert not channel.in_show


@needs_ffmpeg
def test_an_unreadable_show_just_ends(tmp_path):
    played = []
    music, show = _fake_queue("A", 3, tmp_path), _fake_queue("S", 1, tmp_path)

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        played.append(path.stem)
        if path.stem == "S0":
            return  # nothing readable
        if len(played) >= 3:
            channel.stop()
        yield bytes(CHUNK)

    channel = Channel(show, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder,
                      level=False, keep_going=True, after=lambda: (music, 1, 0.0))
    channel.run()
    assert played == ["S0", "A1", "A2"] and not channel.stuck


@needs_ffmpeg
def test_the_fallback_plays_instead_of_silence(tmp_path):
    played = []
    music, jingles = _fake_queue("A", 2, tmp_path), _fake_queue("J", 2, tmp_path)
    during = []

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0):
        played.append(path.stem)
        if path.stem.startswith("A"):
            return  # the music drive is gone
        during.append(channel.on_fallback)
        if len(played) >= 7:
            channel.stop()
        yield bytes(CHUNK)

    channel = Channel(music, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder,
                      level=False, keep_going=True, fallback=lambda: (jingles, lambda: (music, 0, 0.0)))
    channel.run()
    # Both songs fail, the jingles play, then the songs are tried again.
    assert played == ["A0", "A1", "J0", "J1", "A0", "A1", "J0"]
    assert all(during)


@needs_ffmpeg
def test_a_measured_track_leaves_out_its_silence(tmp_path):
    from station_server.analysis import Measured
    calls = []
    music = _fake_queue("A", 1, tmp_path)
    music.tracks[0].measured = Measured(lufs=-12.0, peak_db=-1.0, start=1.25, end=181.25)

    def decoder(ffmpeg, path, gain_db=0.0, start=0.0, length=None):
        calls.append((round(gain_db, 2), start, length))
        channel.stop()
        yield bytes(CHUNK)

    channel = Channel(music, FileSender(tmp_path / "out.mp3"), FFMPEG, pacer=Unpaced(), decoder=decoder,
                      offset=10.0)
    channel.run()
    # Picked up 10 s in: 10 s after the sound starts, to where it ends.
    # Measured at -12 LUFS, so 6 dB down to -18 (no tags on the test file).
    assert calls == [(-6.0, 11.25, 170.0)]
    assert channel.now.levelled_by == "measured"
