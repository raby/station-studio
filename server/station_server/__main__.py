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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="station-server", description="Broadcast station-studio playlists.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")

    app = sub.add_parser("app", help="run the app and open its window (the default)")
    app.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"local port for the page (default {DEFAULT_PORT})")
    app.add_argument("--no-window", action="store_true", help="don't open the window")
    app.add_argument("-v", "--verbose", action="store_true")

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


def _app(args) -> int:
    from station_server.config import ConfigStore
    from station_server.onair import ServerApp
    from station_server.web import make_server
    from station_server.window import open_ui

    url = f"http://127.0.0.1:{args.port}/"
    store = ConfigStore()
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
    print(f"Station Server {__version__} at {url}  (Ctrl+C quits)")

    def stop(*_):
        threading.Thread(target=lambda: (app.shutdown(), server.shutdown()), daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)
    if not args.no_window:
        threading.Timer(0.3, open_ui, args=(url, store.home, store.config.open_window)).start()
    server.serve_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        args = _parser().parse_args(["app", *(argv or [])])
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
    )
    if args.command == "check":
        return _check(args)
    if args.command == "app":
        return _app(args)
    return _play(args)


if __name__ == "__main__":
    sys.exit(main())
