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

    def to_json(self, secrets: bool = True) -> dict:
        data = asdict(self)
        if not secrets:
            data["password"] = ""
            data["has_password"] = bool(self.password)
        return data


_FIELDS = {f.name for f in fields(Station)}


def clean_station(data: dict, existing: Station | None = None) -> Station:
    """A Station from settings or the page, with every field checked.
    Raises ValueError with a message for the page."""
    st = Station(**asdict(existing)) if existing else Station()
    for key, value in data.items():
        if key not in _FIELDS or key == "id":
            continue
        if key == "password" and value in (None, "") and existing is not None:
            continue  # the page never sees the password; blank keeps it
        setattr(st, key, value)
    st.name = str(st.name or "").strip()[:80] or "New station"
    for key in ("playlist", "server", "password", "genre", "url"):
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
    st.public = bool(st.public)
    st.level = bool(st.level)
    st.on_air = bool(st.on_air)
    if st.start_mode not in START_MODES:
        st.start_mode = "clock"
    st.week_start = WeekStart.parse(st.week_start).to_json()
    if not isinstance(st.shows, list):
        raise ValueError("Shows must be a list")
    st.shows = [Show.parse(x).to_json() for x in st.shows]
    if st.server and not re.match(r"^(shoutcast|icecast)://", st.server, re.I):
        raise ValueError("The server address starts shoutcast:// or icecast://")
    st.id = existing.id if existing else (str(data.get("id") or "") or uuid.uuid4().hex[:10])
    return st


@dataclass
class Config:
    stations: list[Station] = field(default_factory=list)
    open_window: bool = True
    check_updates: bool = True  # look for a new version once a day

    def station(self, sid: str) -> Station | None:
        return next((s for s in self.stations if s.id == sid), None)


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
                     check_updates=bool(data.get("check_updates", True)))
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
