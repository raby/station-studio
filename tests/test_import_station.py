"""Tests for scripts/import_station.py (sub-slice #16 commit 2).

Covers the CLI's behaviour layer:
  * Fresh import → Station row created with translated template
  * Re-import same JSON → 'unchanged'
  * Re-import modified JSON → 'updated' (template field rewritten)
  * Default display name derives from slug (title-cased)
  * Explicit --name overrides the default
  * Malformed input (e.g. unknown slot type) exits with code 2 +
    user-facing error
  * Sample fixture round-trips through the CLI end-to-end

Translator-internal behaviour lives in test_template_importer.py;
this file only covers the CLI/DB shim.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station
from scripts.import_station import _default_name, import_one, main, translate_only


REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "sample_weekly_template.json"


@pytest.fixture
def db(tmp_path) -> Session:
    engine = make_engine(f"sqlite:///{tmp_path}/import.db")
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _write_weekly(path: Path, *, slots=None, cursors=None) -> Path:
    data = {
        "slots": slots or [{"type": "commercial"}],
        "initial_cursors": cursors if cursors is not None else {},
    }
    path.write_text(json.dumps(data, indent=2))
    return path


# ---------- _default_name -----------------------------------------------


def test_default_name_title_cases_hyphenated_slug():
    assert _default_name("rebirth-radio") == "Rebirth Radio"
    assert _default_name("oneword") == "Oneword"
    assert _default_name("a-b-c") == "A B C"


# ---------- import_one --------------------------------------------------


def test_import_one_creates_station_row(db, tmp_path):
    path = _write_weekly(
        tmp_path / "t.json",
        slots=[{"type": "jingle", "genre": "Daytime"}],
    )
    summary = import_one(db, slug="alpha", json_path=path, name=None)
    db.commit()

    assert summary["action"] == "created"
    assert summary["slug"] == "alpha"
    assert summary["slot_count"] == 1
    assert summary["cursor_count"] == 0

    row = db.query(Station).filter_by(slug="alpha").one()
    assert row.name == "Alpha"  # default from slug
    assert row.template["cadence"] == "weekly"
    assert row.template["cursor"]["persist"] is True
    assert row.template["layout"]["flat_slots"][0]["source"] == "JINGLES"


def test_import_one_is_idempotent_on_unchanged_json(db, tmp_path):
    path = _write_weekly(
        tmp_path / "t.json",
        slots=[{"type": "commercial"}],
    )
    import_one(db, slug="beta", json_path=path, name=None)
    db.commit()

    summary = import_one(db, slug="beta", json_path=path, name=None)
    db.commit()
    assert summary["action"] == "unchanged"
    assert db.query(Station).filter_by(slug="beta").count() == 1


def test_import_one_updates_row_when_json_changes(db, tmp_path):
    path = _write_weekly(
        tmp_path / "t.json",
        slots=[{"type": "commercial"}],
    )
    import_one(db, slug="gamma", json_path=path, name=None)
    db.commit()

    # Rewrite with a different slot mix.
    _write_weekly(
        path,
        slots=[
            {"type": "commercial"},
            {"type": "jingle", "genre": "Daytime"},
        ],
    )
    summary = import_one(db, slug="gamma", json_path=path, name=None)
    db.commit()

    assert summary["action"] == "updated"
    row = db.query(Station).filter_by(slug="gamma").one()
    assert len(row.template["layout"]["flat_slots"]) == 2


def test_import_one_honours_explicit_name(db, tmp_path):
    path = _write_weekly(tmp_path / "t.json")
    import_one(
        db, slug="rebirth-radio", json_path=path, name="Rebirth Radio Premium",
    )
    db.commit()
    row = db.query(Station).filter_by(slug="rebirth-radio").one()
    assert row.name == "Rebirth Radio Premium"


# ---------- main (CLI entry point) --------------------------------------


def test_main_imports_sample_fixture_end_to_end(tmp_path, monkeypatch, capsys):
    """The committed sample_weekly_template.json passes through the CLI
    cleanly and produces a Station row whose template is parse_template-
    valid (translator's round-trip guarantees this)."""
    db_url = f"sqlite:///{tmp_path}/cli.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)

    rc = main(["sample-rb", str(SAMPLE_FIXTURE)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "created" in out
    assert "sample-rb" in out
    assert "13 slots" in out
    assert "4 cursors" in out

    # Confirm the row landed.
    engine = make_engine(db_url)
    SessionLocal = make_sessionmaker(engine)
    with SessionLocal() as db:
        row = db.query(Station).filter_by(slug="sample-rb").one()
        assert len(row.template["layout"]["flat_slots"]) == 13


def test_main_returns_nonzero_for_missing_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    rc = main(["x", str(tmp_path / "nope.json")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no such file" in err


def test_main_returns_code_2_for_malformed_weekly_json(tmp_path, monkeypatch, capsys):
    """User-input errors get exit code 2 + a 'fix your JSON' message —
    NOT phrased as a station-studio bug."""
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "slots": [{"type": "banana"}],  # unknown weekly slot type
        "initial_cursors": {},
    }))
    rc = main(["x", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "input data problem" in err
    assert "unknown weekly slot type" in err
    assert "station-studio bug" not in err


# ---------- --output mode (§14.36) -------------------------------------


def test_translate_only_returns_station_studio_format():
    """translate_only is the pure-function helper that backs --output;
    its output is byte-identical to what import_one would upsert."""
    out = translate_only(slug="x", json_path=SAMPLE_FIXTURE, name=None)
    assert out["station"] == "x"
    assert out["name"] == "X"
    assert out["cadence"] == "weekly"
    assert out["cursor"]["persist"] is True
    assert len(out["layout"]["flat_slots"]) == 13


def test_main_output_mode_writes_json_without_touching_db(tmp_path, monkeypatch, capsys):
    """--output FILE writes the translated JSON + does NOT create a
    Station row. This is the path the refresh-rebirth-radio Makefile
    target drives."""
    db_url = f"sqlite:///{tmp_path}/should_stay_empty.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    out_path = tmp_path / "out.json"

    rc = main(["sample-rb", str(SAMPLE_FIXTURE), "--output", str(out_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote" in out
    assert str(out_path) in out

    # File exists + parses cleanly.
    written = json.loads(out_path.read_text())
    assert written["station"] == "sample-rb"
    assert len(written["layout"]["flat_slots"]) == 13

    # No DB file was ever created — --output mode short-circuits before
    # make_engine() runs. The file path was set via STATION_STUDIO_DB_URL
    # specifically so this absence is testable.
    db_file = tmp_path / "should_stay_empty.db"
    assert not db_file.exists()


def test_main_output_mode_handles_translator_errors(tmp_path, monkeypatch, capsys):
    """--output mode shares the same error surface as the DB-write path:
    malformed weekly JSON exits with code 2 + user-facing message."""
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/cli.db")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "slots": [{"type": "banana"}],
        "initial_cursors": {},
    }))
    rc = main(["x", str(bad), "--output", str(tmp_path / "out.json")])
    assert rc == 2
    assert not (tmp_path / "out.json").exists()  # file not written on error
    err = capsys.readouterr().err
    assert "input data problem" in err


# ---------- committed stations/rebirth-radio.json artifact -------------


def test_committed_rebirth_radio_template_parses_cleanly():
    """The shipped port artifact (stations/rebirth-radio.json) parses
    via parse_template + has the structure §14.34's translator
    promises. Catches regressions if the translator output shape
    changes without the committed artifact being refreshed."""
    from engine.template import parse_template

    artifact = REPO_ROOT / "stations" / "rebirth-radio.json"
    tmpl_dict = json.loads(artifact.read_text())
    tmpl = parse_template(tmpl_dict)

    assert tmpl.station == "rebirth-radio"
    assert tmpl.cadence == "weekly"
    assert tmpl.rng == "on"
    # Real weekly is 2,196 slots / 9 cursors — the dump+translate
    # pipeline preserves both. Fixed numbers because the artifact is
    # committed; if weekly's template changes, run
    # `make refresh-rebirth-radio WEEKLY_DIR=/path/to/weekly`.
    assert len(tmpl.layout.flat_slots) == 2196
    assert len(tmpl.layout.cursors_init) == 9
    assert set(tmpl.layout.cursors_init.keys()) == {
        "NIGHT_1", "NIGHT_2", "NIGHT_3", "NIGHT_4",
        "NIGHT_5", "NIGHT_6", "NIGHT_7",
        "WEEKEND_1", "WEEKEND_2",
    }
    # §14.30 persistent cursors — required for weekly's stateful mode.
    assert tmpl.cursor.persist is True
    # §14.35: 5 content sources + MASTER reference role.
    by_name = {s.name: s for s in tmpl.sources}
    assert by_name["MASTER"].role == "reference"
    for content_name in ["JINGLES", "COMMERCIAL", "MIXES", "NIGHT", "WEEKEND"]:
        assert by_name[content_name].role == "content"
