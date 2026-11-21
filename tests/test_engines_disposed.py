"""Studio's own code lets go of what it opens once it's done, however it
ends, rather than leaving it for the garbage collector (Python 3.13 and
later warn about each): the database engine each job makes (start-up
seeding, a command, a script), and an HTTP error's response."""
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
    """Every engine made through make_engine, whether it was disposed, and
    whether any connection was still checked out then (dispose() can't
    close one, so it would be left to the garbage collector)."""
    made: list[dict] = []
    real = engine.database.make_engine

    def make(*args, **kwargs):
        eng = real(*args, **kwargs)
        seen = {"engine": eng, "disposed": False, "still_out": 0}
        dispose = eng.dispose

        def disposing(*a, **k):
            seen["disposed"] = True
            seen["still_out"] = max(seen["still_out"], eng.pool.checkedout())
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
    assert engines and all(e["disposed"] and not e["still_out"] for e in engines)


@pytest.mark.parametrize("args", [
    ["export", "999"],  # no such playlist: stops after its session
    ["generate", "no-such-station", "--week", "1"],  # stops inside its session
])
def test_a_command_that_fails_still_disposes_its_engine(tmp_path, monkeypatch, engines, args):
    """Since Click 8.2, ctx.exit() runs call_on_close callbacks before it
    raises: disposing there, inside the session, left its connection open."""
    from station_studio.cli import main

    monkeypatch.setenv("STATION_STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("STATION_STUDIO_STATIONS_DIR", str(tmp_path / "stations"))
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    mine = [e for e in engines if "c.db" in str(e["engine"].url)]
    assert mine and all(e["disposed"] and not e["still_out"] for e in mine)


def test_seeding_from_the_command_line_disposes_its_engine(tmp_path, monkeypatch, engines):
    monkeypatch.setenv("STATION_STUDIO_DB_URL", f"sqlite:///{tmp_path}/seed.db")
    station = Path(__file__).resolve().parent / "fixtures" / "cli_minimal_station.json"
    engine.seeding.main([str(station)])
    assert engines and all(e["disposed"] and not e["still_out"] for e in engines)


def test_an_error_from_station_server_is_closed(monkeypatch):
    """An HTTP error is a response too: the link read Station Server's
    message from it and left its connection for the garbage collector."""
    import io
    import urllib.error

    import engine.stationserver as link

    bodies, kept = [], []  # kept: so only an explicit close() can close it

    def refuse(req, timeout=None):
        bodies.append(io.BytesIO(b'{"error": "Wrong link code"}'))
        kept.append(urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, bodies[-1]))
        raise kept[-1]

    monkeypatch.setattr(link.urllib.request, "urlopen", refuse)
    with pytest.raises(link.LinkError, match="Wrong link code"):
        link.call({"address": "127.0.0.1:8772", "code": "x"}, "GET", "/link/status")
    assert len(bodies) == 1 and bodies[0].closed
