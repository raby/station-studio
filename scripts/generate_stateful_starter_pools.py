"""§16.14.t1 c1 — generate the stateful starter station's pool.

Writes one committed iTunes XML alongside `stations/stateful-starter.json`:

  * `stations/stateful-starter-content.itunes.xml` — 336 tracks
    across 14 fully-synthetic artists (24 tracks each), Genre="Pop".
    Exactly matches the persistent cursor's cycle math: 24 tracks
    per week × 14 weeks = 336 = full pool coverage per cycle.

All track + artist names are entirely made-up (no public-domain
attribution, no CC-0 matchback risk). Persistent IDs are
deterministic from a counter so re-running this script produces
byte-identical XML — fixing a typo is one `make
refresh-stateful-starter-pools` command, the diff is reviewable.

The artist count (14) was chosen to match `cycle_weeks` from the
template's design pass — 24 tracks per artist roughly maps to "one
artist per cursor advance per week." It's an aesthetic match; the
engine doesn't care about the artist distribution for the
`sequential` strategy (no artist-dedup applies).

XML emission uses stdlib `plistlib.dumps` (same approach as
`generate_starter_pools.py` post c1-review 🟡-1) — escape-safe
against future track titles containing XML-special chars, matches
the iTunes plist format the importer accepts.

Run:
    make refresh-stateful-starter-pools
    python -m scripts.generate_stateful_starter_pools  # same
"""
from __future__ import annotations

import plistlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIONS_DIR = REPO_ROOT / "stations"


# ---------- synthetic data ----------

# 14 artists × 24 tracks = 336 total. Artist names self-documenting
# as fixture data (no real-world matchback).
CONTENT_ARTISTS = [
    f"Stateful Artist {i + 1:02d}" for i in range(14)
]

# Track titles cycle through a 24-entry list of mood/time descriptors
# per artist. Per-artist combined with the counter so titles are
# unique across the whole pool.
TRACK_MOODS = [
    "Dawn Theme", "Morning Drift", "Sunrise Pulse", "Coffee Loop",
    "Late Breakfast", "Midmorning Bridge", "Late Morning Pulse",
    "Noon Tag", "Early Afternoon", "Midday Flow", "Afternoon Drift",
    "Late Afternoon", "Dusk Theme", "Twilight Loop", "Evening Bridge",
    "Sunset Tag", "Early Night", "Night Drift", "Late Night Loop",
    "Midnight Theme", "Witching Hour", "Pre-Dawn", "First Light",
    "Day Break",
]
assert len(TRACK_MOODS) == 24, "track mood list must be 24 entries"


# ---------- XML emission helpers ----------


def _persistent_id(prefix: str, n: int) -> str:
    """Build a deterministic 16-hex Persistent ID. Prefix
    `STFCONT...` keeps these distinct from §16.14 starter's
    `STCONT...` IDs so a cross-pool dedupe could never falsely
    collide."""
    return f"{prefix}{n:012X}"


def _duration_ms(tid: int) -> int:
    """Same `tid % 7` variance pattern as the §16.14 starter (c1-review
    🟢-1) — 7 distinct duration buckets keyed off the track id. The
    engine doesn't care about durations; the variance just keeps the
    synthetic XML looking like a real library rather than a
    copy-pasted fixture."""
    return 180_000 + (tid % 7) * 10_000


def _track_dict(
    tid: int, name: str, artist: str, album: str, genre: str, pid: str,
) -> dict:
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
    """Top-level iTunes plist dict for one source pool. Mirrors the
    shape used in generate_starter_pools.py."""
    return {
        "Major Version": 1,
        "Minor Version": 1,
        "Application Version": "10.3",
        "Features": 5,
        "Show Content Ratings": True,
        "Music Folder": (
            "file://localhost/Users/stateful/Music/iTunes/iTunes%20Media/"
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
    """336 tracks: 14 artists × 24 titles each, Genre=Pop.

    Track IDs start at 5000 + counter, persistent IDs in `STFCONT`
    namespace. Two orderings to choose between:
      (a) artist-major (artist 1 fills positions 0..23, artist 2 fills
          positions 24..47, ...). Matches "this week features artist
          N" feel.
      (b) round-robin (artist 1, artist 2, ..., artist 14, artist 1,
          artist 2, ...) — flatter variety, the sequential cursor
          weaves through all artists each week.

    Picking (a) — it makes the `cursor.base_advance_per_week=24`
    behaviour visible to a user reading the trace ("week 1 was
    artist 1; week 2 is artist 2; ...; week 14 cycles back").
    Aesthetic choice; engine doesn't care.
    """
    tracks: list[dict] = []
    counter = 0
    for artist in CONTENT_ARTISTS:
        for mood in TRACK_MOODS:
            counter += 1
            tid = 5000 + counter
            tracks.append(_track_dict(
                tid=tid,
                name=f"{mood} {counter:03d}",
                artist=artist,
                album=f"{artist} — Twenty-Four Hours",
                genre="Pop",
                pid=_persistent_id("STFCONT", counter),
            ))
    return plistlib.dumps(_library_dict(
        library_pid="STFSTARTERLIB001",
        tracks=tracks,
        playlist_name="STATEFUL_CONTENT",
        playlist_pid="STFCONTPLAYLIST1",
    ))


# ---------- entry point ----------


def main(argv: list[str] | None = None) -> int:
    """Write the pool XML in-place under stations/. Idempotent —
    re-running against unchanged inputs writes identical bytes."""
    content_xml = build_content_pool()

    content_path = STATIONS_DIR / "stateful-starter-content.itunes.xml"
    content_path.write_bytes(content_xml)

    print(f"  wrote  {content_path.relative_to(REPO_ROOT)}  "
          f"({len(content_xml):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
