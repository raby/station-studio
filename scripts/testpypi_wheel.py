"""Fetch the station-studio wheel a release just put on test.pypi.org.

  python scripts/testpypi_wheel.py VERSION DEST [--sha256 HEX] [--tries N]

The PyPI release (.github/workflows/publish.yml) uploads the wheel to
test.pypi, smoke-installs it from there, then sends those same bytes to
pypi.org. It used to find the wheel with `pip download` through
test.pypi's package index, which can lag behind an upload by minutes, and
differently on different runners: 1.17.0 and 1.20.1 failed with "No
matching distribution found" a minute after the smoke had installed it.

This asks test.pypi's JSON API for that version instead (the record of the
upload itself), downloads the wheel it names, and keeps it only if its
SHA-256 matches what test.pypi says and, with --sha256, what the build job
built. It waits and tries again (every 10 s, --tries times) while test.pypi
hasn't that version yet or can't be reached.

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://test.pypi.org/pypi/station-studio/{version}/json"


class NotYet(Exception):
    """test.pypi hasn't the wheel yet (or couldn't be asked): try again."""


def fetch_once(version: str, dest: Path, sha256: str | None, opener=urllib.request.urlopen) -> Path:
    try:
        with opener(API.format(version=version), timeout=30) as resp:
            info = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NotYet(f"couldn't read test.pypi's record of {version}: {exc}") from exc
    wheels = [f for f in info.get("urls", []) if f.get("packagetype") == "bdist_wheel"]
    if not wheels:
        raise NotYet(f"test.pypi has no wheel for {version} yet")
    wheel = wheels[0]
    want = wheel.get("digests", {}).get("sha256", "")
    if sha256 and want != sha256:
        # Not a wait: a different file under this version (an earlier run's).
        raise SystemExit(f"test.pypi's {wheel['filename']} isn't the one this run built "
                         f"({want} there, {sha256} built). Bump the version and release again.")
    try:
        with opener(wheel["url"], timeout=120) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise NotYet(f"couldn't download {wheel['filename']}: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != want:
        raise NotYet(f"{wheel['filename']} didn't match test.pypi's SHA-256 (a broken download?)")
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / wheel["filename"]
    path.write_bytes(data)
    return path


def fetch(version: str, dest: Path, sha256: str | None = None, tries: int = 60,
          opener=urllib.request.urlopen, sleep=time.sleep) -> Path:
    for attempt in range(1, tries + 1):
        try:
            path = fetch_once(version, dest, sha256, opener)
        except NotYet as exc:
            print(f"try {attempt}/{tries}: {exc}", flush=True)
            if attempt < tries:
                sleep(10)
            continue
        print(f"got {path.name} (sha256 checked) on try {attempt}")
        return path
    raise SystemExit(f"station-studio {version} never appeared on test.pypi")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("dest", type=Path)
    parser.add_argument("--sha256", help="the wheel the build job built")
    parser.add_argument("--tries", type=int, default=60, help="every 10 s (default 60: ten minutes)")
    args = parser.parse_args(argv)
    fetch(args.version, args.dest, args.sha256, args.tries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
