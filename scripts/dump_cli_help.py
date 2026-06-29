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

# §16.15 c5-review R1+R5 — defensive env override against a
# future regression where cli.py adopts eager imports of
# anything under api/ or engine.database. Today station_studio.cli
# imports lazily inside subcommand bodies — so this isn't
# addressing a present bomb — but HARD-OVERRIDE (not setdefault)
# so a contributor's exported `STATION_STUDIO_DB_URL` (e.g. a
# production path inherited from their shell for an unrelated
# task) can't sneak past this guard and let a docs build write
# to the real database the next time someone refactors cli.py.
os.environ["STATION_STUDIO_DB_URL"] = "sqlite:///:memory:"
os.environ["STATION_STUDIO_SKIP_AUTO_SEED"] = "1"

from click.testing import CliRunner  # noqa: E402
from station_studio.cli import main as cli_main  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT_PATH = _REPO_ROOT / "docs" / "reference" / "cli.md"


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
        # §16.15 c5-review R3+R6 — surface BOTH the captured
        # stdout AND the exception the runner caught
        # (CliRunner stores it on result.exception when
        # catch_exceptions is True, the default). Without the
        # exception object a future regression that raises at
        # dispatch time shows up as `exited 1: ''` in CI logs
        # with no hint at the root cause. Multi-line format
        # so CI logs stay readable instead of one wall of
        # \n-escaped repr.
        raise RuntimeError(
            f"CLI invocation 'station-studio {' '.join(args)}' "
            f"exited {result.exit_code}\n"
            f"--- captured output ---\n"
            f"{result.output}\n"
            f"--- exception ---\n"
            f"{result.exception!r}",
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

    # §16.15 c5-review R2 — discover subcommands from the Click
    # group itself rather than a hard-coded tuple. Click preserves
    # registration order in the .commands dict (Python 3.7+), so
    # iteration order matches cli.py's @cli.command sequence.
    # Self-discovering: any new subcommand added to cli.py
    # automatically shows up in the rendered docs.
    subcommands = list(cli_main.commands.keys())
    for cmd in subcommands:
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
        f"(1 group + {len(subcommands)} subcommands)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
