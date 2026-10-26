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
import random
import re
import socket
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from station_player.library import AUDIO_EXTENSIONS, Track
from station_player.playlog import PlayLog
from station_player.tags import read_tags

from station_server import __version__, alerts, selfupdate, updates
from station_server.analysis import Analyser
from station_server.audio import FfmpegMissing, find_ffmpeg, has_mp3_encoder
from station_server.awake import KeepAwake
from station_server.channel import Channel, NowPlaying
from station_server.config import Config, ConfigStore, Station, clean_alerts, clean_port, clean_station, newest_week
from station_server.desktop import Login, bundled
from station_server.listen import Listeners, Tap
from station_server.live import LiveInput, LiveSource, lan_addresses, port_free
from station_server.playlist import PlaylistError, Queue, load_queue
from station_server.schedule import Show, WeekStart, plan, position, step_seconds, track_seconds, where_now
from station_server.senders import IN_USE, InUse, SenderError, StreamInfo, sender_from_url, someone_sending
from station_server.weeks import WeekPicker
from station_server.window import mac_app_bundle

logger = logging.getLogger(__name__)

WATCH_SECONDS = 60.0
# A show's or the fallback's folder plays at most this many files.
MAX_FOLDER_TRACKS = 5000
# How often the server is asked how many are listening.
COUNT_SECONDS = 60.0
# Going on air again after a problem: first after this many seconds, then
# twice as long each time it fails again, up to the most.
RETRY_SECONDS = 15.0
RETRY_MOST_SECONDS = 120.0
# After End live show, the presenter's encoder can't reconnect for this long.
END_LIVE_BLOCK_SECONDS = 120.0


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
    return source_queue(show.name, show.source, station, f"show-{show.id}")


def source_queue(name: str, source: str, station: Station, key: str) -> Queue:
    """One recording, a playlist, or a folder of recordings in name order
    (including those in folders inside it, like Artist/Album), as a queue
    (a show, or the fallback)."""
    p = Path(source).expanduser()
    if p.is_dir():
        # Not hidden files: a Mac leaves "._song.mp3" beside each song on some drives.
        files = sorted((f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXTENSIONS and f.is_file()
                        and not any(part.startswith(".") for part in f.relative_to(p).parts)),
                       key=lambda f: [part.lower() for part in f.relative_to(p).parts])[:MAX_FOLDER_TRACKS]
    elif p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file():
        files = [p]
    elif p.is_file():
        queue = load_queue(p, station.music_folders, station.remaps)
        return Queue(name, queue.tracks, queue.missing)
    else:
        raise PlaylistError(f"{name}: {p} isn't there")
    tracks = []
    for i, f in enumerate(files):
        tags = read_tags(f)
        tracks.append(Track(id=f"{key}-{i}", name=tags.title or f.stem, artist=tags.artist,
                            album=tags.album or name, duration_ms=tags.duration_ms, path=f, location=f))
    if not tracks:
        raise PlaylistError(f"{name}: no recordings in {p}")
    return Queue(name, tracks)


