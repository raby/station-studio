import random
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
