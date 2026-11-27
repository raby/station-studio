"""Reading the apps' changelogs: CHANGELOG.md (Studio), player/CHANGELOG.md
and server/CHANGELOG.md, in Keep a Changelog's shape.

Each release is a `## [version] - date` heading, then an optional intro
paragraph, then bullets, optionally under `### Added`, `### Changed`,
`### Fixed` and the like. A bullet's wrapped lines are indented. Any
other `## ` heading (like "Earlier versions") ends the releases.

Used three ways: a release's notes on GitHub (`section`, by
scripts/release_notes.py), Station Server's Help › What's new (`parse`),
and what Update now says a new version brings (`whats_new`, from the
notes GitHub has).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_RELEASE = re.compile(r"^## \[(?P<version>[^\]]+)\](?:\s+-\s+(?P<date>\d{4}-\d{2}-\d{2}))?\s*$")
_LINK_DEF = re.compile(r"^\[[^\]]+\]:\s")


@dataclass
class Release:
    version: str
    date: str | None = None
    intro: list[str] = field(default_factory=list)
    # (heading, bullets); heading None for bullets with none above them.
    groups: list[tuple[str | None, list[str]]] = field(default_factory=list)

    @property
    def items(self) -> list[str]:
        return [item for _, items in self.groups for item in items]

    def as_dict(self, plain_text: bool = True) -> dict:
        tidy = plain if plain_text else (lambda s: s)
        return {
            "version": self.version,
            "date": self.date,
            "intro": [tidy(p) for p in self.intro],
            "groups": [{"heading": h, "items": [tidy(i) for i in items]} for h, items in self.groups],
        }


def parse(text: str) -> list[Release]:
    """The releases in a changelog, newest first as written ("Unreleased"
    included, under that name)."""
    releases: list[Release] = []
    current: Release | None = None
    paragraph: list[str] = []
    bullet: list[str] | None = None
    fenced = False

    def finish():
        nonlocal paragraph, bullet
        if current is not None:
            if bullet is not None:
                if not current.groups:
                    current.groups.append((None, []))
                current.groups[-1][1].append(" ".join(bullet))
            elif paragraph:
                current.intro.append(" ".join(paragraph))
        paragraph, bullet = [], None

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        head = _RELEASE.match(line)
        if head:
            finish()
            current = Release(head["version"], head["date"])
            releases.append(current)
            continue
        if line.startswith("## ") or line.startswith("# "):
            finish()
            current = None
            continue
        if current is None or _LINK_DEF.match(line):
            continue
        if line.startswith("### "):
            finish()
            current.groups.append((line[4:].strip(), []))
        elif line.startswith(("- ", "* ")):
            finish()
            bullet = [line[2:].strip()]
        elif not line.strip():
            finish()
        elif bullet is not None and raw.startswith((" ", "\t")):
            bullet.append(line.strip())
        elif bullet is not None:
            finish()
            paragraph = [line.strip()]
        else:
            paragraph.append(line.strip())
    finish()
    return releases


def find(text: str, version: str) -> Release | None:
    return next((r for r in parse(text) if r.version == version), None)


def _unwrap(lines: list[str]) -> list[str]:
    """Markdown with each paragraph and bullet on one line: GitHub shows a
    release's notes with every line break kept, so a changelog's wrapped
    lines would break mid-sentence there. Headings, blank lines and code
    blocks stay as they are."""
    out: list[str] = []
    joinable = False  # the last line out is text a wrapped line continues
    fenced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            out.append(line)
            joinable = False
        elif fenced or not stripped or stripped.startswith("#") or _LINK_DEF.match(stripped):
            out.append(line)
            joinable = False
        elif stripped.startswith(("- ", "* ")) or not joinable:
            out.append(line.rstrip())
            joinable = True
        else:
            out[-1] += " " + stripped
    return out


def section(text: str, version: str) -> str | None:
    """A release's part of the changelog (markdown), without its heading,
    each paragraph and bullet on one line: for its notes on GitHub. None
    when it has none."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if (m := _RELEASE.match(line.rstrip())) and m["version"] == version), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ") or lines[i].startswith("# ")), len(lines))
    body = "\n".join(_unwrap([line for line in lines[start + 1:end] if not _LINK_DEF.match(line)])).strip()
    return body or None


# What a release's notes on GitHub start with (scripts/release_notes.py),
# and where that part ends.
NOTES_HEADING = "## What's new"
NOTES_END = "---"


def whats_new(release_body: str | None) -> Release | None:
    """The changes in a release's notes on GitHub: the part under "## What's
    new…" down to the first `---`. None for older releases without it."""
    if not isinstance(release_body, str):
        return None
    lines = release_body.replace("\r\n", "\n").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(NOTES_HEADING)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == NOTES_END), len(lines))
    found = parse("## [notes]\n" + "\n".join(lines[start + 1:end]))
    return found[0] if found and (found[0].intro or found[0].items) else None


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def plain(text: str) -> str:
    """Markdown read as plain text: links to their words, no ** or `."""
    return _MD_LINK.sub(r"\1", text).replace("**", "").replace("`", "")
