"""Generate the synthetic fixtures backing sub-slice #19's byte-equality
floor (§14.37 commit 1).

Builds + commits:
  * `tests/fixtures/synthetic_weekly_station.json` — a 120-slot
    flat_slots-form station template exercising all 5 weekly-style
    strategies (jingle, weighted, mix, prev_ref, sequential) + the
    9-cursor-style pattern (here compressed to 4 cursors for
    fixture brevity).
  * `tests/fixtures/itunes_xml_byte_eq/{JINGLES,COMMERCIAL,MIXES,
    NIGHT,WEEKEND,MASTER}.xml` — synthetic pools sized large enough
    to satisfy the template + cover artist dedup (multi-artist
    commercials) + mix exclusion (multiple genres) + cross-week
    cursor advancement (long sequential pools).
  * `tests/fixtures/synthetic_pool_manifest.json` — manifest for the
    pool fixtures, importable via `scripts/import_weekly_pools.py`.

Run once, commit outputs, re-run only when the fixture shape needs
to change. The expected-output fixtures (the playlists themselves)
are captured by `tests/test_byte_equality_synthetic.py`'s bootstrap
mode — see that file's docstring.

Idempotent: re-running produces byte-identical output (no timestamps,
no RNG, all IDs deterministic).
"""
from __future__ import annotations

import json
import plistlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
POOLS_DIR = FIXTURES / "itunes_xml_byte_eq"


# ---------- station template ---------------------------------------------


# 4 segments, each ~30 slots; full template ~120 slots.
# Each segment exercises the 5 strategies + cycles through 4 cursors so
# every named cursor is hit at least once. prev_ref positions point at
# slots within the same template so a future "use last week's output"
# lookup has a valid target (prev_ref's fallback path covers week 1).
def _build_template() -> dict:
    # Strategies referenced by segment (genre choices keep the synthetic
    # pool minimal — 4 mix genres total, 2 jingle genres total).
    MIX_GENRES = ["AlphaMix", "BetaMix", "GammaMix", "DeltaMix"]
    CURSORS = ["NIGHT_1", "NIGHT_2", "WEEKEND_1", "WEEKEND_2"]
    PREV_REF_POSITIONS = [5, 20, 50, 80]  # within-template lookups; all
                                          # fall back on week 1
    slots: list[dict] = []
    for seg_idx in range(4):
        # Daytime jingle (start of segment)
        slots.append({
            "source": "JINGLES", "strategy": "jingle", "size": 1,
            "genre": "Daytime",
        })
        # 15 weighted commercials — long enough to exercise artist dedup
        # window (default 10) within a single segment.
        for _ in range(15):
            slots.append({
                "source": "COMMERCIAL", "strategy": "weighted", "size": 1,
            })
        # Mixzone jingle bracket around the mix block.
        slots.append({
            "source": "JINGLES", "strategy": "jingle", "size": 1,
            "genre": "Mixzone",
        })
        # prev_ref — falls back on week 1, looks up on week 2+.
        slots.append({
            "source": "MIXES", "strategy": "prev_ref", "size": 1,
            "position": PREV_REF_POSITIONS[seg_idx],
            "fallback_genre": MIX_GENRES[seg_idx],
        })
        # Closing Mixzone jingle.
        slots.append({
            "source": "JINGLES", "strategy": "jingle", "size": 1,
            "genre": "Mixzone",
        })
        # mix — cross-week exclusion window 5 (smaller than weekly's 20
        # so the 8-week chain exercises the wrap behaviour at the
        # window boundary).
        slots.append({
            "source": "MIXES", "strategy": "mix", "size": 1,
            "genre": MIX_GENRES[seg_idx],
            "exclusion_window": 5,
        })
        # 10 sequential from the segment's cursor.
        for _ in range(10):
            slots.append({
                "source": "NIGHT" if "NIGHT" in CURSORS[seg_idx] else "WEEKEND",
                "strategy": "sequential", "size": 1,
                "cursor_id": CURSORS[seg_idx],
            })

    return {
        "station": "synthetic-byte-eq",
        "name": "Synthetic Byte-Equality Floor",
        "cadence": "weekly",
        "rng": "on",
        "sources": [
            {"name": "JINGLES",    "colour": "#fb8500", "role": "content"},
            {"name": "COMMERCIAL", "colour": "#219ebc", "role": "content"},
            {"name": "MIXES",      "colour": "#8338ec", "role": "content"},
            {"name": "NIGHT",      "colour": "#3a86ff", "role": "content"},
            {"name": "WEEKEND",    "colour": "#ff006e", "role": "content"},
            {"name": "MASTER",     "colour": "#888888", "role": "reference"},
        ],
        # §14.30 stateful cursors (matches weekly's port — required for
        # the multi-week chain test to advance cursors between weeks).
        "cursor": {
            "base_advance_per_week": 0,
            "cycle_weeks": 1,
            "persist": True,
        },
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            # Compact initial positions; pool sizes accommodate
            # advance × 8 weeks.
            "cursors_init": {
                "NIGHT_1": 0,
                "NIGHT_2": 5,
                "WEEKEND_1": 0,
                "WEEKEND_2": 10,
            },
            "flat_slots": slots,
        },
    }


