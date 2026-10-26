"""Settings: the stations Station Server runs, kept in `config.json`.

Everything lives in `~/.station-server` (or `$STATION_SERVER_HOME`): the
settings, each station's play log, and the window's browser profile. The
file holds the stations' source passwords, so on macOS and Linux it is
readable by its owner only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from station_server.schedule import Show, WeekStart

logger = logging.getLogger(__name__)

ENV_VAR = "STATION_SERVER_HOME"
START_MODES = ("clock", "top")  # follow the week's clock, or start at track 1


def get_home() -> Path:
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".station-server"


def studio_exports() -> Path:
    """Where station-studio writes exported playlists."""
    override = os.environ.get("STATION_STUDIO_HOME", "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".station-studio"
    return base / "exports"


MAX_CROSSFADE = 10.0


@dataclass
class Station:
    id: str = ""
    name: str = "New station"
    # An exported playlist, or a folder of them (the newest week is played:
    # station-studio writes ~/.station-studio/exports/<station>/<week>.xml).
    playlist: str = ""
    music_folders: list[str] = field(default_factory=list)
    remaps: list[dict] = field(default_factory=list)
    # Where to send it: shoutcast://host:port[?sid=N] or icecast://[user@]host:port/mount.
    server: str = ""
    password: str = ""
    # A spare stream to rehearse on before going live (a spare port, or
    # stream 2 of a SHOUTcast 2 server), and its password if different.
    test_server: str = ""
    test_password: str = ""
    genre: str = ""
    url: str = ""
    public: bool = False
    bitrate: int = 128
    level: bool = True
    start_mode: str = "clock"
    week_start: dict = field(default_factory=lambda: WeekStart().to_json())
    on_air: bool = False  # was on air when last closed: goes back on air at start-up
    # Shows at set times (schedule.Show.to_json()): a recording or playlist
    # that interrupts the music, which then carries on by the clock.
    shows: list[dict] = field(default_factory=list)
    # Played instead of silence when none of the playlist can be read (the
    # music drive is missing, say): a folder of jingles or songs, or a playlist.
    fallback: str = ""
    # Seconds each song fades out under the next (0: none, end to end).
    crossfade: float = 0.0
    # A folder of jingles (idents), one played after every `jingle_every` songs.
    jingles: str = ""
    jingle_every: int = 4
    # Live shows (live.py): a presenter's encoder connects to this port
    # (Icecast; SHOUTcast to it and the one above) with this password.
    live: bool = False
    live_port: int = 8010
    live_password: str = ""  # shared by all presenters (optional, with presenters' own)
    # Presenters with their own password: {"id", "name", "password"}.
    presenters: list[dict] = field(default_factory=list)
    record_live: bool = True  # keep a recording of each live show
    live_level: bool = True  # even out presenters' volume

    def to_json(self, secrets: bool = True) -> dict:
        data = asdict(self)
        if not secrets:
            data["password"] = ""
            data["has_password"] = bool(self.password)
            data["test_password"] = ""
            data["has_test_password"] = bool(self.test_password)
            data["live_password"] = ""
            data["has_live_password"] = bool(self.live_password)
            data["presenters"] = [{"id": p["id"], "name": p["name"], "password": "",
                                   "has_password": bool(p["password"])} for p in self.presenters]
        return data


_FIELDS = {f.name for f in fields(Station)}


def clean_station(data: dict, existing: Station | None = None) -> Station:
    """A Station from settings or the page, with every field checked.
    Raises ValueError with a message for the page."""
    st = Station(**asdict(existing)) if existing else Station()
    for key, value in data.items():
        if key not in _FIELDS or key == "id":
            continue
        if key in ("password", "test_password", "live_password") and value in (None, "") and existing is not None:
            continue  # the page never sees the password; blank keeps it
        setattr(st, key, value)
    st.name = str(st.name or "").strip()[:80] or "New station"
    for key in ("playlist", "server", "password", "test_server", "test_password", "genre", "url", "fallback",
                "jingles", "live_password"):
        st.__dict__[key] = str(getattr(st, key) or "").strip()
    st.music_folders = [str(p).strip() for p in st.music_folders or [] if str(p).strip()]
    st.remaps = [
        {"from": str(r.get("from", "")).strip(), "to": str(r.get("to", "")).strip()}
        for r in st.remaps or [] if isinstance(r, dict) and r.get("from") and r.get("to")
    ]
    try:
        st.bitrate = int(st.bitrate)
    except (TypeError, ValueError):
        raise ValueError("Bitrate must be a number of kbps") from None
    if not 32 <= st.bitrate <= 320:
        raise ValueError("Bitrate must be between 32 and 320 kbps")
    try:
        st.crossfade = round(float(st.crossfade or 0), 1)
        st.jingle_every = int(st.jingle_every)
    except (TypeError, ValueError):
        raise ValueError("The crossfade and how often jingles play are numbers") from None
    if not 0 <= st.crossfade <= MAX_CROSSFADE:
        raise ValueError(f"The crossfade is between 0 (none) and {MAX_CROSSFADE:.0f} seconds")
    if not 1 <= st.jingle_every <= 20:
        raise ValueError("Play a jingle after every 1 to 20 songs")
    st.live = bool(st.live)
    st.record_live = bool(st.record_live)
    st.live_level = bool(st.live_level)
    try:
        st.live_port = int(st.live_port or 8010)
    except (TypeError, ValueError):
        raise ValueError("The live shows port is a number, like 8010") from None
    if not 1024 <= st.live_port <= 65534:
        raise ValueError("The live shows port is between 1024 and 65534")
    st.presenters = _clean_presenters(st.presenters, existing.presenters if existing else [])
    if st.live and not st.live_password and not st.presenters:
        raise ValueError("Set a password for presenters going live, or add presenters with their own")
    passwords = [p["password"] for p in st.presenters] + ([st.live_password] if st.live_password else [])
    if len(set(passwords)) != len(passwords):
        raise ValueError("Each presenter needs a different password (and different from the shared one)")
    st.public = bool(st.public)
    st.level = bool(st.level)
    st.on_air = bool(st.on_air)
    if st.start_mode not in START_MODES:
        st.start_mode = "clock"
    st.week_start = WeekStart.parse(st.week_start).to_json()
    if not isinstance(st.shows, list):
        raise ValueError("Shows must be a list")
    st.shows = [Show.parse(x).to_json() for x in st.shows]
    for addr in (st.server, st.test_server):
        if addr and not re.match(r"^(shoutcast|icecast)://", addr, re.I):
            raise ValueError("The server address starts shoutcast:// or icecast://")
    if st.test_server and st.test_server == st.server:
        raise ValueError("The test stream must be a different stream from the live one")
    st.id = existing.id if existing else (str(data.get("id") or "") or uuid.uuid4().hex[:10])
    return st


def _clean_presenters(given, before: list[dict]) -> list[dict]:
    """Presenters from the page: a blank password keeps the one they had."""
    if not isinstance(given, list):
        raise ValueError("Presenters must be a list")
    had = {p.get("id"): p.get("password", "") for p in before if isinstance(p, dict)}
    out = []
    for p in given:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()[:60]
        password = str(p.get("password") or "").strip() or had.get(p.get("id"), "")
        if not name and not password:
            continue  # an empty row
        if not name:
            raise ValueError("Give each presenter a name")
        if not password:
            raise ValueError(f"Give presenter {name} a password")
        out.append({"id": str(p.get("id") or "") or uuid.uuid4().hex[:8], "name": name, "password": password})
    return out


SMTP_SECURITY = ("starttls", "ssl", "none")
REMOTE_PORT = 8771
STUDIO_PORT = 8772


@dataclass
class Alerts:
    """Who is told when a station goes off air (alerts.py)."""
    after_minutes: int = 3  # off air this long before anyone is told
    back_on: bool = True  # and told again when it's back
    ntfy: str = ""  # a phone notification: an ntfy topic, https://ntfy.sh/<topic>
    email_to: str = ""  # an email, sent through this mail server
    email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""

    def to_json(self, secrets: bool = True) -> dict:
        data = asdict(self)
        if not secrets:
            data["smtp_password"] = ""
            data["has_smtp_password"] = bool(self.smtp_password)
        return data


def clean_alerts(data: dict, existing: Alerts | None = None) -> Alerts:
    a = Alerts(**asdict(existing)) if existing else Alerts()
    names = {f.name for f in fields(Alerts)}
    for key, value in (data or {}).items():
        if key not in names:
            continue
        if key == "smtp_password" and value in (None, ""):
            continue  # the page never sees it; blank keeps it
        setattr(a, key, value)
    for key in ("ntfy", "email_to", "email_from", "smtp_host", "smtp_user", "smtp_password"):
        setattr(a, key, str(getattr(a, key) or "").strip())
    try:
        a.after_minutes = int(a.after_minutes)
        a.smtp_port = int(a.smtp_port or 0)
    except (TypeError, ValueError):
        raise ValueError("Minutes and the mail server's port are numbers") from None
    if not 1 <= a.after_minutes <= 120:
        raise ValueError("Alert after between 1 and 120 minutes")
    if a.smtp_host and not 1 <= a.smtp_port <= 65535:
        raise ValueError("The mail server's port is a number like 587")
    a.back_on = bool(a.back_on)
    if a.smtp_security not in SMTP_SECURITY:
        a.smtp_security = "starttls"
    if a.ntfy and not re.match(r"^https?://[^/\s]+/[\w-]{1,64}$", a.ntfy):
        raise ValueError("The phone notification topic is a link like https://ntfy.sh/your-topic-name")
    if a.email_to and not a.smtp_host:
        raise ValueError("To send emails, enter the mail server too")
    for addr in (a.email_to, a.email_from):
        if addr and not re.match(r"^[^@\s,]+@[^@\s,]+$", addr):
            raise ValueError(f"{addr} isn't an email address")
    return a


@dataclass
class Config:
    stations: list[Station] = field(default_factory=list)
    open_window: bool = True
    check_updates: bool = True  # look for a new version once a day
    alerts: Alerts = field(default_factory=Alerts)
    # Phones and other computers on the network may see the stations and
    # put them on or off air, with this password (web.RemoteAccess).
    remote: bool = False
    remote_password: str = ""
    remote_port: int = REMOTE_PORT
    # Station Studio sends each week here, with this code (studiolink.py).
    studio_link: bool = False
    studio_code: str = ""
    studio_port: int = STUDIO_PORT
    # What's playing, published to the station's website (feed.py).
    feed: bool = False
    feed_repo: str = ""  # owner/name of a GitHub repository served by GitHub Pages
    feed_token: str = ""

    def station(self, sid: str) -> Station | None:
        return next((s for s in self.stations if s.id == sid), None)


def clean_port(value) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError("The port is a number like 8771") from None
    if not 1024 <= port <= 65535:
        raise ValueError("The port is a number from 1024 to 65535")
    return port


class ConfigStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or get_home()
        self.path = self.home / "config.json"
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        self.config = self._read()

    def _read(self) -> Config:
        if not self.path.is_file():
            return Config()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError) as exc:
            backup = self.path.with_name(f"config.broken-{int(time.time())}.json")
            try:
                self.path.replace(backup)
                where = f"moved to {backup}"
            except OSError:
                where = f"left at {self.path}"
            message = f"Settings file could not be read ({exc}); started with defaults, old file {where}"
            logger.warning(message)
            self.warnings.append(message)
            return Config()
        cfg = Config(open_window=bool(data.get("open_window", True)),
                     check_updates=bool(data.get("check_updates", True)),
                     remote=bool(data.get("remote", False)),
                     remote_password=str(data.get("remote_password") or ""),
                     studio_link=bool(data.get("studio_link", False)),
                     studio_code=str(data.get("studio_code") or ""),
                     feed=bool(data.get("feed", False)),
                     feed_repo=str(data.get("feed_repo") or ""),
                     feed_token=str(data.get("feed_token") or ""))
        try:
            cfg.remote_port = clean_port(data.get("remote_port", REMOTE_PORT))
            cfg.studio_port = clean_port(data.get("studio_port", STUDIO_PORT))
            cfg.alerts = clean_alerts(data.get("alerts") or {})
        except ValueError as exc:
            self.warnings.append(f"Some settings were left out: {exc}")
        for raw in data.get("stations") or []:
            if not isinstance(raw, dict):
                continue
            try:
                cfg.stations.append(clean_station(raw))
            except ValueError as exc:
                self.warnings.append(f"A station in the settings was left out: {exc}")
        return cfg

    def save(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            data = {
                "open_window": self.config.open_window,
                "check_updates": self.config.check_updates,
                "alerts": self.config.alerts.to_json(),
                "remote": self.config.remote,
                "remote_password": self.config.remote_password,
                "remote_port": self.config.remote_port,
                "studio_link": self.config.studio_link,
                "studio_code": self.config.studio_code,
                "studio_port": self.config.studio_port,
                "feed": self.config.feed,
                "feed_repo": self.config.feed_repo,
                "feed_token": self.config.feed_token,
                "stations": [s.to_json() for s in self.config.stations],
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            if os.name == "posix":
                os.chmod(tmp, 0o600)  # it holds the source passwords
            tmp.replace(self.path)


def newest_week(folder: Path) -> Path | None:
    """The playlist to play from a folder of exports: the highest week
    number (`012.xml` beats `011.xml`), ignoring archived versions
    (`012-v2.xml`); for other names, the most recently changed .xml."""
    weeks = []
    others = []
    for p in folder.glob("*.xml"):
        m = re.fullmatch(r"(\d+)\.xml", p.name, re.I)
        if m:
            weeks.append((int(m.group(1)), p))
        elif not re.fullmatch(r"\d+-v\d+\.xml", p.name, re.I):
            others.append(p)
    if weeks:
        return max(weeks)[1]
    if others:
        return max(others, key=lambda p: p.stat().st_mtime)
    return None
