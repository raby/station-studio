"""§16.13 c1 + t2 — `station-studio` CLI entry point.

D5 scope (c1-c3): `serve` + `import` + `seed`. The install-essentials
— an installed user with no repo checkout can start the app, import
their own pools, and re-seed the bundled examples.

t2 scope (this commit): `generate` + `export` + `backup`. The full
design list — installed-user scripting without a repo. Cron-able
generation, file-or-stdout XML export, and a one-shot tar of the
whole app-home for off-box backup.

All six subcommands share the same shape:
  * Click subcommand under the `main` group.
  * `bootstrap()` runs FIRST so the app-home + DB env vars are set
    before any engine/api import touches `engine.database`.
  * Lazy imports for everything beyond `click` + `pathlib` — keeps
    `--help` snappy (no fastapi/sqlalchemy/uvicorn load cost just
    to print help).
  * Errors go to stderr + exit 1 (Click's `ctx.exit(1)`); successes
    print a one-line summary to stdout for shell-pipeline use.
"""
from __future__ import annotations

import os
from pathlib import Path

import click

from station_studio import __version__


# Defaults match `run.py` (the existing dev entry point) so muscle
# memory carries over: 127.0.0.1:8001. Override via --host / --port.
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8001


@click.group(
    name="station-studio",
    help=(
        "Open-source playlist generator for hobbyist radio stations.\n\n"
        "Run `station-studio serve` to start the app, then open "
        "http://localhost:8001 in a browser."
    ),
)
@click.version_option(__version__, prog_name="station-studio")
def main() -> None:
    """Group entry — Click dispatches to subcommands."""


@main.command(name="serve", help="Start the FastAPI app (the web UI + API).")
@click.option(
    "--host",
    default=_DEFAULT_HOST,
    show_default=True,
    help="Bind address. Default 127.0.0.1 (loopback only).",
)
@click.option(
    "--port",
    type=int,
    default=_DEFAULT_PORT,
    show_default=True,
    help="Port to bind. Default 8001 (matches the dev `run.py`).",
)
def serve(host: str, port: int) -> None:
    """Run uvicorn against `api.main:app`.

    `workers=1` is HARDCODED, not configurable. The §16.11 job tracker
    holds state in-memory per worker — multi-worker yields spurious
    poll-404s when the frontend's status fetch lands on a different
    worker. Until the DB-backed job state (§16.21+) lands, single
    worker is the only correct mode. The constraint is enforced here
    so an installed user can't accidentally trip it via uvicorn flags.
    """
    # §16.13 c3: bootstrap before uvicorn. ensure_home + migrations +
    # bundled-stations copy land BEFORE api.main is imported (uvicorn
    # imports its `app` arg lazily, so the env vars bootstrap sets
    # apply to engine.database / scripts.seed_stations once they
    # eventually do load).
    from station_studio.bootstrap import bootstrap
    bootstrap(echo=click.echo)

    # Imported lazily so `station-studio --version` (or --help on other
    # subcommands) doesn't pay the uvicorn import cost.
    import uvicorn

    click.echo(f"Starting station-studio on http://{host}:{port}")
    click.echo("(single worker — see DESIGN.md §16.11 / §16.13)")
    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        workers=1,
        reload=False,
    )


@main.command(
    name="import",
    help=(
        "Import an iTunes XML as a named pool. [Stub — wired in c3 once "
        "the app-home + bootstrap land.]"
    ),
)
@click.argument("path", required=False)
@click.option("--name", help="Pool name (e.g. DAYTIME, JINGLES).")
@click.option(
    "--role",
    type=click.Choice(["content", "reference"]),
    default="content",
    show_default=True,
    help="content = engine samples from this pool; reference = cross-ref only.",
)
@click.pass_context
def import_cmd(
    ctx: click.Context, path: str | None, name: str | None, role: str
) -> None:
    """§16.13 c3 — wired implementation.

    Imports an iTunes XML at PATH as a pool named --name. Uses the
    app-home's DB (bootstrap runs first to ensure the DB is migrated
    + env vars are set). Direct DB access; no running server needed.

    Mirrors the /api/sources/import endpoint's contract: ValueError
    surfaces as a CLI 400-equivalent (exit 1, error message); any
    other exception bubbles up.
    """
    if not path:
        click.echo("Error: PATH (iTunes XML file) is required.", err=True)
        ctx.exit(1)
    if not name:
        click.echo("Error: --name is required.", err=True)
        ctx.exit(1)
    # c3-review 🟡-1: the API layer has `_validate_name` (§16.25 c1) to
    # catch empty / whitespace-only names before they reach the importer
    # — leaving a blank-named pool in the DB is awkward on /api/sources
    # and the UI's pool list. The CLI is a third entry point to the same
    # backend, inherits the same gap. Same fix, inline here to avoid
    # cross-package coupling to api/routers/sources.py (queued as a
    # follow-up — see §16.13.t6 in DESIGN for the consolidation idea).
    name = name.strip()
    if not name:
        click.echo(
            "Error: --name must not be empty or whitespace-only.",
            err=True,
        )
        ctx.exit(1)

    from station_studio.bootstrap import bootstrap
    bootstrap(echo=click.echo)

    # Imports stay lazy — keeps `station-studio --help` snappy.
    from engine.database import make_engine, make_sessionmaker
    from engine.importer import import_source
    from engine.models import Base

    xml_path = Path(path).expanduser().resolve()
    if not xml_path.is_file():
        click.echo(f"Error: file not found: {xml_path}", err=True)
        ctx.exit(1)

    engine = make_engine()
    Base.metadata.create_all(engine)
    SessionLocal = make_sessionmaker(engine)

    try:
        with SessionLocal() as db:
            summary = import_source(db, xml_path, name, role=role)
            db.commit()
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)

    tracks = summary.get("tracks_imported", "?")
    click.echo(
        f"Imported {tracks} tracks into pool '{name}' (role: {role})."
    )


