import logging
import logging.handlers
import urllib.parse

import pytest

from station_appkit import report


def _query(url: str) -> dict:
    base, _, query = url.partition("?")
    assert base == "https://github.com/raby/station-studio/issues/new"
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}


def test_the_issue_has_the_app_version_and_computer_filled_in(monkeypatch):
    monkeypatch.setattr(report, "system", lambda: "macOS 15.1 (Apple Silicon)")
    monkeypatch.setattr(report.platform, "python_version", lambda: "3.12.7")
    got = report.details("Station Player", "0.25.1", "mac")
    assert got["installed"] == "downloaded Mac app"
    q = _query(got["issue_url"])
    assert q["title"] == "[Station Player] "
    assert "- App: Station Player 0.25.1 (downloaded Mac app)" in q["body"]
    assert "- System: macOS 15.1 (Apple Silicon)" in q["body"]
    assert "- Python: 3.12.7" in q["body"]
    assert "**What happened**" in q["body"]


@pytest.mark.parametrize("kind, says", [("windows", "downloaded Windows app"), (None, "installed with pip"),
                                        ("something new", "installed with pip")])
def test_how_it_was_installed(kind, says):
    assert report.details("Station Server", "0.24.1", kind)["installed"] == says


def test_the_log_file_is_shown_but_never_put_in_the_issue(tmp_path, monkeypatch):
    log = tmp_path / "Rob" / "station-studio.log"
    log.parent.mkdir()
    handler = logging.handlers.RotatingFileHandler(log, encoding="utf-8")
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [handler])
    try:
        got = report.details("Station Studio", "1.23.1", None)
        assert got["log_file"] == str(log)
        assert "Rob" not in got["issue_url"] and "station-studio.log" not in got["issue_url"]
    finally:
        handler.close()


def test_no_log_file_when_it_logs_to_the_terminal(monkeypatch):
    monkeypatch.setattr(logging.getLogger(), "handlers", [logging.StreamHandler()])
    assert report.log_file() is None
    assert report.details("Station Studio", "1.23.1", None)["log_file"] is None


def test_the_system_as_people_know_it(monkeypatch):
    monkeypatch.setattr(report.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(report.platform, "mac_ver", lambda: ("15.1", ("", "", ""), "arm64"))
    assert report.system("darwin") == "macOS 15.1 (Apple Silicon)"
    monkeypatch.setattr(report.platform, "machine", lambda: "x86_64")
    assert report.system("darwin") == "macOS 15.1 (Intel)"
    monkeypatch.setattr(report.platform, "release", lambda: "11")
    monkeypatch.setattr(report.platform, "version", lambda: "10.0.22631")
    assert report.system("win32") == "Windows 11 (10.0.22631)"
    monkeypatch.setattr(report.platform, "freedesktop_os_release", lambda: {"PRETTY_NAME": "Ubuntu 24.04.1 LTS"})
    assert report.system("linux") == "Ubuntu 24.04.1 LTS (x86_64)"

    def missing():
        raise OSError("no os-release")
    monkeypatch.setattr(report.platform, "freedesktop_os_release", missing)
    monkeypatch.setattr(report.platform, "release", lambda: "6.8.0")
    assert report.system("linux") == "Linux 6.8.0 (x86_64)"
