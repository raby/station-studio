"""Print a release's "What's new" for its notes on GitHub, from its changelog.

    python3 scripts/release_notes.py player/CHANGELOG.md 0.26.0 "Station Player"

The publish workflows put this at the top of each release's notes, above
the download and install instructions, and Update now reads it back
(station_appkit.changelog.whats_new). It stops the release, before
anything is published, when the changelog has no section for the
version: a release has to say what it changes.

Standard library only, and run from the repository's root, so a workflow
needs no environment: it reads station_appkit.changelog from appkit/.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "appkit"))

from station_appkit.changelog import NOTES_END, NOTES_HEADING, section  # noqa: E402


def notes(changelog: Path, version: str, app: str) -> str:
    body = section(changelog.read_text(encoding="utf-8"), version)
    if body is None:
        raise SystemExit(
            f"{changelog} has no section for {version}: add `## [{version}] - <date>` with what "
            "it changes before releasing (CONTRIBUTING, \"Releasing\").")
    return f"{NOTES_HEADING} in {app} {version}\n\n{body}\n\n{NOTES_END}\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(notes(Path(argv[0]), argv[1], argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
