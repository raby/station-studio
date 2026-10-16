"""Recording radio stations to files.

Each recording has its own connection to the station, separate from
listening, so a station can be recorded while you listen to something
else (or nothing). The audio is saved exactly as the station sends it
(no re-encoding), with the stream's song titles, when it sends any, in a
text file beside it. A dropped connection is reconnected a few times and
the recording carries on in the same file.
"""
from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

from station_player import radio

logger = logging.getLogger(__name__)

MAX_HOURS = 12          # a forgotten recording stops itself
RETRIES = 5             # reconnects in a row before giving up
RETRY_SECONDS = 3
STEADY_SECONDS = 30     # a connection that lasts this long resets the retries

# Playlist links rather than audio: recording them would save the link text.
_NOT_AUDIO = ("audio/x-mpegurl", "audio/mpegurl", "application/vnd.apple.mpegurl",
              "audio/x-scpls", "audio/scpls")

_EXTENSIONS = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/aac": "aac", "audio/aacp": "aac",
    "audio/x-aac": "aac", "audio/ogg": "ogg", "application/ogg": "ogg", "audio/opus": "opus",
    "audio/flac": "flac", "audio/x-flac": "flac", "audio/webm": "webm", "audio/wav": "wav",
    "audio/mp4": "m4a",  # an HLS station's fragmented MP4
}


def default_folder() -> Path:
    music = Path.home() / "Music"
    return (music if music.is_dir() else Path.home()) / "Station Player Recordings"


def file_stem(station: str, when: datetime) -> str:
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", station)
    safe = " ".join(safe.split()).strip(" .")[:80] or "Radio"
    return f"{safe} {when:%Y-%m-%d %H.%M}"