class JingleBag:
    """The station's jingles, each played once, in a shuffled order, before
    any plays again. The folder is looked at again each time round, and
    each jingle is measured (`measure`: Analyser.want) to level it with
    the songs."""

    def __init__(self, station: Station, shuffle=random.shuffle, measure=None) -> None:
        self.station = station
        self._shuffle = shuffle
        self._measure = measure
        self._bag: list[Track] = []
        self._last: Track | None = None
        self.seconds = 0.0  # how long a jingle lasts, on average

    def load(self) -> list[Track]:
        tracks = source_queue("Jingles", self.station.jingles, self.station, "jingle").tracks
        if self._measure:
            self._measure(tracks)
        self.seconds = sum(track_seconds(t) for t in tracks) / len(tracks)
        return tracks

    def __call__(self) -> Track | None:
        if not self._bag:
            try:
                self._bag = list(self.load())
            except (PlaylistError, OSError) as exc:
                logger.warning("%s: no jingles: %s", self.station.name, exc)
                return None
            self._shuffle(self._bag)
            # Not the same one twice running, across the shuffle.
            if len(self._bag) > 1 and self._last is not None and self._bag[-1].path == self._last.path:
                self._bag.insert(0, self._bag.pop())
        self._last = self._bag.pop()
        return self._last


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
                 weeks: WeekPicker | None = None, awake: KeepAwake | None = None,
                 analyser: Analyser | None = None) -> None:
        self.station = station
        self.ffmpeg = ffmpeg
        self.playlog = PlayLog(log_dir / f"{station.id}.jsonl")
        self.weeks = weeks
        self.awake = awake
        self.analyser = analyser
        self.state = "off"  # off | starting | on | waiting (to try again) | stopping | error
        self.error: str | None = None
        self.retry_at: float | None = None  # when it tries again, as time.time()
        self.fallback_queue: Queue | None = None  # on air with the fallback: the playlist can't be read
        self.upcoming: tuple[Path, datetime] | None = None  # the next week and when it goes on
        self.channel: Channel | None = None
        self.sender = None
        self.rehearsal = False  # on air to the test stream, not the live one
        self.listeners = Listeners()  # the page's Listen button
        self.audience: dict | None = None  # how many are listening, from the server
        self.queue: Queue | None = None
        self.xml: Path | None = None
        self._xml_mtime = 0.0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        # The show on air: (show, when listeners heard it start, its length).
        self.show_on: tuple[Show, datetime, float] | None = None
        self._interrupted = 0  # the song a show cut into
        self._show_lengths: dict[str, tuple[tuple, float]] = {}  # source -> ((mtime, crossfade), seconds)
        self.jingles: JingleBag | None = None
        self.live_input: LiveInput | None = None  # presenters connect here, on air with live shows on
        self.live_error: str | None = None  # why it couldn't listen

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
        if self.station.start_mode != "clock" or queue is self.fallback_queue:
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
        key = (mtime, self.station.crossfade)
        known = self._show_lengths.get(show.source)
        if known and known[0] == key:
            return known[1]
        try:
            length = sum(step_seconds(t) for t in self._show_queue(show).tracks)
        except (PlaylistError, OSError):
            length = 0.0
        self._show_lengths[show.source] = (key, length)
        return length

    def _show_queue(self, show: Show) -> Queue:
        """A show's queue, its tracks timed allowing for the crossfade (each
        one's end overlaps the next, or the music after the show)."""
        queue = show_queue(show, self.station)
        for t in queue.tracks:
            t.step_adjust = -self.station.crossfade
        return queue

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
            queue = self._show_queue(show)
        except (PlaylistError, OSError) as exc:
            logger.warning("%s: couldn't play %s: %s", self.station.name, show.name, exc)
            return
        ch = self.channel
        if ch is not None and ch.live is not None:
            logger.info("%s: %s not played: a presenter is live", self.station.name, show.name)
            return
        interrupted = ch.now.index if ch and ch.now and not ch.in_show else 0
        logger.info("%s: %s %s", self.station.name, show.name, "now" if show.on_time else "after this song")
        self.show_on = (show, at, sum(step_seconds(t) for t in queue.tracks))
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
        self._step(queue)
        return queue

    def _step(self, queue: Queue) -> None:
        """Tell the clock how a crossfade and the jingles move each song on
        (schedule.step_seconds): each song's crossfade starts the next one
        early, and every few songs a jingle comes between."""
        st = self.station
        adjust = -st.crossfade
        if self.jingles is not None:
            try:
                if not self.jingles.seconds:
                    self.jingles.load()
                # A jingle shorter than the crossfade still takes the crossfade's time.
                adjust += max(self.jingles.seconds, st.crossfade) / st.jingle_every
            except (PlaylistError, OSError):
                pass
        for t in queue.tracks:
            track_seconds(t)  # learn lengths now, not while on air
            t.step_adjust = adjust

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
        if not self.target:
            raise ValueError("Set the station's test stream first" if self.rehearsal
                             else "Set the station's server first")
        self.jingles = JingleBag(st, measure=self.analyser.want if self.analyser else None) if st.jingles else None
        try:
            self.queue = self._load()
            self.fallback_queue = None
        except PlaylistError as exc:
            # The playlist can't be read at all (its drive isn't there, say):
            # go on air with the fallback, and switch to the playlist when it
            # can be read (_watch).
            fallback = self._fallback()
            if fallback is None:
                raise
            logger.error("%s: %s; playing the fallback until it can be read", st.name, exc)
            self.queue = self.fallback_queue = fallback[0]
            self.xml = None
        info = StreamInfo(name=st.name, genre=st.genre, url=st.url, public=st.public, bitrate=st.bitrate)
        password = (st.test_password or st.password) if self.rehearsal else st.password
        sender = sender_from_url(self.target, info, password or None)
        try:
            sender.connect()
        except (SenderError, OSError) as exc:
            if someone_sending(sender):  # Winamp, say, still on the server
                raise InUse(IN_USE.format(server=sender.describe)) from exc
            raise
        self.sender = Tap(sender, self.listeners)
        done = threading.Event()  # this go on air is over: the helpers stop
        try:
            self._on_air(done)
        finally:
            done.set()
            self.sender.close()
            self.listeners.end()
            self.audience = None

    def _on_air(self, done: threading.Event) -> None:
        st = self.station
        index, offset = self._where(self.queue)
        if self.analyser:  # measure the tracks coming up first
            self.analyser.want(self.queue.tracks[index:] + self.queue.tracks[:index])
            index, offset = self._where(self.queue)  # with the lengths now known
        queue, after = self.queue, None
        self.show_on = None
        joining = self._show_now(datetime.now().astimezone())
        if joining:  # back on air part-way through a show
            show, at, length = joining
            try:
                queue = self._show_queue(show)
                elapsed = (datetime.now().astimezone() - at).total_seconds()
                index, offset = position([step_seconds(t) for t in queue.tracks], elapsed)
                after = self._after_show(0)
                self.show_on = joining
                logger.info("%s: joining %s part-way", st.name, show.name)
            except (PlaylistError, OSError) as exc:
                logger.warning("%s: couldn't play %s: %s", st.name, show.name, exc)
                queue = self.queue
        self.channel = Channel(
            queue, self.sender, self.ffmpeg, bitrate=st.bitrate, start=index, offset=offset,
            level=st.level, on_played=self._played, keep_going=True, after=after,
            fallback=self._fallback, crossfade=st.crossfade, jingle=self._jingle if self.jingles else None,
            jingle_every=st.jingle_every, jingle_title=st.name,
        )
        threading.Thread(target=self._watch, args=(done,), daemon=True).start()
        threading.Thread(target=self._show_clock, args=(done, self.show_on), daemon=True).start()
        threading.Thread(target=self._count, args=(done, self.sender), daemon=True).start()
        if self._stopped.is_set():  # taken off air while connecting
            self.channel.stop()
        self.state, self.error = "on", None
        logger.info("%s on air to %s", st.name, self.sender.describe)
        self._listen_live()
        try:
            self.channel.run()
        finally:
            if self.live_input:
                self.live_input.stop()
                self.live_input = None

    # ---- live shows -------------------------------------------------------

    def _listen_live(self) -> None:
        """With live shows on, listen for a presenter while on air."""
        st = self.station
        self.live_error = None
        if not st.live:
            return
        live = LiveInput(st.live_port, st.live_password, self.ffmpeg, on_source=self._go_live)
        try:
            live.start()
        except OSError as exc:
            self.live_error = (f"Presenters can't go live: port {st.live_port} or {st.live_port + 1} "
                               f"is in use by another program ({exc.strerror or exc})")
            logger.warning("%s: %s", st.name, self.live_error)
            return
        self.live_input = live

    def _go_live(self, source: LiveSource) -> bool:
        """A presenter has connected: on air, the music fading out under them."""
        ch = self.channel
        if ch is None or self.state != "on":
            return False
        interrupted = self._interrupted if ch.in_show else (ch.now.index if ch.now else 0)
        self.show_on = None
        name = source.presenter or "Live"
        logger.info("%s: %s is live", self.station.name, name)
        ch.go_live(source, after=self._after_show(interrupted), on_title=self._live_played,
                   title=f"{self.station.name}: {name}" if source.presenter else f"{self.station.name}: Live")
        return True

    def end_live(self) -> None:
        """Take the presenter off air. Their encoder will try to reconnect,
        so they're kept off for a couple of minutes."""
        if self.live_input:
            self.live_input.end(block_seconds=END_LIVE_BLOCK_SECONDS)

    def _live_played(self, title: str, started: float, seconds: float) -> None:
        """A song a presenter played (by the title their encoder sent), for the play log."""
        if self.rehearsal:
            return
        artist, sep, name = title.partition(" - ")
        if not sep:
            artist, name = "", title
        self.playlog.add(_local(started), seconds, {
            "title": name.strip(), "artist": artist.strip(), "album": "Live", "duration_ms": None, "path": None,
        })

    def _count(self, done: threading.Event, sender, first: float = 5.0) -> None:
        """Ask the server how many are listening, now and then."""
        wait = first
        while not done.wait(wait):
            wait = COUNT_SECONDS
            try:
                counts = sender.count_listeners()
            except Exception as exc:  # an older server, a relay, a firewall: no count
                logger.debug("%s: no listener count: %s", self.station.name, exc)
                counts = None
            if not done.is_set():
                self.audience = {**counts, "at": _local(time.time()).isoformat(timespec="seconds")} if counts else None

    def _jingle(self) -> Track | None:
        """The next jingle, but none while the fallback plays instead of the
        playlist (the fallback may well be jingles itself)."""
        if self.jingles is None or self.playing_fallback():
            return None
        return self.jingles()

    def playing_fallback(self) -> bool:
        ch = self.channel
        return bool(ch and self.state == "on" and (
            ch.on_fallback or (self.fallback_queue is not None and ch.queue is self.fallback_queue)))

    def _fallback(self):
        """The station's fallback, to play once instead of silence, and then
        the playlist again, where the clock says by then."""
        st = self.station
        if not st.fallback:
            return None
        try:
            queue = source_queue("Fallback", st.fallback, st, "fallback")
        except (PlaylistError, OSError) as exc:
            logger.warning("%s: couldn't play the fallback: %s", st.name, exc)
            return None

        def after() -> tuple[Queue, int, float]:
            return (self.queue, *self._resume(self._heard_now(), -1))
        return queue, after

    @property
    def target(self) -> str:
        """Where it goes on air: the live server, or the test stream."""
        return self.station.test_server if self.rehearsal else self.station.server

    def _played(self, now: NowPlaying, seconds: float) -> None:
        if self.rehearsal:  # not on air for real: not for the play log
            return
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
            if self.analyser:
                self.analyser.want(queue.tracks)
            self.queue, self.fallback_queue = queue, None
            # During a show the new week simply comes in after it.
            if self.channel and self.show_on is None:
                self.channel.load(queue, where=self._where)

    # ---- for the page ---------------------------------------------------

    def status(self) -> dict:
        st = self.station
        out = {
            "station": st.to_json(secrets=False),
            "state": self.state,
            "rehearsal": self.rehearsal,
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
            "on_fallback": self.playing_fallback(),
            "listening": self.listeners.count,
            "audience": self.audience if self.state == "on" else None,
            "measuring": self.analyser.pending() if self.analyser and self.state == "on" else 0,
        }
        ch = self.channel
        # On air, the playlist is the one playing; a new week waiting for the
        # end of the track is shown as next.
        playing = ch.queue if ch and self.state == "on" and not ch.in_show else self.queue
        if playing:
            out["playlist"] = _playlist_info(playing)
            if self.queue is not None and self.queue is not playing:
                out["playlist"]["next"] = _playlist_info(self.queue)
        if st.live:
            out["live_input"] = {"port": st.live_port, "listening": self.live_input is not None,
                                 "error": self.live_error if self.state == "on" else None}
        live = ch.live if ch and self.state == "on" else None
        if live is not None:
            out["live"] = {"presenter": live.presenter, "title": live.title, "address": live.address,
                           "since": _local(live.since).isoformat(timespec="seconds"),
                           "elapsed": round(time.time() - live.since)}
        now = ch.now if ch and self.state == "on" else None
        if now and ch:
            t = now.track
            out["now"] = {
                "index": now.index, "title": t.name, "artist": t.artist, "album": t.album,
                "started": _local(now.started).isoformat(timespec="seconds"),
                "elapsed": round(max(0.0, time.time() - now.started), 1),
                "seconds": round(track_seconds(t), 1),
                "gain_db": round(now.gain_db, 1),
                "levelled_by": now.levelled_by,
            }
            if ch.jingle is not None:
                out["now"]["jingle"] = ch.jingle.name
            show_on = self.show_on if ch.in_show else None
            if show_on:
                out["now"]["show"] = show_on[0].name
                # When it really started (a show after the song starts late).
                began = _local(now.started) - timedelta(seconds=sum(step_seconds(t) for t in ch.queue.tracks[:now.index]))
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
        self.analyser = Analyser(self.ffmpeg, self.store.home / "analysis.json")
        self.runners: dict[str, StationRunner] = {}
        for st in self.config.stations:
            self.runners[st.id] = self._new_runner(st)
        self.started = time.time()
        self.updater: selfupdate.Updater | None = None  # set by web.make_server
        self.remote = None  # web.RemoteAccess, set by web.make_server
        self.alerts = alerts.Watcher(self)
        self._stopped = threading.Event()
        self._watching = False
        self.latest: str | None = None  # the newest release, once looked up

    def _new_runner(self, st: Station) -> StationRunner:
        return StationRunner(st, self.ffmpeg, self.store.home / "logs", weeks=self.weeks, awake=self.awake,
                             analyser=self.analyser)

    @property
    def config(self) -> Config:
        return self.store.config

    def resume(self) -> None:
        """Put back on air the stations that were on air when the app closed,
        and start watching them for alerts."""
        for st in self.config.stations:
            if st.on_air:
                self.runners[st.id].start()
        if not self._watching:
            self._watching = True
            threading.Thread(target=self.alerts.run, args=(self._stopped,), name="alerts", daemon=True).start()

    def save_settings(self, data: dict) -> None:
        """Settings › the app's own settings (not a station's)."""
        cfg = self.config
        if "alerts" in data:
            cfg.alerts = clean_alerts(data["alerts"] if isinstance(data["alerts"], dict) else {}, cfg.alerts)
        if "remote_password" in data and str(data["remote_password"] or ""):
            password = str(data["remote_password"])
            if len(password) < 8:
                raise ValueError("Use a password of at least 8 characters")
            cfg.remote_password = password
        if "remote_port" in data:
            cfg.remote_port = clean_port(data["remote_port"])
        if "remote" in data:
            if data["remote"] and not cfg.remote_password:
                raise ValueError("Set a password first")
            cfg.remote = bool(data["remote"])
        cfg.open_window = bool(data.get("open_window", cfg.open_window))
        cfg.check_updates = bool(data.get("check_updates", cfg.check_updates))
        if not cfg.check_updates:
            self.latest = None  # no more notices until checked by hand
        self.store.save()
        if "login" in data:
            self.login.set(bool(data["login"]))
        if self.remote is not None and any(k in data for k in ("remote", "remote_password", "remote_port")):
            self.remote.apply()

    def test_alert(self) -> list[str]:
        """Settings › Alerts › Send a test: returns where it went."""
        cfg = self.config.alerts
        if not (cfg.ntfy or cfg.email_to):
            raise ValueError("Set up a phone notification or an email first, and save")
        errors = alerts.send(cfg, "Station Server test alert",
                             "This is how Station Server tells you a station is off air.")
        if errors:
            raise OSError("; ".join(errors))
        return [w for w, on in (("your phone", cfg.ntfy), (cfg.email_to, cfg.email_to)) if on]

    def check_for_updates(self, force: bool = False, fetch=None) -> str | None:
        """Look for a new version: at most once a day unless `force`, and
        not at all with Settings › Tell me when a new version is out off.
        A forced check (Check for updates) raises if GitHub can't be asked."""
        if force or self.config.check_updates:
            self.latest = updates.latest_version(self.store.home / "update-check.json", fetch=fetch,
                                                 force=force, quiet=not force)
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
            "alerts": {**self.config.alerts.to_json(secrets=False), "last": self.alerts.last},
            "remote": self.remote.status() if self.remote else None,
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
        if st.live:
            taken = {8770} | ({self.config.remote_port} if self.config.remote else set())
            for other in self.config.stations:
                if other.id != st.id and other.live:
                    taken |= {other.live_port, other.live_port + 1}
            if {st.live_port, st.live_port + 1} & taken:
                raise ValueError(f"Live shows port {st.live_port} (or the one above) is used by another station "
                                 "or by Station Server: choose another")
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

    def set_on_air(self, sid: str, on: bool, rehearse: bool = False) -> None:
        """On or off air; `rehearse` goes on air to the test stream instead
        (not remembered at restart, not in the play log, no alerts). Going
        live from a rehearsal switches over."""
        runner = self._runner(sid)
        if on:
            if rehearse and not runner.station.test_server:
                raise ValueError("Set a test stream in Edit › Server first")
            if runner.rehearsal != rehearse and runner.state in ("starting", "on", "waiting"):
                runner.stop()
            runner.rehearsal = rehearse
            runner.start()
        else:
            runner.stop()
            runner.rehearsal = False
        runner.station.on_air = on and not rehearse
        self.store.save()

    def restart(self, sid: str) -> None:
        runner = self._runner(sid)
        runner.stop()
        runner.start()

    def skip(self, sid: str) -> None:
        self._runner(sid).skip()

    def end_live(self, sid: str) -> None:
        self._runner(sid).end_live()

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
        """Connect to the server in `data` (its test stream, with `test`)
        and leave again. Uses the saved password when none is typed."""
        existing = self.config.station(sid) if sid else None
        st = clean_station(data, existing)
        test = bool(data.get("test"))
        if not (st.test_server if test else st.server):
            raise ValueError("Enter the test stream's address" if test else "Enter the server address")
        return self._try_server(st, test)

    def _try_server(self, st: Station, test: bool = False) -> dict:
        """Can this station go on air there? A station already on air there
        isn't disturbed, and nor is another source (Winamp, say): a server
        takes one source at a time, so that's reported instead."""
        addr = st.test_server if test else st.server
        password = (st.test_password or st.password) if test else st.password
        runner = self.runners.get(st.id)
        if runner and runner.state == "on" and runner.target == addr:
            counts = runner.audience
            if counts is None:
                try:
                    counts = runner.sender.count_listeners()
                except Exception:  # an older server or a firewall: no counts
                    counts = None
            return {"server": runner.sender.describe, "on_air": True, "counts": counts}
        sender = sender_from_url(addr, StreamInfo(name=st.name), password or None)
        if someone_sending(sender):
            raise InUse(IN_USE.format(server=sender.describe))
        sender.connect()
        sender.close()
        try:
            counts = sender.count_listeners()
        except Exception:  # an older server or a firewall: no counts
            counts = None
        return {"server": sender.describe, "on_air": False, "counts": counts}

    def ready_check(self, sid: str) -> list[dict]:
        """Everything a station needs to run by itself, checked in one go
        (the station page's Ready check): each is ok, warn or fail."""
        runner = self._runner(sid)
        st = runner.station
        out: list[dict] = []

        def add(name: str, status: str, detail: str) -> None:
            out.append({"name": name, "status": status, "detail": detail})

        add("ffmpeg", "ok" if self.ffmpeg else "fail", self.ffmpeg or self.ffmpeg_error or "not found")
        try:
            xml, upcoming = playlist_file(st, self.weeks, remember=False)
            queue = load_queue(xml, st.music_folders, st.remaps)
            hours = sum(track_seconds(t) for t in queue.tracks) / 3600
            found = len(queue.tracks)
            if not found:
                add("Playlist", "fail", f"{xml.name}: none of its {len(queue.missing)} tracks can be found. "
                    "Add the folder the music is in under Music folders.")
            elif queue.missing:
                add("Playlist", "warn", f"{queue.name}: {found} tracks, {hours:.1f} hours; {len(queue.missing)} can't "
                    "be found and will be left out (listed on the station page).")
            else:
                add("Playlist", "ok", f"{queue.name}: all {found} tracks found, {hours:.1f} hours")
            if upcoming:
                add("Next week", "ok", f"{upcoming[0].stem} goes on {upcoming[1]:%A %d %B %H:%M}")
        except (PlaylistError, OSError) as exc:
            add("Playlist", "fail", str(exc))
        for label, test in (("Live server", False), ("Test stream", True)):
            if not (st.test_server if test else st.server):
                if not test:
                    add(label, "fail", "Not set: enter it in Edit › Server")
                continue
            try:
                r = self._try_server(st, test)
                counts = r.get("counts")
                if r["on_air"]:
                    detail = f"On air to {r['server']} now" + (f", {counts['listeners']} listening" if counts else "")
                else:
                    detail = f"{r['server']} accepted the password"
                if not counts:
                    detail += " (its listener count can't be read, so the page won't show one)"
                add(label, "ok", detail)
            except InUse as exc:  # Winamp still live there: expected until the switch-over
                add(label, "warn", f"{exc} Do that just before Go live; the rest can be checked then.")
            except (SenderError, OSError, ValueError) as exc:
                add(label, "fail", str(exc))
        if st.fallback:
            try:
                fb = source_queue("Fallback", st.fallback, st, "fallback")
                add("Fallback", "ok", f"{len(fb.tracks)} tracks")
            except (PlaylistError, OSError) as exc:
                add("Fallback", "fail", str(exc))
        else:
            add("Fallback", "warn", "None set: if the music can't be read, the station sends silence")
        if st.jingles:
            try:
                bag = JingleBag(st)
                found = bag.load()
                add("Jingles", "ok", f"{len(found)} jingles, about {bag.seconds:.0f} s each, "
                    f"one after every {st.jingle_every} songs")
            except (PlaylistError, OSError) as exc:
                add("Jingles", "fail", str(exc))
        for show in runner.shows():
            if not runner.show_length(show):
                add(f"Show: {show.name}", "fail", f"{show.source} can't be found or played")
        if st.live:
            ports = f"{st.live_port} and {st.live_port + 1}"
            here = next(iter(lan_addresses()), "this computer's address")
            where = (f"Presenters connect to {here} port {st.live_port}. From outside this network, forward "
                     f"ports {ports} on the router to this computer.")
            if runner.live_input is not None:
                add("Live shows", "ok", f"Listening for presenters. {where}")
            elif runner.state == "on" and runner.live_error:
                add("Live shows", "fail", runner.live_error)
            elif all(port_free(p) for p in (st.live_port, st.live_port + 1)):
                add("Live shows", "ok", f"Ports {ports} are free; presenters can connect while it's on air. {where}")
            else:
                add("Live shows", "fail", f"Port {st.live_port} or {st.live_port + 1} is in use by another program: "
                    "choose another live shows port in Edit")
        add("Start at login", "ok" if self.login.enabled() else "warn",
            "On" if self.login.enabled() else "Off: after a restart or power cut the station stays off air "
            "until someone opens Station Server (Settings)")
        alerts_to = [w for w, on in (("phone", self.config.alerts.ntfy), ("email", self.config.alerts.email_to)) if on]
        add("Alerts", "ok" if alerts_to else "warn",
            f"To your {' and '.join(alerts_to)}" if alerts_to else "Off: nobody is told if it goes off air (Settings)")
        return out

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
        cfg = self.config
        secrets = {p for s in cfg.stations for p in (s.password, s.test_password) if p}
        secrets |= {x for x in (cfg.alerts.smtp_password, cfg.remote_password, cfg.alerts.ntfy) if x}
        for secret in secrets:
            text = text.replace(secret, "********")
        return re.sub(r"(://[^/@\s]*?:)[^/@\s]*@", r"\1********@", text)

    def shutdown(self) -> None:
        """Stop every station, remembering which were on air."""
        self._stopped.set()
        if self.remote is not None:
            self.remote.stop()
        for runner in self.runners.values():
            runner.stop()
