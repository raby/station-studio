"""Read song details and cover art from audio files, standard library only.

Supports ID3 (mp3, and ID3 chunks in wav / aiff), FLAC, MP4 / M4A, Ogg
Vorbis and Opus, RIFF INFO (wav) and AIFF. Every reader is defensive:
files come from anywhere, so a damaged or hostile file yields fewer
details, never an exception, a hang or an outsized read.

`read_tags` skips over embedded pictures so a library scan stays cheap;
`read_art` is called for one track at a time when the UI asks for it.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import os
import re
import struct
import threading
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

MAX_ART_BYTES = 16 * 1024 * 1024    # larger embedded pictures are ignored
_MAX_TAG_BYTES = 64 * 1024 * 1024   # an ID3 tag bigger than this is junk
_MAX_TEXT_BYTES = 64 * 1024         # per text value
_MAX_BLOCKS = 4096                  # frames / atoms / chunks walked per file

COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# ID3v1 genre numbers (used by ID3 "(13)" references and MP4 'gnre').
ID3_GENRES = (
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal",
    "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
    "Hard Rock",
)


@dataclass
class Tags:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    duration_ms: int | None = None
    # Volume levelling: the ReplayGain-style track gain in dB (what brings
    # the track to about -18 LUFS) and the sample peak (1.0 = full scale),
    # from ReplayGain, R128 (Opus) or iTunes Sound Check tags.
    gain_db: float | None = None
    peak: float | None = None
    # For music reporting (PRS / PPL returns from the play log).
    composer: str | None = None
    label: str | None = None
    isrc: str | None = None


class _Done(Exception):
    """Internal: stop walking a file (truncated or nonsense data)."""


# ---- public entry points ---------------------------------------------------

_MP3_SUFFIXES = (".mp3", ".mp2", ".mp1")
_SCAN_PACKET_LIMIT = 1024 * 1024  # Ogg comment bytes read when not after art


def read_tags(path: Path) -> Tags:
    """Title, artist, album, genre and duration; empty Tags on any trouble."""
    tags = Tags()
    try:
        with path.open("rb") as fh:
            _dispatch(fh, tags, None, path.suffix.lower())
    except Exception as exc:  # unreadable or malformed: details are optional
        logger.debug("Could not read tags from %s: %s", path, exc)
    return tags


def read_art(path: Path) -> tuple[bytes, str] | None:
    """The embedded cover picture as (bytes, MIME type), if there is one."""
    art: list[tuple[int, bytes]] = []  # (picture type, bytes)
    try:
        with path.open("rb") as fh:
            _dispatch(fh, Tags(), art, path.suffix.lower())
    except Exception as exc:
        logger.debug("Could not read art from %s: %s", path, exc)
    # Picture type 3 is the front cover, 0 "other"; a back cover or a
    # photo of the band only if there's nothing better.
    for _, data in sorted(art, key=lambda a: {3: 0, 0: 1}.get(a[0], 2)):
        mime = image_type(data)
        if mime:
            return data, mime
    return None


def folder_cover(audio_path: Path) -> Path | None:
    """cover.jpg / folder.png / ... beside the audio file, if present."""
    try:
        siblings = {p.name.casefold(): p for p in audio_path.parent.iterdir()}
    except OSError:
        return None
    for stem in COVER_NAMES:
        for ext in _IMAGE_EXTENSIONS:
            p = siblings.get(stem + ext)
            if p is not None and p.is_file():
                return p
    return None


def image_type(data: bytes) -> str | None:
    """MIME type of an image, judged by its bytes rather than any label."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---- helpers ----------------------------------------------------------------

