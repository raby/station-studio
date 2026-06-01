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
def import_cmd(path: str | None, name: str | None, role: str) -> None:
    """Stub for the installed-user pool-import path.

    c3 wires this against the running server's `import_source(...)`
    helper using the app-home's DB. For now, the in-repo flow remains
    `make import-weekly-pools manifest=…` and the UI's /sources page.
    """
    click.echo(
        "station-studio import is queued for §16.13 c3 "
        "(app-home + DB bootstrap).\n"
        "In the meantime, use the /sources page in the web UI or the "
        "in-repo `make import-weekly-pools manifest=…` target.",
        err=True,
    )
    raise SystemExit(1)


@main.command(
    name="seed",
    help=(
        "(Re)copy the bundled example stations into your stations dir. "
        "[Stub — wired in c3 once the app-home lands.]"
    ),
)
def seed() -> None:
    """Stub for re-seeding example stations from the wheel's bundled copies.

    c3 wires this against the app-home's stations dir (D6: copy on first
    run; `seed` is the explicit re-run for "I deleted Classics, get it
    back").
    """
    click.echo(
        "station-studio seed is queued for §16.13 c3 "
        "(app-home + bundled-stations resolution).",
        err=True,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