@main.command(
    name="seed",
    help=(
        "(Re)copy the bundled example stations into your stations dir. "
        "[Stub — wired in c3 once the app-home lands.]"
    ),
)
def seed() -> None:
    """§16.13 c3 — wired implementation.

    Copies any MISSING bundled stations into the app-home's stations
    dir. Existing files are preserved (so user edits aren't clobbered
    between runs); deleting a station first and re-running `seed`
    restores it. The bootstrap step does the actual copy work — this
    subcommand is mostly the explicit re-run affordance for "I
    deleted Classics, get it back."

    A future `seed --force` would overwrite existing files (factory
    reset). Out of scope for c3; queue if a real user asks.
    """
    from station_studio.bootstrap import bootstrap
    bootstrap(echo=click.echo)


# ---------- §16.13.t2 — generate / export / backup ----------


@main.command(
    name="generate",
    help=(
        "Generate a playlist for one station and one week. Persists "
        "Playlist + Trace rows + advances cursors. Equivalent to the "
        "POST /api/stations/{slug}/generate endpoint, runnable without "
        "a running server. Prints the new playlist id on success."
    ),
)
@click.argument("slug")
@click.option(
    "--week",
    type=int,
    required=True,
    help="Week number to generate (1-based, integer ≥ 1).",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help=(
        "Override the random seed. Default: engine-internal seed "
        "(None passes through to `engine.generator.generate`, which "
        "derives one from the run state). Matches the API endpoint's "
        "`GenerateRequest.seed` default."
    ),
)
@click.pass_context
def generate_cmd(
    ctx: click.Context, slug: str, week: int, seed: int | None
) -> None:
    """Run the engine pipeline against an installed app-home's DB.

    Mirrors `api/routers/generate.py:generate_playlist` step-for-step —
    same load_pools / load_alias_map / load_excluded_mix_ids /
    load_previous_playlist / load_cursor_positions sequence, same
    `save_playlist` + `advance_cursors` post-call. The CLI is a thin
    wrapper, NOT a parallel implementation, so engine bug fixes (and
    bugs!) land here automatically.

    Errors that match the API endpoint's HTTPException paths:
      * unknown station slug → exit 1, message "station ... not found"
      * template parse error → exit 1, message from TemplateError
      * missing source pool → exit 1, message from ValueError

    Week < 1 is rejected by Click's int conversion + the explicit
    check below — mirrors the GenerateRequest pydantic `ge=1`.
    """
    if week < 1:
        click.echo("Error: --week must be ≥ 1.", err=True)
        ctx.exit(1)

    from station_studio.bootstrap import bootstrap
    bootstrap(echo=click.echo)

    # Lazy: full engine import chain (sqlalchemy, generator, etc).
    from sqlalchemy.exc import IntegrityError

    from engine.database import make_engine, make_sessionmaker
    from engine.generator import generate as engine_generate
    from engine.models import Base, Station
    from engine.persistence import (
        advance_cursors, load_alias_map, load_cursor_positions,
        load_excluded_mix_ids, load_pools, load_previous_playlist,
        max_mix_exclusion_window, save_playlist,
    )
    from engine.rules import get_rule_value
    from engine.template import TemplateError, parse_template

    db_engine = make_engine()
    Base.metadata.create_all(db_engine)
    SessionLocal = make_sessionmaker(db_engine)

    with SessionLocal() as db:
        station = db.query(Station).filter(Station.slug == slug).first()
        if station is None:
            click.echo(f"Error: station '{slug}' not found.", err=True)
            ctx.exit(1)

        try:
            template = parse_template(station.template)
        except TemplateError as e:
            click.echo(f"Error: stored template is invalid: {e}", err=True)
            ctx.exit(1)

        try:
            pools = load_pools(db, template)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            ctx.exit(1)

        # t2-review 🟡-1: resolve `artist_dedup_window` and pass it as
        # `weighted_window_default` to the engine — same as the API
        # endpoint does (§14.29). Without this, a station with a
        # `weighted` block + an `artist_dedup_window` rule would
        # generate different playlists from CLI vs API for the same
        # week. The CLI claims thin-wrapper status — drift here
        # invalidates that claim.
        artist_dedup_window = get_rule_value(
            db, station_id=station.id, key="artist_dedup_window",
        )
        mix_exclusion_weeks = get_rule_value(
            db, station_id=station.id, key="mix_exclusion_weeks",
        )
        cursor_advance_step = get_rule_value(
            db, station_id=station.id, key="cursor_advance_step",
        )
        cursor_reset_cycle = get_rule_value(
            db, station_id=station.id, key="cursor_reset_cycle",
        )

        excluded_mix_ids = load_excluded_mix_ids(
            db, station_id=station.id,
            window=max_mix_exclusion_window(
                template, default=mix_exclusion_weeks,
            ),
        )
        alias_map = load_alias_map(db)
        previous_playlist = load_previous_playlist(
            db, station_id=station.id, current_week=week,
        )
        named_cursor_positions = None
        if template.cursor.persist:
            named_cursor_positions = load_cursor_positions(
                db, station_id=station.id, template=template,
            )

        try:
            result = engine_generate(
                template, pools,
                week=week, seed=seed,
                excluded_mix_ids=excluded_mix_ids,
                alias_map=alias_map,
                previous_playlist=previous_playlist,
                # t2-review 🟡-1: was missing — now matches the API.
                weighted_window_default=artist_dedup_window,
                named_cursor_positions=named_cursor_positions,
            )
        # t2-review 🟢-1: broaden the catch from `ValueError` to
        # `Exception` — same as the API's `except Exception` →
        # HTTPException(400). Mid-generate failures from malformed
        # templates / corrupt DB rows surface as a clean exit-1 +
        # readable error instead of a Python stack trace.
        except Exception as e:  # noqa: BLE001 — match API surface
            click.echo(f"Error: generation failed: {e}", err=True)
            ctx.exit(1)

        # t2-review 🟡-4: handle the concurrent-regen race the same way
        # the API does (HTTP 409 → CLI exit 1 + retry message). Without
        # this, two `station-studio generate` processes racing the same
        # station+week — exactly the cron-misconfiguration case t2's
        # design rationale calls out — get a SQLAlchemy stack trace.
        try:
            playlist = save_playlist(
                db, station=station, result=result,
                week=week, template=template,
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            click.echo(
                "Error: a playlist for this station and week was "
                "concurrently generated; retry.",
                err=True,
            )
            ctx.exit(1)

        # t2-review 🟡-2: advance_cursors ONLY when the template opts
        # into persistent cursors. The API endpoint has this guard;
        # the CLI was calling unconditionally, doing work that didn't
        # belong for stateless templates (Rebirth Classics et al).
        # t2-review 🟡-3: pass raw rule values (None or int) so
        # `advance_cursors` applies its own internal default. The
        # previous `or 120 / or 0` form silently turned a user-set
        # rule of 0 into 120 — exactly the legitimate-zero footgun.
        if template.cursor.persist:
            advance_cursors(
                db, station_id=station.id, template=template,
                pools=pools,
                advance_step=cursor_advance_step,
                reset_cycle=cursor_reset_cycle,
                generation_count=week - 1,
            )
            db.commit()

        click.echo(
            f"Generated playlist {playlist.id} for '{slug}' week {week} "
            f"({len(result.picks)} slots)."
        )


@main.command(
    name="export",
    help=(
        "Export a generated playlist as iTunes XML. Default: writes to "
        "stdout (pipe-friendly). With `--output PATH`, writes to a file. "
        "With `--to-disk`, writes to the configured export directory "
        "({export_dir}/{station-slug}/{week:03d}.xml)."
    ),
)
@click.argument("playlist_id", type=int)
@click.option(
    "--output", "-o",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help="Write the XML to this file path (creates parent dirs).",
)
@click.option(
    "--to-disk",
    is_flag=True,
    default=False,
    help=(
        "Write to the configured exports dir using the standard layout "
        "{exports}/{slug}/{week:03d}.xml. Mutually exclusive with --output."
    ),
)
@click.pass_context
def export_cmd(
    ctx: click.Context,
    playlist_id: int,
    output: Path | None,
    to_disk: bool,
) -> None:
    """Wraps `engine.exporter.export_playlist` (in-memory bytes) or
    `engine.exporter.export_playlist_to_disk` (configured-dir layout).

    The default (stdout) path is the pipeable form — `station-studio
    export 42 > playlist.xml` works the same as `--output playlist.xml`,
    AND composes with shell redirection / `wc` / `xmllint` / etc.

    --output and --to-disk are mutually exclusive (different sinks).
    Passing both is a usage error (exit 1) — Click can't natively
    express the constraint, so check explicitly.
    """
    if output is not None and to_disk:
        click.echo(
            "Error: --output and --to-disk are mutually exclusive.",
            err=True,
        )
        ctx.exit(1)

    from station_studio.bootstrap import bootstrap
    bootstrap(echo=click.echo)

    import sys

    from engine.database import make_engine, make_sessionmaker
    from engine.exporter import export_playlist, export_playlist_to_disk
    from engine.models import Base

    db_engine = make_engine()
    Base.metadata.create_all(db_engine)
    SessionLocal = make_sessionmaker(db_engine)

    try:
        with SessionLocal() as db:
            if to_disk:
                target = export_playlist_to_disk(db, playlist_id)
                click.echo(f"Wrote {target}")
                return
            xml_bytes = export_playlist(db, playlist_id)
    except ValueError as e:
        # `Playlist {id} not found` lands here.
        click.echo(f"Error: {e}", err=True)
        ctx.exit(1)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(xml_bytes)
        click.echo(f"Wrote {output} ({len(xml_bytes)} bytes)")
        return

    # Default: stdout. Use the buffer to write bytes (Click's
    # `click.echo` is text-mode; iTunes XML is utf-8 bytes, but writing
    # to sys.stdout.buffer keeps the exact byte sequence the on-disk
    # path would produce — bit-for-bit identical between `--output
    # foo.xml` and `> foo.xml`).
    sys.stdout.buffer.write(xml_bytes)


@main.command(
    name="backup",
    help=(
        "Tar up the app-home (DB, stations, exports, anything else under "
        "$STATION_STUDIO_HOME). Writes a timestamped .tar.gz under the "
        "home's `backups/` subdir by default; override with --output."
    ),
)
@click.option(
    "--output", "-o",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    default=None,
    help=(
        "Output path for the backup archive. Default: "
        "$STATION_STUDIO_HOME/backups/station-studio-YYYYMMDD-HHMMSS.tar.gz"
    ),
)
@click.pass_context
def backup_cmd(ctx: click.Context, output: Path | None) -> None:
    """Tarball the whole app-home for off-box backup.

    Strategy: stream-write a `tar.gz` containing every file under
    `$STATION_STUDIO_HOME` EXCEPT the `backups/` subdir itself (would
    cause infinite recursion + bloat — older backups don't belong
    inside newer ones). The archive paths are relative to the home,
    so restore is `tar xzf` into any empty STATION_STUDIO_HOME of the
    user's choice (same machine, new machine, into a docker volume).

    Output path always lives OUTSIDE the recursed-into tree (under
    backups/, which is excluded) so even when --output points inside
    the home, we don't tar our own output mid-write.
    """
    from station_studio.bootstrap import bootstrap
    bootstrap(echo=click.echo)

    import datetime as _dt
    import tarfile as _tarfile

    from station_studio.home import get_home

    home = get_home()
    if not home.is_dir():
        click.echo(
            f"Error: app-home {home} doesn't exist — nothing to back up.",
            err=True,
        )
        ctx.exit(1)

    if output is None:
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backups_dir = home / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        output = backups_dir / f"station-studio-{ts}.tar.gz"
    else:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

    # The exclude filter — keeps `backups/` (recursion-safety) AND
    # any pyc/cache crud out. Tarfile's `filter=` is called per
    # member; returning None drops it.
    def _filter(tarinfo: _tarfile.TarInfo) -> _tarfile.TarInfo | None:
        # Member name is relative to `arcname` (set on add below).
        # Drop the `backups/` subtree + bytecode caches.
        parts = Path(tarinfo.name).parts
        if "backups" in parts:
            return None
        if "__pycache__" in parts:
            return None
        if tarinfo.name.endswith(".pyc"):
            return None
        return tarinfo

    with _tarfile.open(output, "w:gz") as tar:
        # arcname='.' keeps paths relative to the home — restore is
        # `tar xzf foo.tar.gz -C $STATION_STUDIO_HOME` and members land
        # back where they came from.
        tar.add(str(home), arcname=".", filter=_filter)

    size = output.stat().st_size
    click.echo(f"Wrote {output} ({size:,} bytes)")


if __name__ == "__main__":
    main()
