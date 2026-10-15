"""Help with setting the player up: the computer's own folder and file
pickers, likely music sources on this computer, finding moved music, and
trying a radio station before it's saved.
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

from station_player import radio
from station_player.config import Config, station_studio_exports
from station_player.library import AUDIO_EXTENSIONS, Track, name_key

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000  # Windows: no console window flashes up

FOLDER_PROMPT = "Choose a music folder"
FILE_PROMPT = "Choose an iTunes or Music library export (.xml)"


class PickerBusy(Exception):
    """A picker window is already open."""


class PickerUnavailable(Exception):
    """This computer has no picker the player knows how to open."""


class PickerFailed(Exception):
    """The picker couldn't be opened."""


# ---- native pickers ------------------------------------------------------------

def _applescript(kind: str, front: bool) -> str:
    if kind == "folder":
        choose = f'choose folder with prompt "{FOLDER_PROMPT}" default location (path to music folder)'
    else:
        choose = (f'choose file of type {{"xml", "public.xml"}} with prompt "{FILE_PROMPT}" '
                  'default location (path to music folder)')
    # Asked of the frontmost app (the browser), the window opens in front
    # of it rather than behind, as osascript's own would.
    if front:
        return f"tell application (path to frontmost application as text) to POSIX path of ({choose})"
    return f"POSIX path of ({choose})"


_POWERSHELL = r"""
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$music = [Environment]::GetFolderPath('MyMusic')
if ($env:SP_KIND -eq 'folder') {
  $d = New-Object System.Windows.Forms.FolderBrowserDialog
  $d.Description = $env:SP_PROMPT
  $d.ShowNewFolderButton = $false
  $d.SelectedPath = $music
  $ok = $d.ShowDialog($owner) -eq 'OK'
  $path = $d.SelectedPath
} else {
  $d = New-Object System.Windows.Forms.OpenFileDialog
  $d.Title = $env:SP_PROMPT
  $d.Filter = 'iTunes or Music library (*.xml)|*.xml|All files (*.*)|*.*'
  $d.InitialDirectory = $music
  $ok = $d.ShowDialog($owner) -eq 'OK'
  $path = $d.FileName
}
$owner.Dispose()
if ($ok -and $path) { [Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($path))) }
"""


