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

Run:
    make refresh-starter-pools         # writes both XMLs in place
    python -m scripts.generate_starter_pools  # same

Why a generator script + committed XMLs rather than generate-on-boot?
Per §16.14 D5: the XMLs are reviewable in PRs (a typo or content
change is obvious in `git diff`), small enough to commit (~10KB
total), and never go out-of-sync with the template. Generation is
opt-in via the Make target, not a hidden first-boot step.
"""
from __future__ import annotations

import sys
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


# ---------- XML emission ----------

PLIST_HEADER = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Major Version</key><integer>1</integer>
\t<key>Minor Version</key><integer>1</integer>
\t<key>Application Version</key><string>10.3</string>
\t<key>Features</key><integer>5</integer>
\t<key>Show Content Ratings</key><true/>
\t<key>Music Folder</key><string>file://localhost/Users/starter/Music/iTunes/iTunes%20Media/</string>
\t<key>Library Persistent ID</key><string>STARTERLIBRARY01</string>
\t<key>Tracks</key>
\t<dict>
'''

TRACK_BLOCK = '''\t\t<key>{tid}</key>
\t\t<dict>
\t\t\t<key>Track ID</key><integer>{tid}</integer>
\t\t\t<key>Name</key><string>{name}</string>
\t\t\t<key>Artist</key><string>{artist}</string>
\t\t\t<key>Album</key><string>{album}</string>
\t\t\t<key>Genre</key><string>{genre}</string>
\t\t\t<key>Total Time</key><integer>210000</integer>
\t\t\t<key>Persistent ID</key><string>{pid}</string>
\t\t\t<key>Track Type</key><string>File</string>
\t\t</dict>
'''


def _persistent_id(prefix: str, n: int) -> str:
    """Build a deterministic 16-hex Persistent ID. Prefix lets us
    keep content + jingle IDs in distinct namespaces so a future
    cross-pool dedupe never has a false collision."""
    return f"{prefix}{n:012X}"


def _playlist_block(name: str, pid: str, track_ids: list[int]) -> str:
    items = "\n".join(
        f"\t\t\t\t<dict>\n\t\t\t\t\t<key>Track ID</key><integer>{tid}</integer>\n\t\t\t\t</dict>"
        for tid in track_ids
    )
    return f'''\t\t<dict>
\t\t\t<key>Name</key><string>{name}</string>
\t\t\t<key>Playlist ID</key><integer>1</integer>
\t\t\t<key>Playlist Persistent ID</key><string>{pid}</string>
\t\t\t<key>All Items</key><true/>
\t\t\t<key>Playlist Items</key>
\t\t\t<array>
{items}
\t\t\t</array>
\t\t</dict>
'''


def _emit_xml(
    pool_name: str,
    pool_pid: str,
    tracks: list[dict],
) -> str:
    """Assemble the full plist XML for a single pool."""
    out = [PLIST_HEADER]
    for t in tracks:
        out.append(TRACK_BLOCK.format(**t))
    out.append("\t</dict>\n\t<key>Playlists</key>\n\t<array>\n")
    out.append(_playlist_block(
        pool_name, pool_pid, [t["tid"] for t in tracks],
    ))
    out.append("\t</array>\n</dict>\n</plist>\n")
    return "".join(out)


# ---------- pool construction ----------

def build_content_pool() -> str:
    """50 tracks: 10 artists × 5 titles, Genre=Pop. Track IDs start
    at 1000 + counter, persistent IDs `STCONT...` namespace."""
    tracks: list[dict] = []
    counter = 0
    for artist in CONTENT_ARTISTS:
        for title in TRACK_TITLES:
            counter += 1
            tid = 1000 + counter
            tracks.append({
                "tid": tid,
                "name": f"{title} {counter:02d}",
                "artist": artist,
                "album": f"{artist} — Selected Works",
                "genre": "Pop",
                "pid": _persistent_id("STCONT", counter),
            })
    return _emit_xml(
        pool_name="STARTER_CONTENT",
        pool_pid="STCONTPLAYLIST01",
        tracks=tracks,
    )


def build_jingles_pool() -> str:
    """5 tracks, Genre=Jingle. Track IDs start at 2000+counter,
    persistent IDs in `STJING...` namespace."""
    tracks: list[dict] = []
    for i, title in enumerate(JINGLE_TITLES, start=1):
        tracks.append({
            "tid": 2000 + i,
            "name": title,
            "artist": "Station Imaging",
            "album": "Station ID Sweepers",
            "genre": "Jingle",
            "pid": _persistent_id("STJING", i),
        })
    return _emit_xml(
        pool_name="STARTER_JINGLES",
        pool_pid="STJINGPLAYLIST01",
        tracks=tracks,
    )


# ---------- entry point ----------

def main(argv: list[str] | None = None) -> int:
    """Write both pool XMLs in-place under stations/. Idempotent —
    re-running against unchanged inputs writes identical bytes."""
    content_xml = build_content_pool()
    jingles_xml = build_jingles_pool()

    content_path = STATIONS_DIR / "starter-content.itunes.xml"
    jingles_path = STATIONS_DIR / "starter-jingles.itunes.xml"

    content_path.write_text(content_xml)
    jingles_path.write_text(jingles_xml)

    print(f"  wrote  {content_path.relative_to(REPO_ROOT)}  "
          f"({len(content_xml):,} bytes)")
    print(f"  wrote  {jingles_path.relative_to(REPO_ROOT)}  "
          f"({len(jingles_xml):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
