"""Station Studio as an app you open, rather than a command you run.

What the downloaded Mac and Windows apps run (see packaging/), and what
`station-studio app` runs from a terminal. It starts the same server as
`station-studio serve` on 127.0.0.1:8001 and opens it in the browser.
Opening the app again while it runs just opens the browser again. The
page gets a small bar with Quit and, in the downloaded apps, a notice
with Update now when a newer version is out.

The data folder is the usual one (~/.station-studio, or
$STATION_STUDIO_HOME), so the app, `station-studio serve` and updates
all share the same stations, pools, playlists and exports.
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from station_studio import __version__, selfupdate, updates
from station_studio.home import get_home

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8001
# station-studio://start starts Station Studio without opening a window:
# the window asking (the installed app opened from the Dock while Station
# Studio wasn't running, see web/static/offline.js) is already open. The
# Mac app registers it in its Info.plist, the Windows setup in the
# registry (packaging/).
URL_SCHEME = "station-studio"
# Sent with every request that changes something, so another website open
# in the browser can't (browsers won't send a custom header cross-site
# without a CORS go-ahead, which this server never gives).
API_HEADER = "X-Station-Studio"

# Talking to ourselves on 127.0.0.1 must never go through a proxy: a
# system proxy without a localhost exception would hide the running copy.
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str, timeout: float = 2):
    try:
        with _local.open(url, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:
        return None


def running_instance(base: str) -> dict | None:
    """What's answering at `base`: the app's own state, {"service":
    "station-studio"} for a Station Studio started some other way
    (`serve`, Docker), or None when nothing is."""
    state = _get(base + "api/app")
    if isinstance(state, dict) and state.get("service") == "station-studio":
        return state
    # Versions before the app answer only the API.
    meta = _get(base + "api/meta")
    if isinstance(meta, dict) and "strategies" in meta:
        return {"service": "station-studio"}
    return None


def outdated(state: dict) -> bool:
    """True when the app answering should make way for this one: it's an
    older version, left running after an update or by an earlier copy."""
    return bool(state.get("app")) and updates.is_newer(__version__, str(state.get("version", "")))


def quit_instance(base: str) -> bool:
    """Ask the app at `base` to quit. True once it has gone."""
    req = urllib.request.Request(base + "api/app/quit", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", API_HEADER: "1"})
    try:
        _local.open(req, timeout=10).close()
    except Exception as exc:
        logger.warning("Could not ask the older Station Studio to quit: %s", exc)
        return False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if running_instance(base) is None:
            return True
        time.sleep(0.2)
    return False


def wait_for(base: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if running_instance(base) is not None:
            return True
        time.sleep(0.2)
    return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            # As uvicorn does: connections left over from a copy that just
            # quit (after an update, say) mustn't count as the port in use.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _browser_tab(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False, timeout=30)
    else:
        webbrowser.open(url)


def open_browser(url: str) -> None:
    """Show Station Studio: in a window of its own when Chrome, Edge or
    Brave is installed (as Station Player does), else a browser tab.
    STATION_STUDIO_TAB=1 always uses a tab."""
    from station_studio.window import open_ui

    try:
        how = open_ui(url, get_home(), own_window=not os.environ.get("STATION_STUDIO_TAB"), tab=_browser_tab)
        logger.info("Opened %s in a %s", url, how)
    except Exception as exc:
        logger.warning("Could not open the browser at %s: %s", url, exc)


def _log_to_file() -> None:
    """The downloaded app has no terminal, so keep a log beside the data."""
    home = get_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            home / "station-studio.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _mac_app_itself() -> bool:
    """The downloaded Mac app (not its helper): it only ever hands over,
    since while it lives macOS sends later clicks on the app to it rather
    than starting it again."""
    return selfupdate.bundled() and sys.platform == "darwin" and Path(sys.executable).name != selfupdate.MAC_HELPER


def _start_mac_helper(port: int) -> None:
    helper = Path(sys.executable).with_name(selfupdate.MAC_HELPER)
    subprocess.Popen([str(helper), "--no-browser", "--port", str(port)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})


class DesktopState:
    """What the app's own routes need: how to stop the server, the
    updater and the newest version known."""

    def __init__(self, port: int, stop, kind: str | None = None, cache: Path | None = None,
                 fetch_latest=None) -> None:
        self.port = port
        self._stop = stop
        self.kind = kind if kind is not None else selfupdate.install_kind()
        self.cache = cache or get_home() / "update-check.json"
        self._fetch_latest = fetch_latest
        self.latest: str | None = None
        self.updater = selfupdate.Updater(self.quit, port=port, kind=self.kind or "none")

    def quit(self) -> None:
        # A moment's grace so the request that asked gets its answer.
        timer = threading.Timer(0.3, self._stop)
        timer.daemon = True
        timer.start()

    def check_for_updates(self, force: bool = False) -> str | None:
        if not self.kind:
            return None
        self.latest = updates.latest_version(self.cache, fetch=self._fetch_latest, force=force)
        return self.latest

    def keep_checking(self) -> None:
        def loop() -> None:
            while True:
                try:
                    self.check_for_updates()
                except Exception:  # never take the app down over a version check
                    logger.exception("Version check failed")
                time.sleep(60 * 60)  # the cache keeps it to once a day
        threading.Thread(target=loop, name="update-check", daemon=True).start()

    def state(self) -> dict:
        newer = self.latest if updates.is_newer(self.latest, __version__) else None
        return {
            "service": "station-studio",
            "app": True,
            "version": __version__,
            "kind": self.kind,
            "can_update": self.kind in ("mac", "windows"),
            "latest": newer,
            "release_page": selfupdate.RELEASE_PAGE.format(version=newer) if newer else None,
            "update": self.updater.status(),
            "home": str(get_home()),
        }


class UpdateRequest(BaseModel):
    version: str


def attach_routes(app, desk: DesktopState) -> None:
    """Add /api/app (state), /api/app/update and /api/app/quit to the
    FastAPI app, ahead of the page's catch-all route."""
    router = APIRouter(tags=["app"])

    def guard(request: Request) -> None:
        if request.headers.get(API_HEADER) != "1":
            raise HTTPException(403, "Missing the Station Studio header")
        host = (request.headers.get("host") or "").lower()
        if host not in (f"127.0.0.1:{desk.port}", f"localhost:{desk.port}"):
            raise HTTPException(403, "Only from this computer")

    @router.get("/api/app")
    def app_state() -> dict:
        return desk.state()

    @router.post("/api/app/update")
    def app_update(body: UpdateRequest, request: Request) -> dict:
        guard(request)
        try:
            desk.updater.start(body.version)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return desk.state()

    @router.post("/api/app/quit")
    def app_quit(request: Request) -> dict:
        guard(request)
        logger.info("Quit from the page")
        desk.quit()
        return {"ok": True}

    # Ahead of everything else: the page's catch-all would answer first.
    app.router.routes[0:0] = list(router.routes)


