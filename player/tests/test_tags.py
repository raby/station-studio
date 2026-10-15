import random
import zlib
import struct
import time
from pathlib import Path

import pytest

from station_player.tags import (
    Tags, TagCache, folder_cover, image_type, read_art, read_tags,
)

FIXTURES = Path(__file__).parent / "fixtures"
TAGGED = sorted(p for p in FIXTURES.iterdir() if p.name.startswith("tagged"))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 24


@pytest.mark.parametrize("path", TAGGED, ids=lambda p: p.name)
def test_reads_tags_from_every_format(path):
    # Fixtures were written by ffmpeg (and mutagen for Ogg pictures).
    tags = read_tags(path)
    assert tags.title == "Tëst Title"
    assert tags.artist == "The Artist"
    assert tags.album == "An Album"
    assert tags.genre == "Dub"
    assert 950 <= tags.duration_ms <= 1100


@pytest.mark.parametrize("name", ["tagged-id3v23.mp3", "tagged-id3v24.mp3", "tagged.m4a",
                                  "tagged.flac", "tagged.ogg", "tagged.opus"])
def test_reads_embedded_art(name):
    data, mime = read_art(FIXTURES / name)
    assert mime in ("image/png", "image/jpeg")
    assert image_type(data) == mime


def test_mp3_without_tags_still_gets_a_duration():
    tags = read_tags(FIXTURES / "untagged-cbr.mp3")
    assert tags.title is None
    assert 950 <= tags.duration_ms <= 1100


def test_files_without_art_return_none(tmp_path):
    assert read_art(FIXTURES / "tagged.wav") is None
    assert read_art(tmp_path / "missing.mp3") is None


# ---- hand-built ID3 cases -----------------------------------------------------

def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3(frames: bytes, major=4, flags=0, size=None) -> bytes:
    return b"ID3" + bytes([major, 0, flags]) + _syncsafe(size if size is not None else len(frames)) + frames


def frame(fid: str, body: bytes, major=4, flags=0, plain_size=False) -> bytes:
    if major == 2:
        return fid.encode() + len(body).to_bytes(3, "big") + body
    size = struct.pack(">I", len(body)) if (major == 3 or plain_size) else _syncsafe(len(body))
    return fid.encode() + size + struct.pack(">H", flags) + body


def text(value: str, enc=3) -> bytes:
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}[enc]
    return bytes([enc]) + value.encode(codec)


def write(tmp_path, data: bytes, name="x.mp3") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_id3v22_and_text_encodings(tmp_path):
    body = (frame("TT2", text("Ünïcode", 1), major=2) + frame("TP1", text("Latin é", 0), major=2)
            + frame("TAL", text("BE", 2), major=2) + frame("TCO", text("(17)", 0), major=2))
    tags = read_tags(write(tmp_path, id3(body, major=2)))
    assert (tags.title, tags.artist, tags.album, tags.genre) == ("Ünïcode", "Latin é", "BE", "Rock")


def test_id3_genre_forms(tmp_path):
    for raw, want in [("17", "Rock"), ("(17)Indie", "Indie"), ("Drum & Bass", "Drum & Bass"), ("(999)", "(999)")]:
        tags = read_tags(write(tmp_path, id3(frame("TCON", text(raw)))))
        assert tags.genre == want, raw


def test_id3v24_multiple_values_and_album_artist_fallback(tmp_path):
    body = frame("TIT2", text("A\x00B")) + frame("TPE2", text("Various"))
    tags = read_tags(write(tmp_path, id3(body)))
    assert tags.title == "A / B"
    assert tags.artist == "Various"  # no TPE1, so the album artist


def test_itunes_style_non_syncsafe_v24_frame_sizes(tmp_path):
    # A 200-byte title: plain size 0x000000C8 has a byte >= 0x80.
    long_title = "x" * 199
    body = frame("TIT2", text(long_title), plain_size=True) + frame("TPE1", text("After"), plain_size=True)
    tags = read_tags(write(tmp_path, id3(body)))
    assert tags.title == long_title
    assert tags.artist == "After"


