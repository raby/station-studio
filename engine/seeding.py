"""Seed the database with station templates from JSON.

§16.13.t9: library half of what used to be `scripts/seed_stations.py`.
Moved here so the wheel doesn't need to ship the whole `scripts/`
package — the dev tools (bootstrap_classics, dump_weekly_template,
generate_byte_eq_fixtures, etc) stay in `scripts/`, the
runtime-needed code lives in `engine/` alongside the other library
modules. `scripts/seed_stations.py` becomes a thin CLI shim that
re-exports from here.

Idempotent: creates the station row on first run, updates the template
on subsequent runs if the checked-in JSON has changed. Never deletes.

Used by:
  * `api/main.py:lifespan` — auto-seed bundled stations on first boot.
  * `station_studio/bootstrap.py` (indirectly via api.main).
  * `scripts/seed_stations.py` — `python -m scripts.seed_stations` CLI.
  * Tests (`tests/test_seed_stations.py`, `tests/test_cli.py`,
    `tests/test_api.py`).

The `STATIONS_DIR` constant + `_resolve_stations_dir` helper honour
the `STATION_STUDIO_STATIONS_DIR` env var override (§16.13 c3) for
installed-wheel users.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from sqlalchemy.orm import Session

from engine.database import make_engine, make_sessionmaker
from engine.models import Base, Station


logger = logging.getLogger(__name__)
from engine.template import parse_template, TemplateError


REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_STATIONS_DIR = REPO_ROOT / "stations"


def _resolve_stations_dir() -> Path:
    """Resolve the stations directory at call time.

    Reads `STATION_STUDIO_STATIONS_DIR` if set (installed-wheel +
    docker paths set this in the app-home bootstrap), otherwise
    falls back to the repo's `stations/` dir (in-repo dev /
    Makefile-driven workflows).
    """
    override = os.environ.get("STATION_STUDIO_STATIONS_DIR", "").strip()
    if override:
        return Path(override)
    return _REPO_STATIONS_DIR


# Back-compat shim: existing callers (lifespan, tests) read `STATIONS_DIR`
# as a module-level constant. Keep it as the repo default so backward
# compatibility holds; `_collect_paths` uses `_resolve_stations_dir()` now.
STATIONS_DIR = _REPO_STATIONS_DIR


def seed_station(db: Session, path: Path) -> dict:
    """Load one station template from JSON and upsert it.

    Returns a summary dict with slug, action
    (created|updated|unchanged), and slot_count.
    """
    with open(path) as f:
        raw = json.load(f)

    try:
        template = parse_template(raw)
    except TemplateError as e:
        raise SystemExit(f"invalid template at {path}: {e}")

    slug = template.station
    name = raw.get("name", slug)

    existing = db.query(Station).filter(Station.slug == slug).first()
    if existing is None:
        db.add(Station(slug=slug, name=name, template=raw))
        action = "created"
    elif existing.template != raw:
        existing.template = raw
        existing.name = name
        action = "updated"
    else:
        action = "unchanged"

    return {
        "slug": slug,
        "action": action,
        "slot_count": template.slots_per_week(),
    }


def seed_all(db: Session, paths: list[Path]) -> list[dict]:
    """Seed every template in `paths` and commit once at the end."""
    summaries = [seed_station(db, p) for p in paths]
    db.commit()
    return summaries


def _collect_paths(argv: list[str]) -> list[Path]:
    """Resolve the station JSONs to seed.

    Explicit args override the glob. Default: every `*.json` under
    `stations/` EXCEPT:

      * `*.pool-manifest.json` — §14.36 pool manifests co-located
        with their station's template (e.g. `rebirth-radio.json` +
        `rebirth-radio.pool-manifest.json`).

      * `*.editor.json` — §16.2.1 editor metadata sidecars co-located
        with their station's template (e.g. `rebirth-radio.json` +
        `rebirth-radio.editor.json`). Engine ignores the sidecar;
        seed_stations would otherwise mis-parse it as a template.

    Filtering by suffix keeps the manifest + sidecar next to their
    station without forcing a deeper directory structure.
    """
    if argv:
        return [Path(a).resolve() for a in argv]
    stations_dir = _resolve_stations_dir()
    return sorted(
        p for p in stations_dir.glob("*.json")
        if not (
            p.name.endswith(".pool-manifest.json")
            or p.name.endswith(".editor.json")
        )
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: load station JSONs + seed the DB. Mirrors the
    historical `python -m scripts.seed_stations` invocation."""
    argv = argv if argv is not None else sys.argv[1:]
    paths = _collect_paths(argv)
    if not paths:
        print("no station JSON files found", file=sys.stderr)
        return 1

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)

    with SessionLocal() as db:
        summaries = seed_all(db, paths)

    for s in summaries:
        print(f"  {s['action']:<9}  {s['slug']:<30}  {s['slot_count']:>6} slots/week")
    return 0


