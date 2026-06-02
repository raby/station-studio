"""§16.13.t4 — assert `requirements*.txt` ↔ `pyproject.toml` parity.

The project keeps two parallel dependency lists:
  * `requirements.txt` + `requirements-dev.txt` (the dev workflow's
    source-of-truth, consumed by `make install` and CI)
  * `pyproject.toml`'s `[project].dependencies` + `[project.optional-
    dependencies].dev` (consumed by the wheel build and `pip install
    station-studio[dev]`)

Both must list the same packages with the same version specifiers,
or the wheel ships a different runtime contract than `make install`
gives developers. The classic failure mode: a security bump on one
side forgets the other.

This script parses both surfaces, normalises the comparison, and
exits non-zero on drift with a diff that names the missing entries.
Run via `make check-deps-parity` or directly:
  python scripts/check_deps_parity.py

Design choice — TWO-WAY check, not regenerate-from-canonical:
  * Generating requirements.txt from pyproject (via `pip-compile` or
    similar) means an additional tool + commit-the-generated-file
    workflow. Premature for the surface area we have today (7+3 deps).
  * The two-way CI check catches drift the moment it happens, with a
    clear error message. Same outcome, fewer moving parts.

When t4's "real risk over time" actually bites — i.e. the lists grow
beyond ~20 entries or a real security-bump forgot one side — the
right time to revisit is then.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _parse_requirements(path: Path) -> set[str]:
    """Return the set of dependency specifiers from a requirements
    file. Strips blank lines + `#`-comments + `-r other.txt` includes.

    Whitespace + the `[extras]` selector are part of the specifier
    (e.g. `uvicorn[standard]>=0.27` must match pyproject's same form).
    """
    if not path.exists():
        sys.exit(f"check_deps_parity: {path} not found")
    specs: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        # `-r requirements.txt` includes are followed transitively when
        # we parse the dev file; skip the directive itself.
        if line.startswith("-r "):
            continue
        specs.add(line)
    return specs


def _parse_pyproject() -> tuple[set[str], set[str]]:
    """Return `(runtime, dev)` specifier sets from pyproject.toml."""
    pyproject = REPO_ROOT / "pyproject.toml"
    if not pyproject.exists():
        sys.exit(f"check_deps_parity: {pyproject} not found")

    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    runtime = set(project.get("dependencies", []))
    dev = set(project.get("optional-dependencies", {}).get("dev", []))
    return runtime, dev


def main() -> int:
    """Compare and report. Returns 0 iff every surface matches."""
    txt_runtime = _parse_requirements(REPO_ROOT / "requirements.txt")
    txt_dev_full = _parse_requirements(REPO_ROOT / "requirements-dev.txt")
    py_runtime, py_dev = _parse_pyproject()

    # `requirements-dev.txt` includes `-r requirements.txt` so its
    # contents inherit the runtime set. The dev-ONLY entries are
    # what we compare against pyproject's `[project.optional-
    # dependencies].dev` — which is documented as "Mirror
    # requirements-dev.txt" (i.e. dev-only, not the full union).
    txt_dev = txt_dev_full - txt_runtime

    failed = False

    # Runtime parity.
    runtime_only_in_txt = txt_runtime - py_runtime
    runtime_only_in_py = py_runtime - txt_runtime
    if runtime_only_in_txt or runtime_only_in_py:
        failed = True
        print("DRIFT: runtime dependencies differ between "
              "requirements.txt and pyproject.toml [project.dependencies]:")
        for spec in sorted(runtime_only_in_txt):
            print(f"  - requirements.txt only: {spec}")
        for spec in sorted(runtime_only_in_py):
            print(f"  + pyproject only:        {spec}")
        print()

    # Dev parity.
    dev_only_in_txt = txt_dev - py_dev
    dev_only_in_py = py_dev - txt_dev
    if dev_only_in_txt or dev_only_in_py:
        failed = True
        print("DRIFT: dev dependencies differ between "
              "requirements-dev.txt (dev-only entries) and "
              "pyproject.toml [project.optional-dependencies].dev:")
        for spec in sorted(dev_only_in_txt):
            print(f"  - requirements-dev.txt only: {spec}")
        for spec in sorted(dev_only_in_py):
            print(f"  + pyproject only:           {spec}")
        print()

    if failed:
        print(
            "Fix: bring both surfaces into sync, then re-run "
            "`make check-deps-parity`. See pyproject.toml's "
            "[project.optional-dependencies].dev comment for the contract."
        )
        return 1

    total = len(txt_runtime) + len(txt_dev)
    print(f"check_deps_parity: OK ({total} entries match across both surfaces)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
