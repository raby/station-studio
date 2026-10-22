"""Local HTTP server: JSON API, the web UI, and audio with Range support.

Binds to 127.0.0.1 only. Two guards keep other websites in the same
browser from driving it: requests must carry a localhost Host header
(blocks DNS rebinding), and every state-changing request must carry the
`X-Station-Player` header, which a cross-origin page can't send without
a CORS preflight this server never approves.
"""
from __future__ import annotations

import base64
import binascii
import copy
import json
import os
import logging
import math
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from station_player import __version__, loudness, myplaylists, qr, radio, remote, setup, updates
from station_player.config import MAX_RECENT, clean_alarm, clean_alarms, clean_tags
from station_player.directory import Directory, DirectoryError
from station_player.logos import LogoStore
from station_player.playlog import PlayLog
from station_player.recorder import Recorder
from station_player.window import browser_name, find_browser
from station_player.config import ConfigStore
from station_player.desktop import Desktop
from station_player.library import AUDIO_EXTENSIONS, OPENED_ID, Library, _short_hash, name_key
from station_player.tags import MAX_ART_BYTES, TagCache, folder_cover, image_type, read_art

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# The car page (player/car/), published with the docs on GitHub Pages.
CAR_PAGE = "https://raby.github.io/station-studio/car/"
API_HEADER = "X-Station-Player"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
# Fixed tables rather than `mimetypes`, which on Windows reads (sometimes
# wrong) registry mappings. Audio is only ever served with an audio type.
AUDIO_TYPES = {
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".opus": "audio/ogg", ".oga": "audio/ogg",
    ".ogg": "audio/ogg", ".wav": "audio/wav", ".aif": "audio/aiff",
    ".aiff": "audio/aiff", ".webm": "audio/webm", ".mp3": "audio/mpeg",
}
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webmanifest": "application/manifest+json",
    ".ico": "image/x-icon",
}
# The UI only talks to this server; audio may come from radio stations.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self' http: https:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_STREAM_FETCH_SECONDS = 10