class Picker:
    """Opens the computer's own folder or file window, one at a time.

    `run` is `subprocess.run`-like (tests pass a fake); `which` finds
    programs; `env` is checked for a display on Linux.
    """

    def __init__(self, platform: str | None = None, run: Callable | None = None,
                 which: Callable[[str], str | None] = shutil.which, env: dict | None = None) -> None:
        self.platform = platform or sys.platform
        self._run = run or subprocess.run
        self._which = which
        self._env = os.environ if env is None else env
        self._lock = threading.Lock()

    def pick(self, kind: str) -> dict:
        """{"path": ...} or {"cancelled": True}. Raises PickerBusy,
        PickerUnavailable or PickerFailed. Waits as long as the person
        takes: this runs in its own request thread."""
        if kind not in ("folder", "file"):
            raise ValueError("kind must be 'folder' or 'file'")
        if not self._lock.acquire(blocking=False):
            raise PickerBusy("A folder window is already open")
        try:
            if self.platform == "darwin":
                return self._mac(kind)
            if self.platform == "win32":
                return self._windows(kind)
            return self._linux(kind)
        finally:
            self._lock.release()

    def _call(self, argv: list[str], **kwargs):
        try:
            return self._run(argv, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", **kwargs)
        except OSError as exc:
            raise PickerFailed(f"Couldn't open the window: {exc}") from None

    @staticmethod
    def _result(path: str) -> dict:
        path = path.strip("\r\n")
        if not path:
            return {"cancelled": True}
        trimmed = path.rstrip("/\\")  # macOS ends folders with "/"
        if trimmed and not trimmed.endswith(":"):  # but keep "/" and "C:\"
            path = trimmed
        return {"path": path}

    def _mac(self, kind: str) -> dict:
        result = None
        for front in (True, False):  # some frontmost apps can't be asked
            result = self._call(["osascript", "-e", _applescript(kind, front)])
            if result.returncode == 0:
                return self._result(result.stdout)
            if "-128" in (result.stderr or ""):  # "User canceled."
                return {"cancelled": True}
        raise PickerFailed(f"The folder window couldn't open: {(result.stderr or '').strip()}")

    def _windows(self, kind: str) -> dict:
        env = {**self._env, "SP_KIND": kind, "SP_PROMPT": FOLDER_PROMPT if kind == "folder" else FILE_PROMPT}
        result = self._call(["powershell", "-NoProfile", "-NonInteractive", "-STA", "-Command", _POWERSHELL],
                            env=env, creationflags=_CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise PickerFailed(f"The folder window couldn't open: {(result.stderr or '').strip()}")
        raw = (result.stdout or "").strip()
        if not raw:
            return {"cancelled": True}
        try:
            return self._result(base64.b64decode(raw).decode("utf-8"))
        except ValueError:
            raise PickerFailed("The folder window gave back something unexpected") from None

    def _linux(self, kind: str) -> dict:
        if not (self._env.get("DISPLAY") or self._env.get("WAYLAND_DISPLAY")):
            raise PickerUnavailable("There's no screen to show a folder window on")
        prompt = FOLDER_PROMPT if kind == "folder" else FILE_PROMPT
        start = str(Path.home() / "Music") + "/"
        if self._which("zenity"):
            argv = ["zenity", "--file-selection", f"--title={prompt}", f"--filename={start}"]
            argv += ["--directory"] if kind == "folder" else [
                "--file-filter=Library export (XML) | *.xml *.XML", "--file-filter=All files | *"]
        elif self._which("kdialog"):
            argv = (["kdialog", "--title", prompt, "--getexistingdirectory", start] if kind == "folder"
                    else ["kdialog", "--title", prompt, "--getopenfilename", start, "*.xml *.XML|Library export (XML)"])
        else:
            raise PickerUnavailable("No folder window is available (install zenity or kdialog)")
        result = self._call(argv)
        if result.returncode == 1:  # both say "cancelled" this way
            return {"cancelled": True}
        if result.returncode != 0:
            raise PickerFailed(f"The folder window couldn't open: {(result.stderr or '').strip()}")
        return self._result(result.stdout)


# ---- suggestions ---------------------------------------------------------------

COUNT_FILES = 20_000   # files looked at when counting a folder's songs
COUNT_SECONDS = 2.0


def _walk_files(folder: Path, max_files: int, seconds: float) -> tuple[Iterable[Path], Callable[[], bool]]:
    """Files under `folder` (hidden ones skipped), stopping after
    `max_files` or `seconds`; the second value says whether it stopped
    early."""
    state = {"cut": False}

    def files():
        deadline = time.monotonic() + seconds
        seen = 0
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if seen >= max_files or time.monotonic() > deadline:
                    state["cut"] = True
                    return
                seen += 1
                if not name.startswith("."):
                    yield Path(root) / name

    return files(), lambda: state["cut"]


def count_songs(folder: Path, max_files: int = COUNT_FILES, seconds: float = COUNT_SECONDS) -> tuple[int, bool]:
    """(audio files found, whether counting stopped early)."""
    files, cut = _walk_files(folder, max_files, seconds)
    n = sum(1 for f in files if f.suffix.lower() in AUDIO_EXTENSIONS)
    return n, cut()


def _songs(n: int, cut: bool) -> str:
    if cut:
        return f"over {n:,} songs"
    return "1 song" if n == 1 else f"{n:,} songs"


def is_itunes_xml(path: Path) -> bool:
    """A cheap look: iTunes exports start with a plist whose first keys
    include Tracks."""
    try:
        with path.open("rb") as fh:
            head = fh.read(16 * 1024)
    except OSError:
        return False
    return b"<plist" in head and b"<key>Tracks</key>" in head


def _inside(path: str, sources: list[str]) -> bool:
    parts = Path(path).as_posix().split("/")
    for src in sources:
        src_parts = Path(src).as_posix().rstrip("/").split("/")
        if len(parts) >= len(src_parts) and all(name_key(a) == name_key(b) for a, b in zip(parts, src_parts)):
            return True
    return False


def suggestions(config: Config, home: Path | None = None, exports: Path | None = None,
                max_files: int = COUNT_FILES, seconds: float = COUNT_SECONDS) -> list[dict]:
    """Likely music and playlists on this computer, for the set-up screen."""
    home = home or Path.home()  # %USERPROFILE% on Windows
    exports = exports or station_studio_exports()
    out: list[dict] = []

    def add(kind: str, path: Path, label: str, detail: str) -> None:
        clean = str(path.resolve())
        if any(s["path"] == clean for s in out):
            return
        configured = config.music_folders if kind == "music" else config.playlist_sources
        out.append({"kind": kind, "path": clean, "label": label, "detail": detail,
                    "added": _inside(clean, configured)})

    if exports.is_dir():
        try:
            n = sum(1 for _ in exports.glob("*.xml"))
        except OSError:
            n = 0
        detail = "Station Studio playlists" if not n else \
            f"Station Studio playlists ({n} export{'s' if n != 1 else ''})"
        add("playlist", exports, "Station Studio", detail)

    music = home / "Music"
    if music.is_dir():
        n, cut = count_songs(music, max_files, seconds)
        if n:
            add("music", music, "Music folder", _songs(n, cut))

    xmls = [music / "iTunes" / "iTunes Music Library.xml", music / "iTunes" / "iTunes Library.xml"]
    for folder in (music / "Music", music, music / "iTunes"):
        try:
            xmls += sorted(folder.glob("*.xml"))
        except OSError:
            pass
    for xml in xmls:
        if xml.is_file() and is_itunes_xml(xml):
            add("playlist", xml, xml.name, "iTunes library export")
    return out


# ---- finding moved music -------------------------------------------------------

LOCATE_FILES = 300_000
LOCATE_SECONDS = 20.0


def _parts(path: Path) -> list[str]:
    return path.as_posix().split("/")


def find_remap(missing: Iterable[Path], folder: Path, max_files: int = LOCATE_FILES,
               seconds: float = LOCATE_SECONDS) -> dict | None:
    """The path rewrite that finds the most missing files in `folder`.

    Each missing file is looked for by name; between namesakes, the one
    whose parent folders also match wins (an unbroken tie is skipped).
    The parts the two paths share at the end are dropped, leaving a pair
    of prefixes ("/Users/rob/Music" -> "D:/Music"); the commonest wins.
    """
    wanted: dict[str, list[Path]] = {}
    for p in missing:
        wanted.setdefault(name_key(p.name), []).append(p)
    if not wanted:
        return None
    found: dict[str, list[Path]] = {}
    files, _cut = _walk_files(folder, max_files, seconds)
    for f in files:
        key = name_key(f.name)
        if key in wanted:
            found.setdefault(key, []).append(f)

    pairs: Counter = Counter()
    for key, originals in wanted.items():
        for original in originals:
            want = [name_key(x) for x in reversed(_parts(original))]
            best, best_score, tied = None, 0, False
            for f in found.get(key, []):
                have = [name_key(x) for x in reversed(_parts(f))]
                score = 0
                while score < min(len(want), len(have)) and want[score] == have[score]:
                    score += 1
                if score > best_score:
                    best, best_score, tied = f, score, False
                elif score == best_score:
                    tied = True
            if best is None or tied:
                continue
            src, dst = _parts(original)[:-best_score], _parts(best)[:-best_score]
            src_text, dst_text = "/".join(src), "/".join(dst)
            if not src_text or not dst_text or src_text == dst_text:
                continue
            pairs[(src_text, dst_text)] += 1
    if not pairs:
        return None
    (src_text, dst_text), _n = pairs.most_common(1)[0]
    return {"from": src_text, "to": dst_text}


def missing_locations(tracks: Iterable[Track]) -> dict[str, Path]:
    """Where the missing iTunes tracks were, by their location's text."""
    return {str(t.location): t.location for t in tracks if not t.available and t.location is not None}


# ---- trying a station ----------------------------------------------------------

TEST_SECONDS = 8.0


def _reason(exc: Exception) -> str:
    text = str(exc)
    low = text.lower()
    if "timed out" in low or isinstance(exc, TimeoutError):
        return f"The station didn't answer within {TEST_SECONDS:.0f} seconds"
    if "name or service" in low or "nodename" in low or "getaddrinfo" in low or "no address" in low:
        return "Couldn't find that server. Check the address for typos"
    if "refused" in low:
        return "The server refused the connection. The station may be off the air"
    if "certificate" in low or "ssl" in low:
        return "The station's secure connection couldn't be checked"
    if text.startswith("station answered "):
        code = text.rsplit(" ", 1)[1]
        if code == "404":
            return "The server says there's no stream at that address (404)"
        return f"The server turned the request down ({code})"
    if "redirect" in low:
        return "The address keeps sending the player elsewhere"
    return f"Couldn't connect to this station ({text or exc.__class__.__name__})"


def check_station(url: str, resolve: Callable[[str], str], user_agent: str = "StationPlayer",
                  open_stream: Callable = radio.open_stream, seconds: float = TEST_SECONDS) -> dict:
    """Try a stream: {"ok": True, "type", "name", "url"} or {"ok": False,
    "message"}. Never raises for network trouble."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "message": "Stream URL must start with http:// or https://"}
    try:
        stream_url = resolve(url)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": _reason(exc)}
    deadline = time.monotonic() + seconds
    try:
        stream = open_stream(stream_url, user_agent=user_agent, timeout=seconds)
    except Exception as exc:
        return {"ok": False, "message": _reason(exc)}
    try:
        raw_type = stream.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        ctype = stream.content_type
        if ctype is None:
            if "mpegurl" in raw_type:
                return {"ok": False, "message": "This is an HLS stream (.m3u8), which Station Player can't check "
                                                "or record; it may still play"}
            if raw_type.startswith("text/"):
                return {"ok": False, "message": "That address is a web page, not an audio stream"}
            return {"ok": False, "message": f"That address isn't an audio stream ({raw_type or 'unknown type'})"}
        try:
            stream.sock.settimeout(max(0.5, deadline - time.monotonic()))
            data = stream.body.read1(4096)
        except (OSError, radio.StreamError) as exc:
            return {"ok": False, "message": _reason(exc)}
        if not data:
            return {"ok": False, "message": "The station connected but sent no audio"}
        name = (stream.headers.get("icy-name") or "").strip() or None
        return {"ok": True, "type": ctype, "name": name, "url": stream_url}
    except Exception as exc:  # anything odd from a strange server
        logger.warning("Testing %s failed: %s", url, exc)
        return {"ok": False, "message": _reason(exc)}
    finally:
        stream.close()
