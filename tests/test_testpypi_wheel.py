"""scripts/testpypi_wheel.py: the release takes its wheel from test.pypi's
record of the upload, not its (sometimes lagging) package index."""
from __future__ import annotations

import hashlib
import io
import json
import urllib.error

import pytest

from scripts import testpypi_wheel

WHEEL = b"the wheel"
SHA = hashlib.sha256(WHEEL).hexdigest()


def fake_testpypi(record=None, data=WHEEL, fail_json=0):
    """Stands in for test.pypi: `fail_json` answers 404 first, as a
    version not there yet does."""
    state = {"json": 0}

    def opener(url, timeout=None):
        if url.endswith("/json"):
            state["json"] += 1
            if state["json"] <= fail_json:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return io.BytesIO(json.dumps(record if record is not None else {"urls": [
                {"packagetype": "sdist", "url": "https://files/x.tar.gz", "filename": "x.tar.gz"},
                {"packagetype": "bdist_wheel", "url": "https://files/x.whl",
                 "filename": "station_studio-1.2.3-py3-none-any.whl", "digests": {"sha256": SHA}},
            ]}).encode())
        return io.BytesIO(data)
    return opener, state


def test_waits_for_test_pypi_then_takes_the_checked_wheel(tmp_path, capsys):
    opener, state = fake_testpypi(fail_json=3)
    waits = []
    path = testpypi_wheel.fetch("1.2.3", tmp_path / "dist", SHA, tries=10, opener=opener, sleep=waits.append)
    assert path.read_bytes() == WHEEL and path.name == "station_studio-1.2.3-py3-none-any.whl"
    assert state["json"] == 4 and waits == [10, 10, 10]
    assert "on try 4" in capsys.readouterr().out


def test_no_wheel_yet_is_waited_for(tmp_path):
    opener, _ = fake_testpypi(record={"urls": [{"packagetype": "sdist"}]})
    with pytest.raises(SystemExit, match="never appeared"):
        testpypi_wheel.fetch("1.2.3", tmp_path, tries=3, opener=opener, sleep=lambda s: None)


def test_a_broken_download_is_tried_again_and_never_kept(tmp_path):
    opener, _ = fake_testpypi(data=b"truncated")
    with pytest.raises(SystemExit, match="never appeared"):
        testpypi_wheel.fetch("1.2.3", tmp_path / "dist", tries=2, opener=opener, sleep=lambda s: None)
    assert not (tmp_path / "dist").exists() or not any((tmp_path / "dist").iterdir())


def test_another_runs_wheel_under_this_version_stops_it(tmp_path):
    opener, _ = fake_testpypi()
    with pytest.raises(SystemExit, match="isn't the one this run built"):
        testpypi_wheel.fetch("1.2.3", tmp_path, "0" * 64, tries=5, opener=opener, sleep=lambda s: None)


def test_main(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(testpypi_wheel, "fetch", lambda *a: seen.setdefault("args", a))
    assert testpypi_wheel.main(["1.2.3", str(tmp_path), "--sha256", SHA, "--tries", "5"]) == 0
    assert seen["args"] == ("1.2.3", tmp_path, SHA, 5)