def test_tag_level_unsynchronisation_v23(tmp_path):
    pic = b"\x00image/jpeg\x00\x03\x00" + b"\xff\xd8\xff\xe0" + b"\x00" * 8
    body = frame("TIT2", text("Song"), major=3) + frame("APIC", pic, major=3)
    unsynced = body.replace(b"\xff", b"\xff\x00")
    path = write(tmp_path, id3(unsynced, major=3, flags=0x80))
    assert read_tags(path).title == "Song"
    data, mime = read_art(path)
    assert mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff")


def test_compressed_and_grouped_v23_frames(tmp_path):
    import zlib
    raw = text("Packed")
    compressed = struct.pack(">I", len(raw)) + b"\x07" + zlib.compress(raw)  # size, group id, data
    body = frame("TIT2", compressed, major=3, flags=0x0080 | 0x0020)
    assert read_tags(write(tmp_path, id3(body, major=3))).title == "Packed"


def test_extended_header_is_skipped(tmp_path):
    ext = _syncsafe(6) + b"\x01\x00"
    body = ext + frame("TIT2", text("After ext"))
    assert read_tags(write(tmp_path, id3(body, flags=0x40))).title == "After ext"


def test_id3v1_fallback(tmp_path):
    v1 = b"TAG" + b"Old Title".ljust(30, b"\x00") + b"Old Artist".ljust(30, b"\x00") \
        + b"Old Album".ljust(30, b"\x00") + b"1999" + b"\x00" * 30 + bytes([17])
    tags = read_tags(write(tmp_path, b"\x00" * 300 + v1))
    assert (tags.title, tags.artist, tags.album, tags.genre) == ("Old Title", "Old Artist", "Old Album", "Rock")


def test_apic_with_utf16_description(tmp_path):
    desc = "Cover".encode("utf-16") + b"\x00\x00"
    pic = b"\x01image/png\x00\x03" + desc + PNG
    data, mime = read_art(write(tmp_path, id3(frame("APIC", pic))))
    assert mime == "image/png" and data == PNG


def test_art_with_unknown_bytes_is_refused(tmp_path):
    pic = b"\x00text/html\x00\x03\x00<script>alert(1)</script>"
    assert read_art(write(tmp_path, id3(frame("APIC", pic)))) is None


def test_image_type():
    assert image_type(JPEG) == "image/jpeg"
    assert image_type(PNG) == "image/png"
    assert image_type(b"GIF89a....") == "image/gif"
    assert image_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert image_type(b"<svg>") is None


# ---- hostile input -----------------------------------------------------------

