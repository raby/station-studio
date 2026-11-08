"""The page's script, /app.js.

It's kept as one file per part of the page (static/js/, below) and served
joined, in this order, as one script: the parts share their names (one
part's function is another's to call, and the page's tests and help.js
use them too), every function is there before any of it runs, and nothing
can run between one part and the next. Joined, it's the same script the
page has always had; nothing builds it.

/app.js.map maps each line of it back to its file, for the browser's
developer tools: their Sources panel, breakpoints, and the file and line
they show for an error.
"""
from __future__ import annotations

import json
from pathlib import Path

JS_DIR = Path(__file__).parent / "static" / "js"

# In the order they run. Each part's top-level code runs as the page
# loads, so a part that uses another's names as it loads comes after it.
PARTS = (
    "core",        # small helpers, the player's state
    "library",     # the music library and moving between views
    "playlists",   # a playlist, making your own, a song's menu
    "upnext",      # Up Next
    "radio",       # Radio
    "home",        # Home and Discover
    "tuner",       # the tuner, and finding stations
    "globe",       # the world globe
    "recordings",  # the Recordings page
    "clock",       # the clock, alarms going off, night mode
    "ambient",     # ambient mode
    "recorder",    # recording the radio
    "addmusic",    # adding music
    "settings",    # Settings
    "sound",       # volume levelling, the sound wave
    "playback",    # playing, crossfading, remembering where you were
    "wiring",      # the buttons, keys and controls that work on every view
    "desktop",     # the mini player, the Mac window, installing, opened files, the Dock menu
    "extras",      # the play log, sleep timer, alarm, phone remote
    "boot",        # starting up
)

MAP_URL = "/app.js.map"


def _texts(js_dir: Path | None) -> list[tuple[str, str]]:
    js_dir = js_dir or JS_DIR
    out = []
    for name in PARTS:
        text = (js_dir / f"{name}.js").read_text(encoding="utf-8")
        out.append((f"js/{name}.js", text if text.endswith("\n") else text + "\n"))
    return out


def script(js_dir: Path | None = None) -> bytes:
    """The parts joined, and where its source map is."""
    joined = "".join(text for _, text in _texts(js_dir))
    return (joined + f"//# sourceMappingURL={MAP_URL}\n").encode("utf-8")


_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _vlq(value: int) -> str:
    n = (-value << 1) | 1 if value < 0 else value << 1
    out = ""
    while True:
        digit, n = n & 31, n >> 5
        out += _B64[digit | (32 if n else 0)]
        if not n:
            return out


def source_map(js_dir: Path | None = None) -> bytes:
    """A source map (version 3) for script(): each of its lines is the same
    line of one of the parts."""
    sources, segments = [], []
    last_source, last_line = 0, 0
    for index, (path, text) in enumerate(_texts(js_dir)):
        sources.append(path)
        for line in range(text.count("\n")):
            segments.append("A" + _vlq(index - last_source) + _vlq(line - last_line) + "A")
            last_source, last_line = index, line
    return json.dumps({"version": 3, "file": "app.js", "sources": sources, "names": [],
                       "mappings": ";".join(segments)}).encode("utf-8")
