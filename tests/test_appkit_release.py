"""scripts/appkit_release.py: whether a release puts station-appkit on PyPI."""
from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import zipfile

import pytest

from scripts import appkit_release


def wheel(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
        zf.writestr("station_appkit-1.0.0.dist-info/RECORD", "differs every build")
    return buf.getvalue()


OURS = {"station_appkit/__init__.py": "v = 1", "station_appkit/window.py": "x = 1"}


def pypi(published: bytes | None):
    """An opener standing in for pypi.org."""
    def opener(url, timeout=None):
        if url.endswith("/json"):
            if published is None:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return io.BytesIO(json.dumps({"urls": [
                {"packagetype": "sdist", "url": "https://files/x.tar.gz", "digests": {"sha256": "0"}},
                {"packagetype": "bdist_wheel", "url": "https://files/x.whl",
                 "digests": {"sha256": hashlib.sha256(published).hexdigest()}},
            ]}).encode())
        assert url == "https://files/x.whl"
        return io.BytesIO(published)
    return opener


@pytest.fixture
def built(tmp_path):
    path = tmp_path / "station_appkit-1.0.0-py3-none-any.whl"
    path.write_bytes(wheel(OURS))
    return path


def test_a_new_version_is_published(built):
    assert appkit_release.decide(built, pypi(None)) is True


def test_the_same_code_already_there_isnt(built):
    assert appkit_release.decide(built, pypi(wheel(OURS))) is False


def test_other_code_under_the_same_version_stops_the_release(built):
    with pytest.raises(SystemExit, match="other code .*window.py.*Bump its version"):
        appkit_release.decide(built, pypi(wheel({**OURS, "station_appkit/window.py": "x = 2"})))


def test_other_requirements_under_the_same_version_stop_it_too(tmp_path):
    ours = tmp_path / "station_appkit-1.0.0-py3-none-any.whl"
    ours.write_bytes(wheel({**OURS, "station_appkit-1.0.0.dist-info/METADATA": "Requires-Python: >=3.12\n"}))
    theirs = wheel({**OURS, "station_appkit-1.0.0.dist-info/METADATA": "Requires-Python: >=3.11\nSummary: x\n"})
    with pytest.raises(SystemExit, match="its requirements"):
        appkit_release.decide(ours, pypi(theirs))
    same = wheel({**OURS, "station_appkit-1.0.0.dist-info/METADATA": "Requires-Python: >=3.12\nSummary: y\n"})
    assert appkit_release.decide(ours, pypi(same)) is False


def test_a_download_that_doesnt_match_pypis_hash_stops_it(built):
    good = pypi(wheel(OURS))

    def tampered(url, timeout=None):
        resp = good(url, timeout)
        return io.BytesIO(b"tampered") if url.endswith(".whl") else resp

    with pytest.raises(SystemExit, match="didn't match"):
        appkit_release.decide(built, tampered)


def test_pypi_trouble_isnt_taken_for_a_new_version(built):
    def down(url, timeout=None):
        raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        appkit_release.decide(built, down)


def test_main_writes_the_answer_for_the_workflow(built, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(appkit_release, "decide", lambda path: True)
    assert appkit_release.main([str(built)]) == 0
    assert out.read_text() == "publish=true\n" and "publish=true" in capsys.readouterr().out
    assert appkit_release.main([]) == 2