def test_truncated_and_corrupted_files_never_raise(tmp_path):
    rng = random.Random(1234)
    started = time.monotonic()
    for src in TAGGED + [FIXTURES / "untagged-cbr.mp3"]:
        data = src.read_bytes()
        for cut in range(0, len(data), max(1, len(data) // 40)):
            p = write(tmp_path, data[:cut], "cut" + src.suffix)
            assert isinstance(read_tags(p), Tags)
            read_art(p)
        for _ in range(30):
            buf = bytearray(data)
            for _ in range(8):
                buf[rng.randrange(len(buf))] = rng.randrange(256)
            p = write(tmp_path, bytes(buf), "flip" + src.suffix)
            assert isinstance(read_tags(p), Tags)
            read_art(p)
    assert time.monotonic() - started < 30


def test_huge_declared_sizes_are_not_trusted(tmp_path):
    # Claims of gigabyte-sized tags, atoms and blocks must not be read.
    assert read_tags(write(tmp_path, id3(b"", size=(1 << 28) - 1))).title is None
    mp4 = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4 + struct.pack(">I", 0xFFFFFFF0) + b"moov"
    assert read_tags(write(tmp_path, mp4, "x.m4a")).title is None
    flac = b"fLaC" + bytes([0x04]) + b"\xff\xff\xff"
    assert read_tags(write(tmp_path, flac, "x.flac")).title is None
    zero_atom = struct.pack(">I", 16) + b"ftypM4A " + b"\x00" * 4 + struct.pack(">I", 0) + b"free"
    assert read_tags(write(tmp_path, zero_atom, "y.m4a")).title is None


def test_not_audio_at_all(tmp_path):
    assert read_tags(write(tmp_path, b"<html>not audio</html>", "x.mp3")) == Tags()
    assert read_tags(write(tmp_path, b"", "empty.flac")) == Tags()


# ---- folder covers -------------------------------------------------------------

def test_folder_cover_picks_known_names_case_insensitively(tmp_path):
    song = write(tmp_path, b"", "song.mp3")
    assert folder_cover(song) is None
    write(tmp_path, PNG, "notes.png")
    assert folder_cover(song) is None
    write(tmp_path, JPEG, "Folder.JPG")
    assert folder_cover(song).name == "Folder.JPG"
    write(tmp_path, PNG, "cover.png")
    assert folder_cover(song).name == "cover.png"  # "cover" outranks "folder"


# ---- cache -------------------------------------------------------------------------

def test_tag_cache_reuses_unchanged_files_and_drops_gone_ones(tmp_path, monkeypatch):
    src = FIXTURES / "tagged.flac"
    a = tmp_path / "a.flac"
    b = tmp_path / "b.flac"
    a.write_bytes(src.read_bytes())
    b.write_bytes(src.read_bytes())
    cache_file = tmp_path / "cache.json"

    cache = TagCache(cache_file)
    assert cache.get(a).title == "Tëst Title"
    cache.get(b)
    cache.save()

    calls = []
    import station_player.tags as tags_mod
    real = tags_mod.read_tags
    monkeypatch.setattr(tags_mod, "read_tags", lambda p: calls.append(p) or real(p))

    cache = TagCache(cache_file)
    assert cache.get(a).title == "Tëst Title"
    assert calls == []  # served from the cache
    a.write_bytes(src.read_bytes() + b"\x00")  # changed size: read again
    cache.get(a)
    assert calls == [a]
    cache.save()  # b was not seen this round, so it's dropped
    reloaded = TagCache(cache_file)
    assert set(reloaded._entries) == {str(a)}


def test_damaged_cache_is_ignored(tmp_path):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("{not json")
    song = tmp_path / "a.flac"
    song.write_bytes((FIXTURES / "tagged.flac").read_bytes())
    assert TagCache(cache_file).get(song).title == "Tëst Title"
    cache_file.write_text('{"version": 1, "entries": {"%s": [[1, 2], {"title": 5, "bogus": 1}]}}'
                          % str(song).replace("\\", "\\\\"))
    assert TagCache(cache_file).get(song).title == "Tëst Title"


# ---- review round: regressions ---------------------------------------------------

def _zlib_of_zeros(prefix: bytes, megabytes: int) -> bytes:
    """Compress prefix + N MB of zeros without ever holding them."""
    z = zlib.compressobj(9)
    out = [z.compress(prefix)]
    block = b"\x00" * (1024 * 1024)
    for _ in range(megabytes):
        out.append(z.compress(block))
    out.append(z.flush())
    return b"".join(out)


def test_compressed_frame_bombs_are_refused(tmp_path):
    import resource
    title_bomb = _zlib_of_zeros(b"\x03", 256)
    art_bomb = _zlib_of_zeros(b"\x00image/png\x00\x03\x00", 512)
    body = frame("TIT2", struct.pack(">I", 0) + title_bomb, flags=0x0009) + frame("TPE1", text("Still read"))
    path = write(tmp_path, id3(body))
    art_path = write(tmp_path, id3(frame("APIC", struct.pack(">I", 0) + art_bomb, flags=0x0009)), "b.mp3")
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    tags = read_tags(path)
    assert read_art(art_path) is None
    assert time.monotonic() - started < 2
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before < 64 * 1024  # KB on Linux
    assert tags.title is None and tags.artist == "Still read"


@pytest.mark.parametrize("length", [256, 300, 1000])
def test_v24_plain_sizes_whose_bytes_look_syncsafe(tmp_path, length):
    # 256 is 00 00 01 00: valid syncsafe bytes, but meant as a plain integer.
    body = (frame("TIT2", text("t" * (length - 1)), plain_size=True)
            + frame("TPE1", text("Artist"), plain_size=True)
            + frame("APIC", b"\x00image/png\x00\x03\x00" + PNG, plain_size=True))
    path = write(tmp_path, id3(body))
    tags = read_tags(path)
    assert tags.title == "t" * (length - 1)
    assert tags.artist == "Artist"
    assert read_art(path) == (PNG, "image/png")


def test_no_mp3_duration_guessed_for_other_formats(tmp_path):
    # MP3 frames inside a file named .aac/.webm must not produce a duration.
    data = (FIXTURES / "untagged-cbr.mp3").read_bytes()
    assert read_tags(write(tmp_path, data, "x.aac")).duration_ms is None
    assert read_tags(write(tmp_path, data, "x.webm")).duration_ms is None
    assert read_tags(write(tmp_path, data, "x.mp3")).duration_ms


def test_scan_reads_ogg_text_without_reading_big_pictures(monkeypatch):
    import station_player.tags as tags_mod
    monkeypatch.setattr(tags_mod, "_SCAN_PACKET_LIMIT", 120)  # cut inside the comment packet
    tags = read_tags(FIXTURES / "tagged.opus")
    assert tags.title == "Tëst Title"
    assert read_art(FIXTURES / "tagged.opus") is not None  # art still reads in full


def test_front_cover_preferred_over_other_pictures(tmp_path):
    back = b"\x00image/png\x00\x04\x00" + PNG
    front = b"\x00image/jpeg\x00\x03\x00" + JPEG
    data, mime = read_art(write(tmp_path, id3(frame("APIC", back) + frame("APIC", front))))
    assert mime == "image/jpeg"


def test_aiff_text_chunks_and_bad_sample_rate(tmp_path):
    comm = struct.pack(">hIh", 1, 8000, 16) + b"\x7f\xff" + b"\xff" * 8  # absurd rate
    name = b"Song Name\x00"
    chunks = b"COMM" + struct.pack(">I", len(comm)) + comm + b"NAME" + struct.pack(">I", len(name)) + name
    data = b"FORM" + struct.pack(">I", 4 + len(chunks)) + b"AIFF" + chunks
    tags = read_tags(write(tmp_path, data, "x.aiff"))
    assert tags.title == "Song Name" and tags.duration_ms is None


def test_v24_utf16_values_and_tag_wide_unsync(tmp_path):
    two = b"\x01" + "A".encode("utf-16") + b"\x00\x00" + "B".encode("utf-16")
    assert read_tags(write(tmp_path, id3(frame("TIT2", two)))).title == "A / B"
    pic = b"\x00image/jpeg\x00\x03\x00" + JPEG
    raw = frame("APIC", pic.replace(b"\xff", b"\xff\x00"))
    data, mime = read_art(write(tmp_path, id3(raw, flags=0x80), "u.mp3"))
    assert mime == "image/jpeg" and data == JPEG


# ---- volume levelling tags ----------------------------------------------------

def txxx(desc: str, value: str, enc=3) -> bytes:
    codec = {0: "latin-1", 1: "utf-16", 3: "utf-8"}[enc]
    term = b"\x00\x00" if enc == 1 else b"\x00"
    return bytes([enc]) + desc.encode(codec) + term + value.encode(codec)


def test_replaygain_from_id3_txxx(tmp_path):
    body = (frame("TIT2", text("Song"))
            + frame("TXXX", txxx("replaygain_track_gain", "-7.25 dB", enc=1))
            + frame("TXXX", txxx("REPLAYGAIN_TRACK_PEAK", "0.988525")))
    tags = read_tags(write(tmp_path, id3(body)))
    assert (tags.title, tags.gain_db, tags.peak) == ("Song", -7.25, 0.988525)


def test_itunes_sound_check_from_id3_comment(tmp_path):
    # 2000 = 10^(3/10) * 1000, so -3 dB; peaks (7th and 8th) 0x7FFF and 0x4000.
    norm = " 000007D0 000003E8 00001388 00000BB8 00024CA8 00024CA8 00007FFF 00004000 00024CA8 00024CA8"
    comm = b"\x00eng" + b"iTunNORM\x00" + norm.encode()
    tags = read_tags(write(tmp_path, id3(frame("COMM", comm, major=3), major=3)))
    assert tags.gain_db == pytest.approx(-3.01, abs=0.01)
    assert tags.peak == pytest.approx(0x7FFF / 32768)


def test_replaygain_wins_over_sound_check_and_junk_is_ignored(tmp_path):
    comm = b"\x00eng" + b"iTunNORM\x00" + b" 000007D0 000007D0"
    body = frame("COMM", comm) + frame("TXXX", txxx("REPLAYGAIN_TRACK_GAIN", "+2.5 dB"))
    assert read_tags(write(tmp_path, id3(body))).gain_db == 2.5
    for junk in ("loud", "nan dB", "999 dB", ""):
        body = frame("TXXX", txxx("REPLAYGAIN_TRACK_GAIN", junk))
        assert read_tags(write(tmp_path, id3(body))).gain_db is None


def vorbis_comment(*entries: str) -> bytes:
    out = struct.pack("<I", 3) + b"enc" + struct.pack("<I", len(entries))
    for e in entries:
        raw = e.encode()
        out += struct.pack("<I", len(raw)) + raw
    return out


def test_replaygain_from_flac_and_r128_from_opus_style_comments(tmp_path):
    block = vorbis_comment("TITLE=x", "REPLAYGAIN_TRACK_GAIN=-4.10 dB", "REPLAYGAIN_TRACK_PEAK=1.05")
    flac = b"fLaC" + bytes([0x84]) + len(block).to_bytes(3, "big") + block
    tags = read_tags(write(tmp_path, flac, "x.flac"))
    assert (tags.gain_db, tags.peak) == (-4.1, 1.05)
    # R128 is Q7.8 relative to -23 LUFS: -1280/256 = -5 dB there, 0 dB at -18.
    block = vorbis_comment("R128_TRACK_GAIN=-1280")
    flac = b"fLaC" + bytes([0x84]) + len(block).to_bytes(3, "big") + block
    assert read_tags(write(tmp_path, flac, "y.flac")).gain_db == 0.0


def atom(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body) + 8) + kind + body


def test_replaygain_and_sound_check_from_mp4_freeform_atoms(tmp_path):
    def freeform(name: str, value: str) -> bytes:
        return atom(b"----", atom(b"mean", b"\x00" * 4 + b"com.apple.iTunes")
                    + atom(b"name", b"\x00" * 4 + name.encode())
                    + atom(b"data", b"\x00\x00\x00\x01" + b"\x00" * 4 + value.encode()))

    def m4a(*items: bytes) -> bytes:
        ilst = atom(b"ilst", b"".join(items))
        return atom(b"ftyp", b"M4A " + b"\x00" * 4) + atom(b"moov", atom(b"udta", atom(b"meta", b"\x00" * 4 + ilst)))

    tags = read_tags(write(tmp_path, m4a(freeform("replaygain_track_gain", "-9.00 dB")), "a.m4a"))
    assert tags.gain_db == -9.0
    norm = " 000003E8 000003E8 0 0 0 0 00004000 00004000 0 0"
    tags = read_tags(write(tmp_path, m4a(freeform("iTunNORM", norm)), "b.m4a"))
    assert (tags.gain_db, tags.peak) == (0.0, 0.5)


def test_gain_number_formats_and_overlong_sound_check(tmp_path):
    from station_player.tags import _number
    assert _number("-6,48 dB") == -6.48
    assert _number("+.5 dB") == 0.5
    assert _number("1.5e-1") == 0.15
    assert _number("1e999") is None
    # A garbled Sound Check value is ignored, and the other tags survive it.
    comm = b"\x00eng" + b"iTunNORM\x00" + b"F" * 400 + b" 1"
    tags = read_tags(write(tmp_path, id3(frame("TIT2", text("Kept")) + frame("COMM", comm))))
    assert (tags.title, tags.gain_db) == ("Kept", None)
