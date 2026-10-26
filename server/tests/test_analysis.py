"""Measuring tracks: loudness, and the silence at either end."""
import subprocess
import threading
import time

from conftest import FFMPEG, needs_ffmpeg, write_wav
from station_player.library import Track
from station_server.analysis import Analyser, Measured, parse
from station_server.schedule import track_seconds

REPORT = """
size=       0kB time=00:00:00.00 bitrate=N/A speed=   0x    [silencedetect @ 0x5] silence_start: 0
[silencedetect @ 0x5] silence_end: 1.50002 | silence_duration: 1.50002
[silencedetect @ 0x5] silence_start: 4.5
size=N/A time=00:00:05.90 bitrate=N/A speed=69.4x
[silencedetect @ 0x5] silence_end: 6 | silence_duration: 1.5
[Parsed_ebur128_1 @ 0x6] Summary:
  Integrated loudness:
    I:         -16.4 LUFS
    Threshold: -26.5 LUFS
  True peak:
    Peak:      -12.2 dBFS
"""


def test_reading_ffmpegs_report():
    m = parse(REPORT)
    assert (m.start, m.end, m.lufs, m.peak_db) == (1.5, 4.5, -16.4, -12.2)
    assert round(m.gain_db, 1) == -1.6 and round(m.peak, 3) == 0.245


def test_silence_in_the_middle_is_kept_and_all_silence_is_left_alone():
    middle = REPORT.replace("silence_start: 0\n", "silence_start: 0.9\n")
    assert parse(middle).start == 0.0 and parse(middle).end == 4.5
    silent = "time=00:00:03.00\nsilence_start: 0\nsilence_end: 3 | silence_duration: 3\n"
    m = parse(silent)
    assert (m.start, m.end, m.lufs) == (0.0, 3.0, None)
    assert parse("no time here") is None


@needs_ffmpeg
def test_measuring_a_real_file(tmp_path):
    tone = write_wav(tmp_path / "tone.wav", seconds=2.0, rate=8000)
    gap = tmp_path / "gap.wav"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
                    "-t", "1", str(gap)], check=True)
    song = tmp_path / "song.wav"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(gap), "-i", str(tone), "-i", str(gap),
                    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(song)], check=True)
    from station_server.analysis import measure
    m = measure(FFMPEG, song)
    assert abs(m.start - 1.0) < 0.05 and abs(m.end - 3.0) < 0.05 and m.lufs is not None


def test_tracks_are_measured_once_and_remembered(tmp_path):
    files = [write_wav(tmp_path / f"{i}.wav", seconds=0.1) for i in range(3)]
    tracks = [Track(id=str(i), name=f"T{i}", path=f, location=f) for i, f in enumerate(files)]
    asked = []

    def fake(ffmpeg, path):
        asked.append(path.name)
        return Measured(lufs=-20.0, peak_db=-3.0, start=0.5, end=200.5)

    analyser = Analyser("ffmpeg", tmp_path / "analysis.json", measure=fake)
    assert analyser.want(tracks) == 3
    end = time.time() + 5
    while time.time() < end and not all(getattr(t, "measured", None) for t in tracks):
        time.sleep(0.02)
    assert asked == ["0.wav", "1.wav", "2.wav"]  # in play order
    assert track_seconds(tracks[0]) == 200.0
    while time.time() < end and not (tmp_path / "analysis.json").exists():
        time.sleep(0.02)
    # Next time (a new week's playlist, or a restart) it's known straight away.
    again = [Track(id=str(i), name=f"T{i}", path=f, location=f) for i, f in enumerate(files)]
    later = Analyser("ffmpeg", tmp_path / "analysis.json", measure=fake)
    assert later.want(again) == 0 and again[2].measured.start == 0.5
    assert len(asked) == 3
    # An edited file is measured again.
    write_wav(files[1], seconds=0.2)
    assert later.want(again) == 1


def test_no_ffmpeg_no_measuring(tmp_path):
    f = write_wav(tmp_path / "a.wav", seconds=0.1)
    analyser = Analyser(None, None, measure=lambda *a: (_ for _ in ()).throw(AssertionError("measured")))
    assert analyser.want([Track(id="a", name="A", path=f, location=f)]) == 1
    assert analyser.pending() == 0 and threading.active_count() >= 1
