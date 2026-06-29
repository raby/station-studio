"""§16.13.t3 — real-uvicorn subprocess integration test.

The other `test_serve_*` tests in `test_cli.py` mock `uvicorn.run` and
assert dispatch kwargs (workers=1, host, port). That gives confidence
at the boundary BETWEEN our code and uvicorn, but not at the
boundary uvicorn itself crosses (starts the event loop, binds the
socket, dispatches requests through FastAPI lifespan, etc).

`make wheel-smoke` and `make image-smoke` DO test the real path —
but they're shell, not pytest, and they require a built wheel.
This test closes that loop INSIDE the standard pytest run:

  * `subprocess.Popen([python, '-m', 'station_studio.cli', 'serve', ...])`
  * Poll a TCP socket until uvicorn binds (the only signal we get
    without parsing log lines — accept-on-listen is sufficient).
  * Curl /api/stations to confirm FastAPI lifespan completed +
    routes are reachable.
  * SIGTERM + wait + assert clean exit (no zombie processes).

The test is INHERENTLY slow (~2-5s) and has external-network shape
(localhost socket). Marked with `@pytest.mark.slow` so the per-PR
test job can opt out if needed via `pytest -m 'not slow'`; default
runs include it so the gate is the default behavior, not opt-in.

If a future uvicorn release renames `workers=` to something else,
THIS test catches it; the mocked test in test_cli.py would still
pass against the rename.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


# Module-level mark — every test in this file is slow / subprocess-y.
pytestmark = pytest.mark.slow


def _free_tcp_port() -> int:
    """Pick a free TCP port by binding ephemeral + reading + closing.

    Race-prone in principle (another process could bind between
    close + uvicorn's bind), but in practice the window is microseconds
    and CI runners don't have competing localhost binders. The
    alternative — passing port=0 to uvicorn — doesn't fit because
    we need the port number to curl back.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """Block until `host:port` accepts a TCP connection, or `timeout`
    elapses. Returns True iff bound. Used as the readiness probe —
    uvicorn binds the socket BEFORE the FastAPI lifespan runs, so a
    successful connect means the server is at least listening; the
    `/api/stations` curl below proves the lifespan completed too.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)
    return False


@pytest.fixture
def isolated_subprocess_home(tmp_path, monkeypatch):
    """Same shape as `isolated_cli_home` in test_cli.py but designed
    for subprocess invocation: returns the env dict the subprocess
    needs (not monkeypatch'd into the current process).
    """
    home = tmp_path / "ss-subprocess-home"
    env = os.environ.copy()
    env["STATION_STUDIO_HOME"] = str(home)
    env.pop("STATION_STUDIO_DB_URL", None)
    env.pop("STATION_STUDIO_STATIONS_DIR", None)
    return env, home


def test_serve_real_uvicorn_binds_and_serves_api_stations(
    isolated_subprocess_home, tmp_path
) -> None:
    """t3 — `station-studio serve` actually starts a real uvicorn
    process, binds, and serves /api/stations with HTTP 200. Closes
    the gap left by the mocked dispatch tests.

    Failure modes this catches that the mocked tests don't:
      * uvicorn changes its `run()` kwarg names
      * Bootstrap throws on a fresh app-home in a real-spawn context
      * api.main lifespan errors at startup (e.g. alembic upgrade
        crashing on a fresh DB, scripts.seed_stations import failure)
      * FastAPI router registration breaks at app construction
    """
    env, home = isolated_subprocess_home
    port = _free_tcp_port()

    log_path = tmp_path / "serve.log"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "station_studio.cli",
            "serve",
            "--host", "127.0.0.1",
            "--port", str(port),
        ],
        env=env,
        stdout=open(log_path, "wb"),
        stderr=subprocess.STDOUT,
    )

    try:
        # Step 1: TCP socket accepts (uvicorn bound).
        assert _wait_for_port("127.0.0.1", port, timeout=20.0), (
            f"server did not bind on port {port} within 20s.\n"
            f"--- serve.log ---\n{log_path.read_text(errors='replace')}"
        )

        # Step 2: HTTP request succeeds (FastAPI lifespan finished,
        # routes are mounted, /api/stations returns 200 application/json).
        # Use stdlib urllib so we don't add a test dep on `requests`.
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/stations", timeout=10.0
        ) as resp:
            assert resp.status == 200, f"status {resp.status}"
            assert resp.headers["Content-Type"].startswith(
                "application/json"
            ), resp.headers["Content-Type"]
            body = resp.read()
            # Auto-seed of bundled stations runs in lifespan; the
            # response should be a non-empty JSON array.
            assert body.startswith(b"["), body[:80]

        # Step 3: SPA root also serves text/html — but ONLY when a
        # static build is available. On a fresh CI checkout
        # `web/build/` doesn't exist (gitignored; only `make wheel`
        # populates it), and the bundled `station_studio/_bundled/web/`
        # isn't there either pre-pip-install. api/main.py's
        # `_resolve_static_dir()` returns None in that case + the
        # SPA mount doesn't attach, so `GET /` falls through to the
        # JSON service-identity blob (deliberate; see api/main.py
        # lines 197-205). The `make wheel-smoke` / `make image-smoke`
        # CI smoke targets DO build the SPA first and catch SPA
        # mount regressions in their own job. This test's purpose
        # is "uvicorn binds + /api/stations works" — the SPA check
        # is opportunistic.
        from api.main import STATIC_DIR  # noqa: PLC0415 — gated import
        if STATIC_DIR is not None:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=5.0
            ) as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"].startswith("text/html"), (
                    resp.headers["Content-Type"]
                )

        # App-home was actually created — proves bootstrap ran in
        # the subprocess + the env override stuck.
        assert (home / "station_studio.db").is_file()

    finally:
        # SIGTERM + 5s grace + SIGKILL fallback. Avoid leaving zombie
        # uvicorn processes in CI runners or on dev boxes.
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)


def test_serve_real_uvicorn_rejects_bad_port(
    isolated_subprocess_home, tmp_path
) -> None:
    """Negative case: spawning serve with port=0 (or some other invalid
    value the dispatcher rejects) exits non-zero quickly. Belt-and-
    suspenders against a regression where bad arguments hang the
    process indefinitely.

    Click's int parser accepts negative numbers; we pass --port -1.
    uvicorn raises OSError on bind. The subprocess exits with non-zero
    in <2s on the failed bind.
    """
    env, _home = isolated_subprocess_home

    log_path = tmp_path / "serve_badport.log"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "station_studio.cli",
            "serve",
            "--host", "127.0.0.1",
            "--port", "-1",
        ],
        env=env,
        stdout=open(log_path, "wb"),
        stderr=subprocess.STDOUT,
    )

    try:
        rc = proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail(
            f"serve --port -1 hung past 10s.\n"
            f"--- serve_badport.log ---\n{log_path.read_text(errors='replace')}"
        )

    assert rc != 0, (
        f"serve --port -1 exited 0 (expected non-zero).\n"
        f"--- serve_badport.log ---\n{log_path.read_text(errors='replace')}"
    )
