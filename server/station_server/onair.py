"""The running app: every station's channel, started, stopped and watched.

`ServerApp` is what the web page talks to. Each station gets a
`StationRunner`, which owns its channel and sender in a background thread,
keeps the play log, and checks once a minute for a newer week's playlist,
switching to it at the end of the playing track.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from pathlib import Path

from station_player.playlog import PlayLog

from station_server import __version__
from station_server.audio import FfmpegMissing, find_ffmpeg, has_mp3_encoder
from station_server.channel import Channel, NowPlaying
from station_server.config import Config, ConfigStore, Station, clean_station, newest_week
from station_server.playlist import PlaylistError, Queue, load_queue
from station_server.schedule import WeekStart, running_order, track_seconds, where_now
from station_server.senders import SenderError, StreamInfo, sender_from_url

logger = logging.getLogger(__name__)

WATCH_SECONDS = 60.0


def playlist_file(station: Station) -> Path:
    """The XML to play: the file named, or the newest week in the folder."""
    if not station.playlist:
        raise PlaylistError("Choose the station's playlist first")
    p = Path(station.playlist).expanduser()
    if p.is_dir():
        found = newest_week(p)
        if found is None:
            raise PlaylistError(f"No exported playlists in {p}")
        return found
    return p


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


class StationRunner:
    """One station, on air or not."""

    def __init__(self, station: Station, ffmpeg: str | None, log_dir: Path) -> None:
        self.station = station
        self.ffmpeg = ffmpeg
        self.playlog = PlayLog(log_dir / f"{station.id}.jsonl")
        self.state = "off"  # off | starting | on | stopping | error
        self.error: str | None = None
        self.channel: Channel | None = None
        self.sender = None
        self.queue: Queue | None = None
        self.xml: Path | None = None
        self._xml_mtime = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    # ---- control --------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self.state in ("starting", "on"):
                return
            self.state, self.error = "starting", None
            self._stopped.clear()
            self._thread = threading.Thread(target=self._run, name=f"station-{self.station.id}", daemon=True)
            self._thread.start()

    def stop(self, wait: float = 10.0) -> None:
        with self._lock:
            if self.state in ("off", "error"):
                self.state = "off"
                return
            self.state = "stopping"
            self._stopped.set()
            if self.channel:
                self.channel.stop()
            thread = self._thread
        if thread:
            thread.join(wait)
        self.state = "off"

    def skip(self) -> None:
        if self.channel:
            self.channel.skip()

    # ---- the station thread ---------------------------------------------

    def _where(self, queue: Queue) -> tuple[int, float]:
        if self.station.start_mode != "clock":
            return 0, 0.0
        return where_now(queue.tracks, WeekStart.parse(self.station.week_start), datetime.now().astimezone())

    def _load(self) -> Queue:
        xml = playlist_file(self.station)
        queue = load_queue(xml, self.station.music_folders, self.station.remaps)
        self.xml, self._xml_mtime = xml, xml.stat().st_mtime
        for t in queue.tracks:
            track_seconds(t)  # learn lengths now, not while on air
        return queue

    def _run(self) -> None:
        st = self.station
        try:
            if not self.ffmpeg:
                raise FfmpegMissing("ffmpeg isn't installed (see Help)")
            if not st.server:
                raise ValueError("Set the station's server first")
            self.queue = self._load()
            info = StreamInfo(name=st.name, genre=st.genre, url=st.url, public=st.public, bitrate=st.bitrate)
            self.sender = sender_from_url(st.server, info, st.password or None)
            self.sender.connect()
        except (FfmpegMissing, PlaylistError, SenderError, ValueError, OSError) as exc:
            self.state, self.error = "error", str(exc)
            logger.warning("%s couldn't go on air: %s", st.name, exc)
            return
        index, offset = self._where(self.queue)
        self.channel = Channel(
            self.queue, self.sender, self.ffmpeg, bitrate=st.bitrate, start=index, offset=offset,
            level=st.level, on_played=self._played, keep_going=True,
        )
        watcher = threading.Thread(target=self._watch, daemon=True)
        watcher.start()
        self.state = "on"
        logger.info("%s on air to %s", st.name, self.sender.describe)
        try:
            self.channel.run()
        except Exception as exc:  # the encoder died, say
            logger.exception("%s went off air", st.name)
            self.state, self.error = "error", f"Went off air: {exc}"
        else:
            if self.state != "error":
                self.state = "off"
        finally:
            self._stopped.set()
            self.sender.close()

    def _played(self, now: NowPlaying, seconds: float) -> None:
        t = now.track
        self.playlog.add(_local(now.started + now.offset), seconds, {
            "title": t.name, "artist": t.artist, "album": t.album,
            "duration_ms": t.duration_ms, "path": str(t.path) if t.path else None,
        })

    def _watch(self) -> None:
        """Switch to a new week's playlist (or an edited one) when it appears."""
        while not self._stopped.wait(WATCH_SECONDS):
            try:
                xml = playlist_file(self.station)
                if xml == self.xml and xml.stat().st_mtime == self._xml_mtime:
                    continue
                queue = self._load()
            except (PlaylistError, OSError) as exc:
                logger.warning("%s: couldn't read the new playlist: %s", self.station.name, exc)
                continue
            logger.info("%s: switching to %s after this track", self.station.name, queue.name)
            self.queue = queue
            if self.channel:
                self.channel.load(queue, where=self._where)

    # ---- for the page ---------------------------------------------------

    def status(self) -> dict:
        st = self.station
        out = {
            "station": st.to_json(secrets=False),
            "state": self.state,
            "error": self.error,
            "server": getattr(self.sender, "describe", None),
            "connected": bool(self.sender and self.sender.connected),
            "playlist": None,
            "now": None,
            "order": [],
            "stuck": bool(self.channel and self.channel.stuck),
        }
        q = self.queue
        if q:
            out["playlist"] = {
                "name": q.name, "file": str(self.xml), "tracks": len(q.tracks),
                "missing": [{"title": t.name, "artist": t.artist, "location": str(t.location or "")}
                            for t in q.missing[:50]],
                "missing_count": len(q.missing),
                "hours": round(sum(track_seconds(t) for t in q.tracks) / 3600, 1),
            }
        ch = self.channel
        now = ch.now if ch and self.state == "on" else None
        if now and ch:
            t = now.track
            out["now"] = {
                "index": now.index, "title": t.name, "artist": t.artist, "album": t.album,
                "started": _local(now.started).isoformat(timespec="seconds"),
                "elapsed": round(max(0.0, time.time() - now.started), 1),
                "seconds": round(track_seconds(t), 1),
                "gain_db": round(now.gain_db, 1),
            }
            out["order"] = running_order(ch.queue.tracks, now.index, _local(now.started), count=30)
        return out


