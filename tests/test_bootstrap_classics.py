"""Tests for scripts/bootstrap_classics.py.

Uses synthetic iTunes XML plists large enough to drive real Classics
generation (RG_RADIO/RG_PARTY must be >=1165 tracks to cover week 16; we use
2000 to give headroom). MASTER is imported but not referenced by the layout,
so a smaller pool is fine there.

Proves:
  * End-to-end bootstrap: seed + import + generate produces a 3,010-slot playlist
  * The generated playlist is the same one direct-engine generation produces
  * Re-running is safe (idempotent; replaces the week's playlist cleanly)
  * --skip-missing path: bootstrap completes without generation when content files absent
  * Missing content without --skip-missing is a hard error
"""
from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.generator import generate
from engine.models import Base, Playlist, PlaylistEntry, PlaylistVersion, Station, SourcePlaylist
from engine.template import parse_template
from scripts.bootstrap_classics import bootstrap, CLASSICS_JSON


@pytest.fixture
def db(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/boot.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _write_plist(path: Path, playlist_name: str, pool_prefix: str, size: int) -> None:
    tracks = []
    for i in range(1, size + 1):
        tracks.append({
            "Track ID": i,
            "Persistent ID": f"{pool_prefix}{i:014X}",
            "Name": f"{pool_prefix} {i:04d}",
            "Artist": f"{pool_prefix}-Artist-{i % 50}",
        })
    data = {
        "Tracks": {str(t["Track ID"]): t for t in tracks},
        "Playlists": [{
            "Name": playlist_name,
            "Playlist Items": [{"Track ID": t["Track ID"]} for t in tracks],
        }],
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


@pytest.fixture
def from_dir_full(tmp_path) -> Path:
    d = tmp_path / "from_user"
    d.mkdir()
    _write_plist(d / "RG_RADIO.xml",  "RG_RADIO",  "RGRD", 2000)
    _write_plist(d / "RG_PARTY.xml",  "RG_PARTY",  "RGPT", 2000)
    _write_plist(d / "RG_MASTER.xml", "RG_MASTER", "RGMS", 500)
    return d


@pytest.fixture
def from_dir_partial(tmp_path) -> Path:
    """Only RG_RADIO is present."""
    d = tmp_path / "from_user_partial"
    d.mkdir()
    _write_plist(d / "RG_RADIO.xml", "RG_RADIO", "RGRD", 2000)
    return d


# ---------- tests --------------------------------------------------------


def test_full_bootstrap_seeds_imports_generates(db, from_dir_full):
    result = bootstrap(db, from_dir=from_dir_full, week=1)

    assert result.station_action == "created"
    assert [s["name"] for s in result.imported] == ["RG_RADIO", "RG_PARTY", "RG_MASTER"]
    assert result.missing == []
    assert result.playlist_id is not None
    assert result.trace_id is not None
    assert result.slot_count == 3010

    # Verify the playlist was actually persisted with 3,010 entries
    pl = db.query(Playlist).filter_by(id=result.playlist_id).one()
    assert pl.week == 1
    assert pl.station.slug == "rebirth-classics"
    assert db.query(PlaylistEntry).filter_by(playlist_id=pl.id).count() == 3010

    # And the trace was persisted (§16.4 — as version_no=1)
    trace = db.query(PlaylistVersion).filter_by(trace_id=result.trace_id).one()
    assert trace.week == 1
    assert trace.version_no == 1
    assert len(trace.slots) == 3010


def test_generated_playlist_matches_direct_engine(db, from_dir_full):
    """The bootstrap-generated playlist must match what you'd get calling generate() directly."""
    result = bootstrap(db, from_dir=from_dir_full, week=1)

    # Build the same pools the bootstrap fed to the engine, from the DB
    station = db.query(Station).filter_by(slug="rebirth-classics").one()
    template = parse_template(station.template)
    pools = {}
    for name in template.content_source_names:
        sp = db.query(SourcePlaylist).filter_by(name=name).one()
        pools[name] = [e.track for e in sorted(sp.entries, key=lambda e: e.position)]

    direct = generate(template, pools, week=1)

    # Track names per slot must match
    saved = db.query(PlaylistEntry).filter_by(playlist_id=result.playlist_id).order_by(PlaylistEntry.position).all()
    assert [e.track.name for e in saved] == [t.name for t in direct.picks]


def test_bootstrap_is_idempotent(db, from_dir_full):
    """Running twice must succeed and end with a single playlist for week 1."""
    bootstrap(db, from_dir=from_dir_full, week=1)
    result2 = bootstrap(db, from_dir=from_dir_full, week=1)

    assert result2.station_action == "unchanged"  # station JSON didn't change
    assert db.query(Playlist).filter_by(week=1).count() == 1


def test_skip_missing_bootstraps_without_generating(db, from_dir_partial):
    result = bootstrap(db, from_dir=from_dir_partial, skip_missing=True)
    assert result.station_action == "created"
    assert [s["name"] for s in result.imported] == ["RG_RADIO"]
    assert set(result.missing) == {"RG_PARTY", "RG_MASTER"}
    assert result.playlist_id is None  # didn't generate because RG_PARTY is a content pool


def test_missing_content_without_skip_raises(db, from_dir_partial):
    with pytest.raises(SystemExit, match="content pool"):
        bootstrap(db, from_dir=from_dir_partial, skip_missing=False)


def test_no_generate_flag_skips_generation_even_when_complete(db, from_dir_full):
    result = bootstrap(db, from_dir=from_dir_full, generate_playlist=False)
    assert result.playlist_id is None
    assert len(result.imported) == 3


def test_different_weeks_produce_different_playlists(db, from_dir_full):
    r1 = bootstrap(db, from_dir=from_dir_full, week=1)
    r2 = bootstrap(db, from_dir=from_dir_full, week=2)
    assert r1.playlist_id != r2.playlist_id

    p1 = db.query(PlaylistEntry).filter_by(playlist_id=r1.playlist_id).order_by(PlaylistEntry.position).first()
    p2 = db.query(PlaylistEntry).filter_by(playlist_id=r2.playlist_id).order_by(PlaylistEntry.position).first()
    # Week 1 slot 1 = RG_RADIO index 1 (name "RGRD 0001")
    # Week 2 slot 1 = RG_RADIO index 61 (name "RGRD 0061")
    assert p1.track.name == "RGRD 0001"
    assert p2.track.name == "RGRD 0061"
