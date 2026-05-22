"""Generate the synthetic Classics pool fixtures for §14.40 byte-equality.

Builds + commits:
  * `tests/fixtures/itunes_xml_classics/{RG_RADIO,RG_PARTY,RG_MASTER}.xml`
    — synthetic iTunes-XML pools matching Classics's source vocabulary.
  * `tests/fixtures/classics_pool_manifest.json` — manifest for these
    pools, importable via `scripts/import_weekly_pools.py`.

The pool sizes are computed from Classics's real template
(`stations/rebirth-classics.json`):

  * `base_advance_per_week = 60`, `cycle_weeks = 16` →
    max base_cursor at week 16 = 15 × 60 = 900.
  * `max(row_start_offsets) = 50`.
  * `max(block.size) = 215`.
  * Max read index per pool = 50 + 900 + 214 = 1,164.
  * Content pools sized to 1,200 tracks (~30 tracks of headroom).

`RG_MASTER` is reference-role — never sampled by the engine; declared
so the importer has a place to drop the master XML. Synthetic size of
50 is sufficient (reference pools only need to exist).

Why synthetic, not real: real Classics XMLs carry copyrighted track
metadata (artist names, album titles). The byte-equality test's goal
is to prove the engine's generality (row_template + window strategy +
stateless cursor) end-to-end against a structurally distinct station
from weekly — synthetic pools cover that goal without copyright
exposure. Operators with real Classics pools can run the same engine
path via the existing `make import-weekly-pools` flow against their
own manifest.

Run once, commit outputs, re-run only when the fixture shape needs to
change. The expected-output fixture (the 16-week chain of generated
PIDs) is captured by `tests/test_classics_byte_equality.py`'s
bootstrap mode — see that file's docstring.

Idempotent: re-running produces byte-identical output (no timestamps,
no RNG, all IDs deterministic).
"""
from __future__ import annotations

import json
import plistlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
POOLS_DIR = FIXTURES / "itunes_xml_classics"


# Pool sizes — see module docstring for derivation. Content pools must
# be at least 1,165 to satisfy week-16 reads without wrap; the reference
# pool can be small (engine never samples from it).
RG_RADIO_SIZE = 1_200
RG_PARTY_SIZE = 1_200
RG_MASTER_SIZE = 50

# Track ID base ranges per pool (Track ID is iTunes-XML internal; PIDs
# are the cross-pool stable identifier). Disjoint per pool so dedup
# semantics work when the same Track somehow appears in two pools.
RG_RADIO_TID_BASE = 100_000
RG_PARTY_TID_BASE = 200_000
RG_MASTER_TID_BASE = 300_000


def _build_pool(
    name: str,
    pid_prefix: str,
    tid_base: int,
    n_tracks: int,
) -> list[tuple]:
    """Build a list of (track_id, persistent_id, name, artist) tuples.

    PIDs are 16-char-style strings; each pool has a 4-char prefix so
    cross-pool dedup catches accidental collisions (the persistent_id
    column is UNIQUE). Artists rotate through a small set to give
    artist-dedup semantics SOMETHING to chew on — but Classics's window
    strategy doesn't read artists, so this is just for completeness.
    """
    out = []
    artists = [f"Classics Artist {i:02d}" for i in range(1, 11)]
    for i in range(1, n_tracks + 1):
        out.append((
            tid_base + i,
            f"{pid_prefix}{i:010d}AA",  # 4 + 10 + 2 = 16 chars
            f"{name} Track {i:04d}",
            artists[i % len(artists)],
        ))
    return out


def _write_pool_xml(out: Path, playlist_name: str, tracks: list[tuple]) -> None:
    """Write a minimal iTunes plist XML for `tracks`.

    Shape mirrors what `engine.importer.import_source` expects:
    top-level Tracks dict + Playlists list with one playlist holding
    the ordered track refs.
    """
    tracks_dict = {}
    items = []
    for tid, pid, tname, artist in tracks:
        tracks_dict[str(tid)] = {
            "Track ID": tid,
            "Persistent ID": pid,
            "Name": tname,
            "Artist": artist,
        }
        items.append({"Track ID": tid})

    plist = {
        "Library Persistent ID": "CLASSICS00000001",
        "Tracks": tracks_dict,
        "Playlists": [{
            "Name": playlist_name,
            "Playlist Items": items,
        }],
    }
    with open(out, "wb") as f:
        plistlib.dump(plist, f)


def main() -> int:
    POOLS_DIR.mkdir(parents=True, exist_ok=True)

    pools = [
        ("RG_RADIO",  "RGRD", RG_RADIO_TID_BASE,  RG_RADIO_SIZE,  "content"),
        ("RG_PARTY",  "RGPT", RG_PARTY_TID_BASE,  RG_PARTY_SIZE,  "content"),
        ("RG_MASTER", "RGMS", RG_MASTER_TID_BASE, RG_MASTER_SIZE, "reference"),
    ]

    for name, prefix, tid_base, size, _role in pools:
        tracks = _build_pool(name, prefix, tid_base, size)
        path = POOLS_DIR / f"{name}.xml"
        _write_pool_xml(path, name, tracks)
        print(f"wrote {path} — {size} tracks")

    # Manifest — same shape as scripts/import_weekly_pools.py expects.
    manifest = {
        "_comment": (
            "Synthetic Classics pool manifest for §14.40 byte-equality. "
            "RG_RADIO + RG_PARTY are content (engine samples); RG_MASTER "
            "is reference (declared so the import has a place to land "
            "but never sampled — mirrors Classics's real structure). "
            "Generated by scripts/generate_classics_fixtures.py."
        ),
        "schema_version": 1,
        "pools": {
            "RG_RADIO":  {"path": "itunes_xml_classics/RG_RADIO.xml",  "role": "content"},
            "RG_PARTY":  {"path": "itunes_xml_classics/RG_PARTY.xml",  "role": "content"},
            "RG_MASTER": {"path": "itunes_xml_classics/RG_MASTER.xml", "role": "reference"},
        },
    }
    manifest_path = FIXTURES / "classics_pool_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
