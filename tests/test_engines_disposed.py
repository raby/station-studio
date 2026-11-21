"""Studio's own code lets go of each database engine it makes for one job
(start-up seeding, a command, a script) once that's done, however it
ends, rather than leaving its SQLite connections for the garbage
collector: Python 3.13 and later warn about every one left like that."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

import api.main
import engine.database
import engine.seeding


@pytest.fixture
def engines(monkeypatch):
    """Every engine made through make_engine, and whether it was disposed."""
    made: list[dict] = []
    real = engine.database.make_engine

    def make(*args, **kwargs):
        eng = real(*args, **kwargs)
        seen = {"engine": eng, "disposed": False}
        dispose = eng.dispose

        def disposing(*a, **k):
            seen["disposed"] = True
            return dispose(*a, **k)

        eng.dispose = disposing
        made.append(seen)
        return eng

    for module in (engine.database, api.main, engine.seeding):
        monkeypatch.setattr(module, "make_engine", make)
    return made


def test_start_up_seeding_disposes_its_engine(tmp_path, monkeypatch, engines):
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/s.db")
    monkeypatch.delenv("STATION_STUDIO_SKIP_AUTO_SEED", raising=False)
    with TestClient(api.main.app, base_url="http://localhost"):
        pass
    assert engines and all(e["disposed"] for e in engines)


def test_a_command_that_fails_still_disposes_its_engine(tmp_path, monkeypatch, engines):
    from station_studio.cli import main

    monkeypatch.setenv("STATION_STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(tmp_path / "stations"))
    result = CliRunner().invoke(main, ["export", "999"])  # no such playlist
    assert result.exit_code != 0
    mine = [e for e in engines if "c.db" in str(e["engine"].url)]
    assert mine and all(e["disposed"] for e in mine)


def test_seeding_from_the_command_line_disposes_its_engine(tmp_path, monkeypatch, engines):
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/seed.db")
    station = Path(__file__).resolve().parent / "fixtures" / "cli_minimal_station.json"
    engine.seeding.main([str(station)])
    assert engines and all(e["disposed"] for e in engines)