class Recording:
    def __init__(self, url: str, name: str, folder: Path, opener, user_agent: str,
                 index: "RecordingIndex | None" = None) -> None:
        self.index = index
        self.url = url
        self.name = name
        self.folder = folder
        self.started = time.time()
        self.ended: float | None = None
        self.file: Path | None = None
        self.bytes = 0
        self.error: str | None = None
        self.done = False
        self._open = opener
        self._agent = user_agent
        self._stop = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name=f"record {name}", daemon=True)

    def status(self) -> dict:
        return {
            "url": self.url, "name": self.name, "file": str(self.file) if self.file else None,
            "seconds": int((self.ended or time.time()) - self.started), "bytes": self.bytes,
            "error": self.error, "done": self.done,
            # Asked to stop but not finished yet (still connecting to a slow
            # station, say): shown as stopped, so it can be started again.
            "stopping": self.stopping and not self.done,
        }

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            # Wake a read that's waiting on the station (closing alone
            # doesn't), so the thread notices at once.
            sock = getattr(stream, "sock", None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            stream.close()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def _create_file(self, ext: str):
        self.folder.mkdir(parents=True, exist_ok=True)
        stem = file_stem(self.name, datetime.fromtimestamp(self.started))
        n = 1
        while True:  # never overwrite an earlier recording
            self.file = self.folder / (f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}")
            try:
                return self.file.open("xb")
            except FileExistsError:
                n += 1

    def _run(self) -> None:
        out = titles = None
        failures = 0
        deadline = self.started + MAX_HOURS * 3600
        try:
            while not self._stop.is_set() and time.time() < deadline:
                try:
                    stream = self._open(self.url, user_agent=self._agent)
                except radio.StreamError as exc:
                    failures += 1
                    if failures > RETRIES:
                        self.error = f"Couldn't connect to the station: {exc}"
                        return
                    self._stop.wait(RETRY_SECONDS)
                    continue
                connected = time.time()
                with self._lock:
                    self._stream = stream
                if self._stop.is_set():
                    stream.close()
                    break
                try:
                    if out is None:
                        ctype = stream.content_type
                        if ctype is None or ctype in _NOT_AUDIO:
                            self.error = "This station isn't a plain audio stream, so it can't be recorded"
                            return
                        try:
                            out = self._create_file(_EXTENSIONS.get(ctype, "audio"))
                        except OSError as exc:
                            self.error = f"Couldn't create the recording file: {exc}"
                            return

                    def title(text):
                        nonlocal titles
                        if not text:
                            return
                        elapsed = int(time.time() - self.started)
                        try:
                            if titles is None:
                                titles = self.file.with_suffix(".txt").open("a", encoding="utf-8")
                            titles.write(f"{elapsed // 3600:02}:{elapsed // 60 % 60:02}:{elapsed % 60:02}  {text}\n")
                            titles.flush()
                        except OSError:
                            pass  # the song list is a nicety

                    for chunk in radio.relay(stream, title):
                        try:
                            out.write(chunk)
                        except OSError as exc:  # disk full, drive unplugged
                            self.error = f"Couldn't save the recording: {exc}"
                            return
                        self.bytes += len(chunk)
                        if self._stop.is_set() or time.time() >= deadline:
                            break
                except (OSError, ValueError):
                    pass  # the connection dropped (or was closed to stop)
                finally:
                    stream.close()
                    with self._lock:
                        self._stream = None
                if not self._stop.is_set():
                    # The station hung up: reconnect. Only a connection that
                    # lasted a while counts as working again, so a station
                    # that keeps hanging up straight away is given up on.
                    failures = 1 if time.time() - connected >= STEADY_SECONDS else failures + 1
                    if failures > RETRIES:
                        self.error = "The station kept dropping the connection"
                        return
                    self._stop.wait(RETRY_SECONDS)
        except Exception as exc:  # never let a recording thread die silently
            logger.exception("Recording %s failed", self.name)
            self.error = str(exc)
        finally:
            for fh in (out, titles):
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
            self.ended = time.time()
            if self.file is not None and self.index is not None:
                try:
                    self.index.update(self.file.name, station=self.name,
                                      started=datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                                      seconds=int(self.ended - self.started))
                except OSError:
                    pass  # the list falls back on the file name
            self.done = True


class Recorder:
    def __init__(self, folder: Path | None = None, opener=radio.open_stream, user_agent: str = "StationPlayer") -> None:
        self.folder = folder or default_folder()
        self._open = opener
        self._agent = user_agent
        self._lock = threading.Lock()
        self._recordings: dict[str, Recording] = {}  # station url -> latest
        self.index = RecordingIndex(self.folder)

    def start(self, url: str, name: str) -> None:
        with self._lock:
            current = self._recordings.get(url)
            if current is not None and not current.done and not current.stopping:
                return  # already recording
            rec = Recording(url, name, self.folder, self._open, self._agent, self.index)
            self._recordings[url] = rec
        rec.thread.start()

    def stop(self, url: str) -> None:
        with self._lock:
            rec = self._recordings.get(url)
        if rec is not None:
            rec.stop()
            # A moment for the file to be finished; a recording still
            # connecting to a slow station ends on its own soon after.
            rec.thread.join(1)

    def stop_all(self) -> None:
        with self._lock:
            recs = list(self._recordings.values())
        for rec in recs:
            rec.stop()
        for rec in recs:
            rec.thread.join(5)

    def status(self) -> list[dict]:
        with self._lock:
            return [r.status() for r in self._recordings.values()]

    def listing(self) -> list[dict]:
        with self._lock:
            active = {r.file.name for r in self._recordings.values() if not r.done and r.file is not None}
        return self.index.listing(active)

    def forget_finished(self) -> None:
        with self._lock:
            for url in [u for u, r in self._recordings.items() if r.done]:
                del self._recordings[url]


# ---- the recordings list ----------------------------------------------------------

INDEX_NAME = "recordings.json"  # labels and tags, kept beside the recordings
_AUDIO_EXTS = {f".{e}" for e in _EXTENSIONS.values()} | {".audio"}
_STEM = re.compile(r"^(?P<station>.+) (?P<when>\d{4}-\d{2}-\d{2} \d{2}\.\d{2})(?: \(\d+\))?$")
MAX_TAGS = 20
MAX_TAG = 40
MAX_LABEL = 200


def clean_tags(tags) -> list[str]:
    """Tags as typed: trimmed, without repeats (ignoring case). Raises ValueError."""
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("tags must be a list of words")
    out: list[str] = []
    for t in tags:
        t = " ".join(t.split())[:MAX_TAG]
        if t and t.casefold() not in (o.casefold() for o in out):
            out.append(t)
    if len(out) > MAX_TAGS:
        raise ValueError(f"At most {MAX_TAGS} tags")
    return out


def station_from_name(name: str) -> str:
    """The station in a recording's file name ("Rebirth Radio 2026-10-15 18.05.mp3")."""
    m = _STEM.match(Path(name).stem)
    return m.group("station") if m else ""


class RecordingIndex:
    """What's known about each recording beyond its file: the station,
    when it started and how long it ran (written when it finishes), and
    the label and tags you give it. One JSON file in the recordings folder,
    keyed by file name, so it moves with the recordings."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / INDEX_NAME
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else None
            return {k: v for k, v in items.items() if isinstance(v, dict)} if isinstance(items, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, items: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(INDEX_NAME + ".tmp")
        tmp.write_text(json.dumps({"v": 1, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def all(self) -> dict:
        with self._lock:
            return self._read()

    def update(self, name: str, **fields) -> None:
        with self._lock:
            items = self._read()
            entry = items.setdefault(name, {})
            entry.update({k: v for k, v in fields.items() if v is not None})
            try:
                self._write(items)
            except OSError as exc:
                logger.warning("Could not save the recordings list: %s", exc)
                raise

    def listing(self, active: set[str]) -> list[dict]:
        """Every finished recording in the folder, newest first. Files still
        being written (`active`, by name) are left out."""
        known = self.all()
        out = []
        try:
            files = [p for p in self.path.parent.iterdir()
                     if p.suffix.lower() in _AUDIO_EXTS and p.is_file() and p.name not in active]
        except OSError:
            return []
        for f in files:
            meta = known.get(f.name, {})
            try:
                st = f.stat()
            except OSError:
                continue
            m = _STEM.match(f.stem)
            station = meta.get("station") or (m.group("station") if m else "")
            started = meta.get("started")
            if not started:
                try:
                    started = datetime.strptime(m.group("when"), "%Y-%m-%d %H.%M").isoformat() if m else None
                except ValueError:
                    started = None
            started = started or datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
            songs = []
            try:
                with f.with_suffix(".txt").open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.strip():
                            songs.append(line.rstrip("\n"))
                        if len(songs) >= 500:
                            break
            except OSError:
                pass
            out.append({
                "file": str(f), "name": f.name, "label": meta.get("label") or "",
                "tags": meta.get("tags") or [], "station": station, "started": started,
                "seconds": meta.get("seconds"), "bytes": st.st_size, "songs": songs,
            })
        out.sort(key=lambda r: r["started"], reverse=True)
        return out
