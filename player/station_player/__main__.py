"""`station-player` command: start the local server and open the UI."""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from station_player import __version__
from station_player.config import ConfigStore
from station_player.library import AUDIO_EXTENSIONS
from station_player.server import PlayerApp, make_server


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
        if p.is_file():
            if p.suffix.lower() != ".xml":
                print(f"Skipped {raw}: pass an iTunes .xml file or a folder", file=sys.stderr)
                continue
            targets = [store.config.playlist_sources]
        else:
            # Folders holding XML at any depth (station-studio writes
            # exports/<station>/<week>.xml) are playlist sources; folders
            # holding audio are music folders. An iTunes folder is both.
            try:
                has_xml, has_audio = _folder_contents(p)
            except OSError as exc:
                print(f"Skipped {raw}: {exc}", file=sys.stderr)
                continue
            targets = []
            if has_xml:
                targets.append(store.config.playlist_sources)
            if has_audio or not has_xml:
                targets.append(store.config.music_folders)
        for target in targets:
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
