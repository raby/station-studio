"""§16.14 c1 — generate the starter station's synthetic iTunes XML pools.

Writes two committed XMLs alongside `stations/starter.json`:

  * `stations/starter-content.itunes.xml` — 50 tracks across 10 fully
    synthetic artists (5 tracks each), Genre="Pop". Drives the
    `weighted` content slots; the artist-dedup window has enough
    variety to produce an interesting week.
  * `stations/starter-jingles.itunes.xml` — 5 tracks, Genre="Jingle".
    Drives the `jingle` strategy slots (every 8th position).

All track + artist names are entirely made-up (no public-domain
attribution, no CC-0 matchback risk). Persistent IDs are
deterministic from a counter so re-running this script produces
byte-identical XMLs — fixing a typo is one `make
refresh-starter-pools` command, the diff is small + reviewable.

XML emission uses stdlib `plistlib.dumps` (c1-review 🟡-1): the
previous string-concatenation approach was brittle to track titles
that happen to contain `<` or `&`. plistlib escapes correctly +
matches the iTunes plist format the importer already accepts. The
output uses spaces for indent (plistlib default) rather than the
tabs of older hand-rolled fixtures; that's fine — both are valid
plist syntax.

Run:
    make refresh-starter-pools         # writes both XMLs in place
    python -m scripts.generate_starter_pools  # same
"""
from __future__ import annotations

import plistlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIONS_DIR = REPO_ROOT / "stations"


# ---------- synthetic data ----------

# 10 made-up artists × 5 tracks each = 50 content tracks. Artist names
# follow a "Synthetic Artist N" pattern so it's obvious to any reader
# that this is fixture data, not real music.
CONTENT_ARTISTS = [f"Synthetic Artist {i + 1}" for i in range(10)]

# Track titles: short + descriptive + unambiguously synthetic.
TRACK_TITLES = [
    "Sunrise Loop", "Midday Theme", "Afternoon Drift",
    "Evening Pulse", "Night Bridge",
]

JINGLE_TITLES = [
    "Daytime ID 1", "Daytime ID 2", "Daytime ID 3",
    "Hourly Sweep", "Station Tag",
]


# ---------- helpers ----------


def _persistent_id(prefix: str, n: int) -> str:
    """Build a deterministic 16-hex Persistent ID. Prefix lets us
    keep content + jingle IDs in distinct namespaces so a future
    cross-pool dedupe never has a false collision."""
    return f"{prefix}{n:012X}"


def _duration_ms(tid: int) -> int:
    """c1-review 🟢-1: synthetic per-track variance instead of every
    track being 3:30 flat. 7 buckets (180s..240s in 10s steps) keyed
    off the track id mod 7. Pure aesthetic — engine doesn't care, but
    the resulting XML looks like a real library rather than a copy-
    pasted fixture. Deterministic from tid so the generator stays
    idempotent."""
    return (180_000 + (tid % 7) * 10_000)


def _track_dict(
    tid: int, name: str, artist: str, album: str, genre: str, pid: str,
) -> dict:
    """One Track entry as a Python dict — `plistlib.dumps` serialises
    it to the iTunes-shaped XML block."""
    return {
        "Track ID": tid,
        "Name": name,
        "Artist": artist,
        "Album": album,
        "Genre": genre,
        "Total Time": _duration_ms(tid),
        "Persistent ID": pid,
        "Track Type": "File",
    }


def _library_dict(
    *,
    library_pid: str,
    tracks: list[dict],
    playlist_name: str,
    playlist_pid: str,
) -> dict:
    """Top-level iTunes plist dict for one source pool.

    `Tracks` is keyed by string-form Track ID (iTunes' XML format
    uses string `<key>` tags even for numeric track IDs). `Playlists`
    is a single-element array referencing every track in source
    order.
    """
    return {
        "Major Version": 1,
        "Minor Version": 1,
        "Application Version": "10.3",
        "Features": 5,
        "Show Content Ratings": True,
        "Music Folder": (
            "file://localhost/Users/starter/Music/iTunes/iTunes%20Media/"
        ),
        "Library Persistent ID": library_pid,
        "Tracks": {str(t["Track ID"]): t for t in tracks},
        "Playlists": [
            {
                "Name": playlist_name,
                "Playlist ID": 1,
                "Playlist Persistent ID": playlist_pid,
                "All Items": True,
                "Playlist Items": [
                    {"Track ID": t["Track ID"]} for t in tracks
                ],
            },
        ],
    }


# ---------- pool construction ----------


def build_content_pool() -> bytes:
    """50 tracks: 10 artists × 5 titles, Genre=Pop. Track IDs start
    at 1000 + counter, persistent IDs `STCONT...` namespace."""
    tracks: list[dict] = []
    counter = 0
    for artist in CONTENT_ARTISTS:
        for title in TRACK_TITLES:
            counter += 1
            tid = 1000 + counter
            tracks.append(_track_dict(
                tid=tid,
                name=f"{title} {counter:02d}",
                artist=artist,
                album=f"{artist} — Selected Works",
                genre="Pop",
                pid=_persistent_id("STCONT", counter),
            ))
    return plistlib.dumps(_library_dict(
        library_pid="STARTERLIBRARY01",
        tracks=tracks,
        playlist_name="STARTER_CONTENT",
        playlist_pid="STCONTPLAYLIST01",
    ))


def build_jingles_pool() -> bytes:
    """5 tracks, Genre=Jingle. Track IDs start at 2000+counter,
    persistent IDs in `STJING...` namespace."""
    tracks: list[dict] = []
    for i, title in enumerate(JINGLE_TITLES, start=1):
        tid = 2000 + i
        tracks.append(_track_dict(
            tid=tid,
            name=title,
            artist="Station Imaging",
            album="Station ID Sweepers",
            genre="Jingle",
            pid=_persistent_id("STJING", i),
        ))
    return plistlib.dumps(_library_dict(
        library_pid="STARTERLIBRARY01",
        tracks=tracks,
        playlist_name="STARTER_JINGLES",
        playlist_pid="STJINGPLAYLIST01",
    ))


# ---------- entry point ----------


def main(argv: list[str] | None = None) -> int:
    """Write both pool XMLs in-place under stations/. Idempotent —
    re-running against unchanged inputs writes identical bytes."""
    content_xml = build_content_pool()
    jingles_xml = build_jingles_pool()

    content_path = STATIONS_DIR / "starter-content.itunes.xml"
    jingles_path = STATIONS_DIR / "starter-jingles.itunes.xml"

    content_path.write_bytes(content_xml)
    jingles_path.write_bytes(jingles_xml)

    print(f"  wrote  {content_path.relative_to(REPO_ROOT)}  "
          f"({len(content_xml):,} bytes)")
    print(f"  wrote  {jingles_path.relative_to(REPO_ROOT)}  "
          f"({len(jingles_xml):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
