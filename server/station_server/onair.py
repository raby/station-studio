"""The running app: every station's channel, started, stopped and watched.

`ServerApp` is what the web page talks to. Each station gets a
`StationRunner`, which owns its channel and sender in a background thread,
keeps the play log, and checks once a minute for a newer week's playlist,
switching to it at the end of the playing track.

Nobody may be watching, so a station looks after itself: if the server
can't be reached, the playlist's drive isn't there yet (after a power
cut, say) or the broadcast fails, it keeps trying again, and while any
station is on air the computer is kept from sleeping.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from station_player.library import AUDIO_EXTENSIONS, Track
from station_player.playlog import PlayLog
from station_player.tags import read_tags

from station_server import __version__, selfupdate, updates
from station_server.audio import FfmpegMissing, find_ffmpeg, has_mp3_encoder
from station_server.awake import KeepAwake
from station_server.channel import Channel, NowPlaying
from station_server.config import Config, ConfigStore, Station, clean_station, newest_week
from station_server.desktop import Login, bundled
from station_server.playlist import PlaylistError, Queue, load_queue
from station_server.schedule import Show, WeekStart, plan, position, track_seconds, where_now
from station_server.senders import SenderError, StreamInfo, sender_from_url
from station_server.weeks import WeekPicker
from station_server.window import mac_app_bundle

logger = logging.getLogger(__name__)

WATCH_SECONDS = 60.0
# Going on air again after a problem: first after this many seconds, then
# twice as long each time it fails again, up to the most.
RETRY_SECONDS = 15.0
RETRY_MOST_SECONDS = 120.0


def playlist_file(station: Station, weeks: WeekPicker | None = None, remember: bool = True,
                  ) -> tuple[Path, tuple[Path, datetime] | None]:
    """The XML to play: the file named, or this week's from the folder
    (see weeks.py), and the week waiting to go on next, if any."""
    if not station.playlist:
        raise PlaylistError("Choose the station's playlist first")
    p = Path(station.playlist).expanduser()
    if p.is_dir():
        if weeks is not None:
            found, upcoming = weeks.pick(station.id, p, WeekStart.parse(station.week_start), remember=remember)
            if found is not None:
                return found, upcoming
        found = newest_week(p)
        if found is None:
            raise PlaylistError(f"No exported playlists in {p}")
        return found, None
    if not p.exists():
        raise PlaylistError(f"Playlist not found: {p}")
    return p, None


def show_queue(show: Show, station: Station) -> Queue:
    """What a show plays: one recording, a playlist, or a folder of
    recordings in name order."""
    p = Path(show.source).expanduser()
    if p.is_dir():
        files = sorted((f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS and f.is_file()),
                       key=lambda f: f.name.lower())
    elif p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file():
        files = [p]
    elif p.is_file():
        queue = load_queue(p, station.music_folders, station.remaps)
        return Queue(show.name, queue.tracks, queue.missing)
    else:
        raise PlaylistError(f"{show.name}: {p} isn't there")
    tracks = []
    for i, f in enumerate(files):
        tags = read_tags(f)
        tracks.append(Track(id=f"show-{show.id}-{i}", name=tags.title or f.stem, artist=tags.artist,
                            album=tags.album or show.name, duration_ms=tags.duration_ms, path=f, location=f))
    if not tracks:
        raise PlaylistError(f"{show.name}: no recordings in {p}")
    return Queue(show.name, tracks)


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _playlist_info(q: Queue) -> dict:
    return {
        "name": q.name, "tracks": len(q.tracks),
        "missing": [{"title": t.name, "artist": t.artist, "location": str(t.location or "")}
                    for t in q.missing[:50]],
        "missing_count": len(q.missing),
        "hours": round(sum(track_seconds(t) for t in q.tracks) / 3600, 1),
    }


class StationRunner:
    """One station, on air or not."""

    def __init__(self, station: Station, ffmpeg: str | None, log_dir: Path,
                 weeks: WeekPicker | None = None, awake: KeepAwake | None = None) -> None:
        self.station = station
        self.ffmpeg = ffmpeg
        self.playlog = PlayLog(log_dir / f"{station.id}.jsonl")
        self.weeks = weeks
        self.awake = awake
        self.state = "off"  # off | starting | on | waiting (to try again) | stopping | error
        self.error: str | None = None
        self.retry_at: float | None = None  # when it tries again, as time.time()
        self.upcoming: tuple[Path, datetime] | None = None  # the next week and when it goes on
        self.channel: Channel | None = None
        self.sender = None
        self.queue: Queue | None = None
        self.xml: Path | None = None
        self._xml_mtime = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        # The show on air: (show, when listeners heard it start, its length).
        self.show_on: tuple[Show, datetime, float] | None = None
        self._interrupted = 0  # the song a show cut into
        self._show_lengths: dict[str, tuple[float, float]] = {}  # source -> (mtime, seconds)

    # ---- control --------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self.state in ("starting", "on", "waiting"):
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

    def _resume(self, at: datetime, interrupted: int) -> tuple[int, float]:
        """Where the music comes back after a show ending at `at`: where the
        clock says, or (from the top) the song after the one interrupted."""
        if self.station.start_mode != "clock" or not self.queue:
            return interrupted + 1, 0.0
        return where_now(self.queue.tracks, WeekStart.parse(self.station.week_start), at)

    def shows(self) -> list[Show]:
        out = []
        for raw in self.station.shows:
            try:
                out.append(Show.parse(raw))
            except ValueError:
                continue
        return out

    def show_length(self, show: Show) -> float:
        """How long a show runs, remembered while its file is unchanged."""
        p = Path(show.source).expanduser()
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return 0.0
        known = self._show_lengths.get(show.source)
        if known and known[0] == mtime:
            return known[1]
        try:
            length = sum(track_seconds(t) for t in show_queue(show, self.station).tracks)
        except (PlaylistError, OSError):
            length = 0.0
        self._show_lengths[show.source] = (mtime, length)
        return length

    def _heard_now(self) -> datetime:
        """The time listeners are hearing: audio is sent a little ahead."""
        ahead = self.channel.pacer.ahead() if self.channel else 0.0
        return datetime.now().astimezone() + timedelta(seconds=ahead)

    def _after_show(self, interrupted: int):
        def after() -> tuple[Queue, int, float]:
            self.show_on = None
            index, offset = self._resume(self._heard_now(), interrupted)
            logger.info("%s: back to the music", self.station.name)
            return self.queue, index, offset
        return after

    def _show_now(self, now: datetime) -> tuple[Show, datetime, float] | None:
        """A show that should be on air at `now` (after a restart, say)."""
        best = None
        for show in self.shows():
            length = self.show_length(show)
            for at in show.starts(now - timedelta(seconds=length), now + timedelta(microseconds=1)):
                if best is None or at > best[1]:
                    best = (show, at, length)
        return best

    def _start_show(self, show: Show, at: datetime) -> None:
        try:
            queue = show_queue(show, self.station)
        except (PlaylistError, OSError) as exc:
            logger.warning("%s: couldn't play %s: %s", self.station.name, show.name, exc)
            return
        ch = self.channel
        interrupted = ch.now.index if ch and ch.now and not ch.in_show else 0
        logger.info("%s: %s %s", self.station.name, show.name, "now" if show.on_time else "after this song")
        self.show_on = (show, at, sum(track_seconds(t) for t in queue.tracks))
        self._interrupted = interrupted
        ch.insert(queue, self._after_show(interrupted), now=show.on_time)

    def _show_clock(self, done: threading.Event, joined: tuple[Show, datetime, float] | None = None) -> None:
        """Start each show at its time (allowing for the audio sent ahead)."""
        fired: set[tuple[str, str]] = {(joined[0].id, joined[1].isoformat())} if joined else set()
        last = self._heard_now()
        while not done.wait(0.2):
            heard = self._heard_now() + timedelta(seconds=0.1)
            for show in self.shows():
                for at in show.starts(last, heard):
                    key = (show.id, at.isoformat())
                    if key not in fired:
                        fired.add(key)
                        self._start_show(show, at)
            last = max(last, heard)

    def _load(self) -> Queue:
        xml, self.upcoming = playlist_file(self.station, self.weeks)
        queue = load_queue(xml, self.station.music_folders, self.station.remaps)
        self.xml, self._xml_mtime = xml, xml.stat().st_mtime
        for t in queue.tracks:
            track_seconds(t)  # learn lengths now, not while on air
        return queue

    def _run(self) -> None:
        """Go on air, and keep going back on air after any problem a retry
        can fix, until taken off air."""
        if self.awake:
            self.awake.hold()
        wait = RETRY_SECONDS
        try:
            while not self._stopped.is_set():
                began = time.monotonic()
                try:
                    self._broadcast()
                    return  # taken off air
                except (FfmpegMissing, ValueError) as exc:  # only the settings can fix these
                    self.state, self.error = "error", str(exc)
                    logger.warning("%s couldn't go on air: %s", self.station.name, exc)
                    return
                except (ConnectionError, TimeoutError, socket.gaierror) as exc:
                    problem = f"the server can't be reached ({exc.strerror or exc})"
                except (PlaylistError, SenderError, OSError) as exc:
                    problem = str(exc) or type(exc).__name__
                except Exception as exc:  # the encoder died, say
                    logger.exception("%s went off air", self.station.name)
                    problem = f"Went off air: {exc}"
                if self._stopped.is_set():
                    return
                if time.monotonic() - began > 10 * 60:  # it had been fine for a while
                    wait = RETRY_SECONDS
                logger.warning("%s: %s; trying again in %.0f s", self.station.name, problem, wait)
                self.state, self.error = "waiting", problem
                self.retry_at = time.time() + wait
                if self._stopped.wait(wait):
                    return
                wait = min(wait * 2, RETRY_MOST_SECONDS)
                self.state, self.retry_at = "starting", None
        finally:
            self.retry_at = None
            if self.state != "error":
                self.state = "off"
            if self.awake:
                self.awake.release()

    def _broadcast(self) -> None:
        """One go on air, until taken off air (returns) or a problem (raises)."""
        st = self.station
        self.channel = None
        if not self.ffmpeg:
            raise FfmpegMissing("ffmpeg isn't installed (see Help)")
        if not st.server:
            raise ValueError("Set the station's server first")
        self.queue = self._load()
        info = StreamInfo(name=st.name, genre=st.genre, url=st.url, public=st.public, bitrate=st.bitrate)
        self.sender = sender_from_url(st.server, info, st.password or None)
        self.sender.connect()
        done = threading.Event()  # this go on air is over: the helpers stop
        try:
            self._on_air(done)
        finally:
            done.set()
            self.sender.close()

    def _on_air(self, done: threading.Event) -> None:
        st = self.station
        index, offset = self._where(self.queue)
        queue, after = self.queue, None
        self.show_on = None
        joining = self._show_now(datetime.now().astimezone())
        if joining:  # back on air part-way through a show
            show, at, length = joining
            try:
                queue = show_queue(show, st)
                elapsed = (datetime.now().astimezone() - at).total_seconds()
                index, offset = position([track_seconds(t) for t in queue.tracks], elapsed)
                after = self._after_show(0)
                self.show_on = joining
                logger.info("%s: joining %s part-way", st.name, show.name)
            except (PlaylistError, OSError) as exc:
                logger.warning("%s: couldn't play %s: %s", st.name, show.name, exc)
                queue = self.queue
        self.channel = Channel(
            queue, self.sender, self.ffmpeg, bitrate=st.bitrate, start=index, offset=offset,
            level=st.level, on_played=self._played, keep_going=True, after=after,
        )
        threading.Thread(target=self._watch, args=(done,), daemon=True).start()
        threading.Thread(target=self._show_clock, args=(done, self.show_on), daemon=True).start()
        if self._stopped.is_set():  # taken off air while connecting
            self.channel.stop()
        self.state, self.error = "on", None
        logger.info("%s on air to %s", st.name, self.sender.describe)
        self.channel.run()

    def _played(self, now: NowPlaying, seconds: float) -> None:
        t = now.track
        self.playlog.add(_local(now.started + now.offset), seconds, {
            "title": t.name, "artist": t.artist, "album": t.album,
            "duration_ms": t.duration_ms, "path": str(t.path) if t.path else None,
        })

    def _watch(self, done: threading.Event) -> None:
        """Switch to the next week's playlist when its week begins, or to the
        week on air when it's edited."""
        while not done.wait(WATCH_SECONDS):
            try:
                xml, self.upcoming = playlist_file(self.station, self.weeks)
                if xml == self.xml and xml.stat().st_mtime == self._xml_mtime:
                    continue
                queue = self._load()
            except (PlaylistError, OSError) as exc:
                logger.warning("%s: couldn't read the new playlist: %s", self.station.name, exc)
                continue
            logger.info("%s: switching to %s after this track", self.station.name, queue.name)
            self.queue = queue
            # During a show the new week simply comes in after it.
            if self.channel and self.show_on is None:
                self.channel.load(queue, where=self._where)

    # ---- for the page ---------------------------------------------------

    def status(self) -> dict:
        st = self.station
        out = {
            "station": st.to_json(secrets=False),
            "state": self.state,
            "error": self.error,
            "retry_in": round(max(0.0, self.retry_at - time.time())) if self.retry_at else None,
            "next_week": {"name": self.upcoming[0].stem, "at": self.upcoming[1].isoformat(timespec="minutes")}
            if self.upcoming else None,
            "server": getattr(self.sender, "describe", None),
            "connected": bool(self.sender and self.sender.connected),
            "playlist": None,
            "file": str(self.xml) if self.xml else None,
            "now": None,
            "order": [],
            "stuck": bool(self.channel and self.channel.stuck),
        }
        ch = self.channel
        # On air, the playlist is the one playing; a new week waiting for the
        # end of the track is shown as next.
        playing = ch.queue if ch and self.state == "on" and not ch.in_show else self.queue
        if playing:
            out["playlist"] = _playlist_info(playing)
            if self.queue is not None and self.queue is not playing:
                out["playlist"]["next"] = _playlist_info(self.queue)
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
            show_on = self.show_on if ch.in_show else None
            if show_on:
                out["now"]["show"] = show_on[0].name
                # When it really started (a show after the song starts late).
                began = _local(now.started) - timedelta(seconds=sum(track_seconds(t) for t in ch.queue.tracks[:now.index]))
                show_on = (show_on[0], began, show_on[2])
            shows = [(show, self.show_length(show)) for show in self.shows()]
            if self.queue and self.queue.tracks:
                out["order"] = plan(
                    self.queue.tracks, now.index if not show_on else self._interrupted, _local(now.started),
                    datetime.now().astimezone(), shows, self._resume, count=30, show_playing=show_on)
        out["shows"] = [{**show.to_json(), "label": show.label(), "seconds": round(self.show_length(show), 1)}
                        for show in self.shows()]
        return out