# ---------- pool XMLs ----------------------------------------------------


def _write_pool(
    out_path: Path,
    pool_name: str,
    tracks: list[tuple[int, str, str, str | None, str | None]],
) -> None:
    """Write a synthetic iTunes plist for `pool_name`.

    `tracks` is a list of (track_id, persistent_id, name, artist|None, genre|None).
    Playlist ordering follows the track list order.
    """
    tracks_dict: dict[str, dict] = {}
    items = []
    for tid, pid, name, artist, genre in tracks:
        entry = {
            "Track ID": tid,
            "Persistent ID": pid,
            "Name": name,
        }
        if artist:
            entry["Artist"] = artist
        if genre:
            entry["Genre"] = genre
        tracks_dict[str(tid)] = entry
        items.append({"Track ID": tid})
    data = {
        "Library Persistent ID": f"BEFIX{pool_name[:11]:>11}".replace(" ", "X"),
        "Tracks": tracks_dict,
        "Playlists": [{"Name": pool_name, "Playlist Items": items}],
    }
    with open(out_path, "wb") as f:
        plistlib.dump(data, f)


def _jingles_pool() -> list[tuple]:
    """Daytime jingles (2) + Mixzone jingles (2). Each segment uses
    exactly 1 Daytime + 2 Mixzone; 2 of each is enough since jingle
    strategy picks from the genre-filtered subset deterministically
    via RNG per generation."""
    return [
        # (Track ID, Persistent ID, Name, Artist, Genre)
        (1001, "JDAY00000000001A", "Daytime Jingle 01", "StationID", "Daytime"),
        (1002, "JDAY00000000002A", "Daytime Jingle 02", "StationID", "Daytime"),
        (1003, "JMIX00000000001A", "Mixzone Jingle 01", "StationID", "Mixzone"),
        (1004, "JMIX00000000002A", "Mixzone Jingle 02", "StationID", "Mixzone"),
    ]


def _commercial_pool() -> list[tuple]:
    """30 tracks across 10 distinct artists (3 tracks per artist).
    Big enough for the dedup window (default 10) to bite within a
    15-slot weighted block, AND for shuffle-bag rotation across
    multiple weeks."""
    out = []
    tid = 2001
    for artist_idx in range(1, 11):
        for track_idx in range(1, 4):
            out.append((
                tid,
                f"CCOM{artist_idx:02d}{track_idx:02d}0000A",
                f"Commercial A{artist_idx:02d}-T{track_idx:02d}",
                f"Artist {artist_idx:02d}",
                None,
            ))
            tid += 1
    return out


