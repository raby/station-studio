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
from engine.models import Base, Rule, Station
from engine.rules import set_rule_list_value, set_rule_value


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


# §16.14.t5 c2 + §16.14.t9 — imperative list-rule fallback registry.
#
# Keyed by station slug → dict of list-rule key → list of source pool
# names. Applied by `seed_station` AFTER the template's own `rules:`
# block; the template wins for keys declared in both, the registry
# fills in for keys NOT declared by the template (D6 fallback).
#
# §16.14.t9 reasoning: templates declare their rules in the JSON
# (the §16.14.t5 demo for the starter moved to
# `stations/starter.json:rules`), so the registry is empty today.
# It stays as a forward-extension point — e.g. host-specific
# defaults that don't belong in the committed template JSON. If
# no use case appears, a future slice can delete the table.
#
# Both the template-declared path AND this registry use
# only-set-if-absent semantics (§16.14.t9 D4 + D6 uniformity):
# the operator's API customisations persist across re-seeds. To
# revert to the template default, DELETE the rule via API; the
# next seed_station writes the template value back.
_STATION_LIST_RULES: dict[str, dict[str, list[str]]] = {
    # Empty today (§16.14.t9 c2 migrated the starter's rule into
    # stations/starter.json's `rules:` block). New entries land
    # here only when a station can't carry the value in its
    # committed template — extremely rare.
}


def _rule_row_exists(db: Session, *, station_id: int, key: str) -> bool:
    """§16.14.t9 D4 helper: does a Rule row already exist for this
    (station_id, key)?

    seed_station consults this before writing template-declared or
    registry-default rules so the operator's API edits persist
    across re-seeds. The row's value isn't read — only its
    presence determines whether seed_station writes the default.
    """
    return db.query(Rule).filter_by(
        station_id=station_id, key=key,
    ).first() is not None


def seed_station(db: Session, path: Path) -> dict:
    """Load one station template from JSON and upsert it.

    Also applies the template's declarative `rules:` block
    (§16.14.t9) plus any `_STATION_LIST_RULES` registry fallback
    for keys NOT declared by the template (D6). Both paths use
    only-set-if-absent semantics (D4): the Rule row gets written
    only when none exists for that (station_id, key). API
    customisations persist across re-seeds. To revert a rule to
    its template/registry default, DELETE it via API; the next
    `seed_station` writes the default back.

    Precedence for a given key:
      1. Existing Rule row (operator's API edit) — left untouched.
      2. Template's `rules:` block (the "factory default" that
         ships with the station).
      3. `_STATION_LIST_RULES[slug]` (host-specific fallback when
         the template doesn't declare the key).

    Returns a summary dict with slug, action
    (created|updated|unchanged), slot_count, and rules_applied —
    the count of Rule rows actually written by THIS call (always 0
    on a re-seed where every declared key already has a row).
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

    # §16.14.t9: apply declarative rules from template + registry
    # fallback. Template wins for keys declared in both; registry
    # supplies keys absent from the template. Both use
    # only-set-if-absent (D4 + D6).
    registry_list = _STATION_LIST_RULES.get(slug, {})
    # Template list-rules override registry entries for the same key.
    effective_list = {**registry_list, **template.rule_list_overrides}
    effective_int = dict(template.rule_int_overrides)

    rules_applied = 0
    if effective_int or effective_list:
        db.flush()
        station = db.query(Station).filter(Station.slug == slug).one()
        for key, value in effective_int.items():
            if not _rule_row_exists(db, station_id=station.id, key=key):
                set_rule_value(
                    db, station_id=station.id, key=key, value=value,
                )
                rules_applied += 1
        for key, value in effective_list.items():
            if not _rule_row_exists(db, station_id=station.id, key=key):
                set_rule_list_value(
                    db, station_id=station.id, key=key, value=value,
                )
                rules_applied += 1

    return {
        "slug": slug,
        "action": action,
        "slot_count": template.slots_per_week(),
        "rules_applied": rules_applied,
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
        # §16.14.t5 slice-review S2 + §16.14.t9: surface
        # rules_applied so operators see when a template-declared
        # OR registry-default rule landed. Suffix is empty when
        # no rules wrote — keeps output clean for the common case
        # (re-seeds where every declared key already has a row).
        applied = s.get("rules_applied", 0)
        rules_suffix = f"  + {applied} rule(s)" if applied else ""
        print(
            f"  {s['action']:<9}  {s['slug']:<30}  "
            f"{s['slot_count']:>6} slots/week{rules_suffix}"
        )
    return 0


# ---------- §16.14 c2 + §16.14.t1 — bundled-station auto-import + sidecar ----
#
# Renamed in §16.14.t1 c1-review 🟢-1 from `_STARTER_POOLS` to
# `_FIRST_BOOT_POOLS` — covers the §16.14 starter AND the §16.14.t1
# stateful-starter, and any future bundled station that needs its
# companion XMLs auto-imported on a fresh install.
#
# Doc note (c1-review 🟢-2): originally this section only handled the
# §16.14 starter; the §16.14.t1 stateful starter joined the table when
# that slice landed. Keep this comment honest as more bundled stations
# join — the test_starter_pools_table_matches_bundled_station_sources
# pin enforces that the table's pool names match every bundled
# station's `sources` block.


# Tuple shape: (pool name, iTunes-XML filename, role). Pool names
# mirror each bundled station's `sources` block by NAME so the
# generator finds the imported pools at slot-generation time.
_FIRST_BOOT_POOLS = (
    # §16.14 starter (stateless, weighted + jingle).
    ("STARTER_CONTENT", "starter-content.itunes.xml", "content"),
    ("STARTER_JINGLES", "starter-jingles.itunes.xml", "content"),
    # §16.14.t3 reference-role teaching pool. Declared in
    # stations/starter.json's sources with role: reference; the
    # engine never samples from it, but it appears in the
    # visualiser + the Sources page so the role is visible.
    # See stations/README.md > "What does role: reference mean?"
    # for the full contract.
    ("REFERENCE_ARCHIVE", "starter-reference.itunes.xml", "reference"),
    # §16.14.t1 stateful-starter (persistent cursor, sequential + prev_ref).
    ("STATEFUL_CONTENT", "stateful-starter-content.itunes.xml", "content"),
)

# §16.14.t1 c1-review 🟢-1: back-compat alias for any external caller
# (or future docs) still referencing the old name. Will warn-and-remove
# at the next opportunity.
_STARTER_POOLS = _FIRST_BOOT_POOLS


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
    for name, filename, role in _FIRST_BOOT_POOLS:
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
    # derive helper. §16.14.t2 / §16.14.t1 slice-review sweep:
    # imports from `engine.editor_metadata` (the canonical home post
    # lift), NOT `scripts.derive_sidecar` (the CLI shim that doesn't
    # ship in the wheel post §16.13.t9). The pre-lift import was a
    # production 🔴 — broke every CLI bootstrap on a wheel install.
    from engine.editor_metadata import derive_sidecar
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
