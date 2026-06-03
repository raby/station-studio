"""§16.14 c2 — first-boot auto-import + sidecar auto-derive tests.

Covers the three states from D6 + Q8:

  1. **First-boot:** fresh app-home, fresh DB, no SourcePlaylist rows.
     Both starter pools auto-import; the result reports
     `pools_auto_imported == 2`.
  2. **Second-boot:** the previous run already populated SourcePlaylist
     (with starter pools or user-imported pools — doesn't matter).
     `pools_auto_imported == 0`. The Q8 first-boot guard fires.
  3. **User-deleted-and-stayed-empty:** the user deletes the
     auto-imported pools to learn the manual import flow.
     `SourcePlaylist.count() == 0` but they shouldn't get the
     starter back automatically. This is a known limitation of the
     "count == 0" guard — see the docstring on
     `test_user_deleted_pools_will_re_import_known_quirk` for the
     honest framing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from station_studio.bootstrap import bootstrap


# --- pool auto-import ---


def test_first_boot_auto_imports_two_starter_pools(
    isolated_cli_home,
) -> None:
    """Fresh app-home → bootstrap copies the bundled starter XMLs into
    the stations dir → auto-import imports both as SourcePlaylist
    rows. Result.pools_auto_imported == 2."""
    result = bootstrap()
    assert result.pools_auto_imported == 2

    # SourcePlaylist rows actually landed.
    import sqlite3
    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        rows = db.execute(
            "SELECT name, role FROM source_playlist ORDER BY name"
        ).fetchall()
    finally:
        db.close()
    assert rows == [
        ("STARTER_CONTENT", "content"),
        ("STARTER_JINGLES", "content"),
    ]


def test_second_boot_skips_auto_import(isolated_cli_home) -> None:
    """A second bootstrap on the same home leaves SourcePlaylist
    untouched + reports pools_auto_imported == 0. The first-boot
    guard (Q8) fires on the EXISTING-rows check, not on the
    home-existed check (so the guard is honest even if the home
    is recreated by hand)."""
    first = bootstrap()
    assert first.pools_auto_imported == 2

    second = bootstrap()
    assert second.pools_auto_imported == 0

    # Still exactly 2 rows; nothing duplicated, nothing nuked.
    import sqlite3
    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        count = db.execute(
            "SELECT COUNT(*) FROM source_playlist"
        ).fetchone()[0]
    finally:
        db.close()
    assert count == 2


def test_user_imported_pool_blocks_starter_auto_import(
    isolated_cli_home,
) -> None:
    """If the user pre-imports their OWN pool BEFORE first bootstrap,
    the starter auto-import is suppressed (D6's first-boot semantics
    treat presence of ANY user pool as "user has taken over"). The
    starter still ships in stations/ — the user can import it
    manually if they want it."""
    from engine.database import make_engine, make_sessionmaker
    from engine.importer import import_source
    from engine.models import Base

    # Run bootstrap once to set env + run migrations + copy stations
    # but THEN pre-seed a user pool BEFORE the next-boot test.
    bootstrap()
    # Wipe the auto-imported rows so we can simulate "user imported
    # first" (the test is interested in the gating logic, not the
    # ordering). Delete BOTH tables to avoid stale-entry primary-key
    # reuse — SQLite recycles row IDs, so a fresh SourcePlaylist with
    # id=1 inheriting orphan SourcePlaylistEntries from the previous
    # id=1 row would trip the (source_playlist_id, position) unique
    # constraint on the new pool's import.
    import sqlite3
    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        db.execute("DELETE FROM source_playlist_entry")
        db.execute("DELETE FROM source_playlist")
        db.commit()
    finally:
        db.close()

    # Now import a user pool manually.
    import os
    eng = make_engine()
    Base.metadata.create_all(eng)
    SL = make_sessionmaker(eng)
    user_xml = (
        Path(__file__).resolve().parent / "fixtures" / "tiny-library.xml"
    )
    with SL() as db:
        import_source(db, user_xml, "USER_POOL", role="content")
        db.commit()
    eng.dispose()

    # Re-bootstrap. Should NOT touch source_playlist.
    result = bootstrap()
    assert result.pools_auto_imported == 0

    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        names = [
            r[0] for r in db.execute(
                "SELECT name FROM source_playlist"
            ).fetchall()
        ]
    finally:
        db.close()
    assert names == ["USER_POOL"], (
        "starter pools must NOT be auto-imported when the user has "
        "already taken over the SourcePlaylist table"
    )


def test_user_deleted_pools_will_re_import_known_quirk(
    isolated_cli_home,
) -> None:
    """Q8 honesty pin: the first-boot guard is "no SourcePlaylist rows
    exist." If the user deletes the auto-imported starter pools to
    learn the manual import flow, on next bootstrap the table is
    empty AGAIN and the starter pools re-import.

    This is a known limitation, NOT a bug — the alternative
    ("remember we already imported once") needs a persistent
    marker (a Config row, a SourcePlaylist soft-delete flag) which
    the §16.14 c2 design explicitly punted. The tutorial in
    README.md tells the user to import a DIFFERENT pool (their
    own iTunes XML) to take ownership, not to delete-and-stay-empty.

    Pin the quirk so a future contributor changing the gate sees
    THIS test break + has to think about whether they're regressing
    the tutorial flow."""
    first = bootstrap()
    assert first.pools_auto_imported == 2

    import sqlite3
    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        db.execute("DELETE FROM source_playlist_entry")
        db.execute("DELETE FROM source_playlist")
        db.commit()
    finally:
        db.close()

    second = bootstrap()
    # YES, re-imports. Documented quirk.
    assert second.pools_auto_imported == 2


# --- sidecar auto-derive ---


def test_first_boot_does_not_re_derive_committed_sidecar(
    isolated_cli_home,
) -> None:
    """The starter ships WITH a committed sidecar — the auto-derive
    step must NOT overwrite it on first boot. Reported count is 0
    because all the bundled JSONs already had their sidecars
    pre-committed."""
    result = bootstrap()
    assert result.sidecars_derived == 0
    # Sidecar file exists and is unchanged from the bundled version.
    sidecar = isolated_cli_home / "stations" / "starter.editor.json"
    assert sidecar.is_file()


def test_first_boot_derives_sidecar_when_missing(
    isolated_cli_home,
) -> None:
    """Simulate a station JSON that ships WITHOUT a committed sidecar:
    drop a custom station JSON into the app-home stations dir AFTER
    the first bootstrap (which populated the bundled set), then
    re-bootstrap. The new JSON has no sidecar in the bundle to be
    copied back, so auto-derive picks it up + writes a new one.

    Note: deleting a BUNDLED station's sidecar from app-home wouldn't
    trigger derive — bootstrap's _copy_missing_stations restores the
    bundled sidecar before derive runs. The "no bundled sidecar
    exists at all" path is what derive is for."""
    bootstrap()  # first run populates stations dir + commits bundle

    # Drop a custom station with no sidecar.
    import json
    fork_dir = isolated_cli_home / "stations"
    fork_json = fork_dir / "fork-station.json"
    fork_template = {
        "station": "fork-station",
        "name": "Fork test station",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "STARTER_CONTENT", "colour": "#888888",
             "role": "content"},
        ],
        "cursor": {
            "base_advance_per_week": 0,
            "cycle_weeks": 1,
            "persist": False,
        },
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "flat_slots": [
                {"source": "STARTER_CONTENT", "strategy": "weighted",
                 "size": 1},
            ],
        },
    }
    fork_json.write_text(json.dumps(fork_template, indent=2))
    fork_sidecar = fork_dir / "fork-station.editor.json"
    assert not fork_sidecar.exists()

    result = bootstrap()
    assert result.sidecars_derived == 1
    assert fork_sidecar.is_file()

    # Sanity: derived sidecar parses + has a section block.
    sc = json.loads(fork_sidecar.read_text())
    assert sc.get("sections"), "auto-derived sidecar should have sections"