def _mixes_pool() -> list[tuple]:
    """8 tracks per genre × 4 genres = 32 tracks. Big enough for the
    5-week mix exclusion window to wrap correctly (8 weeks × 1 pick
    per genre per week = 8 picks within an 8-track pool with the
    5-week window forces wrap on weeks 6+)."""
    out = []
    tid = 3001
    for genre_idx, genre in enumerate(["AlphaMix", "BetaMix", "GammaMix", "DeltaMix"], start=1):
        for track_idx in range(1, 9):
            out.append((
                tid,
                f"MMIX{genre_idx:01d}{track_idx:02d}00000A",
                f"{genre} Track {track_idx:02d}",
                f"Mix Artist {genre_idx}-{track_idx}",
                genre,
            ))
            tid += 1
    return out


def _night_pool() -> list[tuple]:
    """100 tracks. Two NIGHT cursors × 10 slots × 8 weeks = 160 reads
    max; with cursor_advance_step rule defaulting to a small value
    (test will set it explicitly), 100 tracks comfortably accommodate
    wrap behaviour."""
    out = []
    for i in range(1, 101):
        out.append((
            4000 + i,
            f"NNIT{i:04d}00000000",
            f"Night Track {i:03d}",
            f"Night Artist {i // 5 + 1:02d}",
            None,
        ))
    return out


def _weekend_pool() -> list[tuple]:
    """100 tracks. Same sizing logic as NIGHT."""
    out = []
    for i in range(1, 101):
        out.append((
            5000 + i,
            f"WWKD{i:04d}00000000",
            f"Weekend Track {i:03d}",
            f"Weekend Artist {i // 5 + 1:02d}",
            None,
        ))
    return out


def _master_pool() -> list[tuple]:
    """Small reference pool — never sampled. 10 tracks so the role
    semantics are exercised on a non-empty pool."""
    return [
        (6000 + i, f"REFX{i:04d}00000000", f"Master Ref {i:02d}", None, None)
        for i in range(1, 11)
    ]


# ---------- manifest -----------------------------------------------------


_MANIFEST = {
    "_comment": (
        "Synthetic pool manifest for §14.37 byte-equality floor. "
        "Paths are relative to this manifest's parent directory "
        "(tests/fixtures/). All 6 pools are exercised by "
        "synthetic_weekly_station.json; MASTER is reference-role."
    ),
    "schema_version": 1,
    "pools": {
        "JINGLES":    {"path": "itunes_xml_byte_eq/JINGLES.xml",    "role": "content"},
        "COMMERCIAL": {"path": "itunes_xml_byte_eq/COMMERCIAL.xml", "role": "content"},
        "MIXES":      {"path": "itunes_xml_byte_eq/MIXES.xml",      "role": "content"},
        "NIGHT":      {"path": "itunes_xml_byte_eq/NIGHT.xml",      "role": "content"},
        "WEEKEND":    {"path": "itunes_xml_byte_eq/WEEKEND.xml",    "role": "content"},
        "MASTER":     {"path": "itunes_xml_byte_eq/MASTER.xml",     "role": "reference"},
    },
}


def main() -> int:
    POOLS_DIR.mkdir(parents=True, exist_ok=True)

    # Station template
    tmpl_path = FIXTURES / "synthetic_weekly_station.json"
    tmpl_path.write_text(json.dumps(_build_template(), indent=2) + "\n")
    print(f"wrote {tmpl_path}")

    # Pool XMLs
    pools = {
        "JINGLES":    _jingles_pool(),
        "COMMERCIAL": _commercial_pool(),
        "MIXES":      _mixes_pool(),
        "NIGHT":      _night_pool(),
        "WEEKEND":    _weekend_pool(),
        "MASTER":     _master_pool(),
    }
    for name, tracks in pools.items():
        out = POOLS_DIR / f"{name}.xml"
        _write_pool(out, name, tracks)
        print(f"wrote {out} — {len(tracks)} tracks")

    # Manifest
    manifest_path = FIXTURES / "synthetic_pool_manifest.json"
    manifest_path.write_text(json.dumps(_MANIFEST, indent=2) + "\n")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
