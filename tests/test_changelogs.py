"""Each app's changelog says what its current version changes.

A release's notes on GitHub, and what Update now says about it, come from
its changelog (scripts/release_notes.py): the publish workflows stop when
it has no section for the version. This catches it on the release PR
instead, before anything is tagged.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release_notes  # noqa: E402
from station_appkit import changelog  # noqa: E402

APPS = {
    "Station Studio": (ROOT / "pyproject.toml", ROOT / "CHANGELOG.md"),
    "Station Player": (ROOT / "player" / "pyproject.toml", ROOT / "player" / "CHANGELOG.md"),
    "Station Server": (ROOT / "server" / "pyproject.toml", ROOT / "server" / "CHANGELOG.md"),
}


def _version(pyproject: Path) -> str:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


@pytest.mark.parametrize("app", APPS)
def test_the_changelog_says_what_this_version_changes(app):
    pyproject, log = APPS[app]
    version = _version(pyproject)
    release = changelog.find(log.read_text(encoding="utf-8"), version)
    assert release is not None, f"{log.relative_to(ROOT)} has no section for {version}"
    assert release.items, f"{log.relative_to(ROOT)}'s {version} lists no changes"
    assert release.date, f"{log.relative_to(ROOT)}'s {version} has no date"


@pytest.mark.parametrize("app", APPS)
def test_releases_are_newest_first(app):
    from station_appkit.updates import parse_version
    versions = [parse_version(r.version) for r in changelog.parse(APPS[app][1].read_text(encoding="utf-8"))
                if r.version != "Unreleased"]
    assert all(versions), "every release heading is a version"
    assert versions == sorted(versions, reverse=True)


def test_release_notes_lead_with_what_is_new_and_update_now_reads_them_back():
    log = APPS["Station Player"][1]
    version = _version(APPS["Station Player"][0])
    notes = release_notes.notes(log, version, "Station Player")
    assert notes.startswith(f"## What's new in Station Player {version}\n\n")
    body = notes + "Station Player …: plays radio.\n\n**Download** …\n"  # as the workflow adds
    assert changelog.whats_new(body).items == changelog.find(log.read_text(encoding="utf-8"), version).items


def test_release_notes_stop_a_release_with_no_section():
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_notes.py"),
                          str(APPS["Station Server"][1]), "99.0.0", "Station Server"],
                         capture_output=True, text=True)
    assert run.returncode == 1 and run.stdout == ""
    assert "has no section for 99.0.0" in run.stderr
