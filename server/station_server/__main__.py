"""`station-server` command: the app, or put one playlist on air directly.

    station-server                      # the app: stations, on air, play log
    station-server play week-12.xml --to shoutcast://my.server:8000
    station-server play week-12.xml --to icecast://source@my.server:8000/radio
    station-server play week-12.xml --to test.mp3        # record instead

The password comes from --password or $STATION_SERVER_PASSWORD (or the
URL). Ctrl+C stops. Type `s` and Enter to skip the playing track.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from station_server import __version__
from station_server.audio import FfmpegMissing, find_ffmpeg, has_mp3_encoder
from station_server.channel import Channel
from station_server.playlist import PlaylistError, load_queue
from station_server.senders import SenderError, StreamInfo, sender_from_url
from station_server.web import DEFAULT_PORT

PASSWORD_ENV = "STATION_SERVER_PASSWORD"
# station-server://start starts Station Server without opening a window:
# the window asking (the installed app opened from the Dock while Station
# Server wasn't running, see static/offline.js) is already open. The Mac
# app registers it in its Info.plist, the Windows setup in the registry.
URL_SCHEME = "station-server"
UPDATE_CHECK_EVERY = 60 * 60  # looks hourly; GitHub is asked at most daily


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="station-server", description="Broadcast station-studio playlists.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")

    app = sub.add_parser("app", help="run the app and open its window (the default)")
    app.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"local port for the page (default {DEFAULT_PORT})")
    app.add_argument("--no-window", action="store_true", help="don't open the window")
    app.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("remove-login", help="stop starting Station Server at login (the uninstaller runs this)")

    play = sub.add_parser("play", help="play a playlist to a server (or an MP3 file)")
    play.add_argument("xml", type=Path, help="station-studio export or iTunes XML")
    play.add_argument("--to", required=True, metavar="URL",
                      help="shoutcast://host:port[?sid=N], icecast://[user@]host:port/mount, or an .mp3 path")
    play.add_argument("--password", help=f"source password (default: ${PASSWORD_ENV}, or from --to)")
    play.add_argument("--playlist", help="which playlist, when the XML has several")
    play.add_argument("--music-folder", action="append", default=[], metavar="DIR",
                      help="where to look for tracks that have moved (repeatable)")
    play.add_argument("--remap", action="append", default=[], metavar="FROM=TO",
                      help="rewrite a path prefix, e.g. '/Users/rob/Music=D:/Music' (repeatable)")
    play.add_argument("--start", type=int, default=1, metavar="N", help="start at track N (default 1)")
    play.add_argument("--once", action="store_true", help="stop at the end instead of starting again")
    play.add_argument("--bitrate", type=int, default=128, help="MP3 bitrate in kbps (default 128)")
    play.add_argument("--no-level", action="store_true", help="turn off volume levelling")
    play.add_argument("--name", help="station name shown by the server (default: the playlist name)")
    play.add_argument("--genre", default="", help="station genre shown by the server")
    play.add_argument("--url", default="", help="station website shown by the server")
    play.add_argument("--public", action="store_true", help="ask the server to list the station in its directory")
    play.add_argument("--ffmpeg", help="path to ffmpeg (default: $STATION_SERVER_FFMPEG, then PATH)")
    play.add_argument("-v", "--verbose", action="store_true")

    check = sub.add_parser("check", help="check ffmpeg and read a playlist without broadcasting")
    check.add_argument("xml", type=Path, nargs="?")
    check.add_argument("--playlist")
    check.add_argument("--music-folder", action="append", default=[], metavar="DIR")
    check.add_argument("--remap", action="append", default=[], metavar="FROM=TO")
    check.add_argument("--ffmpeg")
    return p


def _remaps(pairs: list[str]) -> list[dict]:
    out = []
    for pair in pairs:
        src, sep, dst = pair.partition("=")
        if not sep or not src or not dst:
            raise SystemExit(f"--remap needs FROM=TO, got {pair!r}")
        out.append({"from": src, "to": dst})
    return out


def _ffmpeg(explicit: str | None) -> str:
    try:
        ffmpeg = find_ffmpeg(explicit)
    except FfmpegMissing as exc:
        raise SystemExit(str(exc))
    if not has_mp3_encoder(ffmpeg):
        raise SystemExit(f"{ffmpeg} has no MP3 encoder (libmp3lame); install a full ffmpeg build")
    return ffmpeg


def _queue(args):
    try:
        return load_queue(args.xml, args.music_folder, _remaps(args.remap), args.playlist)
    except PlaylistError as exc:
        raise SystemExit(str(exc))


def _report(queue) -> None:
    print(f'"{queue.name}": {len(queue.tracks)} tracks to play', end="")
    if queue.missing:
        print(f", {len(queue.missing)} missing (skipped):")
        for t in queue.missing[:20]:
            print(f"  - {t.artist + ' - ' if t.artist else ''}{t.name}  [{t.location}]")
        if len(queue.missing) > 20:
            print(f"  ...and {len(queue.missing) - 20} more")
    else:
        print()


def _check(args) -> int:
    ffmpeg = _ffmpeg(args.ffmpeg)
    print(f"ffmpeg: {ffmpeg} (MP3 encoder found)")
    if args.xml:
        _report(_queue(args))
    return 0


def _keyboard(channel: Channel) -> None:
    """`s` + Enter skips; end of input is ignored (running in the background)."""
    for line in sys.stdin:
        if line.strip().lower() in ("s", "skip"):
            channel.skip()


def _play(args) -> int:
    ffmpeg = _ffmpeg(args.ffmpeg)
    queue = _queue(args)
    _report(queue)
    info = StreamInfo(
        name=args.name or queue.name, genre=args.genre, url=args.url,
        public=args.public, bitrate=args.bitrate,
    )
    try:
        sender = sender_from_url(args.to, info, args.password or os.environ.get(PASSWORD_ENV) or None)
        sender.connect()
    except ValueError as exc:
        raise SystemExit(str(exc))
    except (SenderError, OSError) as exc:
        raise SystemExit(f"Couldn't connect to {args.to}: {exc}")
    print(f"On air to {sender.describe}. Ctrl+C stops; s + Enter skips.")

    channel = Channel(
        queue, sender, ffmpeg, bitrate=args.bitrate, loop=not args.once,
        start=max(args.start, 1) - 1, level=not args.no_level,
    )
    signal.signal(signal.SIGINT, lambda *_: channel.stop())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: channel.stop())
    if sys.stdin and sys.stdin.isatty():
        threading.Thread(target=_keyboard, args=(channel,), daemon=True).start()
    try:
        channel.run()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        sender.close()
    return 0


def _running(port: int) -> bool:
    """Whether Station Server already answers on `port`."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as resp:
            return "stations" in json.loads(resp.read())
    except (OSError, ValueError):
        return False