def content_type(path: Path) -> str | None:
    return AUDIO_TYPES.get(path.suffix.lower())


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Parse a single `bytes=` range into inclusive (start, end).

    Returns None when the header is unusable (caller serves the whole
    file) and raises ValueError when it is unsatisfiable (416).
    """
    m = _RANGE_RE.match(header.strip())
    if not m:
        return None
    first, last = m.groups()
    if first == "" and last == "":
        return None
    if size == 0:
        raise ValueError("empty file has no satisfiable range")
    if first == "":  # suffix range: last N bytes
        n = int(last)
        if n == 0:
            raise ValueError("empty suffix range")
        return max(0, size - n), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable range")
    return start, min(end, size - 1)


def resolve_stream_url(url: str, fetch=None) -> str:
    """Turn a .pls / .m3u playlist URL into the stream URL inside it.

    Raises ValueError when a playlist link can't be read or holds no
    stream, rather than saving a station that can never play.
    """
    path = urlparse(url).path.lower()
    if not path.endswith((".pls", ".m3u")):
        return url
    fetch = fetch or _fetch_text
    try:
        body = fetch(url)
    except Exception as exc:
        raise ValueError(f"Couldn't read that playlist link: {exc}") from None
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("file") and "=" in line:
            line = line.split("=", 1)[1].strip()
        if line.startswith(("http://", "https://")):
            return line
    raise ValueError("That playlist link doesn't contain a stream URL")


class _HttpOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme not in ("http", "https"):
            raise urllib.error.URLError(f"refusing redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpOnlyRedirects)


def _fetch_text(url: str, deadline: float = _STREAM_FETCH_SECONDS) -> str:
    """Fetch up to 64 KB, giving up after `deadline` seconds overall
    (urllib's own timeout is per socket read, so a slow-drip server
    could otherwise hold a request thread indefinitely)."""
    result: dict = {}

    def work():
        try:
            with _opener.open(url, timeout=deadline) as resp:
                result["body"] = resp.read(64 * 1024).decode("utf-8", "replace")
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(deadline)
    if "body" in result:
        return result["body"]
    raise result.get("error") or TimeoutError("timed out")


class AlarmNotRinging(Exception):
    """Snooze asked for with no alarm ringing."""


class PlayerApp:
    """Shared state for request handlers.

    A lock serialises config changes (mutate, then save; a failed save
    rolls the in-memory config back). Library rebuilds run outside the
    lock from a snapshot of the config, so reads stay responsive during a
    long scan; each change bumps a generation counter and a rebuild only
    installs its result if no newer change has happened since, so a slow
    rebuild can never replace a newer library with a stale one.
    """

    def __init__(self, store: ConfigStore, scan: bool = True, desktop: Desktop | None = None,
                 recorder: Recorder | None = None) -> None:
        self.store = store
        self.library = Library()
        self.desktop = desktop or Desktop()
        self.tag_cache = TagCache(store.home / "tag-cache.json")
        self.loudness_store = loudness.LoudnessStore(store.home / "loudness.json")
        self.my_playlists = myplaylists.PlaylistStore(store.home / "my-playlists.json")
        self.recorder = recorder or Recorder(user_agent=f"StationPlayer/{__version__}")
        # Finding stations in the Radio Browser directory.
        self.directory = Directory(user_agent=f"StationPlayer/{__version__}")
        self.logos = LogoStore(store.home / "logos")
        # which browser the player's own window uses (None: opens in a tab)
        self.window_browser = browser_name(find_browser())
        self.latest_version: str | None = None
        self._update_lock = threading.Lock()
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()  # one disk scan at a time
        self._generation = 0
        self._installed = -1
        self._titles: dict[str, str | None] = {}  # station url -> song
        # Files opened with "Open With" this session, and the latest
        # request for the page to play one.
        self.opened: list[Path] = []
        self.open_request: dict | None = None
        self.page_seen = 0.0  # when a page last checked for requests
        self.play_log = PlayLog(store.home / "play-log.jsonl")
        # The alarm going off: what the page plays (same pattern as open_request).
        self.alarm_request: dict | None = None
        # The alarm that is ringing now, and a snoozed one (see alarm_state).
        self._ringing: dict | None = None
        self._snooze: dict | None = None
        # The times each alarm last went off for ({alarm id: ["YYYY-MM-DD
        # HH:MM", ...]}), kept on disk so a restart just after an alarm
        # doesn't set it off again.
        self._alarm_file = store.home / "alarm-fired.json"
        self._alarm_fired: dict[str, list[str]] = self._read_fired()
        # Opens a player window (set by __main__; None in tests).
        self.show_window = None
        # Phone remote: its server, commands waiting for the page, and the
        # page's latest report of what's playing.
        self.remote_server: remote.RemoteServer | None = None
        self.remote_error: str | None = None
        self.remote_commands: list[dict] = []
        self.player_status: dict = {}
        self._window_asked = 0.0
        # The computer's own folder / file windows, and where suggestions
        # look for music (both swapped in tests).
        self.picker = setup.Picker()
        self.suggest_home: Path | None = None
        if scan:
            self._rebuild()

    def scan_in_background(self, on_done=None) -> threading.Thread:
        """First scan without blocking startup; the UI shows progress."""
        def work():
            self._rebuild()
            if on_done:
                on_done()

        worker = threading.Thread(target=work, name="library-scan", daemon=True)
        worker.start()
        return worker

    def _rebuild(self) -> None:
        # Requests that pile up behind a running scan share its result:
        # whoever gets the scan lock next scans the newest settings, and
        # the rest find that generation already installed and return.
        with self._scan_lock:
            with self._lock:
                generation = self._generation
                if generation == self._installed:
                    return
                cfg = copy.deepcopy(self.store.config)
            lib = Library()
            try:
                lib.load(cfg.playlist_sources, cfg.music_folders, cfg.path_remaps, self.tag_cache,
                         self._recording_names())
            except Exception as exc:
                # Never leave the UI waiting on a scan that died.
                logger.exception("Library scan failed")
                lib = Library()
                lib.errors.append(f"Reading the library failed: {exc}")
            finally:
                self.tag_cache.save()
            with self._lock:
                if generation == self._generation:
                    self.library = lib.with_opened(self.opened, self.tag_cache) if self.opened else lib
                    self._installed = generation

    def rescan(self) -> None:
        with self._lock:
            self._generation += 1
        self._rebuild()

    def state(self) -> dict:
        with self._lock:
            lib = self.library
            config = copy.deepcopy(self.store.config.to_json())
            scanning = self._installed != self._generation
        stations = [{**s, "id": station_id(s["url"])} for s in config["stations"]]
        saved = {s["url"] for s in stations}
        recent = [{**s, "saved": s["url"] in saved} for s in config["recent_stations"]]
        return {
            "version": __version__,
            "playlists": lib.playlist_summaries() + self.my_summaries(),
            "stations": stations,
            "recent_stations": recent,
            "config": config,
            "errors": self.store.warnings + list(lib.errors)
            + ([self.my_playlists.warning] if self.my_playlists.warning else []),
            "scanning": scanning,
            "desktop": self.desktop.status(),
            "bundled": getattr(self.desktop, "bundled", False),
            "recordings": self.recorder.status(),
            "recordings_folder": str(self.recorder.folder),
            "window_browser": self.window_browser,
            "remote": self.remote_info(),
            "update": self.latest_version
            if config["check_updates"] and updates.is_newer(self.latest_version, __version__) else None,
            "sources": _source_summaries(lib, config),
            "missing": lib.missing_count(),
            "next_alarm": self.next_alarm(),
            "alarm_state": self.alarm_state(),
        }

    # ---- setting up -----------------------------------------------------------

    def suggestions(self) -> list[dict]:
        with self._lock:
            cfg = copy.deepcopy(self.store.config)
        return setup.suggestions(cfg, home=self.suggest_home)

    def locate(self, folder: str) -> dict:
        """Find missing tracks in `folder` and add the path rewrite that
        finds the most of them. Returns how many were found and the rewrite
        (None when nothing matched)."""
        root = Path(folder.strip()).expanduser() if folder.strip() else None
        if root is None or not root.is_dir():
            raise ValueError(f"Folder not found: {folder}")
        root = root.resolve()
        before = setup.missing_locations(self.library.tracks.values())
        remap = setup.find_remap(before.values(), root)
        if remap is not None:
            key = name_key(remap["from"])
            with self._lock:
                old_remaps = copy.deepcopy(self.store.config.path_remaps)
            with self._change() as cfg:
                # A newer answer for the same old folder replaces the old one.
                cfg.path_remaps[:] = [r for r in cfg.path_remaps
                                      if name_key(r["from"].replace("\\", "/").rstrip("/")) != key] + [remap]
        lib = self.library
        now = {str(t.location) for t in lib.tracks.values() if t.available and t.location is not None}
        found = len(before.keys() & now)
        if remap is not None and not found:
            # Another, more specific rewrite still wins for these tracks:
            # this one would do nothing, so it isn't kept.
            with self._change() as cfg:
                cfg.path_remaps[:] = old_remaps
            lib = self.library
            remap = None
        return {"found": found, "missing": lib.missing_count(), "remap": remap}

    def test_station(self, url: str) -> dict:
        return setup.check_station(url, resolve_stream_url, user_agent=f"StationPlayer/{__version__}")

    # ---- recording ---------------------------------------------------------------

    def record(self, sid: str, on: bool) -> None:
        url = self.station_url(sid)
        if url is None:
            raise LookupError("No such station")
        if on:
            name = next((s["name"] for s in self.store.config.stations if s["url"] == url), url)
            self.recorder.start(url, name)
        else:
            self.recorder.stop(url)

    def _recording_names(self) -> dict[str, dict]:
        """Recordings' labels and stations, so the library shows them."""
        folder = self.recorder.folder
        out = {}
        from station_player.recorder import station_from_name
        for name, meta in self.recorder.index.all().items():
            label, station = meta.get("label"), meta.get("station") or station_from_name(name)
            if label or station:
                out[str(folder / name)] = {"name": label, "artist": station}
        return out

    def recordings_list(self) -> dict:
        return {"recordings": self.recorder.listing(), "folder": str(self.recorder.folder)}

    def label_recording(self, file: str, label, tags) -> None:
        """Give a finished recording a label and tags. Raises ValueError."""
        from station_player.recorder import MAX_LABEL, clean_tags
        folder = self.recorder.folder.resolve()
        path = Path(file).resolve() if file else None
        if path is None or path.parent != folder or not path.is_file():
            raise ValueError("That recording wasn't found")
        if not isinstance(label, str):
            raise ValueError("label must be text")
        label = " ".join(label.split())[:MAX_LABEL]
        tags = clean_tags(tags)
        try:
            self.recorder.index.update(path.name, label=label, tags=tags)
        except OSError as exc:
            raise ValueError(f"Couldn't save the label: {exc}") from None
        if any(Path(f).expanduser().resolve() == folder for f in self.store.config.music_folders):
            self.rescan()  # so the new name shows when it plays

    def open_recordings_folder(self) -> None:
        """Show the recordings folder in Finder / Explorer / the file manager."""
        folder = self.recorder.folder
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        else:
            proc = subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if hasattr(proc, "wait"):
                threading.Thread(target=proc.wait, daemon=True).start()  # no zombie left behind

    MAX_OPENED = 500

    def open_files(self, paths) -> dict:
        """Play audio files opened with Station Player from the computer's
        file manager: they join "Opened files" and the page plays the
        first. Returns the request the page picks up."""
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a list of files")
        files = []
        for raw in paths[:self.MAX_OPENED]:
            if not isinstance(raw, str) or not raw.strip():
                continue
            p = Path(raw).expanduser().resolve()
            if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file() and p not in files:
                files.append(p)
        if not files:
            raise ValueError("None of those are audio files Station Player can play")
        with self._lock:
            self.opened = [f for f in self.opened if f not in files] + files
            del self.opened[:-self.MAX_OPENED]
            self.library = self.library.with_opened(self.opened, self.tag_cache)
            # Unique across restarts: the page remembers the last one it played.
            seq = max(time.time_ns(), (self.open_request or {}).get("seq", 0) + 1)
            self.open_request = {"seq": seq, "playlist": OPENED_ID,
                                 "track": _short_hash("file", str(files[0])), "at": time.time()}
            return dict(self.open_request)

    def inbox(self, only: str | None = None) -> dict:
        """What a page polls: the latest open request and alarm (with how
        old they are), and commands from the phone remote, which are
        handed to the first page that asks. `only`: just the commands of
        that kind (a page starting up plays a station before the library
        is read)."""
        now = time.time()
        self.page_seen = now
        with self._lock:
            live = [c for c in self.remote_commands if c.get("hold") or now - c["at"] < 60]
            commands = [c for c in live if only is None or c["cmd"] == only]
            self.remote_commands = [c for c in live if c not in commands]
        out = {"commands": commands}
        if only is not None:
            return out
        for key, req in (("open", self.open_request), ("alarm", self.alarm_request)):
            out[key] = {**req, "age": now - req["at"]} if req else None
        # Which alarm is ringing now (its seq), so a window showing one
        # notices when it was snoozed or stopped elsewhere, or timed out.
        out["ringing"] = (self.alarm_state()["ringing"] or {}).get("seq")
        return out

    def start_station(self, url: str, hold: bool = False) -> None:
        """Have the page play a saved station (a desktop icon for it was
        clicked). `hold`: the player is starting, so the request waits for
        the window however long that takes. LookupError when it isn't one
        of the saved stations (the page says so too)."""
        with self._lock:
            known = any(s.get("url") == url for s in self.store.config.stations)
            command = {"cmd": "station", "id": station_id(url)} if known else {
                "cmd": "notice", "text": "That desktop icon's station isn't one of your stations any more "
                                         "(or its link has changed). Edit the station and Put on desktop again."}
            self.remote_commands.append({**command, "at": time.time(), **({"hold": True} if hold else {})})
            del self.remote_commands[:-20]
        if not known:
            raise LookupError("That station isn't one of your stations any more")

    def station_shortcut(self, url: str) -> str:
        """Put an icon for a saved station on the desktop; returns where."""
        with self._lock:
            station = next((dict(s) for s in self.store.config.stations if s.get("url") == url), None)
        if station is None:
            raise LookupError("No such station")
        logo = self.logos.folder / station["logo"] if station.get("logo") and self.logos.has(station["logo"]) else None
        return str(self.desktop.station_shortcut(station["name"], url, self.store.home / "station-icons", logo))

    def page_open(self) -> bool:
        """Whether a player page is open: it checks in every second, but a
        hidden window's timers can be slowed to once a minute, so allow
        for that. A window that closes says so (`page_closed`)."""
        return time.time() - self.page_seen < 70

    def page_closed(self) -> None:
        self.page_seen = 0.0

    # ---- play log ---------------------------------------------------------------

    def played(self, track_id, started, seconds) -> None:
        """The page reports a track it played: when it started (ms since
        1970) and how many seconds were listened to."""
        if not isinstance(track_id, str) or not isinstance(started, (int, float)) \
                or not isinstance(seconds, (int, float)) or isinstance(seconds, bool) \
                or not math.isfinite(seconds) or not math.isfinite(started):
            raise ValueError("id, started and seconds are needed")
        if not self.store.config.play_log or seconds <= 0:
            return
        track = self.library.tracks.get(track_id)
        if track is None:
            return
        try:
            when = datetime.fromtimestamp(started / 1000)
        except (ValueError, OverflowError, OSError):
            raise ValueError("started is not a time") from None
        self.play_log.add(when, min(float(seconds), 24 * 3600), {
            "title": track.name, "artist": track.artist, "album": track.album,
            "duration_ms": track.duration_ms, "path": str(track.path) if track.path else None})

    def set_play_log(self, on: bool) -> None:
        with self._change(rescan=False) as cfg:
            cfg.play_log = on

    # ---- alarm ----------------------------------------------------------------

    RING_FOR = 30 * 60  # an alarm nobody stops stops ringing after this (seconds)
    SNOOZE_MINUTES = 9

    def _read_fired(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self._alarm_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return {k: [t for t in v if isinstance(t, str)] for k, v in data.items()
                    if isinstance(k, str) and isinstance(v, list)}
        # 0.6 kept one time for its one alarm, which is now the first.
        try:
            legacy = (self.store.home / "alarm-fired.txt").read_text(encoding="utf-8").strip()
        except OSError:
            legacy = ""
        return {a["id"]: [legacy] for a in self.store.config.alarms} if legacy else {}

    def set_alarms(self, value) -> None:
        alarms = clean_alarms(value)
        with self._change(rescan=False) as cfg:
            cfg.alarms = alarms
        with self._lock:
            snooze = self._snooze
            if snooze and not any(a["id"] == snooze["alarm_id"] and a["on"] for a in alarms):
                self._snooze = None  # its alarm was deleted or turned off
                logger.info("Alarm: snooze cancelled (the alarm was changed)")

    def set_alarm(self, value) -> None:
        """The single alarm of 0.6's settings: the whole list becomes it."""
        alarm = clean_alarm(value)
        self.set_alarms([] if alarm is None else [{**value, "id": alarm["id"]}])

    def check_alarm(self, now: datetime | None = None) -> bool:
        """Set an alarm off if it's due: at its time, or up to five minutes
        late (the computer was busy or just woke), or a snoozed alarm when
        its snooze is up. Opens a window when none is open. Returns
        whether one went off."""
        when = now.timestamp() if now else time.time()
        now = now or datetime.now()
        with self._lock:
            snooze = self._snooze
            if snooze and when >= snooze["until"]:
                self._snooze = None
                self._ring(snooze, snoozed=True)
                fired = True
            else:
                fired = self._check_due(now)
        if fired and not self.page_open():
            self._open_window()
        return fired

    def _check_due(self, now: datetime) -> bool:
        due_now: list[tuple[dict, str]] = []
        for alarm in self.store.config.alarms:
            if not alarm.get("on"):
                continue
            hour, minute = map(int, alarm["time"].split(":"))
            fired = self._alarm_fired.get(alarm["id"], [])
            for days_back in (0, 1):  # an alarm at 23:58 is still due at 00:01
                due = (now - timedelta(days=days_back)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                key = due.strftime("%Y-%m-%d %H:%M")
                # A time the clocks skip (spring forward) goes off once they have.
                late = timedelta(minutes=65 if _skipped(due) else 5)
                if _goes_off_on(alarm, due) and timedelta(0) <= now - due < late and key not in fired:
                    due_now.append((alarm, key))
                    break
        if not due_now:
            return False
        # Several due at once ring once: the first in the list. All of them
        # count as gone off, so the others don't ring a moment later.
        recent = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        for alarm, key in due_now:
            keys = [k for k in self._alarm_fired.get(alarm["id"], []) if k >= recent] + [key]
            self._alarm_fired[alarm["id"]] = keys[-10:]
        ids = {a["id"] for a in self.store.config.alarms}
        self._alarm_fired = {k: v for k, v in self._alarm_fired.items() if k in ids}
        try:
            self._alarm_file.parent.mkdir(parents=True, exist_ok=True)
            self._alarm_file.write_text(json.dumps(self._alarm_fired), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not save the alarm time: %s", exc)
        # One-off ones have had their day: they turn themselves off, as a
        # phone's alarms do.
        once = {alarm["id"] for alarm, _ in due_now if alarm.get("date")}
        if once:
            try:
                with self._change(rescan=False) as cfg:
                    cfg.alarms = [{**a, "on": False} if a["id"] in once else a for a in cfg.alarms]
            except OSError as exc:
                logger.warning("Could not turn off a one-off alarm: %s", exc)
        # A schedule switch changes what's playing (the page ignores it
        # when nothing is): the last one due wins.
        switches = [alarm for alarm, _ in due_now if alarm.get("action") == "switch"]
        if switches:
            switch = switches[-1]
            self.remote_commands.append({"cmd": "switch", "kind": switch["source"]["kind"], "id": switch["source"]["id"],
                                         "name": switch["source"].get("name", ""), "label": switch.get("label", ""),
                                         "at": time.time()})
            del self.remote_commands[:-20]
            logger.info("Schedule: switching to %s", switch["source"].get("name") or switch["source"]["id"])
        rings = [alarm for alarm, _ in due_now if alarm.get("action") != "switch"]
        if not rings:
            return False
        alarm = rings[0]
        self._snooze = None  # a new alarm takes over from a snoozed one
        self._ring({"alarm_id": alarm["id"], "label": alarm.get("label", ""),
                    "source": dict(alarm["source"])}, snoozed=False)
        return True

    def _ring(self, alarm: dict, snoozed: bool) -> None:
        """Set an alarm ringing (called with the lock held)."""
        seq = max(time.time_ns(), (self.alarm_request or {}).get("seq", 0) + 1)
        at = time.time()
        info = {"alarm_id": alarm["alarm_id"], "label": alarm["label"],
                "source": dict(alarm["source"]), "snoozed": snoozed}
        self.alarm_request = {"seq": seq, **info, "at": at}
        self._ringing = {"seq": seq, **info, "source": dict(alarm["source"]), "at": at}
        logger.info("Alarm%s%s: playing %s", " (after snoozing)" if snoozed else "",
                    f" \"{alarm['label']}\"" if alarm["label"] else "",
                    alarm["source"].get("name") or alarm["source"]["id"])

    def _ringing_now(self) -> dict | None:
        ringing = self._ringing
        if ringing and time.time() - ringing["at"] >= self.RING_FOR:
            self._ringing = ringing = None
        return ringing

    def alarm_state(self) -> dict:
        with self._lock:
            ringing = self._ringing_now()
            snooze = self._snooze
            return {"ringing": copy.deepcopy(ringing),
                    "snoozed_until": snooze["until"] if snooze else None}

    def snooze_alarm(self, minutes: int = SNOOZE_MINUTES) -> dict:
        """Snooze the ringing alarm. Raises AlarmNotRinging when none is."""
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 60:
            raise ValueError("minutes must be a whole number from 1 to 60")
        with self._lock:
            ringing = self._ringing_now()
            if ringing is None:
                raise AlarmNotRinging("No alarm is ringing")
            self._ringing = None
            self._snooze = {"until": time.time() + minutes * 60, "alarm_id": ringing["alarm_id"],
                            "label": ringing["label"], "source": dict(ringing["source"])}
        logger.info("Alarm snoozed for %d minute%s", minutes, "" if minutes == 1 else "s")
        return self.alarm_state()

    def stop_alarm(self) -> bool:
        """Stop the ringing alarm and any snooze. Returns whether there was
        either to stop."""
        with self._lock:
            had = self._ringing_now() is not None or self._snooze is not None
            self._ringing = self._snooze = None
        if had:
            logger.info("Alarm stopped")
        return had

    def next_alarm(self, now: datetime | None = None) -> dict | None:
        """The soonest alarm to come (a snoozed one counts), or None."""
        now = now or datetime.now()
        best: tuple[float, datetime, dict] | None = None
        for alarm in self.store.config.alarms:
            if not alarm.get("on") or alarm.get("action") == "switch":
                continue
            hour, minute = map(int, alarm["time"].split(":"))
            if alarm.get("date"):
                first = datetime.strptime(alarm["date"], "%Y-%m-%d")
                days = [(first - now.replace(hour=0, minute=0, second=0, microsecond=0)).days]
            else:
                days = range(8)
            for ahead in days:
                due = (now + timedelta(days=ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                if due > now and _goes_off_on(alarm, due):
                    at = due.astimezone()
                    if best is None or at.timestamp() < best[0]:
                        best = (at.timestamp(), at, {"alarm_id": alarm["id"], "label": alarm.get("label", ""),
                                                     "source_name": alarm["source"].get("name", "")})
                    break
        with self._lock:
            snooze = self._snooze
        if snooze and (best is None or snooze["until"] < best[0]):
            at = datetime.fromtimestamp(snooze["until"]).astimezone()
            best = (snooze["until"], at, {"alarm_id": snooze["alarm_id"], "label": snooze["label"],
                                          "source_name": snooze["source"].get("name", "")})
        if best is None:
            return None
        return {"at": best[1].isoformat(timespec="seconds"), **best[2]}

    def watch_alarm(self) -> threading.Thread:
        def loop():
            while True:
                try:
                    self.check_alarm()
                except Exception:
                    logger.exception("Alarm check failed")
                time.sleep(15)

        thread = threading.Thread(target=loop, name="alarm", daemon=True)
        thread.start()
        return thread

    def _open_window(self) -> None:
        if self.show_window and time.time() - self._window_asked > 20:
            self._window_asked = time.time()
            try:
                self.show_window()
            except Exception:
                logger.exception("Could not open a player window")

    # ---- phone remote -----------------------------------------------------------

    def set_remote(self, on: bool) -> None:
        with self._change(rescan=False) as cfg:
            cfg.remote = on
            if on and not cfg.remote_token:
                cfg.remote_token = remote.new_token()
        self.apply_remote()

    def new_remote_link(self) -> None:
        with self._change(rescan=False) as cfg:
            cfg.remote_token = remote.new_token()

    def apply_remote(self) -> None:
        """Start or stop the remote's server to match the settings."""
        cfg = self.store.config
        with self._lock:
            running = self.remote_server
            if cfg.remote and cfg.remote_token and running is None:
                try:
                    self.remote_server = remote.RemoteServer(self, cfg.remote_port)
                    self.remote_error = None
                except OSError as exc:
                    self.remote_error = f"The phone remote couldn't start on port {cfg.remote_port}: {exc}"
                    logger.warning(self.remote_error)
            elif not cfg.remote:
                self.remote_server = None
                self.remote_error = None
        if running is not None and not cfg.remote:
            running.stop()

    def car_link(self) -> str:
        """The link to the car page with this player's stations in it
        (after the #, so they stay on the phone). See car/car.js."""
        with self._lock:
            stations = [[s["name"], s["url"], *([s["tags"]] if s.get("tags") else [])]
                        for s in self.store.config.stations]
        data = json.dumps({"v": 1, "s": stations}, ensure_ascii=False, separators=(",", ":")).encode()
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)  # raw deflate, as DecompressionStream wants
        packed = base64.urlsafe_b64encode(packer.compress(data) + packer.flush()).rstrip(b"=").decode()
        return f"{CAR_PAGE}#s={packed}"

    def remote_info(self) -> dict:
        cfg = self.store.config
        address = remote.lan_address() if self.remote_server else None
        url = (f"http://{address}:{self.remote_server.port}/r/{cfg.remote_token}/"
               if address and self.remote_server else None)
        return {"on": cfg.remote, "url": url, "error": self.remote_error,
                "network": address is not None or not self.remote_server}

    def remote_command(self, command: dict) -> None:
        if command["cmd"] in ("snooze", "stop_alarm"):
            # Done here and now, so it works with no window open; the page
            # is told too, to pause and close its alarm screen. Nothing
            # ringing (or snoozed, for stop): nothing to do.
            if command["cmd"] == "snooze":
                try:
                    self.snooze_alarm(self.SNOOZE_MINUTES)
                except AlarmNotRinging:
                    return
            elif not self.stop_alarm():
                return
            with self._lock:
                self.remote_commands.append({**command, "at": time.time()})
                del self.remote_commands[:-20]
            return
        with self._lock:
            self.remote_commands.append({**command, "at": time.time()})
            del self.remote_commands[:-20]
        if not self.page_open():
            self._open_window()

    def set_player_status(self, data: dict) -> None:
        status = {}
        for key in ("mode", "title", "artist", "album", "station", "track", "queue"):
            if isinstance(data.get(key), str):
                status[key] = data[key][:300]
        for key in ("playing",):
            if isinstance(data.get(key), bool):
                status[key] = data[key]
        for key in ("volume", "position", "duration", "sleep_left"):
            if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
                status[key] = data[key]
        if data.get("sleep_left") == "track":
            status["sleep_left"] = "track"
        self.player_status = status

    def remote_view(self) -> dict:
        """What the phone shows: what's playing, and what it can play."""
        with self._lock:
            lib = self.library
        return {
            "page_open": self.page_open(),
            "status": self.player_status if self.page_open() else {},
            "playlists": [{"id": p["id"], "name": p["name"], "kind": p["kind"]}
                          for p in self.my_summaries() + lib.playlist_summaries()],
            "stations": [{"id": station_id(s["url"]), "name": s["name"]} for s in self.store.config.stations],
            "alarm": self._remote_alarm(),
        }

    def _remote_alarm(self) -> dict:
        state = self.alarm_state()
        with self._lock:
            snooze = copy.deepcopy(self._snooze)
        ringing = state["ringing"]
        label = ringing["label"] if ringing else snooze["label"] if snooze else ""
        until = state["snoozed_until"]
        return {"ringing": ringing is not None, "label": label, "snoozed_until": until,
                # in the computer's time, like "next" (the phone's zone may differ)
                "snoozed_until_text": datetime.fromtimestamp(until).strftime("%H:%M") if until else "",
                "next": self.next_alarm()}

    def play_recording(self, file: str) -> dict:
        """Put a finished recording in the library (the recordings folder
        becomes one of the music folders) and say where to find it."""
        folder = self.recorder.folder.resolve()
        path = Path(file).resolve() if file else None
        if path is None or path.parent != folder or not path.is_file():
            raise ValueError("That recording wasn't found")
        if str(folder) not in self.store.config.music_folders:
            self.add_source("music", str(folder))
        elif self.library.by_path(str(path)) is None:
            self.rescan()  # a recording made since the last scan
        track = self.library.by_path(str(path))
        playlist = next((p for p in self.library.playlists.values()
                         if p.kind == "folder" and Path(p.source).resolve() == folder), None)
        if track is None or playlist is None:
            raise ValueError("That recording can't be played")
        return {"playlist": playlist.id, "track": track.id}

    # ---- my playlists ---------------------------------------------------------

    def _resolve(self, entry: dict) -> tuple[dict, str | None]:
        """A playlist entry as a track (and its file), found by id, then by
        path; a missing one keeps its name and shows as not found."""
        lib = self.library
        track = lib.tracks.get(entry["id"]) or lib.by_path(entry.get("path"))
        if track is not None:
            return track.to_json(), str(track.path) if track.path else None
        return {
            "id": entry["id"], "name": entry.get("name") or "Unknown track", "artist": entry.get("artist"),
            "album": entry.get("album"), "genre": None, "duration_ms": None, "available": False,
        }, None

    def my_summaries(self) -> list[dict]:
        out = []
        for p in self.my_playlists.all():
            tracks = [self._resolve(e)[0] for e in p["entries"]]
            out.append({"id": p["id"], "name": p["name"], "kind": "mine", "source": "",
                        "count": len(tracks), "missing": sum(1 for t in tracks if not t["available"])})
        return sorted(out, key=lambda s: s["name"].lower())

    def playlist_detail(self, pid: str) -> dict | None:
        if not pid.startswith("mine-"):
            return self.library.playlist_detail(pid)
        p = self.my_playlists.get(pid)
        if p is None:
            return None
        tracks = [self._resolve(e)[0] for e in p["entries"]]
        return {"id": pid, "name": p["name"], "kind": "mine", "source": "", "count": len(tracks),
                "missing": sum(1 for t in tracks if not t["available"]), "tracks": tracks}

    def _entries(self, ids, current: list[dict] | None = None) -> list[dict]:
        """Entries for track ids: ones already in the playlist keep what
        they remember (so missing tracks aren't lost), others come from
        the library; unknown ids are dropped."""
        if not isinstance(ids, list) or len(ids) > myplaylists.MAX_ENTRIES:
            raise ValueError("ids must be a list")
        spare: dict[str, list[dict]] = {}
        for e in current or []:
            spare.setdefault(e["id"], []).append(e)
        out = []
        for tid in ids:
            if not isinstance(tid, str):
                continue
            if spare.get(tid):
                out.append(spare[tid].pop(0))
                continue
            t = self.library.tracks.get(tid)
            if t is not None:
                out.append({"id": t.id, "path": str(t.path) if t.path else None,
                            "name": t.name, "artist": t.artist, "album": t.album})
        return out

    def create_playlist(self, name, ids) -> str:
        return self.my_playlists.create(name, self._entries(ids or []))

    def add_to_playlist(self, pid: str, ids) -> None:
        self.my_playlists.add(pid, self._entries(ids))

    def set_playlist_tracks(self, pid: str, ids) -> None:
        self.my_playlists.update_entries(pid, lambda current: self._entries(ids, current))

    def m3u(self, pid: str) -> tuple[str, str] | None:
        """(name, text) of any playlist as an M3U file."""
        if pid.startswith("mine-"):
            p = self.my_playlists.get(pid)
            if p is None:
                return None
            resolved = [self._resolve(e) for e in p["entries"]]
            return p["name"], myplaylists.m3u(p["name"], [r[0] for r in resolved], [r[1] for r in resolved])
        lib = self.library
        pl = lib.playlists.get(pid)
        if pl is None:
            return None
        tracks = [lib.tracks[t] for t in pl.track_ids]
        return pl.name, myplaylists.m3u(pl.name, [t.to_json() for t in tracks],
                                        [str(t.path) if t.path else None for t in tracks])

    # ---- new versions ------------------------------------------------------

    def check_for_update(self, fetch=None) -> threading.Thread | None:
        """Look for a newer release in the background (when allowed)."""
        if not self.store.config.check_updates:
            return None

        def work():
            kwargs = {"fetch": fetch} if fetch else {}
            with self._update_lock:  # one check at a time
                self.latest_version = updates.latest_version(self.store.home / "update-check.json", **kwargs)

        worker = threading.Thread(target=work, name="update-check", daemon=True)
        worker.start()
        return worker

    def keep_checking_for_updates(self) -> threading.Thread:
        """Check now and then daily: it may run at login for weeks."""
        def loop():
            while True:
                worker = self.check_for_update()
                if worker:
                    worker.join()
                time.sleep(updates.CHECK_EVERY)

        thread = threading.Thread(target=loop, name="update-checks", daemon=True)
        thread.start()
        return thread

    def set_own_window(self, on: bool) -> None:
        with self._change(rescan=False) as cfg:
            cfg.own_window = on

    def set_check_updates(self, on: bool) -> None:
        with self._change(rescan=False) as cfg:
            cfg.check_updates = on
        if on:
            self.check_for_update()

    # ---- volume levelling ------------------------------------------------------

    def loudness(self, track_id: str) -> dict | None:
        """How loud a track is: from its tags, else a remembered
        measurement. None for unknown tracks; {"lufs": None} when it
        still needs measuring."""
        track = self.library.tracks.get(track_id)
        if track is None or track.path is None:
            return None
        tags = self.tag_cache.get(track.path)
        return (loudness.from_tags(tags.gain_db, tags.peak)
                or self.loudness_store.get(track.path)
                or {"lufs": None, "peak": None, "source": None})

    def set_loudness(self, track_id: str, lufs, peak) -> None:
        track = self.library.tracks.get(track_id)
        if track is None or track.path is None:
            raise LookupError("No such track")
        lufs, peak = loudness.valid(lufs, peak)
        self.loudness_store.put(track.path, lufs, peak)

    # ---- radio -----------------------------------------------------------

    def station_url(self, sid: str) -> str | None:
        with self._lock:
            for s in self.store.config.stations:
                if station_id(s["url"]) == sid:
                    return s["url"]
        return None

    def set_title(self, url: str, title: str | None) -> None:
        with self._lock:
            self._titles[url] = title

    def title(self, url: str) -> str | None:
        with self._lock:
            return self._titles.get(url)

    # ---- artwork -----------------------------------------------------------

    def artwork(self, track_id: str) -> tuple[bytes, str] | None:
        """A track's embedded picture, else a cover image in its folder."""
        track = self.library.tracks.get(track_id)
        if track is None or track.path is None:
            return None
        found = read_art(track.path)
        if found:
            return found
        cover = folder_cover(track.path)
        if cover is None:
            return None
        try:
            if cover.stat().st_size > MAX_ART_BYTES:
                return None
            data = cover.read_bytes()
        except OSError:
            return None
        mime = image_type(data)
        return (data, mime) if mime else None

    @contextmanager
    def _change(self, rescan: bool = True):
        """Apply a config change atomically and save it, then rebuild."""
        with self._lock:
            snapshot = copy.deepcopy(self.store.config)
            try:
                yield self.store.config
                self.store.save()
            except BaseException:
                self.store.config = snapshot
                raise
            if rescan:
                self._generation += 1
        if rescan:
            self._rebuild()

    # ---- mutations ------------------------------------------------------

    def add_source(self, kind: str, path: str) -> None:
        if not path.strip():
            raise ValueError("Path is empty")
        # Same normalisation as the CLI, so one folder is stored one way.
        clean = str(Path(path.strip()).expanduser().resolve())
        if not Path(clean).exists():
            raise ValueError(f"Not found: {clean}")
        with self._change() as cfg:
            target = _source_list(cfg, kind)
            if clean not in target:
                target.append(clean)

    def remove_source(self, kind: str, path: str) -> None:
        with self._change() as cfg:
            target = _source_list(cfg, kind)
            if path in target:
                target.remove(path)

    def add_remap(self, src: str, dst: str) -> None:
        if not src.strip() or not dst.strip():
            raise ValueError("Both 'from' and 'to' are needed")
        remap = {"from": src.strip(), "to": dst.strip()}
        with self._change() as cfg:
            if remap not in cfg.path_remaps:
                cfg.path_remaps.append(remap)

    def remove_remap(self, src: str, dst: str) -> None:
        with self._change() as cfg:
            cfg.path_remaps[:] = [
                r for r in cfg.path_remaps if not (r.get("from") == src and r.get("to") == dst)
            ]

    def add_station(self, name: str, url: str, logo: str | None = None, logo_url: str | None = None,
                    tags=None) -> dict:
        """`logo`: a logo already kept (from Recently played); `logo_url`:
        one the finder listed, fetched now; `tags`: your own labels."""
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("Stream URL must start with http:// or https://")
        # Network fetches (.pls/.m3u, the logo) happen outside the lock.
        station = {"name": name.strip() or url, "url": resolve_stream_url(url)}
        name_ = self._keep_logo(logo, logo_url)
        if name_:
            station["logo"] = name_
        with self._change(rescan=False) as cfg:
            if clean_tags(tags):
                station["tags"] = _same_spelling(clean_tags(tags), cfg.stations)
            if all(s.get("url") != station["url"] for s in cfg.stations):
                cfg.stations.append(station)
        return station

    def _keep_logo(self, logo: str | None, logo_url: str | None) -> str | None:
        if logo and self.logos.has(logo):
            return logo
        if logo_url:
            got = self.directory.logo(logo_url)
            if got:
                try:
                    return self.logos.save(got[0])
                except (ValueError, OSError) as exc:
                    logger.info("Couldn't keep the logo %s: %s", logo_url, exc)
        return None

    def _prune_logos(self) -> None:
        with self._lock:
            cfg = self.store.config
            keep = {s["logo"] for s in cfg.stations + cfg.recent_stations if s.get("logo")}
        self.logos.prune(keep)

    def remove_station(self, url: str) -> None:
        with self._change(rescan=False) as cfg:
            cfg.stations[:] = [s for s in cfg.stations if s.get("url") != url]
        self._prune_logos()

    def set_station_logo(self, url: str, image: bytes | None = None, find: bool = False) -> bool:
        """Give a saved station a logo: a picture chosen from a file
        (`image`), the one Radio Browser has for it (`find`; False if it
        has none), or none at all. LookupError for an unknown station."""
        with self._lock:
            if not any(s.get("url") == url for s in self.store.config.stations):
                raise LookupError("No such station")
        name = None
        try:
            if find:
                got = self.directory.find_logo(url)  # network: outside the lock
                if got is None:
                    return False
                name = self.logos.save(got[0])
            elif image is not None:
                name = self.logos.save(image)
        except OSError as exc:
            logger.warning("Couldn't keep a logo: %s", exc)
            raise ValueError("Couldn't save the logo on this computer") from None
        with self._change(rescan=False) as cfg:
            for st in cfg.stations + cfg.recent_stations:
                if st.get("url") == url:
                    if name:
                        st["logo"] = name
                    else:
                        st.pop("logo", None)
        self._prune_logos()
        return True

    def can_preview(self, url: str) -> bool:
        """Whether a station that isn't saved may be relayed: the finder
        listed it, or it's in Recently played."""
        if self.directory.listed(url):
            return True
        with self._lock:
            return any(s.get("url") == url for s in self.store.config.recent_stations)

    def station_played(self, name: str, url: str, logo_url: str | None = None) -> None:
        """Put a station at the top of Recently played. Only saved
        stations and ones the finder listed count."""
        name = name.strip()[:200]
        with self._lock:
            saved = next((s for s in self.store.config.stations if s.get("url") == url), None)
            before = next((s for s in self.store.config.recent_stations if s.get("url") == url), None)
        if saved is None and before is None and not self.directory.listed(url):
            raise ValueError("Not a station the player knows")
        entry = {"name": (saved or {}).get("name") or name or (before or {}).get("name") or url, "url": url}
        logo = (saved or {}).get("logo") or (before or {}).get("logo") or self._keep_logo(None, logo_url)
        if logo:
            entry["logo"] = logo
        with self._change(rescan=False) as cfg:
            cfg.recent_stations[:] = [entry] + [s for s in cfg.recent_stations if s.get("url") != url][:MAX_RECENT - 1]
        self._prune_logos()

    def forget_recent(self) -> None:
        with self._change(rescan=False) as cfg:
            cfg.recent_stations.clear()
        self._prune_logos()

    def update_station(self, url: str, name=None, new_url=None, tags=None) -> None:
        """Rename a station, change its address and/or its tags (None
        leaves each as it is). Raises LookupError for an unknown station."""
        if name is not None:
            name = str(name).strip()
            if not name:
                raise ValueError("Station name is empty")
        resolved = None
        if new_url is not None:
            new_url = str(new_url).strip()
            if not new_url.startswith(("http://", "https://")):
                raise ValueError("Stream URL must start with http:// or https://")
            resolved = resolve_stream_url(new_url)  # network: outside the lock
        with self._change(rescan=False) as cfg:
            station = next((s for s in cfg.stations if s.get("url") == url), None)
            if station is None:
                raise LookupError("No such station")
            # alarms find stations by an id made from the address
            wakes = [a["source"] for a in cfg.alarms
                     if a["source"].get("kind") == "station" and a["source"].get("id") == station_id(url)]
            if resolved is not None and resolved != url:
                if any(r["url"] == url and not r["done"] and not r.get("stopping") for r in self.recorder.status()):
                    raise ValueError("Stop recording this station before changing its link")
                if any(s.get("url") == resolved for s in cfg.stations):
                    raise ValueError("That stream is already one of your stations")
                station["url"] = resolved
                for source in wakes:
                    source["id"] = station_id(resolved)
            if name:
                station["name"] = name
                for source in wakes:
                    source["name"] = name[:200]
            if tags is not None:
                if clean_tags(tags):
                    station["tags"] = _same_spelling(clean_tags(tags), [s for s in cfg.stations if s is not station])
                else:
                    station.pop("tags", None)

    def move_station(self, url: str, to) -> None:
        if not isinstance(to, int) or isinstance(to, bool):
            raise ValueError("to must be a position (0 is first)")
        with self._change(rescan=False) as cfg:
            index = next((i for i, s in enumerate(cfg.stations) if s.get("url") == url), None)
            if index is None:
                raise LookupError("No such station")
            station = cfg.stations.pop(index)
            cfg.stations.insert(max(0, min(to, len(cfg.stations))), station)


def _goes_off_on(alarm: dict, day: datetime) -> bool:
    """Whether an alarm goes off on this day: its date, or one of its days."""
    if alarm.get("date"):
        return day.strftime("%Y-%m-%d") == alarm["date"]
    return day.weekday() in alarm["days"]


def _skipped(local: datetime) -> bool:
    """Whether a local time doesn't exist (skipped when the clocks go forward)."""
    try:
        return datetime.fromtimestamp(local.timestamp()).replace(tzinfo=None) != local
    except (OverflowError, OSError, ValueError):
        return False


def _exists(path: Path) -> bool:
    """Whether `path` is there; a drive that has dropped, or a folder the
    system won't let us look in, counts as not there."""
    try:
        return path.exists()
    except OSError:
        return False


def _source_summaries(lib: Library, config: dict) -> list[dict]:
    """Each configured source with what the library found in it."""
    out = []
    for kind, key in (("playlist", "playlist_sources"), ("music", "music_folders")):
        for raw in config[key]:
            path = Path(raw).expanduser()
            src = str(path)  # how the library wrote it into Playlist.source
            inside = src.rstrip("/\\") + os.sep
            want = "itunes" if kind == "playlist" else "folder"
            pls = [p for p in lib.playlists.values()
                   if p.kind == want and p.id != OPENED_ID and (p.source == src or p.source.startswith(inside))]
            tracks = set()
            for p in pls:
                tracks.update(p.track_ids)
            out.append({"kind": kind, "path": raw, "name": path.name or raw, "exists": _exists(path),
                        "tracks": len(tracks), "playlists": len(pls)})
    return out


def _same_spelling(tags: list[str], stations: list[dict]) -> list[str]:
    """Tags spelled the way other stations already have them ("Jazz"
    becomes "jazz" when that's in use), so one tag isn't shown twice."""
    known = {t.casefold(): t for s in stations for t in s.get("tags", [])}
    return [known.get(t.casefold(), t) for t in tags]


def station_id(url: str) -> str:
    return _short_hash("station", url)


def _source_list(cfg, kind: str) -> list[str]:
    if kind == "playlist":
        return cfg.playlist_sources
    if kind == "music":
        return cfg.music_folders
    raise ValueError(f"Unknown source kind: {kind}")


def make_handler(app: PlayerApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"StationPlayer/{__version__}"

        def handle(self):
            # Browsers drop audio connections whenever they seek or skip.
            # That surfaces as BrokenPipe/ConnectionReset on macOS and
            # Linux and ConnectionAborted on Windows; none is an error.
            try:
                super().handle()
            except ConnectionError:
                pass

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def log_message(self, fmt, *args):  # quieter than stderr-per-request
            logger.debug("%s - %s", self.address_string(), fmt % args)

        # ---- helpers ----------------------------------------------------

        def _host_ok(self) -> bool:
            try:
                host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            except ValueError:
                return False
            return host in _ALLOWED_HOSTS

        def _send_json(self, payload, status=HTTPStatus.OK):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_download(self, body: bytes, name: str, ext: str, ctype: str):
            safe = re.sub(r'[^\w .()-]+', "_", name, flags=re.ASCII).strip(" ._") or "playlist"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"{safe}.{ext}\"; filename*=UTF-8''{quote(name)}.{ext}")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _error(self, status, message):
            self._send_json({"error": message}, status)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 1_000_000:
                raise ValueError("Bad Content-Length")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object")
            return data

        # ---- GET --------------------------------------------------------

        def do_HEAD(self):
            self.do_GET(head_only=True)

        def do_GET(self, head_only: bool = False):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Bad host")
            # Browsers say where a request came from. Other websites may
            # not embed the player's audio, art or radio relay (that would
            # also tell them it's running and what's in the library).
            if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                return self._error(HTTPStatus.FORBIDDEN, "Cross-site request")
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send_json(app.state())
            if path.startswith("/api/playlists/") and path.endswith(".m3u"):
                found = app.m3u(path[len("/api/playlists/"):-len(".m3u")])
                if found is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No such playlist")
                return self._send_download(found[1].encode("utf-8"), found[0], "m3u", "audio/x-mpegurl")
            if path.startswith("/api/playlists/"):
                detail = app.playlist_detail(path.rsplit("/", 1)[1])
                if detail is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No such playlist")
                return self._send_json(detail)
            if path.startswith("/audio/"):
                return self._serve_audio(path.rsplit("/", 1)[1], head_only)
            if path.startswith("/art/"):
                return self._serve_art(path.rsplit("/", 1)[1], head_only)
            if path.startswith("/radio/"):
                return self._serve_radio(path.rsplit("/", 1)[1], head_only)
            if path.startswith("/logo/"):
                return self._serve_logo(path.rsplit("/", 1)[1], head_only)
            if path == "/api/search":
                query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
                return self._send_json(app.library.search(query[:200]))
            if path.startswith("/api/loudness/"):
                found = app.loudness(path.rsplit("/", 1)[1])
                if found is None:
                    return self._error(HTTPStatus.NOT_FOUND, "Track not available")
                return self._send_json(found)
            if path == "/api/inbox":
                only = parse_qs(urlparse(self.path).query).get("only", [None])[0]
                return self._send_json(app.inbox(only if only in ("station", "notice") else None))
            if path == "/api/playlog.csv":
                q = parse_qs(urlparse(self.path).query)
                try:
                    first = date.fromisoformat(q.get("from", [""])[0])
                    last = date.fromisoformat(q.get("to", [""])[0])
                except ValueError:
                    return self._error(HTTPStatus.BAD_REQUEST, "from and to must be dates (YYYY-MM-DD)")
                body, _count = app.play_log.csv(first, last)
                return self._send_download(body.encode("utf-8-sig"), f"Play log {first} to {last}",
                                           "csv", "text/csv; charset=utf-8")
            if path == "/api/car":
                with app._lock:
                    http_only = [st["name"] for st in app.store.config.stations
                                 if st["url"].lower().startswith("http:")]
                return self._send_json({"url": app.car_link(), "http_only": http_only})
            if path == "/api/car/qr.svg":
                body = qr.svg(app.car_link()).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
                return None
            if path == "/api/remote/qr.svg":
                info = app.remote_info()
                if not info["url"]:
                    return self._error(HTTPStatus.NOT_FOUND, "The phone remote is off")
                body = qr.svg(info["url"]).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
                return None
            if path == "/api/suggestions":
                return self._send_json({"suggestions": app.suggestions()})
            if path == "/api/recordings/list":
                return self._send_json(app.recordings_list())
            if path == "/api/recordings":
                return self._send_json({"recordings": app.recorder.status(),
                                        "folder": str(app.recorder.folder)})
            if path.startswith("/api/directory/"):
                return self._serve_directory(path, head_only)
            if path == "/api/nowplaying":
                q = parse_qs(urlparse(self.path).query)
                sid, preview = q.get("station", [""])[0], q.get("u", [""])[0]
                url = preview if preview and app.can_preview(preview) else app.station_url(sid)
                if url is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No such station")
                return self._send_json({"title": app.title(url)})
            return self._serve_static(path, head_only)

        def _serve_directory(self, path: str, head_only: bool):
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if path == "/api/directory/logo":
                found = app.directory.logo(q.get("u", ""))
                if found is None:
                    return self._error(HTTPStatus.NOT_FOUND, "No logo")
                body, ctype = found
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)
                return None
            try:
                if path == "/api/directory/search":
                    try:
                        offset = int(q.get("offset", "0") or 0)
                    except ValueError:
                        offset = 0
                    return self._send_json(app.directory.search_page(
                        q=q.get("q", ""), tag=q.get("tag", ""), country=q.get("country", ""),
                        order=q.get("order", "votes"), offset=offset))
                if path == "/api/directory/somafm":
                    return self._send_json({"stations": app.directory.somafm()})
            except DirectoryError as exc:
                return self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            return self._error(HTTPStatus.NOT_FOUND, "Not found")

        def _serve_art(self, track_id: str, head_only: bool):
            found = app.artwork(track_id)
            if found is None:
                return self._error(HTTPStatus.NOT_FOUND, "No artwork")
            data, mime = found
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def _serve_radio(self, sid: str, head_only: bool):
            """Relay a station, keeping the song titles it sends. "preview"
            relays a station the finder listed (?u=), from public hosts only."""
            check = None
            if sid == "preview":
                url = parse_qs(urlparse(self.path).query).get("u", [""])[0]
                if not app.can_preview(url):
                    return self._error(HTTPStatus.NOT_FOUND, "Not a station the finder listed")
                check = app.directory.public
            else:
                url = app.station_url(sid)
            if url is None:
                return self._error(HTTPStatus.NOT_FOUND, "No such station")
            if head_only:
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "Use GET")
            app.set_title(url, None)  # no stale song from the last visit
            try:
                stream = radio.open_stream(url, user_agent=f"StationPlayer/{__version__}", check=check)
            except radio.StreamError as exc:
                logger.warning("Radio relay for %s failed, playing it directly: %s", url, exc)
                return self._error(HTTPStatus.BAD_GATEWAY, "Couldn't connect to this station")
            try:
                ctype = stream.content_type
                if ctype is None:
                    return self._error(HTTPStatus.BAD_GATEWAY, "This station isn't a plain audio stream")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                # A paused tab stops reading; don't hold the station forever.
                self.connection.settimeout(radio.READ_SECONDS)
                for chunk in radio.relay(stream, lambda t: app.set_title(url, t)):
                    self.wfile.write(chunk)
            except OSError:
                pass  # the listener or the station went away
            finally:
                stream.close()

        def _serve_logo(self, name: str, head_only: bool):
            found = app.logos.get(name)
            if found is None:
                return self._error(HTTPStatus.NOT_FOUND, "No such logo")
            body, ctype = found
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")  # named by its contents
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve_static(self, path: str, head_only: bool):
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (STATIC_DIR / name).resolve()
            if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            ctype = _STATIC_TYPES.get(target.suffix.lower())
            if ctype is None:
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            body = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            if target.suffix.lower() == ".html":
                self.send_header("Content-Security-Policy", _CSP)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve_audio(self, track_id: str, head_only: bool):
            track = app.library.tracks.get(track_id)
            ctype = content_type(track.path) if track and track.path else None
            if ctype is None:  # unknown id, missing file, or not audio
                return self._error(HTTPStatus.NOT_FOUND, "Track not available")
            try:
                size = track.path.stat().st_size
            except OSError:
                return self._error(HTTPStatus.NOT_FOUND, "File is gone")
            start, end, status = 0, size - 1, HTTPStatus.OK
            range_header = self.headers.get("Range")
            if range_header:
                try:
                    parsed = parse_range(range_header, size)
                except ValueError:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if parsed:
                    start, end = parsed
                    status = HTTPStatus.PARTIAL_CONTENT
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            try:
                with track.path.open("rb") as fh:
                    fh.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = fh.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except ConnectionError:
                pass  # the browser seeks by dropping the connection

        # ---- POST -------------------------------------------------------

        def do_POST(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Bad host")
            if self.headers.get(API_HEADER) != "1":
                return self._error(HTTPStatus.FORBIDDEN, f"Missing {API_HEADER} header")
            path = urlparse(self.path).path
            try:
                data = self._read_json()
                if path == "/api/rescan":
                    app.rescan()
                elif path == "/api/sources":
                    app.add_source(str(data.get("kind", "")), str(data.get("path", "")))
                elif path == "/api/sources/remove":
                    app.remove_source(str(data.get("kind", "")), str(data.get("path", "")))
                elif path == "/api/remaps":
                    app.add_remap(str(data.get("from", "")), str(data.get("to", "")))
                elif path == "/api/remaps/remove":
                    app.remove_remap(str(data.get("from", "")), str(data.get("to", "")))
                elif path == "/api/stations":
                    app.add_station(str(data.get("name", "")), str(data.get("url", "")),
                                    logo=str(data.get("logo") or "") or None,
                                    logo_url=str(data.get("logo_url") or "") or None,
                                    tags=data.get("tags"))
                elif path == "/api/stations/played":
                    app.station_played(str(data.get("name", "")), str(data.get("url", "")),
                                       logo_url=str(data.get("logo_url") or "") or None)
                elif path == "/api/stations/recent/clear":
                    app.forget_recent()
                elif path == "/api/stations/logo":
                    image = data.get("image")
                    try:
                        raw = base64.b64decode(image, validate=True) if isinstance(image, str) and image else None
                    except binascii.Error:
                        raise ValueError("That picture couldn't be read") from None
                    try:
                        found = app.set_station_logo(str(data.get("url", "")), image=raw, find=data.get("find") is True)
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "No such station")
                    except DirectoryError as exc:
                        return self._error(HTTPStatus.BAD_GATEWAY, str(exc))
                    if not found:
                        return self._error(HTTPStatus.NOT_FOUND, "Radio Browser has no logo for this station")
                elif path == "/api/stations/remove":
                    app.remove_station(str(data.get("url", "")))
                elif path == "/api/stations/update":
                    try:
                        app.update_station(str(data.get("url", "")), data.get("name"), data.get("new_url"),
                                           data.get("tags"))
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "No such station")
                elif path == "/api/stations/move":
                    try:
                        app.move_station(str(data.get("url", "")), data.get("to"))
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "No such station")
                elif path == "/api/stations/test":
                    return self._send_json(app.test_station(str(data.get("url", ""))))
                elif path == "/api/pick":
                    try:
                        return self._send_json(app.picker.pick(str(data.get("kind", ""))))
                    except setup.PickerBusy as exc:
                        return self._error(HTTPStatus.CONFLICT, str(exc))
                    except setup.PickerUnavailable as exc:
                        return self._send_json({"error": str(exc), "unsupported": True},
                                               HTTPStatus.NOT_IMPLEMENTED)
                    except setup.PickerFailed as exc:
                        return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                elif path == "/api/locate":
                    found = app.locate(str(data.get("folder", "")))
                    return self._send_json({**app.state(), **found})
                elif path == "/api/desktop":
                    try:
                        if "shortcut" in data:
                            app.desktop.set_shortcut(bool(data["shortcut"]))
                        if "login" in data:
                            app.desktop.set_login(bool(data["login"]))
                    except OSError as exc:
                        return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Couldn't change shortcuts: {exc}")
                elif path == "/api/tracks":
                    ids = data.get("ids")
                    if not isinstance(ids, list) or len(ids) > 40_000:
                        raise ValueError("ids must be a list")
                    return self._send_json({"tracks": app.library.tracks_json([i for i in ids if isinstance(i, str)])})
                elif path == "/api/loudness":
                    try:
                        app.set_loudness(str(data.get("id", "")), data.get("lufs"), data.get("peak"))
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "Track not available")
                    return self._send_json({"ok": True})
                elif path == "/api/settings":
                    if "check_updates" in data:
                        if not isinstance(data["check_updates"], bool):
                            raise ValueError("check_updates must be true or false")
                        app.set_check_updates(data["check_updates"])
                    for key, apply in (("own_window", app.set_own_window), ("play_log", app.set_play_log),
                                       ("remote", app.set_remote)):
                        if key in data:
                            if not isinstance(data[key], bool):
                                raise ValueError(f"{key} must be true or false")
                            apply(data[key])
                    if "alarms" in data:
                        app.set_alarms(data["alarms"])
                    elif "alarm" in data:
                        app.set_alarm(data["alarm"])
                elif path == "/api/alarm/snooze":
                    minutes = data.get("minutes", PlayerApp.SNOOZE_MINUTES)
                    try:
                        app.snooze_alarm(minutes)
                    except AlarmNotRinging as exc:
                        return self._error(HTTPStatus.CONFLICT, str(exc))
                elif path == "/api/alarm/stop":
                    app.stop_alarm()
                elif path == "/api/played":
                    app.played(data.get("id"), data.get("started"), data.get("seconds"))
                    return self._send_json({"ok": True})
                elif path == "/api/page-closed":
                    app.page_closed()
                    return self._send_json({"ok": True})
                elif path == "/api/remote/status":
                    app.set_player_status(data)
                    return self._send_json({"ok": True})
                elif path == "/api/remote/new-link":
                    app.new_remote_link()
                elif path == "/api/my-playlists":
                    pid = app.create_playlist(data.get("name"), data.get("ids"))
                    return self._send_json({**app.state(), "created": pid})
                elif path.startswith("/api/my-playlists/"):
                    parts = path.split("/")
                    if len(parts) != 5:
                        return self._error(HTTPStatus.NOT_FOUND, "Not found")
                    pid, action = parts[3], parts[4]
                    try:
                        if action == "rename":
                            app.my_playlists.rename(pid, data.get("name"))
                        elif action == "delete":
                            app.my_playlists.delete(pid)
                        elif action == "add":
                            app.add_to_playlist(pid, data.get("ids"))
                        elif action == "set":
                            app.set_playlist_tracks(pid, data.get("ids"))
                        else:
                            return self._error(HTTPStatus.NOT_FOUND, "Not found")
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "No such playlist")
                elif path == "/api/record":
                    if not isinstance(data.get("on"), bool):
                        raise ValueError("on must be true or false")
                    try:
                        app.record(str(data.get("station", "")), data["on"])
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "No such station")
                elif path == "/api/stations/start":
                    try:
                        app.start_station(str(data.get("url", "")))
                    except LookupError as exc:
                        return self._error(HTTPStatus.NOT_FOUND, str(exc))
                    return self._send_json({"page_open": app.page_open()})
                elif path == "/api/stations/shortcut":
                    try:
                        where = app.station_shortcut(str(data.get("url", "")))
                    except LookupError:
                        return self._error(HTTPStatus.NOT_FOUND, "No such station")
                    except OSError as exc:
                        return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Couldn't make the icon: {exc}")
                    return self._send_json({"path": where})
                elif path == "/api/open":
                    found = app.open_files(data.get("paths"))
                    return self._send_json({**found, "page_open": app.page_open()})
                elif path == "/api/recordings/label":
                    app.label_recording(str(data.get("file", "")), data.get("label", ""), data.get("tags", []))
                    return self._send_json(app.recordings_list())
                elif path == "/api/recordings/play":
                    found = app.play_recording(str(data.get("file", "")))
                    return self._send_json({**app.state(), **found})
                elif path == "/api/recordings/open":
                    try:
                        app.open_recordings_folder()
                    except OSError as exc:
                        return self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                                           f"Couldn't open the recordings folder ({app.recorder.folder}): {exc}")
                elif path == "/api/directory/clicked":
                    app.directory.clicked(str(data.get("uuid", "")))
                    return self._send_json({"ok": True})
                elif path == "/api/quit":
                    self._send_json({"ok": True})
                    app.recorder.stop_all()  # close recordings cleanly
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "Not found")
            except (ValueError, TypeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except OSError as exc:
                logger.warning("Could not save settings: %s", exc)
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Could not save settings: {exc}")
            return self._send_json(app.state())

    return Handler


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program bind a port that is
    # already in use, so two copies could both "start"; ask for the port
    # exclusively there instead.
    if sys.platform == "win32":
        allow_reuse_address = False

        def server_bind(self):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()


def make_server(app: PlayerApp, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = _Server((host, port), make_handler(app))
    server.daemon_threads = True
    return server