class ServerApp:
    def __init__(self, store: ConfigStore, ffmpeg: str | None = None, login: Login | None = None) -> None:
        self.store = store
        self.login = login or Login()
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
        self.weeks = WeekPicker(self.store.home / "weeks.json")
        self.awake = KeepAwake()
        self.runners: dict[str, StationRunner] = {}
        for st in self.config.stations:
            self.runners[st.id] = self._new_runner(st)
        self.started = time.time()
        self.updater: selfupdate.Updater | None = None  # set by web.make_server
        self.latest: str | None = None  # the newest release, once looked up

    def _new_runner(self, st: Station) -> StationRunner:
        return StationRunner(st, self.ffmpeg, self.store.home / "logs", weeks=self.weeks, awake=self.awake)

    @property
    def config(self) -> Config:
        return self.store.config

    def resume(self) -> None:
        """Put back on air the stations that were on air when the app closed."""
        for st in self.config.stations:
            if st.on_air:
                self.runners[st.id].start()

    def check_for_updates(self, force: bool = False, fetch=None) -> str | None:
        """Look for a new version: at most once a day unless `force`, and
        not at all with Settings › Tell me when a new version is out off."""
        if force or self.config.check_updates:
            self.latest = updates.latest_version(self.store.home / "update-check.json", fetch=fetch, force=force)
        return self.latest

    def state(self) -> dict:
        latest = self.latest if updates.is_newer(self.latest, __version__) else None
        kind = self.updater.kind if self.updater else selfupdate.install_kind()
        return {
            "version": __version__,
            "update": latest,
            "updating": self.updater.status() if self.updater else None,
            "can_update": kind in ("mac", "windows"),
            "release_page": selfupdate.RELEASE_PAGE.format(version=latest) if latest else None,
            "check_updates": self.config.check_updates,
            "ffmpeg": self.ffmpeg,
            "ffmpeg_error": self.ffmpeg_error,
            "warnings": self.store.warnings,
            "open_window": self.config.open_window,
            "login": self.login.enabled(),
            "bundled": bundled(),
            # The downloaded Mac app, which has a window of its own (macwindow.py).
            "mac_app": mac_app_bundle() is not None,
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
            if runner.state == "error":  # the old problem may be what was just fixed
                runner.state, runner.error = "off", None
        else:
            self.config.stations.append(st)
            self.runners[st.id] = self._new_runner(st)
        self.store.save()
        return st

    def delete_station(self, sid: str) -> None:
        runner = self._runner(sid)
        runner.stop()
        self.config.stations = [s for s in self.config.stations if s.id != sid]
        del self.runners[sid]
        self.weeks.forget(sid)
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
        st = clean_station(data, self.config.station(data.get("id") or ""))
        xml, _ = playlist_file(st, self.weeks, remember=False)
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

    def diagnostics(self, lines: int = 200) -> str:
        """A report to send when asking for help: versions, each station's
        state and the end of the log, with passwords taken out."""
        import platform
        import sys
        out = [
            f"Station Server {__version__}",
            f"{platform.platform()} · Python {platform.python_version()}"
            + (" · downloaded app" if getattr(sys, "frozen", False) else ""),
            f"ffmpeg: {self.ffmpeg or self.ffmpeg_error}",
            f"Start at login: {'on' if self.login.enabled() else 'off'}"
            f" · keeping the computer awake: {'yes' if self.awake.held else 'no'}",
            "",
        ]
        for st in self.config.stations:
            r = self.runners[st.id]
            out.append(f"{st.name}: {r.state}" + (f" ({r.error})" if r.error else ""))
            out.append(f"  server {st.server or '-'} · playlist {r.xml or st.playlist or '-'}"
                       f" · {st.start_mode} · {len(st.shows)} show(s)")
            if r.channel and r.channel.now and r.state == "on":
                out.append(f"  playing {r.channel.now.track.artist} - {r.channel.now.track.name}"
                           f" · {'connected' if r.sender and r.sender.connected else 'reconnecting'}")
        log = self.store.home / "station-server.log"
        out += ["", f"Last lines of {log}:"]
        try:
            with log.open(encoding="utf-8", errors="replace") as fh:
                out += [line.rstrip("\n") for line in fh.readlines()[-lines:]]
        except OSError:
            out.append("(no log)")
        text = "\n".join(out)
        secrets = {s.password for s in self.config.stations if s.password}
        for secret in secrets:
            text = text.replace(secret, "********")
        return re.sub(r"(://[^/@\s]*?:)[^/@\s]*@", r"\1********@", text)

    def shutdown(self) -> None:
        """Stop every station, remembering which were on air."""
        for runner in self.runners.values():
            runner.stop()