# ---------- §16.14 c2 — starter pool auto-import + sidecar auto-derive ----


# Naming + role + filename mapping for the bundled starter pools (D4).
# Mirrors stations/starter.json's `sources` block by NAME so the
# generated station finds the imported pools at slot-generation time.
# Tuple shape: (pool name, iTunes-XML filename, role).
_STARTER_POOLS = (
    ("STARTER_CONTENT", "starter-content.itunes.xml", "content"),
    ("STARTER_JINGLES", "starter-jingles.itunes.xml", "content"),
)


def auto_import_starter_pools(
    db: Session, stations_dir: Path,
) -> int:
    """§16.14 D6 + Q8: first-boot auto-import the starter station's
    pools. Returns the count of pools imported on this call.

    First-boot guard: if ANY `SourcePlaylist` rows exist already, skip
    everything and return 0. The contract is "starter just works on a
    fresh install AND a user who deletes the auto-imported pools to
    learn the manual flow doesn't get them silently re-imported on
    next boot." That second case is the Q8-locked behaviour: presence
    of any user-imported pool is the "user has taken over" signal.

    Missing-file tolerance: if the XML pools aren't in `stations_dir`
    (e.g. a fork that removed the starter from its bundle), skip
    cleanly with 0 — no error, no log noise.
    """
    from sqlalchemy import func

    from engine.importer import import_source
    from engine.models import SourcePlaylist

    if db.query(func.count(SourcePlaylist.id)).scalar() > 0:
        return 0

    imported = 0
    for name, filename, role in _STARTER_POOLS:
        xml_path = stations_dir / filename
        if not xml_path.is_file():
            continue
        import_source(db, xml_path, name, role=role)
        imported += 1
    return imported


def auto_derive_missing_sidecars(stations_dir: Path) -> int:
    """§16.14 D8: if a station JSON ships without a committed
    `.editor.json` sidecar, derive one in-place so the visualiser is
    interesting on first paint. Returns the count of sidecars
    written on this call.

    Idempotent: existing sidecars are NEVER overwritten. A user (or
    future contributor) who hand-edits a sidecar gets their edits
    preserved across boots. To regenerate, `rm
    <home>/stations/foo.editor.json` then restart.

    c2-review 🟡-2: narrow the exception catch to the expected
    user-input failure modes (broken JSON syntax, missing template
    keys, template parse errors). Truly unexpected exceptions
    (NameError / TypeError from a derive_sidecar refactor bug,
    ImportError from a broken install) propagate so they don't
    silently degrade the bootstrap. Caught errors are LOGGED via
    the module logger so a contributor running `STATION_STUDIO_LOG=
    info station-studio serve` sees what was skipped.
    """
    # Lazy import — keeps engine.seeding light when nobody calls the
    # derive helper.
    from scripts.derive_sidecar import derive_sidecar
    from engine.template import TemplateError

    if not stations_dir.is_dir():
        return 0
    derived = 0
    for station_path in sorted(stations_dir.glob("*.json")):
        # Skip sidecar files themselves + the pool-manifest companion.
        if station_path.name.endswith(".editor.json"):
            continue
        if ".pool-manifest" in station_path.name:
            continue
        sidecar_path = (
            station_path.parent / f"{station_path.stem}.editor.json"
        )
        if sidecar_path.exists():
            continue
        try:
            raw = json.loads(station_path.read_text())
            sidecar = derive_sidecar(raw)
        except (json.JSONDecodeError, KeyError, ValueError,
                TemplateError) as e:
            logger.warning(
                "auto_derive: skipping %s — %s: %s",
                station_path.name, type(e).__name__, e,
            )
            continue
        sidecar_path.write_text(
            json.dumps(sidecar, indent=2, sort_keys=False) + "\n"
        )
        derived += 1
    return derived


if __name__ == "__main__":
    raise SystemExit(main())