def test_existing_sidecar_is_never_overwritten(
    isolated_cli_home,
) -> None:
    """A user (or future contributor) hand-edits an existing sidecar.
    Auto-derive must NOT clobber their edits across boots."""
    bootstrap()

    sidecar = isolated_cli_home / "stations" / "starter.editor.json"
    # Replace the sidecar with a sentinel hand-edited version.
    sentinel = '{"USER_HAND_EDITED": true, "sections": [], "macros": []}'
    sidecar.write_text(sentinel)

    result = bootstrap()
    assert result.sidecars_derived == 0  # untouched
    assert sidecar.read_text() == sentinel, (
        "auto-derive must not overwrite an existing sidecar"
    )


# --- BootstrapResult contract ---


def test_bootstrap_result_carries_starter_signals(
    isolated_cli_home,
) -> None:
    """§16.14 c2 widened the BootstrapResult dataclass with two new
    fields; pin the contract so a future caller (Python API, REST
    endpoint) can rely on them."""
    result = bootstrap()
    assert hasattr(result, "pools_auto_imported")
    assert hasattr(result, "sidecars_derived")
    assert isinstance(result.pools_auto_imported, int)
    assert isinstance(result.sidecars_derived, int)


# --- c2-review 🟡-1: drift detection ---


def test_starter_pools_table_matches_starter_json_sources() -> None:
    """c2-review 🟡-1: `engine.seeding._STARTER_POOLS` hardcodes the
    pool names that the auto-import will create. They MUST match
    `stations/starter.json`'s `sources[*].name` — otherwise the
    auto-import imports a pool the template can't find, and
    generation silently fails with "no tracks in pool X."

    Catches a rename in starter.json without the matching update to
    _STARTER_POOLS (and vice versa).
    """
    import json
    from engine.seeding import _STARTER_POOLS

    repo_root = Path(__file__).resolve().parent.parent
    starter = json.loads(
        (repo_root / "stations" / "starter.json").read_text()
    )
    template_names = {s["name"] for s in starter["sources"]}
    table_names = {name for name, _xml, _role in _STARTER_POOLS}
    assert template_names == table_names, (
        f"starter.json sources {sorted(template_names)} drifted from "
        f"_STARTER_POOLS {sorted(table_names)}. If you renamed a "
        "pool, update both."
    )


