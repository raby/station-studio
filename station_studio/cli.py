"""§16.13 c1 — `station-studio` CLI entry point.

D5 scope: `serve` + `import` + `seed`. `serve` is fully working today
(starts uvicorn against `api.main:app`, hardcoded `workers=1` per the
§16.11 in-memory job-store constraint). `import` and `seed` are stubs
in c1 — they need the app-home + first-run bootstrap that c3 wires in.

The full design list (`serve` + `import` + `generate` + `export` +
`backup` + `seed`) is queued as §16.13.t2; this slice ships the
install-essentials only.
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


if __name__ == "__main__":
    main()