class ServerApp:
    def __init__(self, store: ConfigStore, ffmpeg: str | None = None) -> None:
        self.store = store
        self.ffmpeg_error: str | None = None
        if ffmpeg is None:
            try:
                ffmpeg = find_ffmpeg()
                if not has_mp3_encoder(ffmpeg):
                    self.ffmpeg_error = f"{ffmpeg} has no MP3 encoder (libmp3lame)"
                    ffmpeg = None
            except FfmpegMissing as exc:
                self.ffmpeg_error = str(exc)
        self.ffmpeg = ffmpeg
        self.runners: dict[str, StationRunner] = {}
        for st in self.config.stations:
            self.runners[st.id] = StationRunner(st, self.ffmpeg, self.store.home / "logs")
        self.started = time.time()

    @property
    def config(self) -> Config:
        return self.store.config

    def resume(self) -> None:
        """Put back on air the stations that were on air when the app closed."""
        for st in self.config.stations:
            if st.on_air:
                self.runners[st.id].start()

    def state(self) -> dict:
        return {
            "version": __version__,
            "ffmpeg": self.ffmpeg,
            "ffmpeg_error": self.ffmpeg_error,
            "warnings": self.store.warnings,
            "open_window": self.config.open_window,
            "stations": [self.runners[s.id].status() for s in self.config.stations],
        }

    def _runner(self, sid: str) -> StationRunner:
        if sid not in self.runners:
            raise KeyError(sid)
        return self.runners[sid]

    def save_station(self, data: dict, sid: str | None = None) -> Station:
        """Add a station, or change one (taking effect the next time it goes
        on air; the page offers to restart it)."""
        existing = self.config.station(sid) if sid else None
        if sid and existing is None:
            raise KeyError(sid)
        st = clean_station(data, existing)
        if existing:
            i = self.config.stations.index(existing)
            self.config.stations[i] = st
            runner = self.runners[sid]
            runner.station = st
        else:
            self.config.stations.append(st)
            self.runners[st.id] = StationRunner(st, self.ffmpeg, self.store.home / "logs")
        self.store.save()
        return st

    def delete_station(self, sid: str) -> None:
        runner = self._runner(sid)
        runner.stop()
        self.config.stations = [s for s in self.config.stations if s.id != sid]
        del self.runners[sid]
        self.store.save()

    def set_on_air(self, sid: str, on: bool) -> None:
        runner = self._runner(sid)
        if on:
            runner.start()
        else:
            runner.stop()
        runner.station.on_air = on
        self.store.save()

    def restart(self, sid: str) -> None:
        runner = self._runner(sid)
        runner.stop()
        runner.start()

    def skip(self, sid: str) -> None:
        self._runner(sid).skip()

    def check_playlist(self, data: dict) -> dict:
        """What the playlist settings in `data` would play (before saving)."""
        st = clean_station(data)
        xml = playlist_file(st)
        queue = load_queue(xml, st.music_folders, st.remaps)
        return {
            "file": str(xml), "name": queue.name, "tracks": len(queue.tracks),
            "missing_count": len(queue.missing),
            "hours": round(sum(track_seconds(t) for t in queue.tracks) / 3600, 1),
        }

    def check_server(self, data: dict, sid: str | None = None) -> dict:
        """Connect to the server in `data` and leave again. Uses the saved
        password when none is typed. A station already on air there isn't
        disturbed (a SHOUTcast server would refuse a second source)."""
        existing = self.config.station(sid) if sid else None
        st = clean_station(data, existing)
        if not st.server:
            raise ValueError("Enter the server address")
        runner = self.runners.get(sid or "")
        if runner and runner.state == "on" and st.server == runner.station.server:
            return {"server": runner.sender.describe, "on_air": True}
        sender = sender_from_url(st.server, StreamInfo(name=st.name), st.password or None)
        sender.connect()
        sender.close()
        return {"server": sender.describe, "on_air": False}

    def playlog_csv(self, sid: str, first: date, last: date) -> tuple[str, int, str]:
        runner = self._runner(sid)
        body, count = runner.playlog.csv(first, last)
        return body, count, runner.station.name

    def shutdown(self) -> None:
        """Stop every station, remembering which were on air."""
        for runner in self.runners.values():
            runner.stop()