def _read(fh: BinaryIO, n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise _Done()
    return data


def _file_size(fh: BinaryIO) -> int:
    here = fh.tell()
    end = fh.seek(0, os.SEEK_END)
    fh.seek(here)
    return end


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\x00", " ").strip()
    return text or None


def _set(tags: Tags, key: str, value) -> None:
    if getattr(tags, key) is None:
        value = _clean(value) if isinstance(value, str) else value
        if value:
            setattr(tags, key, value)


def _number(text: str | None) -> float | None:
    """"-6.48 dB" -> -6.48; None for anything that isn't a sane number."""
    if not text:
        return None
    # Some writers use a decimal comma ("-6,48 dB").
    m = re.match(r"\s*([-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d{1,3})?)", text)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", "."))
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _set_gain(tags: Tags, gain: float | None, peak: float | None = None) -> None:
    """Keep the first track gain found; ignore absurd values."""
    if tags.gain_db is None and gain is not None and -60 <= gain <= 60:
        tags.gain_db = round(gain, 2)
    if tags.peak is None and peak is not None and 0 < peak <= 16:
        tags.peak = round(peak, 6)


def _soundcheck(text: str | None) -> tuple[float, float | None] | None:
    """iTunes Sound Check ("iTunNORM"): ten hex numbers. The first two are
    the left and right adjustment (1000 = 0 dB, as 10^(-dB/10) * 1000),
    the 7th and 8th the left and right peaks in 16-bit sample units."""
    if not text:
        return None
    tokens = text.split()
    if any(len(v) > 8 for v in tokens):  # each value is a 32-bit number
        return None
    try:
        values = [int(v, 16) for v in tokens]
    except ValueError:
        return None
    if len(values) < 2 or max(values[0], values[1]) <= 0:
        return None
    gain = -10 * math.log10(max(values[0], values[1]) / 1000)
    peak = max(values[6], values[7]) / 32768 if len(values) >= 8 and max(values[6], values[7]) else None
    return gain, peak


def _gain_fields(fields: dict[str, str], tags: Tags) -> None:
    """ReplayGain / R128 / Sound Check from upper-cased name -> value pairs."""
    gain = _number(fields.get("REPLAYGAIN_TRACK_GAIN"))
    peak = _number(fields.get("REPLAYGAIN_TRACK_PEAK"))
    if gain is None and fields.get("R128_TRACK_GAIN"):
        r128 = _number(fields["R128_TRACK_GAIN"])  # Q7.8 dB relative to -23 LUFS
        gain = r128 / 256 + 5 if r128 is not None else None
    if gain is None:
        sc = _soundcheck(fields.get("ITUNNORM"))
        if sc:
            gain, peak = sc[0], peak if peak is not None else sc[1]
    _set_gain(tags, gain, peak)
    # Named fields for the reporting details (Vorbis comments, ID3 TXXX,
    # MP4 '----' items).
    _set(tags, "composer", fields.get("COMPOSER"))
    _set(tags, "label", fields.get("LABEL") or fields.get("ORGANIZATION") or fields.get("PUBLISHER"))
    _set(tags, "isrc", fields.get("ISRC"))


def _genre_name(raw: str) -> str:
    """ID3 genres may be "(17)", "17", "(17)Rock" or plain text."""
    text = raw.strip()
    if text.startswith("(") and ")" in text:
        num, rest = text[1:].split(")", 1)
        if rest.strip():
            return rest.strip()
        text = num
    if text.isdigit() and int(text) < len(ID3_GENRES):
        return ID3_GENRES[int(text)]
    return raw


def _dispatch(fh: BinaryIO, tags: Tags, art: list | None, suffix: str = ".mp3") -> None:
    # MP3 has no signature, so frame-sync hunting is only done on files
    # named as MP3: on AAC or WebM it finds false syncs and bad durations.
    mp3 = suffix in _MP3_SUFFIXES
    head = fh.read(12)
    fh.seek(0)
    if head[:3] == b"ID3":
        end = _id3v2(fh, 0, tags, art)
        fh.seek(end)
        if fh.read(4) == b"fLaC":  # FLAC files sometimes carry ID3 too
            fh.seek(end + 4)
            _flac(fh, tags, art)
        else:
            if mp3:
                _mp3_duration(fh, end, tags)
            if tags.title is None:
                _id3v1(fh, tags)
    elif head[:4] == b"fLaC":
        fh.seek(4)
        _flac(fh, tags, art)
    elif head[:4] == b"OggS":
        _ogg(fh, tags, art)
    elif head[4:8] == b"ftyp":
        _mp4(fh, tags, art)
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        _riff(fh, tags, art)
    elif head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        _aiff(fh, tags, art)
    elif mp3:  # an mp3 with no ID3v2 tag at the front
        _mp3_duration(fh, 0, tags)
        _id3v1(fh, tags)


# ---- ID3v2 ------------------------------------------------------------------

_ID3_TEXT = {
    "TIT2": "title", "TT2": "title",
    "TPE1": "artist", "TP1": "artist",
    "TPE2": "albumartist", "TP2": "albumartist",
    "TALB": "album", "TAL": "album",
    "TCON": "genre", "TCO": "genre",
    "TCOM": "composer", "TCM": "composer",
    "TPUB": "label", "TPB": "label",
    "TSRC": "isrc", "TRC": "isrc",
}
_ID3_ART = ("APIC", "PIC")
_ID3_USER = ("TXXX", "TXX", "COMM", "COM")  # ReplayGain and Sound Check live here


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _unsync(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


def _id3_text(data: bytes) -> str | None:
    if not data:
        return None
    enc, body = data[0], data[1:_MAX_TEXT_BYTES]
    if enc == 0:
        text = body.decode("latin-1")
    elif enc == 1:
        text = body.decode("utf-16", "replace")
    elif enc == 2:
        text = body.decode("utf-16-be", "replace")
    elif enc == 3:
        text = body.decode("utf-8", "replace")
    else:
        return None
    # v2.4 separates multiple values with NUL (each UTF-16 value with its
    # own byte order mark); keep them readable.
    values = [v.strip().lstrip("\ufeff").strip() for v in text.split("\x00")]
    values = [v for v in values if v]
    return " / ".join(values) or None


def _split_terminated(data: bytes, enc: int) -> bytes:
    """Drop a NUL-terminated string in encoding `enc` from the front."""
    if enc in (1, 2):  # UTF-16: terminator is two zero bytes, aligned
        for i in range(0, len(data) - 1, 2):
            if data[i:i + 2] == b"\x00\x00":
                return data[i + 2:]
        return b""
    i = data.find(b"\x00")
    return data[i + 1:] if i >= 0 else b""


def _id3_picture(fid: str, data: bytes) -> tuple[int, bytes] | None:
    if len(data) < 6:
        return None
    enc = data[0]
    if fid == "PIC":  # v2.2: 3-char image format, then type, description
        ptype, rest = data[4], data[5:]
    else:
        mime_end = data.find(b"\x00", 1)
        if mime_end < 0 or mime_end + 1 >= len(data):
            return None
        ptype, rest = data[mime_end + 1], data[mime_end + 2:]  # mime, NUL, type
    pic = _split_terminated(rest, enc)
    return (ptype, pic) if pic else None


def _id3_frames(src: BinaryIO, start: int, end: int, major: int, plain: bool):
    """Walk frame headers: ([(id, flags, data offset, size)], clean). Not
    clean means a frame ran past the tag or garbage followed one, which
    is how a wrong guess about the size format shows itself."""
    id_len, head_len = (3, 6) if major == 2 else (4, 10)
    frames = []
    pos = start
    for _ in range(_MAX_BLOCKS):
        if pos + head_len > end:
            return frames, True
        src.seek(pos)
        head = src.read(head_len)
        if len(head) < head_len or head[0] == 0:
            return frames, True  # padding
        fid_raw = head[:id_len]
        if not fid_raw.isalnum() or not fid_raw.isascii():
            return frames, False
        if major == 2:
            size, flags = int.from_bytes(head[3:6], "big"), 0
        else:
            raw = head[4:8]
            size = struct.unpack(">I", raw)[0] if plain else _syncsafe(raw)
            flags = struct.unpack(">H", head[8:10])[0]
        if size <= 0 or pos + head_len + size > end:
            return frames, False
        frames.append((fid_raw.decode("ascii"), flags, pos + head_len, size))
        pos += head_len + size
    return frames, True


def _id3v2(fh: BinaryIO, start: int, tags: Tags, art: list | None) -> int:
    """Parse the ID3v2 tag at `start`; returns the offset just after it."""
    fh.seek(start)
    header = fh.read(10)
    if len(header) < 10 or header[:3] != b"ID3":
        return start
    major, flags = header[3], header[5]
    size = _syncsafe(header[6:10])
    end = start + 10 + size + (10 if major == 4 and flags & 0x10 else 0)
    if major not in (2, 3, 4) or size > _MAX_TAG_BYTES:
        return end
    if major == 2 and flags & 0x40:  # v2.2 "compressed": no defined scheme
        return end

    # Whole-tag unsynchronisation (v2.2 / v2.3) makes raw offsets
    # meaningless, so such tags are read into memory first.
    if flags & 0x80 and major < 4:
        body = _unsync(fh.read(size))
        src: BinaryIO = io.BytesIO(body)
        base, body_end = 0, len(body)
    else:
        src, base, body_end = fh, start + 10, start + 10 + size
    src.seek(base)

    if flags & 0x40 and major in (3, 4):  # extended header
        raw = src.read(4)
        if len(raw) < 4:
            return end
        ext = _syncsafe(raw) if major == 4 else struct.unpack(">I", raw)[0]
        src.seek(src.tell() + (ext - 4 if major == 4 else ext))

    # Frame sizes are syncsafe in v2.4 and plain in v2.3, but iTunes and
    # others wrote v2.4 tags with plain sizes. Try syncsafe; if the frames
    # don't line up, plain; keep whichever walks the tag cleanly.
    frames_start = src.tell()
    frames, clean = _id3_frames(src, frames_start, body_end, major, plain=major != 4)
    if major == 4 and not clean:
        plain_frames, plain_clean = _id3_frames(src, frames_start, body_end, major, plain=True)
        if plain_clean or len(plain_frames) > len(frames):
            frames = plain_frames
    tag_unsync = major == 4 and bool(flags & 0x80)

    albumartist = None
    gain_fields: dict[str, str] = {}
    for fid, fflags, data_start, fsize in frames:
        key = _ID3_TEXT.get(fid)
        is_art = fid in _ID3_ART
        if fid in _ID3_USER:
            key = "user"
        if not (key or (is_art and art is not None)):
            continue
        cap = MAX_ART_BYTES if is_art else _MAX_TEXT_BYTES * 4
        if fsize > cap:
            continue
        src.seek(data_start)
        data = _id3_frame_body(major, fflags, src.read(fsize), tag_unsync, cap)
        if data is not None:
            if is_art:
                pic = _id3_picture(fid, data)
                if pic and art is not None:
                    art.append(pic)
            elif key == "user":
                pair = _id3_user_text(fid, data)
                if pair:
                    gain_fields.setdefault(*pair)
            elif key == "albumartist":
                albumartist = albumartist or _id3_text(data)
            elif key == "genre":
                text = _id3_text(data)
                if text:
                    _set(tags, "genre", _genre_name(text))
            else:
                _set(tags, key, _id3_text(data))
    if albumartist:
        _set(tags, "artist", albumartist)
    _gain_fields(gain_fields, tags)
    return end


def _id3_user_text(fid: str, data: bytes) -> tuple[str, str] | None:
    """(DESCRIPTION, value) from a TXXX or COMM frame."""
    if len(data) < 2:
        return None
    enc = data[0]
    body = data[4:] if fid in ("COMM", "COM") else data[1:]  # COMM has a language
    value = _split_terminated(body, enc)
    desc = _id3_text(bytes([enc]) + body[:len(body) - len(value)])
    text = _id3_text(bytes([enc]) + value)
    if not desc or not text:
        return None
    return desc.upper(), text


def _inflate(data: bytes, cap: int) -> bytes | None:
    """zlib-decompress at most `cap` bytes; None for bad or oversized
    data (a tiny compressed frame can claim gigabytes)."""
    d = zlib.decompressobj()
    try:
        out = d.decompress(data, cap)
    except zlib.error:
        return None
    return None if d.unconsumed_tail else out


def _id3_frame_body(major: int, flags: int, data: bytes, tag_unsync: bool = False,
                    cap: int = MAX_ART_BYTES) -> bytes | None:
    """Undo per-frame flags; None for frames we can't decode."""
    if major == 3:
        if flags & 0x0040:  # encrypted
            return None
        # Optional extras sit before the data: a 4-byte decompressed size
        # when compressed, then a group id byte when grouped.
        skip = (4 if flags & 0x0080 else 0) + (1 if flags & 0x0020 else 0)
        data = data[skip:]
        if flags & 0x0080:
            return _inflate(data, cap)
        return data
    if major == 4:
        if flags & 0x0004:
            return None
        if flags & 0x0040:
            data = data[1:]
        if flags & 0x0001:  # data length indicator
            data = data[4:]
        # Some writers set only the tag-wide unsync flag in v2.4.
        if flags & 0x0002 or tag_unsync:
            data = _unsync(data)
        if flags & 0x0008:
            return _inflate(data, cap)
        return data
    return data


def _id3v1(fh: BinaryIO, tags: Tags) -> None:
    size = _file_size(fh)
    if size < 128:
        return
    fh.seek(size - 128)
    block = fh.read(128)
    if block[:3] != b"TAG":
        return

    def field(b: bytes) -> str | None:
        return b.split(b"\x00", 1)[0].decode("latin-1").strip() or None

    _set(tags, "title", field(block[3:33]))
    _set(tags, "artist", field(block[33:63]))
    _set(tags, "album", field(block[63:93]))
    if block[127] < len(ID3_GENRES):
        _set(tags, "genre", ID3_GENRES[block[127]])


# ---- MP3 duration -------------------------------------------------------------

_MP3_BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}


def _mp3_header(h: bytes):
    """(version, layer, bitrate kbps, sample rate, frame length, mono) or None."""
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return None
    ver_bits, layer_bits = (h[1] >> 3) & 3, (h[1] >> 1) & 3
    br_idx, sr_idx, pad = h[2] >> 4, (h[2] >> 2) & 3, (h[2] >> 1) & 1
    if ver_bits == 1 or layer_bits == 0 or br_idx in (0, 15) or sr_idx == 3:
        return None
    version = {3: 1, 2: 2, 0: 25}[ver_bits]
    layer = 4 - layer_bits
    bitrate = _MP3_BITRATES[(1 if version == 1 else 2, layer)][br_idx]
    rate = _MP3_RATES[version][sr_idx]
    if layer == 1:
        length = (12 * bitrate * 1000 // rate + pad) * 4
    elif layer == 3 and version != 1:
        length = 72 * bitrate * 1000 // rate + pad
    else:
        length = 144 * bitrate * 1000 // rate + pad
    mono = (h[3] >> 6) == 3
    return version, layer, bitrate, rate, length, mono


def _mp3_duration(fh: BinaryIO, start: int, tags: Tags) -> None:
    size = _file_size(fh)
    fh.seek(start)
    window = fh.read(64 * 1024)
    i = -1
    while True:
        i = window.find(b"\xff", i + 1)
        if i < 0 or i + 4 > len(window):
            return
        hdr = _mp3_header(window[i:i + 4])
        if hdr is None:
            continue
        version, layer, bitrate, rate, length, mono = hdr
        # Trust a sync only when the next two frames follow it.
        nxt = _mp3_header(window[i + length:i + length + 4])
        if len(window) >= i + length + 4 and nxt is None:
            continue
        if nxt and len(window) >= i + length + nxt[4] + 4 \
                and _mp3_header(window[i + length + nxt[4]:i + length + nxt[4] + 4]) is None:
            continue
        samples = 384 if layer == 1 else (576 if layer == 3 and version != 1 else 1152)
        frame = window[i:i + length]
        side = (17 if mono else 32) if version == 1 else (9 if mono else 17)
        xing = frame[4 + side:4 + side + 12]
        frames = None
        if xing[:4] in (b"Xing", b"Info") and len(xing) == 12:
            if struct.unpack(">I", xing[4:8])[0] & 1:
                frames = struct.unpack(">I", xing[8:12])[0]
        elif frame[36:40] == b"VBRI" and len(frame) >= 36 + 18:
            frames = struct.unpack(">I", frame[36 + 14:36 + 18])[0]
        if frames:
            _set(tags, "duration_ms", frames * samples * 1000 // rate)
        else:
            audio = size - (start + i)
            fh.seek(max(size - 128, 0))
            if fh.read(3) == b"TAG":
                audio -= 128
            if bitrate:
                _set(tags, "duration_ms", audio * 8 // bitrate)
        return


# ---- FLAC and Vorbis comments ------------------------------------------------

def _vorbis_comments(data: bytes, tags: Tags, art: list | None) -> None:
    fields: dict[str, str] = {}
    try:
        _vorbis_fields(data, fields, art)
    finally:  # a cut-short packet still yields the fields before the cut
        _set(tags, "title", fields.get("TITLE"))
        _set(tags, "artist", fields.get("ARTIST") or fields.get("ALBUMARTIST"))
        _set(tags, "album", fields.get("ALBUM"))
        _set(tags, "genre", fields.get("GENRE"))
        _gain_fields(fields, tags)


def _vorbis_fields(data: bytes, fields: dict[str, str], art: list | None) -> None:
    pos = 0

    def u32() -> int:
        nonlocal pos
        if pos + 4 > len(data):
            raise _Done()
        (v,) = struct.unpack_from("<I", data, pos)
        pos += 4
        return v

    vendor = u32()
    pos += vendor
    count = u32()
    for _ in range(min(count, _MAX_BLOCKS)):
        n = u32()
        if pos + n > len(data):
            break
        entry = data[pos:pos + n]
        pos += n
        if b"=" not in entry:
            continue
        k, v = entry.split(b"=", 1)
        key = k.decode("ascii", "replace").upper()
        if key == "METADATA_BLOCK_PICTURE":
            if art is not None:
                try:
                    pic = _flac_picture(base64.b64decode(v, validate=False))
                except (ValueError, _Done):
                    pic = None
                if pic:
                    art.append(pic)
            continue
        if key not in fields and len(v) <= _MAX_TEXT_BYTES:
            fields[key] = v.decode("utf-8", "replace")


def _flac_picture(block: bytes) -> tuple[int, bytes] | None:
    pos = 4
    try:
        (ptype,) = struct.unpack_from(">I", block, 0)
        (mime_len,) = struct.unpack_from(">I", block, pos)
        pos += 4 + mime_len
        (desc_len,) = struct.unpack_from(">I", block, pos)
        pos += 4 + desc_len + 16  # width, height, depth, colours
        (data_len,) = struct.unpack_from(">I", block, pos)
    except struct.error:
        return None
    pos += 4
    data = block[pos:pos + data_len]
    return (ptype, data) if len(data) == data_len and data_len else None


def _flac_block(kind: int, data: bytes, tags: Tags, art: list | None) -> None:
    if kind == 0 and len(data) >= 18:
        (packed,) = struct.unpack(">Q", data[10:18])
        rate, total = packed >> 44, packed & ((1 << 36) - 1)
        if rate and total:
            _set(tags, "duration_ms", total * 1000 // rate)
    elif kind == 4:
        _vorbis_comments(data, tags, art)
    elif kind == 6 and art is not None:
        pic = _flac_picture(data)
        if pic:
            art.append(pic)


def _flac(fh: BinaryIO, tags: Tags, art: list | None) -> None:
    for _ in range(_MAX_BLOCKS):
        head = fh.read(4)
        if len(head) < 4:
            return
        last, kind = head[0] & 0x80, head[0] & 0x7F
        length = int.from_bytes(head[1:4], "big")
        start = fh.tell()
        if kind == 127:  # invalid block type
            return
        if kind in (0, 4) or (kind == 6 and art is not None and length <= MAX_ART_BYTES + 1024):
            _flac_block(kind, _read(fh, length), tags, art)
        fh.seek(start + length)
        if last:
            return


# ---- Ogg (Vorbis / Opus) ----------------------------------------------------

def _ogg_packets(fh: BinaryIO, limit: int):
    """Yield (serial, packet) for the first logical stream's first packets."""
    serial = None
    packet = bytearray()
    for _ in range(_MAX_BLOCKS):
        head = fh.read(27)
        if len(head) < 27 or head[:4] != b"OggS":
            return
        page_serial = struct.unpack_from("<I", head, 14)[0]
        segs = fh.read(head[26])
        if len(segs) < head[26]:
            return
        body = fh.read(sum(segs))
        if serial is None:
            serial = page_serial
        if page_serial != serial:
            continue
        pos = 0
        for seg in segs:
            packet += body[pos:pos + seg]
            pos += seg
            if len(packet) > limit:
                # Enough to read: hand over what we have and stop.
                yield serial, bytes(packet[:limit])
                return
            if seg < 255:
                yield serial, bytes(packet)
                packet = bytearray()


def _ogg(fh: BinaryIO, tags: Tags, art: list | None) -> None:
    # Base64 pictures make the comment packet ~4/3 the picture's size.
    # Without art wanted, the text before any picture is plenty.
    packets = _ogg_packets(fh, MAX_ART_BYTES * 2 if art is not None else _SCAN_PACKET_LIMIT)
    first = next(packets, None)
    if first is None:
        return
    serial, ident = first
    rate, preskip = None, 0
    if ident[:5] == b"\x7fFLAC" and len(ident) >= 51 and ident[9:13] == b"fLaC":
        # FLAC in Ogg: STREAMINFO rides in the first packet, the other
        # metadata blocks in the packets after it.
        _flac_block(ident[13] & 0x7F, ident[17:], tags, art)
        for _ in range(min(int.from_bytes(ident[7:9], "big") or 16, 64)):
            nxt = next(packets, None)
            if nxt is None or len(nxt[1]) < 4:
                break
            _flac_block(nxt[1][0] & 0x7F, nxt[1][4:], tags, art)
        # Streamed encoders leave STREAMINFO's sample count at zero.
        rate = struct.unpack(">Q", ident[27:35])[0] >> 44
        if tags.duration_ms is None and rate:
            granule = _ogg_last_granule(fh, serial)
            if granule:
                _set(tags, "duration_ms", granule * 1000 // rate)
        return
    if ident[:7] == b"\x01vorbis" and len(ident) >= 16:
        rate = struct.unpack_from("<I", ident, 12)[0]
        comment_magic = b"\x03vorbis"
    elif ident[:8] == b"OpusHead" and len(ident) >= 12:
        rate, preskip = 48000, struct.unpack_from("<H", ident, 10)[0]
        comment_magic = b"OpusTags"
    else:
        return
    second = next(packets, None)
    if second and second[1].startswith(comment_magic):
        try:
            _vorbis_comments(second[1][len(comment_magic):], tags, art)
        except _Done:
            pass
    if rate:
        granule = _ogg_last_granule(fh, serial)
        if granule and granule > preskip:
            _set(tags, "duration_ms", (granule - preskip) * 1000 // rate)


def _ogg_last_granule(fh: BinaryIO, serial: int) -> int | None:
    size = _file_size(fh)
    fh.seek(max(0, size - 64 * 1024))
    tail = fh.read()
    pos = tail.rfind(b"OggS")
    while pos >= 0:
        if pos + 27 <= len(tail) and struct.unpack_from("<I", tail, pos + 14)[0] == serial:
            granule = struct.unpack_from("<q", tail, pos + 6)[0]
            if granule >= 0:
                return granule
        pos = tail.rfind(b"OggS", 0, pos)
    return None


# ---- MP4 / M4A ------------------------------------------------------------

_MP4_TEXT = {
    b"\xa9nam": "title", b"\xa9ART": "artist", b"aART": "albumartist",
    b"\xa9alb": "album", b"\xa9gen": "genre", b"\xa9wrt": "composer",
}


def _atoms(fh: BinaryIO, start: int, end: int):
    """Yield (type, data start, data end) for the atoms in [start, end)."""
    pos = start
    for _ in range(_MAX_BLOCKS):
        if pos + 8 > end:
            return
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I", head[:4])[0], head[4:8]
        header = 8
        if size == 1:
            ext = fh.read(8)
            if len(ext) < 8:
                return
            size, header = struct.unpack(">Q", ext)[0], 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _mp4(fh: BinaryIO, tags: Tags, art: list | None) -> None:
    size = _file_size(fh)
    moov = next(((s, e) for k, s, e in _atoms(fh, 0, size) if k == b"moov"), None)
    if not moov:
        return
    albumartist = None
    for kind, s, e in _atoms(fh, *moov):
        if kind == b"mvhd":
            fh.seek(s)
            body = fh.read(min(e - s, 32))
            if body[:1] == b"\x01" and len(body) >= 32:
                scale, duration = struct.unpack_from(">IQ", body, 20)
            elif len(body) >= 20:
                scale, duration = struct.unpack_from(">II", body, 12)
            else:
                continue
            if scale and duration and duration not in (0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                _set(tags, "duration_ms", duration * 1000 // scale)
        elif kind == b"udta":
            for k2, s2, e2 in _atoms(fh, s, e):
                if k2 != b"meta":
                    continue
                # 'meta' is a full box (4 version/flag bytes) in MP4, but
                # some QuickTime writers omit them; peek for a child atom.
                fh.seek(s2 + 4)
                inner = s2 if fh.read(4) == b"hdlr" else s2 + 4
                for k3, s3, e3 in _atoms(fh, inner, e2):
                    if k3 == b"ilst":
                        albumartist = _mp4_ilst(fh, s3, e3, tags, art) or albumartist
    if albumartist:
        _set(tags, "artist", albumartist)


def _mp4_freeform(fh, start: int, end: int) -> tuple[str, str] | None:
    """A '----' item: (NAME, value), e.g. ("REPLAYGAIN_TRACK_GAIN", "-6.5 dB")."""
    name = value = None
    for kind, s, e in _atoms(fh, start, end):
        if e - s > _MAX_TEXT_BYTES:
            continue
        fh.seek(s)
        body = fh.read(e - s)
        if kind == b"name" and len(body) > 4:
            name = body[4:].decode("utf-8", "replace").strip().upper()
        elif kind == b"data" and len(body) > 8:
            value = body[8:].decode("utf-8", "replace").strip("\x00 ")
    return (name, value) if name and value else None


def _mp4_ilst(fh, start, end, tags: Tags, art: list | None) -> str | None:
    albumartist = None
    gain_fields: dict[str, str] = {}
    for item, s, e in _atoms(fh, start, end):
        if item == b"----":
            pair = _mp4_freeform(fh, s, e)
            if pair:
                gain_fields.setdefault(*pair)
            continue
        want_art = item == b"covr" and art is not None
        if item not in _MP4_TEXT and item != b"gnre" and not want_art:
            continue
        for kind, ds, de in _atoms(fh, s, e):
            if kind != b"data" or de - ds < 8:
                continue
            length = de - ds - 8
            if length > (MAX_ART_BYTES if want_art else _MAX_TEXT_BYTES):
                continue
            fh.seek(ds + 8)
            payload = fh.read(length)
            if want_art:
                art.append((3, payload))  # type: ignore[union-attr]
                continue
            if item == b"gnre":
                if len(payload) >= 2:
                    n = struct.unpack(">H", payload[:2])[0]
                    if 0 < n <= len(ID3_GENRES):
                        _set(tags, "genre", ID3_GENRES[n - 1])
                continue
            key = _MP4_TEXT[item]
            text = payload.decode("utf-8", "replace")
            if key == "albumartist":
                albumartist = albumartist or _clean(text)
            else:
                _set(tags, key, text)
            break
    _gain_fields(gain_fields, tags)
    return albumartist


# ---- WAV and AIFF --------------------------------------------------------------

_RIFF_INFO = {b"INAM": "title", b"IART": "artist", b"IPRD": "album", b"IGNR": "genre"}


def _riff(fh: BinaryIO, tags: Tags, art: list | None) -> None:
    size = _file_size(fh)
    pos, byte_rate = 12, None
    for _ in range(_MAX_BLOCKS):
        if pos + 8 > size:
            return
        fh.seek(pos)
        kind, length = fh.read(4), struct.unpack("<I", _read(fh, 4))[0]
        data_start = pos + 8
        if kind == b"fmt " and length >= 12:
            byte_rate = struct.unpack("<I", _read(fh, 12)[8:12])[0]
        elif kind == b"data" and byte_rate:
            audio = min(length, size - data_start)
            _set(tags, "duration_ms", audio * 1000 // byte_rate)
        elif kind == b"LIST" and length <= _MAX_TEXT_BYTES * 4:
            body = _read(fh, length)
            if body[:4] == b"INFO":
                p = 4
                while p + 8 <= len(body):
                    sub, n = body[p:p + 4], struct.unpack_from("<I", body, p + 4)[0]
                    value = body[p + 8:p + 8 + n]
                    if sub in _RIFF_INFO:
                        text = value.split(b"\x00", 1)[0]
                        try:
                            decoded = text.decode("utf-8")
                        except UnicodeDecodeError:
                            decoded = text.decode("latin-1")
                        _set(tags, _RIFF_INFO[sub], decoded)
                    p += 8 + n + (n & 1)
        elif kind in (b"id3 ", b"ID3 "):
            _id3v2(fh, data_start, tags, art)
        pos = data_start + length + (length & 1)


def _extended_float(b: bytes) -> float:
    """80-bit IEEE 754 extended precision (AIFF sample rates)."""
    exponent = ((b[0] & 0x7F) << 8) | b[1]
    mantissa = int.from_bytes(b[2:10], "big")
    if exponent == 0 and mantissa == 0:
        return 0.0
    try:
        value = mantissa * 2.0 ** (exponent - 16383 - 63)
    except OverflowError:
        return 0.0
    return -value if b[0] & 0x80 else value


_AIFF_TEXT = {b"NAME": "title", b"AUTH": "artist"}


def _aiff(fh: BinaryIO, tags: Tags, art: list | None) -> None:
    size = _file_size(fh)
    pos = 12
    for _ in range(_MAX_BLOCKS):
        if pos + 8 > size:
            return
        fh.seek(pos)
        kind, length = fh.read(4), struct.unpack(">I", _read(fh, 4))[0]
        data_start = pos + 8
        if kind == b"COMM" and length >= 18:
            body = _read(fh, 18)
            frames = struct.unpack(">I", body[2:6])[0]
            rate = _extended_float(body[8:18])
            if rate > 0 and frames:
                _set(tags, "duration_ms", int(frames * 1000 / rate))
        elif kind in (b"ID3 ", b"id3 "):
            _id3v2(fh, data_start, tags, art)
        elif kind in _AIFF_TEXT and length <= _MAX_TEXT_BYTES:
            raw = _read(fh, length).split(b"\x00", 1)[0]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            _set(tags, _AIFF_TEXT[kind], text)
        pos = data_start + length + (length & 1)


# ---- cache ------------------------------------------------------------------------

class TagCache:
    """Remembers tags by file path, size and modification time, so only new
    or changed files are opened on later scans. Stored as JSON beside the
    settings; a missing or damaged cache just means a slower scan."""

    VERSION = 3  # 2: gain_db and peak; 3: Sound Check peaks read from the right fields

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, list] = {}
        self._seen: set[str] = set()
        self._dirty = False
        if path is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("version") == self.VERSION:
                    entries = data.get("entries")
                    if isinstance(entries, dict):
                        self._entries = entries
            except (OSError, ValueError):
                pass

    def get(self, path: Path) -> Tags:
        key = str(path)
        try:
            st = path.stat()
        except OSError:
            return Tags()
        stamp = [st.st_size, st.st_mtime_ns]
        with self._lock:
            self._seen.add(key)
            hit = self._entries.get(key)
        if isinstance(hit, list) and len(hit) == 2 and hit[0] == stamp and isinstance(hit[1], dict):
            try:
                return Tags(**{k: hit[1].get(k) for k in Tags.__dataclass_fields__})
            except TypeError:
                pass
        tags = read_tags(path)
        with self._lock:
            self._entries[key] = [stamp, asdict(tags)]
            self._dirty = True
        return tags

    def save(self) -> None:
        """Write the cache, dropping files not seen since the last save."""
        with self._lock:
            stale = set(self._entries) - self._seen
            for key in stale:
                del self._entries[key]
            dirty = self._dirty or bool(stale)
            self._seen = set()
            self._dirty = False
            payload = {"version": self.VERSION, "entries": dict(self._entries)}
        if not dirty or self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("Could not save the tag cache: %s", exc)
