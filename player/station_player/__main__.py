"""`station-player` command: start the local server and open the UI."""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from station_player import __version__
from station_player.config import ConfigStore, get_home
from station_player.window import open_ui
from station_player.desktop import URL_SCHEME, Desktop, app_executable, bundled
from station_player.library import AUDIO_EXTENSIONS
from station_player.server import API_HEADER, PlayerApp, make_server


def _folder_contents(folder: Path) -> tuple[bool, bool]:
    """(has .xml, has audio) anywhere below folder; stops once both seen."""
    has_xml = has_audio = False
    for p in folder.rglob("*"):
        suffix = p.suffix.lower()
        if suffix == ".xml":
            has_xml = True
        elif suffix in AUDIO_EXTENSIONS:
            has_audio = True
        if has_xml and has_audio:
            break
    return has_xml, has_audio


def _classify(raw: str) -> list[tuple[str, str]] | str:
    """[(kind, path)] for a source given on the command line, or a reason
    to skip it. Folders holding XML at any depth (station-studio writes
    exports/<station>/<week>.xml) are playlist sources; folders holding
    audio are music folders. An iTunes folder is both."""
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        return "not found"
    if p.is_file():
        if p.suffix.lower() in AUDIO_EXTENSIONS:
            return [("open", str(p))]  # play it now (Open With)
        if p.suffix.lower() != ".xml":
            return "pass an audio file, an iTunes .xml file or a folder"
        return [("playlist", str(p))]
    try:
        has_xml, has_audio = _folder_contents(p)
    except OSError as exc:
        return str(exc)
    kinds = []
    if has_xml:
        kinds.append(("playlist", str(p)))
    if has_audio or not has_xml:
        kinds.append(("music", str(p)))
    return kinds


# Talking to ourselves on 127.0.0.1 must never go through a proxy: a
# system proxy without a localhost exception would hide the running copy.
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _running_instance(url: str) -> bool:
    """True when a Station Player already answers at `url`."""
    try:
        with _local.open(url + "api/state", timeout=2) as resp:
            return "version" in json.load(resp)
    except Exception:
        return False


def _send_open(url: str, files: list[str]) -> bool:
    """Ask the running copy to play files. True when a player window is
    open to play them (so no new one is needed)."""
    req = urllib.request.Request(
        url + "api/open", data=json.dumps({"paths": files}).encode(),
        headers={"Content-Type": "application/json", API_HEADER: "1"}, method="POST")
    try:
        with _local.open(req, timeout=30) as resp:
            return bool(json.load(resp).get("page_open"))
    except Exception as exc:
        print(f"Could not open {', '.join(files)}: {exc}", file=sys.stderr)
        return True  # nothing to show


def _send_station(url: str, station: str) -> bool:
    """Ask the running copy to play a saved station. True when a player
    window is open to play it."""
    req = urllib.request.Request(
        url + "api/stations/start", data=json.dumps({"url": station}).encode(),
        headers={"Content-Type": "application/json", API_HEADER: "1"}, method="POST")
    try:
        with _local.open(req, timeout=30) as resp:
            return bool(json.load(resp).get("page_open"))
    except urllib.error.HTTPError as exc:
        try:
            reason = json.load(exc).get("error") or exc.reason
        except Exception:
            reason = exc.reason
        print(f"Could not play {station}: {reason}", file=sys.stderr)
        logging.warning("Could not play %s: %s", station, reason)
    except Exception as exc:
        print(f"Could not play {station}: {exc}", file=sys.stderr)
    return False


def _send_sources(url: str, sources: list[tuple[str, str]]) -> None:
    """Hand sources to the copy that's already running, so its settings
    (which it will save again) include them."""
    for kind, path in sources:
        if kind == "open":
            continue
        req = urllib.request.Request(
            url + "api/sources", data=json.dumps({"kind": kind, "path": path}).encode(),
            headers={"Content-Type": "application/json", API_HEADER: "1"}, method="POST")
        try:
            _local.open(req, timeout=120).close()
        except Exception as exc:
            print(f"Could not add {path}: {exc}", file=sys.stderr)


