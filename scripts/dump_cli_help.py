"""§16.15 c5 — dump the `station-studio` CLI's --help output to a
markdown page for the docs site.

Invoked by `make docs-cli` before `make docs-build` so the docs
site's `reference/cli.md` reflects the running CLI's surface.
Drift between the docs and the CLI is impossible by construction —
every push-to-main re-runs the dump.

Approach: invoke the CLI via Click's `CliRunner.invoke(..., prog_name=
"station-studio")`. Click owns `--help` formatting; the runner lets
us pin `prog_name` to what end-users see (`station-studio`) instead
of `python -m station_studio.cli` (the subprocess invocation form).
Output is byte-identical to what a user invoking the installed
script gets.

To prevent the CLI's import path from triggering the same DB-
side-effect bomb fixed in §16.15 c4-review R1 (api.deps runs
`Base.metadata.create_all()` at module load), this module
sets `STATION_STUDIO_DB_URL=sqlite:///:memory:` +
`STATION_STUDIO_SKIP_AUTO_SEED=1` BEFORE importing the CLI.

Output is committed-disabled (gitignored — it's a build artifact
regenerated on every docs build).
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

# §16.15 c4-review R1 — neutralise api.deps' module-load
# side effects (DB creation) by pointing at in-memory SQLite
# BEFORE the CLI import below transitively imports api/.
os.environ.setdefault("STATION_STUDIO_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("STATION_STUDIO_SKIP_AUTO_SEED", "1")

from click.testing import CliRunner  # noqa: E402
from station_studio.cli import main as cli_main  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_PATH = _REPO_ROOT / "docs" / "reference" / "cli.md"

# Subcommands to dump --help for, in the order they should appear
# on the page. Mirrors the Click group's registration order in
# station_studio/cli.py (top → bottom: serve, import, seed,
# generate, export, backup). The top-level `--help` lands first.
_SUBCOMMANDS: tuple[str, ...] = (
    "serve",
    "import",
    "seed",
    "generate",
    "export",
    "backup",
)


_RUNNER = CliRunner()


def _invoke(args: list[str]) -> str:
    """Invoke the CLI with `prog_name='station-studio'` so the
    rendered Usage line matches what end-users see in their
    terminal (not the in-tree `python -m station_studio.cli`
    invocation form). Click formats `--help` to a fixed 80
    columns by default when no TTY is present — that's stable
    across CI / local / contributor terminals, so no env
    override needed."""
    result = _RUNNER.invoke(cli_main, args, prog_name="station-studio")
    if result.exit_code != 0:
        raise RuntimeError(
            f"CLI invocation 'station-studio {' '.join(args)}' "
            f"exited {result.exit_code}: {result.output!r}",
        )
    return result.output


def _fence(invocation: str, body: str) -> str:
    """Render an invocation + its captured --help output as
    a fenced code block. Backticks inside `body` are passed
    through verbatim — Click's --help text never contains
    triple-backticks. The fence uses ``text`` for syntax
    highlighting since the output isn't shell + isn't a
    specific language."""
    return f"## `{invocation}`\n\n```text\n{body.rstrip()}\n```\n"


def main() -> int:
    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []

    # Group-level --help first.
    sections.append(_fence("station-studio --help", _invoke(["--help"])))

    # Each subcommand's --help next.
    for cmd in _SUBCOMMANDS:
        sections.append(
            _fence(f"station-studio {cmd} --help", _invoke([cmd, "--help"]))
        )

    header = textwrap.dedent(
        """\
        # CLI reference

        Auto-generated from `station-studio --help` at docs-build
        time by `scripts/dump_cli_help.py`. Drift between this
        page and the running CLI is impossible by construction —
        every push-to-main rebuilds it from
        [`station_studio/cli.py`][cli-src].

        [cli-src]: https://github.com/raby/station-studio/blob/main/station_studio/cli.py

        """
    )

    _OUT_PATH.write_text(header + "\n".join(sections), encoding="utf-8")
    print(
        f"dump_cli_help: wrote {_OUT_PATH} "
        f"(1 group + {len(_SUBCOMMANDS)} subcommands)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
