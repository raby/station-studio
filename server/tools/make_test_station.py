"""Make a test station to try Station Server without the real music.

Writes a folder of short test songs (tones, made by ffmpeg, tagged with
made-up titles) and a week's playlist exported the way station-studio
does, into ~/station-server-test unless you name another folder:

    python server/tools/make_test_station.py
    python server/tools/make_test_station.py --week 13    # next week's, to test the switch

Then point a station's playlist at the exports folder it prints.
"""
from __future__ import annotations

import argparse
import plistlib
import random
import shutil
import subprocess
import sys
from pathlib import Path

ARTISTS = ["The Velvet Hours", "Marlowe & The Tides", "Ruby Lane", "The Night Shift",
           "Clara Vance", "Northern Lights Orchestra", "Johnny Arden", "The Satellites"]
WORDS = ["Midnight", "Summer", "Heartbeat", "Dancing", "Neon", "Moonlight", "Highway",
         "Golden", "Rainy", "Silver", "Saturday", "Radio", "Paradise", "Echoes", "Velvet"]
ENDINGS = ["Love", "Again", "Tonight", "Forever", "Dreams", "Lights", "Train", "Days",
           "Groove", "Serenade", "Blues", "Rendezvous"]
NOTES = [220, 247, 262, 294, 330, 349, 392, 440, 494, 523]


def make_song(ffmpeg: str, path: Path, seconds: int, freq: int, title: str, artist: str, album: str) -> None:
    """A tone that pulses, so you can hear one song end and the next begin."""
    tone = f"sine=frequency={freq}:duration={seconds}"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", tone,
        "-af", "tremolo=f=2:d=0.6,volume=0.5,afade=t=in:d=1,afade=t=out:st=%d:d=2" % (seconds - 2),
        "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k",
        "-metadata", f"title={title}", "-metadata", f"artist={artist}", "-metadata", f"album={album}",
        str(path),
    ], check=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("folder", nargs="?", type=Path, default=Path.home() / "station-server-test")
    p.add_argument("--station", default="Rebirth Classics")
    p.add_argument("--week", type=int, default=12)
    p.add_argument("--songs", type=int, default=12)
    p.add_argument("--missing", action="store_true",
                   help="also list a song whose file doesn't exist, to see the missing-tracks warning")
    args = p.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg isn't installed (macOS: brew install ffmpeg)")
    slug = args.station.lower().replace(" ", "-")
    music = (args.folder / "music" / slug).expanduser()
    exports = (args.folder / "exports" / slug).expanduser()
    music.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.week)  # the same week gives the same songs
    album = f"Test Tones Vol. {args.week}"
    tracks, items = {}, []
    for i in range(1, args.songs + 1):
        title = f"{rng.choice(WORDS)} {rng.choice(ENDINGS)}"
        artist = rng.choice(ARTISTS)
        seconds = rng.randint(60, 150)
        path = music / f"w{args.week:03d}-{i:02d} {artist} - {title}.mp3"
        if not path.exists():
            make_song(ffmpeg, path, seconds, rng.choice(NOTES), title, artist, album)
        print(f"  {i:2d}. {artist} - {title} ({seconds // 60}:{seconds % 60:02d})")
        tracks[i] = {"Track ID": i, "Name": title, "Artist": artist, "Album": album, "Genre": "Oldies",
                     "Kind": "MPEG audio file", "Total Time": seconds * 1000,
                     "Persistent ID": f"{args.week:04d}{i:012X}", "Track Type": "File",
                     "Location": path.resolve().as_uri()}
        items.append({"Track ID": i})
    if args.missing:
        i = args.songs + 1
        gone = music / "Not Here - Lost Song.mp3"
        tracks[i] = {"Track ID": i, "Name": "Lost Song", "Artist": "Not Here", "Total Time": 180000,
                     "Persistent ID": f"{args.week:04d}{i:012X}", "Track Type": "File",
                     "Location": gone.resolve().as_uri()}
        items.insert(len(items) // 2, {"Track ID": i})

    library = {
        "Major Version": 1, "Minor Version": 1, "Application Version": "12.13.0.9",
        "Library Persistent ID": "5354554449305354",
        "Tracks": {str(k): v for k, v in tracks.items()},
        "Playlists": [{
            "Name": f"{args.station} {args.week:03d}",
            "Playlist ID": 1000 + args.week,
            "Playlist Persistent ID": f"{slug.upper().replace('-', '_')}_{args.week:03d}",
            "All Items": True,
            "Playlist Items": items,
        }],
    }
    xml = exports / f"{args.week:03d}.xml"
    with xml.open("wb") as fh:
        plistlib.dump(library, fh, sort_keys=False)
    total = sum(tracks[i]["Total Time"] for i in range(1, args.songs + 1)) // 60000
    print(f"\nWrote {xml}  (about {total} minutes, then it loops)")
    print(f"Music in {music}")
    print(f"\nIn Station Server, set the station's playlist to:\n  {exports}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
