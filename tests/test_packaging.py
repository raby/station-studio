"""§16.13 c1 — wheel contents regression tests.

Builds the wheel into a temp dir, then inspects its zip contents to
prove the `force-include` block (c1-review 🟡-1) actually ships the
bundled data c3's first-run bootstrap will look for. Without this,
silently deleting the force-include block would break c3 with a
runtime "FileNotFoundError" rather than a build-time test failure.

The test is gated behind a `hatch` build invocation — slow on first
run (build isolation creates a fresh venv), fast on subsequent runs
(build cache).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_wheel(into: Path) -> Path:
    """Build the wheel into `into` and return the wheel's path. Skips
    if `python -m build` isn't available (the wheel-build tests are
    inherently slower; the dev workflow may not always have `build`
    installed)."""
    try:
        import build  # noqa: F401 — presence check
    except ImportError:  # pragma: no cover — covered by skip below
        pytest.skip("`build` package not installed; skipping wheel test")

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(into)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(into.glob("station_studio-*.whl"))
    assert wheels, f"no wheel produced in {into}"
    return wheels[0]


@pytest.fixture(scope="module")
def wheel_contents() -> set[str]:
    """Build the wheel once per module; return the set of paths inside."""
    with tempfile.TemporaryDirectory() as tmp:
        wheel_path = _build_wheel(Path(tmp))
        with zipfile.ZipFile(wheel_path) as zf:
            return set(zf.namelist())


def test_wheel_ships_python_packages(wheel_contents: set[str]) -> None:
    """The three top-level Python packages declared in
    `[tool.hatch.build.targets.wheel.packages]` actually land in the
    wheel. Regression catch for an accidental `packages` reduction."""
    assert any(p.startswith("station_studio/") for p in wheel_contents)
    assert any(p.startswith("api/") for p in wheel_contents)
    assert any(p.startswith("engine/") for p in wheel_contents)


def test_wheel_ships_bundled_stations(wheel_contents: set[str]) -> None:
    """c1-review 🟡-1: D6 says copy bundled example stations into the
    user's `~/.station-studio/stations/` on first run. C3 reads them
    via `importlib.resources.files("station_studio") / "_bundled" /
    "stations"`. This test catches the day someone deletes the
    `force-include` block without thinking about c3."""
    stations = {
        p for p in wheel_contents
        if p.startswith("station_studio/_bundled/stations/")
        and p.endswith(".json")
    }
    assert stations, (
        "no bundled stations in wheel — c3's first-run seed will break"
    )
    # Specifically: both example stations from the repo's `stations/`
    # dir made it through.
    names = {Path(p).name for p in stations}
    assert "rebirth-classics.json" in names
    assert "rebirth-radio.json" in names


def test_wheel_ships_alembic_migrations(wheel_contents: set[str]) -> None:
    """c1-review 🟡-1: c3's first-run bootstrap invokes `alembic upgrade
    head` against the bundled migrations. Catches a future
    `force-include` removal that would break installed-user DB init."""
    migrations = {
        p for p in wheel_contents
        if p.startswith("station_studio/_bundled/migrations/")
    }
    assert migrations, (
        "no migrations in wheel — c3's first-run alembic upgrade will fail"
    )
    # The env.py is the alembic entry point — must be present.
    assert any(p.endswith("/env.py") for p in migrations)
    # At least one version script (the schema baseline).
    assert any(
        p.startswith("station_studio/_bundled/migrations/versions/")
        and p.endswith(".py")
        for p in migrations
    )


def test_wheel_ships_alembic_ini(wheel_contents: set[str]) -> None:
    """The alembic.ini bundled alongside the migrations carries the env
    config (URL template + script_location). c3 reads it from the
    bundled path."""
    assert "station_studio/_bundled/alembic.ini" in wheel_contents


def test_wheel_ships_svelte_static_assets(wheel_contents: set[str]) -> None:
    """§16.13 c4: pyproject's `force-include "web/build" =
    "station_studio/_bundled/web"` MUST result in the SvelteKit build
    output landing in the wheel. Without these, `station-studio
    serve` would fall back to the API-only blob at `/` and ship a
    broken UX. Asserts the SPA shell + the immutable asset dir
    that c2's StaticFiles mount serves."""
    web_files = {
        p for p in wheel_contents
        if p.startswith("station_studio/_bundled/web/")
    }
    assert web_files, (
        "no web assets in wheel — c2's SPA mount falls back to "
        "API-only and the installed user gets a broken UI"
    )
    # The SPA shell c2's spa_index() serves.
    assert "station_studio/_bundled/web/index.html" in wheel_contents
    # SvelteKit's hashed-asset dir that c2's StaticFiles mount cares about.
    assert any(
        p.startswith("station_studio/_bundled/web/_app/")
        for p in web_files
    ), "no _app/ assets — the JS/CSS bundles aren't shipping"


def test_wheel_ships_scripts_package(wheel_contents: set[str]) -> None:
    """§16.13 c4: ship `scripts/` so `api/main.py`'s auto-seed lifespan
    can `from scripts.seed_stations import ...` at install time.
    Over-includes some dev tools today (see §16.13.t9 for the future
    library/dev-tool split); the runtime import is the load-bearing
    case. Without this, the served app crashes on startup with
    ModuleNotFoundError: scripts."""
    scripts_files = {
        p for p in wheel_contents
        if p.startswith("scripts/") and p.endswith(".py")
    }
    assert "scripts/seed_stations.py" in scripts_files
    assert "scripts/__init__.py" in scripts_files
