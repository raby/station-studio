"""Whether a release should put station-appkit on PyPI too.

Station Studio, Station Player and Station Server all depend on
station-appkit (appkit/), so a pip or pipx install of any of them needs
the appkit version it was built with on PyPI. Each app's Publish workflow
builds appkit's wheel beside its own and runs this on it:

  python scripts/appkit_release.py dist/station_appkit-1.0.0-py3-none-any.whl

- That version isn't on PyPI: prints `publish=true` (and writes it to
  $GITHUB_OUTPUT), so the workflow uploads appkit's files with the app's.
- It's there with the same code: `publish=false`; the workflow leaves
  appkit's files out of the upload.
- It's there with different code: fails, saying to bump appkit's version
  (appkit/pyproject.toml and station_appkit/__init__.py). PyPI never
  takes a second copy of a version, so an app would otherwise install the
  old code.

Standard library only; asks only pypi.org.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PYPI = "https://pypi.org/pypi/station-appkit/{version}/json"


def code(wheel: bytes) -> dict[str, str]:
    """The package's own files in a wheel (not its .dist-info), by hash."""
    with zipfile.ZipFile(io.BytesIO(wheel)) as zf:
        return {name: hashlib.sha256(zf.read(name)).hexdigest()
                for name in zf.namelist() if name.startswith("station_appkit/") and not name.endswith("/")}


def published(version: str, opener=urllib.request.urlopen) -> bytes | None:
    """The wheel PyPI has for `version`, or None when it hasn't that version."""
    try:
        with opener(PYPI.format(version=version), timeout=30) as resp:
            info = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    for file in info.get("urls", []):
        if file.get("packagetype") == "bdist_wheel":
            with opener(file["url"], timeout=60) as resp:
                data = resp.read()
            if hashlib.sha256(data).hexdigest() != file.get("digests", {}).get("sha256"):
                raise SystemExit("station-appkit's wheel from PyPI didn't match its published hash")
            return data
    raise SystemExit(f"PyPI has station-appkit {version} but no wheel for it")


def decide(wheel_path: Path, opener=urllib.request.urlopen) -> bool:
    """True when this version should be published; raises SystemExit when
    PyPI already has it with other code."""
    version = wheel_path.name.split("-")[1]
    ours = wheel_path.read_bytes()
    theirs = published(version, opener)
    if theirs is None:
        print(f"station-appkit {version} isn't on PyPI yet: it goes up with this release.")
        return True
    if code(theirs) != code(ours):
        changed = sorted(set(code(theirs).items()) ^ set(code(ours).items()))
        raise SystemExit(
            f"station-appkit {version} is on PyPI already, with other code ({', '.join(sorted({n for n, _ in changed}))}). "
            "Bump its version in appkit/pyproject.toml and appkit/station_appkit/__init__.py, then release again.")
    print(f"station-appkit {version} is on PyPI already, with this code.")
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: python scripts/appkit_release.py <station_appkit wheel>", file=sys.stderr)
        return 2
    publish = decide(Path(argv[0]))
    print(f"publish={'true' if publish else 'false'}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"publish={'true' if publish else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