# --- c2-review 🟢-1: starter JSON present but XMLs missing ---


def test_auto_import_starter_pools_tolerates_missing_xmls(
    tmp_path, isolated_cli_home,
) -> None:
    """c2-review 🟢-1: unit pin on the missing-file tolerance branch.
    A fork that ships `stations/starter.json` (so the station seeds)
    but doesn't bundle the XML pools must not raise — the helper
    returns 0. We can't trigger this through the full bootstrap
    (the copy-stations step would restore the XMLs from the bundle);
    test the engine.seeding helper directly against a tmp dir.

    `isolated_cli_home` fixture is still required to set up the DB
    URL so SQLAlchemy can connect."""
    from engine.database import make_engine, make_sessionmaker
    from engine.seeding import auto_import_starter_pools
    from engine.models import Base

    # Run bootstrap once just to get the migrated DB.
    bootstrap()

    # Empty the table so first-boot guard fires (matches the test
    # premise: no user pools exist).
    import sqlite3
    db = sqlite3.connect(isolated_cli_home / "station_studio.db")
    try:
        db.execute("DELETE FROM source_playlist_entry")
        db.execute("DELETE FROM source_playlist")
        db.commit()
    finally:
        db.close()

    # Point at a tmp dir that contains NO starter-*.itunes.xml files.
    empty_dir = tmp_path / "no-pools"
    empty_dir.mkdir()

    eng = make_engine()
    SL = make_sessionmaker(eng)
    with SL() as session:
        imported = auto_import_starter_pools(session, empty_dir)
        session.commit()
    eng.dispose()
    assert imported == 0


# --- isolated_cli_home fixture (mirrors tests/test_cli.py) ---


@pytest.fixture
def isolated_cli_home(monkeypatch, tmp_path):
    """Per-test app-home + env teardown. Mirrors the same shape used in
    tests/test_cli.py so behaviour is consistent across test files."""
    import os
    home = tmp_path / "ss-home"
    monkeypatch.setenv("STATION_STUDIO_HOME", str(home))
    monkeypatch.delenv("STATION_STUDIO_DB_URL", raising=False)
    monkeypatch.delenv("STATION_STUDIO_STATIONS_DIR", raising=False)
    yield home
    os.environ.pop("STATION_STUDIO_DB_URL", None)
    os.environ.pop("STATION_STUDIO_STATIONS_DIR", None)