def _log_to_file(home: Path) -> None:
    """The downloaded app and the login start have no terminal: keep a log."""
    import logging.handlers
    try:
        home.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            home / "station-server.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _hand_over(port: int, url: str) -> int:
    """The Mac app itself starts its helper, which runs Station Server, so
    the stations stay on air with the window closed (and the app quit,
    when the window is turned off)."""
    import subprocess
    import time
    from station_server.desktop import CHILD_ENV, app_executable
    if not _running(port):
        subprocess.Popen([str(app_executable()), "app", "--no-window", "--port", str(port)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True,
                         env={**os.environ, CHILD_ENV: "1", "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        end = time.time() + 60  # the first start of a new download is slow while macOS checks it
        while not _running(port):
            if time.time() > end:
                logging.error("Station Server did not start")
                return 1
            time.sleep(0.5)
    return 0


def _quit_server(url: str) -> None:
    import urllib.request
    from station_server.web import API_HEADER
    req = urllib.request.Request(url + "api/quit", data=b"{}", method="POST",
                                 headers={API_HEADER: "1", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5):
        pass


def _own_mac_window(url: str) -> int:
    """The Mac app is Station Server's window until Quit."""
    from station_server import macwindow  # needs PyObjC, which only the Mac app carries
    from station_server.window import mac_app_bundle
    return macwindow.run(url, quit_server=lambda: _quit_server(url), bundle=mac_app_bundle())


def _watch_for_updates(app, every: float = UPDATE_CHECK_EVERY, wait=None) -> None:
    """Keep the new-version notice current while Station Server runs
    (a station computer can run for weeks)."""
    wait = wait or threading.Event().wait
    while True:
        try:
            app.check_for_updates()
        except Exception:  # never let the check stop Station Server
            logging.exception("Checking for a new version failed")
        if wait(every):
            return


def _app(args) -> int:
    from station_server.config import ConfigStore
    from station_server.desktop import CHILD_ENV, app_executable, bundled
    from station_server.onair import ServerApp
    from station_server.web import make_server
    from station_server.window import open_ui

    url = f"http://127.0.0.1:{args.port}/"
    store = ConfigStore()
    if bundled() or os.environ.get(CHILD_ENV):
        _log_to_file(store.home)
    if bundled() and sys.platform == "darwin" and app_executable() != Path(sys.executable):
        code = _hand_over(args.port, url)
        if code == 0 and not args.no_window:
            if store.config.open_window:
                return _own_mac_window(url)
            open_ui(url, store.home, False)  # a browser tab
        return code
    if _running(args.port):  # opened again: just show the window
        if not args.no_window:
            open_ui(url, store.home, store.config.open_window)
        return 0
    app = ServerApp(store)
    try:
        server = make_server(app, port=args.port)
    except OSError as exc:
        raise SystemExit(f"Port {args.port} is in use ({exc}); try --port")
    app.resume()
    logging.info("Station Server %s at %s", __version__, url)
    print(f"Station Server {__version__} at {url}  (Ctrl+C quits)")

    def stop(*_):
        threading.Thread(target=lambda: (app.shutdown(), server.shutdown()), daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    threading.Thread(target=_watch_for_updates, args=(app,), name="update-check", daemon=True).start()
    if not args.no_window:
        threading.Timer(0.3, open_ui, args=(url, store.home, store.config.open_window)).start()
    server.serve_forever()
    return 0


def _from_link(argv: list[str]) -> list[str]:
    """Opened through a station-server: link (or by an older macOS, with a
    process id): start in the background, with no window."""
    argv = [a for a in argv if not a.startswith("-psn_")]
    if any(a.lower().startswith(f"{URL_SCHEME}:") for a in argv):
        argv = ["app", "--no-window"]
    return argv


def main(argv: list[str] | None = None) -> int:
    argv = _from_link(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    if args.command is None:
        args = _parser().parse_args(["app", *(argv or [])])
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )
    if args.command == "remove-login":
        from station_server.desktop import Login
        Login().set(False)
        return 0
    if args.command == "check":
        return _check(args)
    if args.command == "app":
        return _app(args)
    return _play(args)


if __name__ == "__main__":
    sys.exit(main())
