"""§16.13.t4 — verify scripts/check_deps_parity.py works AND that the
repo's actual dep surfaces are in sync.

t4-review 🟢-1 + 🟢-2: tests now drive the script's helpers + main()
via direct import rather than subprocess. ~150ms saved across the
file. Helpers raise `DepsParityError` instead of `sys.exit`, so the
"missing file" path is testable inline without re-subprocessing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


# scripts/ is on sys.path via pyproject's `packages = [..., "scripts"]`
# (c4 + the §16.13.t9 over-include — see DESIGN). Import directly.
from scripts.check_deps_parity import (  # noqa: E402
    DepsParityError, _normalize_spec, _parse_pyproject,
    _parse_requirements, main,
)


def test_real_repo_pyproject_and_requirements_in_parity() -> None:
    """The CURRENT repo state is in parity. Contributors who bump
    deps locally see the failure here before pushing — same gate
    ci.yml runs."""
    rc = main()
    assert rc == 0, "live repo is drifting; see check_deps_parity output"


def test_checker_detects_synthetic_runtime_drift(
    tmp_path: Path, monkeypatch
) -> None:
    """Synthetic drift fixture: pyproject lists `fastapi`, requirements
    doesn't. The set-diff helpers must catch it.

    Monkeypatches the script's `REPO_ROOT` to the sandbox so we
    exercise the real helpers (not a copy of them) against controlled
    inputs. Direct-import is what t4-review 🟢-1 unlocks; subprocess
    would also work but is slower."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "pyproject.toml").write_text(
        '[project]\n'
        'name = "sandbox"\nversion = "0"\n'
        'dependencies = ["fastapi>=0.110", "click>=8.1"]\n'
        '[project.optional-dependencies]\n'
        'dev = ["pytest>=8.0"]\n'
    )
    (sandbox / "requirements.txt").write_text("click>=8.1\n")
    (sandbox / "requirements-dev.txt").write_text(
        "-r requirements.txt\npytest>=8.0\n"
    )

    import scripts.check_deps_parity as mod
    monkeypatch.setattr(mod, "REPO_ROOT", sandbox)

    txt_runtime = mod._parse_requirements(sandbox / "requirements.txt")
    py_runtime, _py_dev = mod._parse_pyproject()
    # fastapi appears in pyproject runtime, NOT in requirements.txt → diff.
    assert "fastapi>=0.110" in py_runtime
    assert "fastapi>=0.110" not in txt_runtime

    # main() should report exit 1 with a named diff to stdout.
    capsys_buf = []

    class _Cap:
        def write(self, s):
            capsys_buf.append(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", _Cap())
    rc = mod.main()
    monkeypatch.undo()
    out = "".join(capsys_buf)
    assert rc == 1
    assert "DRIFT" in out
    assert "fastapi" in out
    assert "pyproject only" in out


def test_checker_handles_missing_files(
    tmp_path: Path, monkeypatch
) -> None:
    """A sandbox missing pyproject.toml: helpers raise
    DepsParityError. main() catches + returns 1 with a clear
    "not found" message. Pinned via direct call — no subprocess,
    no stack trace path to manage."""
    empty = tmp_path / "empty"
    empty.mkdir()
    import scripts.check_deps_parity as mod
    monkeypatch.setattr(mod, "REPO_ROOT", empty)

    # Helpers raise (t4-review 🟢-2).
    with pytest.raises(DepsParityError, match="not found"):
        mod._parse_requirements(empty / "requirements.txt")
    with pytest.raises(DepsParityError, match="not found"):
        mod._parse_pyproject()

    # main() translates to exit code 1 + stderr message.
    err_buf = []

    class _Cap:
        def write(self, s):
            err_buf.append(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stderr", _Cap())
    rc = mod.main()
    monkeypatch.undo()
    assert rc == 1
    assert "not found" in "".join(err_buf).lower()


# ---------- t4-review 🟡-1 + 🟡-2 normalisation tests ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # PEP 503 — case-insensitive name
        ("Fastapi>=0.110", "fastapi>=0.110"),
        ("FastAPI>=0.110", "fastapi>=0.110"),
        # PEP 503 — `_/-/.` runs collapse to single `-`
        ("python_multipart>=0.0.9", "python-multipart>=0.0.9"),
        ("python.multipart>=0.0.9", "python-multipart>=0.0.9"),
        ("python__multipart>=0.0.9", "python-multipart>=0.0.9"),
        ("python-_-multipart>=0.0.9", "python-multipart>=0.0.9"),
        # Whitespace normalisation
        ("fastapi >= 0.110", "fastapi>=0.110"),
        ("fastapi>= 0.110", "fastapi>=0.110"),
        ("  fastapi>=0.110  ", "fastapi>=0.110"),
        # Extras stay intact, name still normalised
        ("Uvicorn[standard]>=0.27", "uvicorn[standard]>=0.27"),
        # Version-specifier chars / operators preserved as-is
        ("alembic>=1.13,<2.0", "alembic>=1.13,<2.0"),
        ("click~=8.1", "click~=8.1"),
    ],
)
def test_normalize_spec_pep503_and_whitespace(raw: str, expected: str) -> None:
    """t4-review 🟡-1 + 🟡-2 regression pin: PEP 503 name
    normalisation (case + `[-_.]` runs) AND internal-whitespace
    collapse. Without these, the checker false-flags drift when both
    surfaces refer to the same package via visually-different but
    pip-equivalent names."""
    assert _normalize_spec(raw) == expected


def test_normalize_spec_treats_pep503_aliases_as_equal(
    tmp_path: Path, monkeypatch
) -> None:
    """Integration: a sandbox where one surface uses `python_multipart`
    and the other uses `python-multipart` should PASS parity (they're
    the same package per PEP 503), not flag drift."""
    sandbox = tmp_path / "alias"
    sandbox.mkdir()
    (sandbox / "pyproject.toml").write_text(
        '[project]\n'
        'name = "alias"\nversion = "0"\n'
        # underscore form
        'dependencies = ["python_multipart>=0.0.9"]\n'
        '[project.optional-dependencies]\n'
        'dev = []\n'
    )
    (sandbox / "requirements.txt").write_text(
        # dash form — should compare equal post-normalisation
        "python-multipart>=0.0.9\n"
    )
    (sandbox / "requirements-dev.txt").write_text("-r requirements.txt\n")

    import scripts.check_deps_parity as mod
    monkeypatch.setattr(mod, "REPO_ROOT", sandbox)
    assert mod.main() == 0
