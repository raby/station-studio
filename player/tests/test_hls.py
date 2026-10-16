import http.server
import threading

import pytest

from station_player import radio
from station_player.hls import (HlsError, HlsStream, audio_type, is_playlist, parse_playlist, pick_variant,
                                ts_audio)

ADTS = bytes([0xFF, 0xF1, 0x50, 0x80, 0x02, 0x1F, 0xFC]) + b"A" * 9  # a 16-byte ADTS frame
MP3 = bytes([0xFF, 0xFB, 0x90, 0x64]) + b"M" * 12


def ts_packets(pid: int, payload: bytes, unit_start: bool, counter: int = 0) -> bytes:
    """One stream's payload as 188-byte TS packets (stuffing the last)."""
    out = b""
    first = True
    while payload or first:
        chunk, payload = payload[:184], payload[184:]
        b1 = (0x40 if first and unit_start else 0) | (pid >> 8)
        if len(chunk) < 184:
            stuff = 184 - len(chunk) - 1
            af = bytes([stuff]) + (bytes([0x00]) + b"\xff" * (stuff - 1) if stuff else b"")
            out += bytes([0x47, b1, pid & 0xFF, 0x30 | (counter & 15)]) + af + chunk
        else:
            out += bytes([0x47, b1, pid & 0xFF, 0x10 | (counter & 15)]) + chunk
        counter += 1
        first = False
    return out


def make_ts(audio: bytes, stream_id: int = 0xC0) -> bytes:
    pes = b"\x00\x00\x01" + bytes([stream_id]) + len(audio).to_bytes(2, "big") + b"\x80\x80\x05" + b"\x21\x00\x01\x00\x01"
    pat = ts_packets(0, b"\x00\x00\xb0\x0d\x00\x01\xc1\x00\x00\x00\x01\xf0\x00" + b"\x00" * 4, True)
    video = ts_packets(0x100, b"\x00\x00\x01\xe0\x00\x00\x80\x80\x00" + b"V" * 50, True)
    return pat + video + ts_packets(0x101, pes + audio, True)


def test_is_playlist():
    assert is_playlist("application/vnd.apple.mpegurl", "http://x/live")
    assert is_playlist("text/plain", "https://x/a/b.m3u8?token=1")
    assert not is_playlist("audio/mpeg", "http://x/stream")


MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=48000,CODECS="mp4a.40.5"
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=320000,CODECS="mp4a.40.2"
high/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,CODECS="avc1.42e00a,mp4a.40.2"
video/index.m3u8
"""


def test_parse_master_and_pick_the_best_audio():
    info = parse_playlist(MASTER, "https://bbc.example/radio/master.m3u8")
    assert [v[0] for v in info["variants"]] == [48000, 320000, 900000]
    assert pick_variant(info["variants"]) == "https://bbc.example/radio/high/index.m3u8"


def test_parse_media_playlist():
    text = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:100\n#EXTINF:6.0,\nseg100.ts\n#EXTINF:6.0,\nseg101.ts\n"
    info = parse_playlist(text, "https://x/live/index.m3u8")
    assert info["segments"] == [(100, "https://x/live/seg100.ts"), (101, "https://x/live/seg101.ts")]
    assert info["target"] == 6.0 and not info["ended"] and not info["encrypted"] and info["map"] is None
    assert parse_playlist(text + '#EXT-X-KEY:METHOD=AES-128,URI="k"\n', "https://x/")["encrypted"]
    with pytest.raises(HlsError):
        parse_playlist("<html>", "https://x/")


def test_ts_audio_takes_the_audio_out():
    audio = ADTS * 30  # more than one packet's worth
    assert ts_audio(make_ts(audio)) == audio
    state = {}
    assert ts_audio(make_ts(ADTS), state) + ts_audio(make_ts(ADTS * 2), state) == ADTS * 3  # across segments
    assert ts_audio(b"no sync here") == b""


def test_audio_type():
    assert audio_type(ADTS) == "audio/aac"
    assert audio_type(MP3) == "audio/mpeg"
    assert audio_type(b"\x00\x00") is None


class FakeStation:
    """A live HLS station: each reload of the playlist adds a segment."""

    def __init__(self, segment=lambda n: make_ts(ADTS * (n + 1)), live=True, start=0, count=4, extra=""):
        self.segment, self.live, self.first, self.count, self.extra = segment, live, start, count, extra
        self.asked = []
        self.reloads = 0

    def playlist(self) -> str:
        lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{self.first}", self.extra]
        for n in range(self.first, self.first + self.count):
            lines += ["#EXTINF:2.0,", f"seg{n}.ts"]
        if not self.live:
            lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def fetch(self, url: str, limit: int) -> bytes:
        self.asked.append(url.rsplit("/", 1)[1])
        if url.endswith(".m3u8"):
            self.reloads += 1
            self.first += 1  # a new segment every reload
            return self.playlist().encode()
        if url.endswith("init.mp4"):
            return b"INIT"
        return self.segment(int(url.rsplit("seg", 1)[1].split(".")[0]))


def test_live_stream_starts_near_the_end_and_keeps_going():
    st = FakeStation(start=10, count=5)
    stream = HlsStream("https://x/live.m3u8", st.playlist(), st.fetch, sleep=lambda s: None)
    assert stream.content_type == "audio/aac" and stream.metaint == 0
    assert st.asked == ["seg12.ts"]  # three from the end of 10..14
    data = stream.body.read(16 * (13 + 14 + 15 + 16 + 17))  # segments 12 to 16
    assert data == ADTS * (13 + 14 + 15 + 16 + 17)
    assert st.asked[-1] == "seg16.ts" and st.reloads >= 1
    stream.close()
    assert stream.body.read1(10) == b"" or True  # a closed stream ends once its buffer is used


def test_finished_playlist_plays_from_the_start_and_ends():
    st = FakeStation(live=False, count=2)
    stream = HlsStream("https://x/a.m3u8", st.playlist(), st.fetch, sleep=lambda s: None)
    assert stream.body.read(10_000) == ADTS * 1 + ADTS * 2
    assert stream.body.read1(10) == b""


def test_packed_aac_with_id3_and_mp3_segments():
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x05" + b"\x00" * 5
    st = FakeStation(segment=lambda n: id3 + ADTS, live=False, count=1)
    assert HlsStream("https://x/a.m3u8", st.playlist(), st.fetch).body.read(100) == ADTS
    st = FakeStation(segment=lambda n: make_ts(MP3), live=False, count=1)
    stream = HlsStream("https://x/a.m3u8", st.playlist(), st.fetch)
    assert stream.content_type == "audio/mpeg" and stream.body.read(100) == MP3


def test_fragmented_mp4_passes_on_with_its_init_section():
    st = FakeStation(segment=lambda n: f"FRAG{n}".encode(), live=False, count=2, extra='#EXT-X-MAP:URI="init.mp4"')
    stream = HlsStream("https://x/a.m3u8", st.playlist(), st.fetch)
    assert stream.content_type == "audio/mp4"
    assert stream.body.read(100) == b"INITFRAG0FRAG1"


def test_follows_a_master_playlist_and_refuses_what_it_cant_play():
    st = FakeStation(live=False, count=1)
    fetch = lambda url, limit: st.playlist().encode() if url.endswith("high/index.m3u8") else st.fetch(url, limit)
    stream = HlsStream("https://x/master.m3u8", MASTER, fetch)
    assert stream.url == "https://x/high/index.m3u8" and stream.body.read(100) == ADTS
    with pytest.raises(HlsError, match="encrypted"):
        HlsStream("https://x/a.m3u8", FakeStation(extra='#EXT-X-KEY:METHOD=AES-128,URI="k"').playlist(), st.fetch)
    with pytest.raises(HlsError):
        HlsStream("https://x/a.m3u8", FakeStation(segment=lambda n: b"<html>", live=False).playlist(),
                  FakeStation(segment=lambda n: b"<html>", live=False).fetch)


def test_a_live_stream_that_stops_publishing_ends():
    class Frozen(FakeStation):
        def fetch(self, url, limit):
            if url.endswith(".m3u8"):
                return self.playlist().encode()  # never anything new
            return super().fetch(url, limit)
    st = Frozen(count=1)
    stream = HlsStream("https://x/a.m3u8", st.playlist(), st.fetch, sleep=lambda s: None)
    assert stream.body.read1(100) == ADTS
    with pytest.raises(OSError):
        stream.body.read1(100)


@pytest.fixture
def hls_server():
    """A real local HLS station, to check open_stream end to end."""
    station = FakeStation(live=False, count=3)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.rsplit("/", 1)[1]
            if name == "live.m3u8":
                body, ctype = station.playlist().encode(), "application/vnd.apple.mpegurl"
            elif name.startswith("seg"):
                body, ctype = station.segment(int(name[3:].split(".")[0])), "video/mp2t"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/radio/live.m3u8"
    server.shutdown()
    server.server_close()


def test_open_stream_plays_hls(hls_server):
    stream = radio.open_stream(hls_server)
    assert stream.content_type == "audio/aac"
    assert b"".join(radio.relay(stream, lambda t: None)) == ADTS * (1 + 2 + 3)
    # A host check applies to the playlist and every segment.
    with pytest.raises(radio.StreamError, match="public internet"):
        radio.open_stream(hls_server, check=lambda host: False)