_CHILD_ENV = "STATION_PLAYER_CHILD"


def _detach_needed() -> bool:
    """macOS opens an app only once while its process lives, so a second
    click on the shortcut would do nothing. There the shortcut starts the
    player as a separate process and exits, and every click gets through."""
    return sys.platform == "darwin" and not os.environ.get(_CHILD_ENV)


def _app_on_mac() -> bool:
    """The downloaded Mac app itself (not its helper): it only ever hands
    over, since while it lives macOS sends later clicks on the app to it."""
    return bundled() and sys.platform == "darwin" and app_executable() != Path(sys.executable)


def _misplaced() -> bool:
    """The Mac app opened from the disk image (or from Downloads, which
    macOS runs from a temporary copy): what it sets up stops working."""
    exe = str(sys.executable)
    return bundled() and sys.platform == "darwin" and (exe.startswith("/Volumes/") or "/AppTranslocation/" in exe)


def _start_detached(port: int, sources: list[str], url: str, station: str | None = None) -> bool:
    me = [str(app_executable())] if bundled() else [sys.executable, "-P", "-m", "station_player"]
    cmd = [*me, "--app", "--no-browser",
           "--port", str(port), *(["--station", station] if station else []), *sources]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, _CHILD_ENV: "1"})
    except OSError as exc:
        logging.error("Could not start Station Player: %s", exc)
        return False
    if _wait_for_instance(url, 20):
        return True
    logging.error("Station Player did not start; see the messages above")
    return False


