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
    installed).

    c4-review 🟡-2: ALSO skip if `web/build/` doesn't exist yet — the
    pyproject force-include block needs that directory or hatchling
    crashes with a "directory not found" deep inside the subprocess
    call, leaving `capture_output=True`-swallowed stderr that gives
    the contributor no clue what went wrong. A fresh-clone contributor
    who runs `pytest` after `make install` (without `make wheel` or
    `cd web && npm run build`) should see a clear skip message
    pointing at the fix, not a cryptic CalledProcessError.
    """
    try:
        import build  # noqa: F401 — presence check
    except ImportError:  # pragma: no cover — covered by skip below
        pytest.skip("`build` package not installed; skipping wheel test")

    if not (REPO_ROOT / "web" / "build").is_dir():
        pytest.skip(
            "web/build/ missing — run `make wheel` (or `cd web && npm run "
            "build`) first so the pyproject force-include block resolves. "
            "Skipping wheel-content tests instead of failing cryptically "
            "inside hatchling."
        )

    try:
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(into)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:  # pragma: no cover
        # c4-review 🟡-2 follow-on: surface the actual hatchling error.
        # Without re-raising via pytest.fail we'd get the bare subprocess
        # trace with stderr only visible by digging into exc.stderr.
        pytest.fail(
            "wheel build failed:\n"
            f"--- stdout ---\n{exc.stdout}\n"
            f"--- stderr ---\n{exc.stderr}"
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
    # All four example stations from the repo's `stations/` dir
    # made it through. §16.14 added `starter.json`; §16.14.t1
    # added `stateful-starter.json`.
    names = {Path(p).name for p in stations}
    assert "rebirth-classics.json" in names
    assert "rebirth-radio.json" in names
    assert "starter.json" in names
    assert "stateful-starter.json" in names


def test_wheel_ships_starter_pool_xmls_and_sidecar(
    wheel_contents: set[str],
) -> None:
    """§16.14 slice-review 🟡-2: pin that the starter station's
    auto-importable iTunes XMLs + the committed editor sidecar all
    ship in the wheel. Pre-fix the packaging test only covered
    `*.json` station templates; the XMLs the bootstrap auto-import
    reads on first boot could have been silently excluded by a
    force-include refactor without anyone noticing until a fresh
    install + `station-studio serve` showed "no tracks in pool
    STARTER_CONTENT" on the starter station.
    """
    bundled = {
        p for p in wheel_contents
        if p.startswith("station_studio/_bundled/stations/")
    }
    names = {Path(p).name for p in bundled}
    # The two auto-imported iTunes-XML pools (D4 + Q8 auto-import path).
    assert "starter-content.itunes.xml" in names, (
        "starter content pool missing — auto-import on first boot "
        "will silently skip the starter station's content slots"
    )
    assert "starter-jingles.itunes.xml" in names, (
        "starter jingles pool missing — every 8th slot will fail "
        "with 'no track for genre Jingle'"
    )
    # The pre-committed editor sidecar (D8 — visualiser interesting
    # on first paint). Without it, bootstrap's auto-derive would
    # fall back to producing one at runtime — works, but slower +
    # depends on the sidecar derive helper being shipped too.
    assert "starter.editor.json" in names, (
        "starter editor sidecar missing — visualiser will fall "
        "back to slow runtime derive on first boot"
    )
    # §16.14.t3 D1: the reference-archive teaching pool. Without
    # this XML in the wheel, REFERENCE_ARCHIVE's auto-import would
    # silently skip and the role: reference teaching annotation in
    # starter.json's sources[] would have no actual pool behind it
    # — the visualiser + Sources page would show an entry with
    # zero tracks, undermining the lesson.
    assert "starter-reference.itunes.xml" in names, (
        "starter-reference pool missing — auto-import will skip "
        "REFERENCE_ARCHIVE and the role: reference teaching "
        "example will be hollow"
    )


def test_wheel_ships_stateful_starter_pool_and_sidecar(
    wheel_contents: set[str],
) -> None:
    """§16.14.t1 slice-review 🟡-1: pin that the stateful-starter
    station's content pool + committed sidecar both ship. Same
    failure mode as the §16.14 starter pool test (slice-review
    🟡-2 closure) — the auto-import helper would silently skip
    STATEFUL_CONTENT on first boot if the XML weren't bundled,
    leaving the stateful-starter station with no tracks. The
    sidecar landing pre-committed keeps the visualiser fast on
    first paint.
    """
    bundled = {
        p for p in wheel_contents
        if p.startswith("station_studio/_bundled/stations/")
    }
    names = {Path(p).name for p in bundled}
    # The content pool: D6 first-boot auto-import lands this; an
    # absence here means STATEFUL_CONTENT silently won't import.
    assert "stateful-starter-content.itunes.xml" in names, (
        "stateful-starter content pool missing — auto-import on "
        "first boot will silently skip STATEFUL_CONTENT and the "
        "stateful-starter station will have zero tracks"
    )
    # The pre-committed editor sidecar (D8 parity with the
    # stateless starter).
    assert "stateful-starter.editor.json" in names, (
        "stateful-starter editor sidecar missing — visualiser "
        "falls back to slow runtime derive on first boot"
    )


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


def test_force_include_targets_land_under_station_studio_bundled(
    wheel_contents: set[str],
) -> None:
    """c4-review 🟢-2: pin the force-include MAPPING TARGET, not just
    that some path exists for each datum. The pyproject block today
    maps `web/build` → `station_studio/_bundled/web`. If someone swaps
    the target to e.g. `web` (top-level), the existing
    test_wheel_ships_svelte_static_assets test could still pass IF
    other paths happen to match, but `c2`'s SPA mount (which reads
    via `importlib.resources.files("station_studio") / "_bundled" /
    "web"`) would break at runtime.

    This test pins: every bundled-data shipment under
    `station_studio/_bundled/` is present, AND the top-level repo
    paths the force-include block reads FROM are NOT in the wheel
    (i.e. `stations/`, `migrations/`, `web/` shouldn't appear as
    wheel-top-level dirs — they belong nested under _bundled/).
    """
    # Every documented force-include destination is populated.
    for dst in [
        "station_studio/_bundled/stations/",
        "station_studio/_bundled/migrations/",
        "station_studio/_bundled/web/",
    ]:
        assert any(p.startswith(dst) for p in wheel_contents), (
            f"force-include mapping broken: nothing under {dst} in wheel"
        )

    # The single-file destination too.
    assert "station_studio/_bundled/alembic.ini" in wheel_contents

    # And the source-side paths are NOT mirrored at the wheel top level.
    # (Hatchling's force-include copies; it doesn't symlink-and-keep-original.
    # If these appear top-level, someone added a duplicate `packages=` or
    # accidentally changed the force-include target to "" / "/".)
    for src_top in ["stations/", "migrations/", "web/", "alembic.ini"]:
        leaked = [
            p for p in wheel_contents
            if p.startswith(src_top)
            and not p.startswith("station_studio/")
        ]
        assert not leaked, (
            f"force-include mapping leaked top-level entries: {leaked[:3]}... "
            f"(the destination should be station_studio/_bundled/...)"
        )


def test_wheel_ships_engine_seeding_module(
    wheel_contents: set[str],
) -> None:
    """§16.13.t9 (closes c4 over-include): the seed-stations LIBRARY
    moved to `engine.seeding` so the wheel no longer needs to ship
    the entire `scripts/` package. `api/main.py:lifespan` imports
    from engine.seeding now. Pin that the new library module is in
    the wheel — without it, the served app's auto-seed lifespan
    crashes with ModuleNotFoundError: engine.seeding."""
    assert "engine/seeding.py" in wheel_contents


def test_wheel_does_not_ship_scripts_package(
    wheel_contents: set[str],
) -> None:
    """§16.13.t9: `scripts/` is dev tooling (bootstrap_classics,
    dump_weekly_template, generate_byte_eq_fixtures, ...). The wheel
    must not ship them — an installed-wheel user never touches them.
    Pin that NO `scripts/*.py` file appears in the wheel as a
    regression catch against a future `packages += ["scripts"]`
    accidentally re-introducing the over-include."""
    leaked = {
        p for p in wheel_contents
        if p.startswith("scripts/") and p.endswith(".py")
    }
    assert not leaked, (
        f"scripts/ should not ship in the wheel post-t9; leaked: "
        f"{sorted(leaked)[:5]}"
    )