def serve(port: int, show: bool) -> int:
    from station_studio.bootstrap import bootstrap

    home = bootstrap(echo=logger.info).home
    # Exports go in the data folder's exports/ (where Station Player looks
    # for them) unless set otherwise on the page, not wherever the app
    # happened to start: "/" on a Mac, the install folder on Windows.
    os.environ.setdefault("STATION_STUDIO_EXPORT_DIR", str(home / "exports"))
    os.chdir(home)

    import uvicorn

    from api.main import app

    base = f"http://127.0.0.1:{port}/"
    # Plain-Python choices throughout, so the downloaded app needs no
    # optional speedups; workers=1 as for `serve` (see cli.py).
    config = uvicorn.Config(app, host="127.0.0.1", port=port, workers=1, loop="asyncio", http="h11",
                            ws="none", log_config=None, access_log=False)
    server = uvicorn.Server(config)

    def stop() -> None:
        server.should_exit = True

    desk = DesktopState(port, stop)
    attach_routes(app, desk)
    desk.keep_checking()

    if show:
        def show_when_ready() -> None:
            if wait_for(base, 60):
                open_browser(base)
        threading.Thread(target=show_when_ready, name="open-browser", daemon=True).start()

    logger.info("Station Studio %s on %s (data in %s)", __version__, base, get_home())
    print(f"Station Studio {__version__} on {base}  (Ctrl+C, or Quit on the page, to stop)")
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:  # uvicorn exits when it can't listen
        return int(exc.code or 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="station-studio app",
        description="Open Station Studio in the browser, starting it if it isn't running.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to use (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--version", action="version", version=f"station-studio {__version__}")
    if argv is None:
        argv = sys.argv[1:]
    # Older macOS hands the app a process id.
    argv = [a for a in argv if not a.startswith("-psn_")]
    if any(a.lower().startswith(f"{URL_SCHEME}:") for a in argv):
        argv = [a for a in argv if not a.lower().startswith(f"{URL_SCHEME}:")] + ["--no-browser"]
    args = parser.parse_args(argv)

    if selfupdate.bundled():
        _log_to_file()
    elif not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    base = f"http://127.0.0.1:{args.port}/"
    show = not args.no_browser
    state = running_instance(base)
    if state and selfupdate.bundled() and outdated(state):
        logger.info("Replacing the older Station Studio %s running on %s", state.get("version"), base)
        if quit_instance(base):
            state = None
    if state:
        if not state.get("app"):
            logger.info("A Station Studio started another way (serve or Docker) is already on %s", base)
        print(f"Station Studio is already running on {base}")
        if show:
            open_browser(base)
        return 0

    if _mac_app_itself():
        _start_mac_helper(args.port)
        # Generous: the first start of a newly downloaded app is slow
        # while macOS checks it, more so on older Macs.
        if not wait_for(base, 90):
            logger.error("Station Studio did not start; see the log above")
            return 1
        if show:
            open_browser(base)
        return 0

    if not port_free(args.port):
        # Two clicks at once: the other copy won the port, so show that one.
        if wait_for(base, 10):
            if show:
                open_browser(base)
            return 0
        msg = f"Something else is using port {args.port}, so Station Studio can't start."
        logger.error(msg)
        print(msg, file=sys.stderr)
        return 1

    return serve(args.port, show)


def run(argv: list[str] | None = None) -> int:
    """main(), with anything unexpected written to the log: the
    downloaded app has no terminal to show it in."""
    try:
        return main(argv)
    except Exception:
        logger.exception("Station Studio stopped with an error")
        raise


if __name__ == "__main__":
    sys.exit(run())