def _wait_for_instance(url: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _running_instance(url):
            return True
        time.sleep(0.2)
    return False


def _refresh_shortcut(app) -> None:
    try:
        if app.desktop.refresh():
            logging.info("Updated the Station Player shortcut")
    except OSError as exc:
        logging.warning("Could not update the shortcut: %s", exc)


def _log_to_file() -> None:
    """Shortcuts start Station Player with no console, so keep a log."""
    home = get_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            home / "station-player.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _show(url: str, store: ConfigStore | None = None) -> None:
    store = store or ConfigStore()
    open_ui(url, store.home, own_window=store.config.own_window)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="station-player",
        description="Play station-studio playlists, local music folders and radio streams.",
    )
    parser.add_argument("sources", nargs="*", metavar="PATH",
                        help="iTunes XML files, folders of XMLs, or folders of audio to add")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--app", action="store_true",
                        help="started from a shortcut: log to a file, reuse a running copy")
    parser.add_argument("--station", metavar="URL",
                        help="play this saved station (what a station's desktop icon runs)")
    parser.add_argument("--add-shortcut", action="store_true",
                        help="add Station Player to your apps menu (and desktop), then exit")
    parser.add_argument("--start-at-login", action="store_true",
                        help="start Station Player in the background when you log in, then exit")
    parser.add_argument("--remove-shortcuts", action="store_true",
                        help="remove the shortcuts and the start-at-login entry, then exit")
    parser.add_argument("--version", action="version", version=f"station-player {__version__}")
    if argv is None:
        argv = sys.argv[1:]
    # The Mac app may be handed a process id by older macOS, and a
    # station-player: link (the offline page's Start button) asks for the
    # player to start without a window of its own.
    argv = [a for a in argv if not a.startswith("-psn_")]
    if any(a.lower().startswith(f"{URL_SCHEME}:") for a in argv):
        argv = [a for a in argv if not a.lower().startswith(f"{URL_SCHEME}:")] + ["--no-browser"]
    args = parser.parse_args(argv)
    if bundled():
        args.app = True  # the downloaded app has no terminal: log to a file, reuse a running copy

    if args.add_shortcut or args.start_at_login or args.remove_shortcuts:
        desktop = Desktop()
        try:
            if args.remove_shortcuts:
                desktop.remove_all()
                print("Removed Station Player's shortcuts.")
            if args.add_shortcut:
                desktop.set_shortcut(True)
                print("Added Station Player to your apps menu.")
            if args.start_at_login:
                desktop.set_login(True)
                print("Station Player will start when you log in.")
        except OSError as exc:
            print(f"Could not change shortcuts: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.app:
        _log_to_file()
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    sources: list[tuple[str, str]] = []
    for raw in args.sources:
        result = _classify(raw)
        if isinstance(result, str):
            print(f"Skipped {raw}: {result}", file=sys.stderr)
        else:
            sources.extend(result)

    opened = [path for kind, path in sources if kind == "open"]
    url = f"http://127.0.0.1:{args.port}/"

    def hand_over(store: ConfigStore | None = None) -> int:
        # Clicking the shortcut again (or starting from a terminal while
        # it runs at login) just brings up the copy that's running; files
        # from Open With play in its window, if one is open.
        _send_sources(url, sources)
        window_open = _send_open(url, opened) if opened else False
        if args.station:
            window_open = _send_station(url, args.station) or window_open
        if not args.no_browser and not window_open:
            _show(url, store)
        return 0

    if _running_instance(url):
        print(f"Station Player is already running on {url}")
        return hand_over()

    if args.app and (not args.no_browser and _detach_needed() or _app_on_mac()):
        if not _start_detached(args.port, args.sources, url, args.station):
            return 1
        if not args.no_browser:
            _show(url)
        return 0

    store = ConfigStore()
    added = False
    for kind, path in sources:
        if kind == "open":
            continue
        target = store.config.playlist_sources if kind == "playlist" else store.config.music_folders
        if path not in target:
            target.append(path)
            added = True
    if added:
        try:
            store.save()
        except OSError as exc:
            print(f"Could not save settings to {store.path}: {exc}", file=sys.stderr)
    app = PlayerApp(store, scan=False)
    if opened:
        app.open_files(opened)  # played as soon as the window opens
    if _misplaced():
        app.remote_commands.append({"cmd": "notice", "hold": True, "at": time.time(), "text":
                                    "Drag Station Player into your Applications folder and open it from there: "
                                    "opened from the download, start-at-login and desktop icons won't keep working."})
    if args.station:
        try:
            app.start_station(args.station, hold=True)  # likewise, however long it takes to open
        except LookupError as exc:
            print(f"Could not play {args.station}: {exc}", file=sys.stderr)
            logging.warning("Could not play %s: %s", args.station, exc)

    try:
        server = make_server(app, port=args.port)
    except OSError as exc:
        # Two clicks at once, or a click while the login copy starts:
        # the other copy won the port, so show that one.
        if args.app and _wait_for_instance(url, 5):
            return hand_over(store)
        print(f"Could not listen on port {args.port}: {exc}", file=sys.stderr)
        logging.error("Could not listen on port %s: %s", args.port, exc)
        return 1

    def report_scan() -> None:
        for err in app.library.errors:
            print(f"  ! {err}")
            logging.warning("%s", err)

    # Serve straight away; the library fills in as the scan finishes.
    app.scan_in_background(on_done=report_scan)
    app.keep_checking_for_updates()
    # The alarm opens a window if none is open when it goes off; so do
    # commands from the phone remote.
    app.show_window = lambda: _show(url, store)
    app.watch_alarm()
    app.apply_remote()
    # A shortcut made by an older version is brought up to date (for Open With).
    threading.Thread(target=_refresh_shortcut, args=(app,), daemon=True).start()
    print(f"Station Player {__version__} on {url}  (Ctrl+C or Quit in the app to stop)")
    print(f"Settings: {store.path}")
    if args.app:
        logging.info("Station Player %s started on %s", __version__, url)
    if not args.no_browser:
        _show(url, store)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.recorder.stop_all()
        if app.remote_server:
            app.remote_server.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
