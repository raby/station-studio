"""Tests for scripts/import_weekly_pools.py (§14.35 commit 2).

Covers the manifest CLI:
  * Strict-mode manifest parsing (missing required keys, unknown
    top-level / per-pool keys, invalid role, empty pools, non-string
    name, malformed path)
  * Per-pool action labelling (created → updated on re-import)
  * Relative path resolution against the manifest's parent directory
  * MASTER reference role flows through to SourcePlaylist.role
  * End-to-end with the committed sample_pool_manifest.json + 6
    inline iTunes XMLs under tests/fixtures/itunes_xml/
  * CLI exit codes: 0 success, 1 missing manifest, 2 ManifestError,
    3 importer ValueError (malformed XML)

Translator-internal tests live in test_importer.py (the role param +
load_pools cross-check); this file only covers the CLI/manifest shim.
"""
from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, SourcePlaylist
from scripts.import_weekly_pools import (
    ManifestError, _validate_manifest, import_manifest, main,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_MANIFEST = REPO_ROOT / "tests" / "fixtures" / "sample_pool_manifest.json"


@pytest.fixture
def db(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/pool_import.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# ---------- helpers ------------------------------------------------------


def _make_xml(path: Path, pool_name: str, n_tracks: int = 2) -> None:
    """Build a tiny iTunes plist with n synthetic tracks. Each test gets
    its own XML; persistent IDs are unique-per-call via pool_name +
    index so re-import across tests doesn't collide."""
    pid_prefix = pool_name[:4].upper().ljust(4, "X")
    tracks = {}
    items = []
    for i in range(1, n_tracks + 1):
        tid = i + ord(pid_prefix[0]) * 100
        pid = f"{pid_prefix}{i:012d}"
        tracks[str(tid)] = {
            "Track ID": tid,
            "Persistent ID": pid,
            "Name": f"{pool_name}-{i}",
        }
        items.append({"Track ID": tid})
    data = {
        "Library Persistent ID": "0000000000000001",
        "Tracks": tracks,
        "Playlists": [{"Name": pool_name, "Playlist Items": items}],
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f)


def _write_manifest(path: Path, pools: dict) -> Path:
    """Build + write a manifest JSON. The XMLs are expected to already
    exist at the paths referenced (caller's responsibility)."""
    path.write_text(json.dumps({"pools": pools}, indent=2))
    return path


# ---------- _validate_manifest strict-mode parsing ----------------------


def test_validate_rejects_non_dict_top_level():
    with pytest.raises(ManifestError, match="must be a JSON object"):
        _validate_manifest(["not", "a", "dict"])


def test_validate_rejects_missing_pools_key():
    with pytest.raises(ManifestError, match="missing required keys.*pools"):
        _validate_manifest({"_comment": "no pools"})


def test_validate_rejects_unknown_top_level_keys():
    with pytest.raises(ManifestError, match="unknown top-level keys.*'pool'"):
        _validate_manifest({"pools": {"X": {"path": "x.xml", "role": "content"}}, "pool": "typo"})


def test_validate_allows_documented_extras():
    """_comment and schema_version are documented future-friendly extras,
    mirroring §14.34's allowed_extras pattern."""
    out = _validate_manifest({
        "_comment": "doc",
        "schema_version": 1,
        "pools": {"X": {"path": "x.xml", "role": "content"}},
    })
    assert out == {"X": {"path": "x.xml", "role": "content"}}


def test_validate_rejects_empty_pools():
    with pytest.raises(ManifestError, match="must contain at least one pool"):
        _validate_manifest({"pools": {}})


def test_validate_rejects_non_dict_pools():
    with pytest.raises(ManifestError, match="'pools' must be an object"):
        _validate_manifest({"pools": ["JINGLES", "COMMERCIAL"]})


def test_validate_rejects_pool_missing_required_fields():
    with pytest.raises(ManifestError, match="missing required fields.*'role'"):
        _validate_manifest({"pools": {"X": {"path": "x.xml"}}})
    with pytest.raises(ManifestError, match="missing required fields.*'path'"):
        _validate_manifest({"pools": {"X": {"role": "content"}}})


def test_validate_rejects_unknown_per_pool_fields():
    with pytest.raises(ManifestError, match="unknown fields.*'cursor_id'"):
        _validate_manifest({"pools": {"X": {
            "path": "x.xml", "role": "content", "cursor_id": "oops",
        }}})


def test_validate_rejects_invalid_role():
    with pytest.raises(ManifestError, match="invalid role 'banana'"):
        _validate_manifest({"pools": {"X": {"path": "x.xml", "role": "banana"}}})


def test_validate_rejects_empty_path():
    with pytest.raises(ManifestError, match="'path' must be a non-empty string"):
        _validate_manifest({"pools": {"X": {"path": "", "role": "content"}}})


# ---------- import_manifest end-to-end ---------------------------------


def test_import_manifest_creates_pools_with_correct_roles(db, tmp_path):
    _make_xml(tmp_path / "a.xml", "A", n_tracks=2)
    _make_xml(tmp_path / "b.xml", "B", n_tracks=3)
    manifest = _write_manifest(tmp_path / "m.json", {
        "A": {"path": "a.xml", "role": "content"},
        "B": {"path": "b.xml", "role": "reference"},
    })
    summaries = import_manifest(db, manifest)
    db.commit()

    assert len(summaries) == 2
    by_name = {s["name"]: s for s in summaries}
    assert by_name["A"]["action"] == "created"
    assert by_name["A"]["role"] == "content"
    assert by_name["A"]["tracks_imported"] == 2
    assert by_name["B"]["action"] == "created"
    assert by_name["B"]["role"] == "reference"
    assert by_name["B"]["tracks_imported"] == 3

    # DB state mirrors the summary.
    a = db.query(SourcePlaylist).filter_by(name="A").one()
    b = db.query(SourcePlaylist).filter_by(name="B").one()
    assert a.role == "content"
    assert b.role == "reference"


def test_import_manifest_re_run_reports_updated(db, tmp_path):
    """Second invocation of the same manifest reports action=updated
    for every pool (import_source's drop-and-recreate semantics)."""
    _make_xml(tmp_path / "p.xml", "P")
    manifest = _write_manifest(tmp_path / "m.json", {
        "P": {"path": "p.xml", "role": "content"},
    })
    import_manifest(db, manifest)
    db.commit()

    summaries = import_manifest(db, manifest)
    db.commit()
    assert summaries[0]["action"] == "updated"


def test_import_manifest_resolves_relative_paths_against_manifest_dir(db, tmp_path):
    """Manifest sits next to the XMLs — relative paths walk from
    there, not from the CWD."""
    subdir = tmp_path / "weekly_xmls"
    subdir.mkdir()
    _make_xml(subdir / "X.xml", "X")
    manifest = subdir / "m.json"
    manifest.write_text(json.dumps({
        "pools": {"X": {"path": "X.xml", "role": "content"}},
    }))

    # Even if we CD elsewhere, the import works because the manifest's
    # parent directory is the base.
    summaries = import_manifest(db, manifest)
    db.commit()
    assert summaries[0]["name"] == "X"
    assert summaries[0]["action"] == "created"


def test_import_manifest_raises_when_xml_file_missing(db, tmp_path):
    manifest = _write_manifest(tmp_path / "m.json", {
        "X": {"path": "missing.xml", "role": "content"},
    })
    with pytest.raises(ManifestError, match="'X': file not found"):
        import_manifest(db, manifest)


def test_import_manifest_can_change_role_on_re_run(db, tmp_path):
    """Re-running the manifest with a different role updates
    SourcePlaylist.role — mirrors test_reimport_can_change_role at
    the engine layer."""
    _make_xml(tmp_path / "p.xml", "P")
    manifest = _write_manifest(tmp_path / "m.json", {
        "P": {"path": "p.xml", "role": "content"},
    })
    import_manifest(db, manifest)
    db.commit()
    assert db.query(SourcePlaylist).filter_by(name="P").one().role == "content"

    _write_manifest(manifest, {"P": {"path": "p.xml", "role": "reference"}})
    import_manifest(db, manifest)
    db.commit()
    assert db.query(SourcePlaylist).filter_by(name="P").one().role == "reference"


# ---------- main (CLI entry point) -------------------------------------


def test_main_imports_sample_fixture_end_to_end(tmp_path, monkeypatch, capsys):
    """The committed sample_pool_manifest.json passes through the CLI
    cleanly with the 6 committed XMLs."""
    db_url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)

    rc = main([str(SAMPLE_MANIFEST)])
    assert rc == 0
    out = capsys.readouterr().out
    # All 6 pools created.
    for name in ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND", "MASTER"]:
        assert name in out
    assert "reference" in out  # MASTER role surfaces

    # Confirm rows landed with correct roles.
    engine = make_engine(db_url)
    SessionLocal = make_sessionmaker(engine)
    with SessionLocal() as db:
        master = db.query(SourcePlaylist).filter_by(name="MASTER").one()
        assert master.role == "reference"
        jingles = db.query(SourcePlaylist).filter_by(name="JINGLES").one()
        assert jingles.role == "content"
        # 6 pools total.
        assert db.query(SourcePlaylist).count() == 6


def test_main_returns_nonzero_for_missing_manifest_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    rc = main([str(tmp_path / "no_such.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no such manifest file" in err


def test_main_returns_code_2_for_malformed_manifest(tmp_path, monkeypatch, capsys):
    """User-input problem with the manifest itself — exit 2 + clear
    error phrased as user error, NOT as 'station-studio bug'."""
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"pools": {"X": {"path": "x.xml", "role": "banana"}}}))
    rc = main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "import failed (manifest problem)" in err
    assert "invalid role 'banana'" in err
    assert "station-studio bug" not in err


def test_main_returns_code_3_for_malformed_xml(tmp_path, monkeypatch, capsys):
    """Distinct exit code for importer-level errors so the user can
    tell 'fix your JSON' from 'fix your XML'."""
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    # Write a file that isn't an iTunes library XML.
    bad_xml = tmp_path / "broken.xml"
    bad_xml.write_text("<?xml version='1.0'?><plist><dict></dict></plist>")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "pools": {"BROKEN": {"path": "broken.xml", "role": "content"}},
    }))
    rc = main([str(manifest)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "import failed (XML problem)" in err
    assert "not an iTunes library XML" in err
