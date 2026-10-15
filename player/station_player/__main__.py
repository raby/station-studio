"""`station-player` command: start the local server and open the UI."""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from station_player import __version__
from station_player.config import ConfigStore
from station_player.server import PlayerApp, make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="station-player",
        description="Play station-studio playlists, local music folders and radio streams.",
    )
    parser.add_argument("sources", nargs="*", metavar="PATH",
                        help="iTunes XML files, folders of XMLs, or folders of audio to add")
    parser.add_argument("--port", type=int, default=8765, help="port to listen on (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    parser.add_argument("--version", action="version", version=f"station-player {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store = ConfigStore()
    added = False
    for raw in args.sources:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            print(f"Skipped {raw}: not found", file=sys.stderr)
            continue
        # XML files, and folders holding XML at any depth (station-studio
        # writes exports/<station>/<week>.xml), are playlists; any other
        # folder is a music folder.
        if p.is_file() and p.suffix.lower() != ".xml":
            print(f"Skipped {raw}: pass an iTunes .xml file or a folder", file=sys.stderr)
            continue
        is_playlist = p.is_file() or next(p.rglob("*.xml"), None) is not None
        target = store.config.playlist_sources if is_playlist else store.config.music_folders
        if str(p) not in target:
            target.append(str(p))
            added = True
    if added:
        try:
            store.save()
        except OSError as exc:
            print(f"Could not save settings to {store.path}: {exc}", file=sys.stderr)
    app = PlayerApp(store)  # scans once, with every source in place

    try:
        server = make_server(app, port=args.port)
    except OSError as exc:
        print(f"Could not listen on port {args.port}: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Station Player {__version__} on {url}  (Ctrl+C to quit)")
    print(f"Settings: {store.path}")
    for err in app.library.errors:
        print(f"  ! {err}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
