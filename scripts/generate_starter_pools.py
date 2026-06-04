"""§16.14 c1 + §16.14.t3 c1 — generate the starter station's
synthetic iTunes XML pools.

Writes THREE committed XMLs alongside `stations/starter.json`:

  * `stations/starter-content.itunes.xml` — 50 tracks across 10 fully
    synthetic artists (5 tracks each), Genre="Pop". Drives the
    `weighted` content slots; the artist-dedup window has enough
    variety to produce an interesting week.
  * `stations/starter-jingles.itunes.xml` — 5 tracks, Genre="Jingle".
    Drives the `jingle` strategy slots (every 8th position).
  * `stations/starter-reference.itunes.xml` — 9 tracks across 3
    fully-synthetic archive artists (3 tracks each), Genre="Archive".
    §16.14.t3 teaching pool: declared in starter.json's `sources[]`
    with `role: reference` but NOT used in any slot block. The
    engine never samples from it; its purpose is to make the
    reference-role concept tangible on the station detail page +
    in the Sources page, and to anchor the cross-check test
    (§14.35) that's the only real engine behaviour of role:
    reference today. See stations/README.md for the full story.

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


# §16.14.t3 D2: small reference pool. 3 fully-synthetic "archive
# artists" × 3 tracks each = 9 reference tracks. Distinct artist
# names from CONTENT_ARTISTS so a reader inspecting the visualiser
# can see the two pools are genuinely separate collections (not
# slices of the same pool). Persistent IDs use the STREF... namespace
# to keep them distinct from STCONT + STJING.
ARCHIVE_ARTISTS = [f"Archive Artist {i + 1}" for i in range(3)]
ARCHIVE_TITLES = ["Archive Tape A", "Archive Tape B", "Archive Tape C"]


# §16.14.t5 c2 — content-echo counters for the cross-pool dedup demo.
#
# REFERENCE_ARCHIVE carries 9 archive-only tracks PLUS 3 "echoes" of
# specific STARTER_CONTENT tracks (start / middle / end of the
# content pool). Both XMLs declare the same `Persistent ID` for these
# 3 tracks, so the importer reuses a single `Track` row across both
# pools; load_excluded_track_pids resolves REFERENCE_ARCHIVE to a
# 12-PID set, of which 3 are also in STARTER_CONTENT.
#
# At generation time the §16.14.t5 station rule
# `cross_pool_dedup_sources=["REFERENCE_ARCHIVE"]` (seeded by
# engine.seeding._STATION_LIST_RULES) makes the weighted strategy
# drop those 3 PIDs from the STARTER_CONTENT pool before picking.
#
# The demo PIDs are exposed as a module-level constant so the c2
# integration test + future docs can reference them by name without
# re-deriving the counter math.
_CONTENT_ECHO_COUNTERS = (1, 25, 50)
CONTENT_ECHOED_PERSISTENT_IDS: tuple[str, ...] = tuple(
    _persistent_id("STCONT", c) for c in _CONTENT_ECHO_COUNTERS
)


def _content_track_at_counter(counter: int) -> dict:
    """Rebuild the exact `_track_dict` `build_content_pool` would emit
    at the given counter. Kept as a separate helper (vs reusing
    build_content_pool() + indexing) so the echo logic is local to
    this file and obvious: the echoes BYTE-FOR-BYTE match the
    content tracks, which is the whole mechanism — same PID → one
    Track row in the importer's dedup-by-pid path.
    """
    artist_idx = (counter - 1) // len(TRACK_TITLES)
    title_idx = (counter - 1) % len(TRACK_TITLES)
    artist = CONTENT_ARTISTS[artist_idx]
    title = TRACK_TITLES[title_idx]
    tid = 1000 + counter
    return _track_dict(
        tid=tid,
        name=f"{title} {counter:02d}",
        artist=artist,
        album=f"{artist} — Selected Works",
        genre="Pop",
        pid=_persistent_id("STCONT", counter),
    )


def build_reference_pool() -> bytes:
    """§16.14.t3 D1+D2 + §16.14.t5 c2: the reference-role companion pool.

    Carries TWO kinds of tracks:

    1. **Archive-only** (9 tracks). 3 fully-synthetic archive artists
       × 3 titles each, Genre="Archive", PID namespace `STREF`. These
       are the original §16.14.t3 teaching tracks — they sit in
       REFERENCE_ARCHIVE only, distinct from STARTER_CONTENT.

    2. **Content echoes** (3 tracks). Byte-identical copies of
       STARTER_CONTENT entries at counters `_CONTENT_ECHO_COUNTERS`
       (start / middle / end of the content pool). Same Persistent
       ID → the importer reuses the existing Track row; the same
       Track shows up in both SourcePlaylist rows. These exist to
       make the §16.14.t5 cross-pool dedup demo tangible — the
       starter station's rule `cross_pool_dedup_sources=
       ["REFERENCE_ARCHIVE"]` (seeded imperatively in
       engine.seeding._STATION_LIST_RULES) causes the weighted
       strategy to drop these 3 PIDs from its pick set.

    Total: 12 entries. Without the rule set, none of this matters
    (the engine still doesn't sample from REFERENCE_ARCHIVE — it's
    role=reference). The rule is what wires the demo end-to-end.
    """
    tracks: list[dict] = []
    counter = 0
    for artist in ARCHIVE_ARTISTS:
        for title in ARCHIVE_TITLES:
            counter += 1
            tid = 3000 + counter
            tracks.append(_track_dict(
                tid=tid,
                name=f"{title} {counter:02d}",
                artist=artist,
                album=f"{artist} — Master Archive",
                genre="Archive",
                pid=_persistent_id("STREF", counter),
            ))
    # §16.14.t5 c2: append 3 content echoes. Track IDs reuse the
    # content pool's numbering (1000-range) so the XML's "Track ID"
    # field matches what STARTER_CONTENT publishes — iTunes Track IDs
    # are per-library but the importer keys by Persistent ID, so the
    # numeric Track ID is purely cosmetic. Echoes appear AFTER the
    # archive-original tracks in playlist order so the trace + viewer
    # show the archive-originals first (which is what hobbyists
    # inspecting the pool expect to see).
    for c in _CONTENT_ECHO_COUNTERS:
        tracks.append(_content_track_at_counter(c))
    # c2-review 🟡-1: playlist_name MUST match the name the
    # `_FIRST_BOOT_POOLS` auto-import uses (REFERENCE_ARCHIVE) so a
    # user inspecting starter-reference.itunes.xml sees the same
    # name the docs + UI + Sources page show. Pre-fix the XML's
    # internal playlist_name was "STARTER_REFERENCE" which the
    # importer ignored (it takes the name as a parameter), creating
    # a docs-versus-XML naming inconsistency.
    return plistlib.dumps(_library_dict(
        library_pid="STARTERLIBRARY01",
        tracks=tracks,
        playlist_name="REFERENCE_ARCHIVE",
        playlist_pid="STREFPLAYLIST001",
    ))


# ---------- entry point ----------


def main(argv: list[str] | None = None) -> int:
    """Write both pool XMLs in-place under stations/. Idempotent —
    re-running against unchanged inputs writes identical bytes."""
    content_xml = build_content_pool()
    jingles_xml = build_jingles_pool()
    reference_xml = build_reference_pool()

    content_path = STATIONS_DIR / "starter-content.itunes.xml"
    jingles_path = STATIONS_DIR / "starter-jingles.itunes.xml"
    reference_path = STATIONS_DIR / "starter-reference.itunes.xml"

    content_path.write_bytes(content_xml)
    jingles_path.write_bytes(jingles_xml)
    reference_path.write_bytes(reference_xml)

    print(f"  wrote  {content_path.relative_to(REPO_ROOT)}  "
          f"({len(content_xml):,} bytes)")
    print(f"  wrote  {jingles_path.relative_to(REPO_ROOT)}  "
          f"({len(jingles_xml):,} bytes)")
    print(f"  wrote  {reference_path.relative_to(REPO_ROOT)}  "
          f"({len(reference_xml):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
