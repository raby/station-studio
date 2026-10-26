"""Player settings, stored as JSON under the player's home folder.

Resolution: `$STATION_PLAYER_HOME`, else `~/.station-player/`. The file
is small and human-editable; the UI writes it through the API.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from station_player.logos import is_name as is_logo_name

logger = logging.getLogger(__name__)

ENV_VAR = "STATION_PLAYER_HOME"

DEFAULT_STATIONS = [
    {
        "name": "Rebirth Radio",
        "url": "https://sh.uk.audio-stream.com/proxy/adgerdes?mp=/stream",
    },
    {
        "name": "Rebirth Classics",
        "url": "http://81.150.147.206:8000",
    },
]
# Settings from before the player kept track of which built-in stations it
# had already added only ever had this one.
_FIRST_DEFAULTS = [DEFAULT_STATIONS[0]["url"]]


def get_home() -> Path:
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".station-player"


def station_studio_exports() -> Path:
    """Where station-studio writes exported playlists (its app-home)."""
    override = os.environ.get("STATION_STUDIO_HOME", "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".station-studio"
    return base / "exports"


@dataclass
class Config:
    # iTunes XML files, or folders that are scanned for *.xml
    playlist_sources: list[str] = field(default_factory=list)
    # folders of audio files; each becomes a playlist, and their files
    # are used to find tracks whose iTunes Location has moved
    music_folders: list[str] = field(default_factory=list)
    # [{"from": "/Users/rob/Music", "to": "D:/Music"}]
    path_remaps: list[dict] = field(default_factory=list)
    # [{"name": ..., "url": ..., "logo": a logos.py name (optional),
    #   "tags": your own labels, like "jazz" (optional)}]
    stations: list[dict] = field(default_factory=lambda: [dict(s) for s in DEFAULT_STATIONS])
    # the built-in stations already added once: a new one is added to
    # existing settings once too, and one you remove stays removed
    defaults_offered: list[str] = field(default_factory=lambda: [s["url"] for s in DEFAULT_STATIONS])
    # stations listened to lately, newest first (saved or not), same shape
    recent_stations: list[dict] = field(default_factory=list)
    # ask PyPI once a day whether a newer Station Player is out
    check_updates: bool = True
    # open in an app window of its own (Chrome/Edge/Brave) rather than a tab
    own_window: bool = True
    # keep a log of the tracks played, for music reporting (PRS / PPL)
    play_log: bool = True
    # wake-up alarms (up to MAX_ALARMS): [{"id": 8 hex digits, "on",
    # "time": "HH:MM", "days": [0-6, Monday = 0],
    # "source": {"kind": "playlist" | "station", "id", "name"}, "label"}]
    alarms: list[dict] = field(default_factory=list)
    # studio tools, for people who make a station: station-studio's
    # exported playlists, recording radio and the play log. Off, it's the
    # plain radio and music player. New installs start with them only if
    # station-studio is on this computer; older settings keep them.
    studio: bool = True
    # control the player from a phone on the same network (see remote.py)
    remote: bool = False
    remote_token: str = ""
    remote_port: int = 8766

    def to_json(self) -> dict:
        return asdict(self)


def _str_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v.strip()]


def _pairs(value, keys: tuple[str, str]) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        {k: v[k] for k in keys} for v in value
        if isinstance(v, dict) and all(isinstance(v.get(k), str) and v[k].strip() for k in keys)
    ]


MAX_RECENT = 8
MAX_TAGS = 12
MAX_TAG = 30


def clean_tags(value) -> list[str]:
    """Tags as given (a list, or text split at commas): tidied, at most
    MAX_TAGS, without repeats that differ only in capitals."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        if not isinstance(v, str):
            continue
        tag = " ".join(v.split())[:MAX_TAG].strip()
        if tag and tag.casefold() not in (t.casefold() for t in out):
            out.append(tag)
        if len(out) == MAX_TAGS:
            break
    return out


def _stations(value) -> list[dict]:
    """[{"name", "url"}] pairs, keeping a logo name and tags where there are some."""
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, dict) and all(isinstance(v.get(k), str) and v[k].strip() for k in ("name", "url")):
            st = {"name": v["name"], "url": v["url"]}
            if is_logo_name(v.get("logo")):
                st["logo"] = v["logo"]
            tags = clean_tags(v.get("tags"))
            if tags:
                st["tags"] = tags
            if v.get("tuner") is False:  # left off the Radio page's tuner dial
                st["tuner"] = False
            feed = v.get("feed")  # what's on, from its Station Server (whatson.py); "" once taken off
            if isinstance(feed, str) and (not feed or feed.startswith(("http://", "https://"))):
                st["feed"] = feed
            out.append(st)
    return out


MAX_ALARMS = 30  # alarms and radio schedule switches together
MAX_ALARM_LABEL = 60
_ALARM_ID = re.compile(r"[0-9a-f]{8}")


def _valid_date(text: str) -> bool:
    try:
        return re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) is not None and bool(date.fromisoformat(text))
    except ValueError:
        return False


def new_alarm_id() -> str:
    return secrets.token_hex(4)


def _clean_one_alarm(value, where: str = "The alarm") -> dict:
    """One alarm, checked; its id is kept as given (clean_alarms fixes it)."""
    if not isinstance(value, dict):
        raise ValueError(f"{where} isn't set out properly")
    when = value.get("time")
    if not isinstance(when, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", when):
        raise ValueError(f"{where} needs a time written as HH:MM, like 07:30")
    # A date makes it go off once, on that day; otherwise every week on
    # its days.
    once = value.get("date") or ""
    if not isinstance(once, str) or (once and not _valid_date(once)):
        raise ValueError(f"{where} needs a date written as YYYY-MM-DD, like 2026-10-23")
    days = value.get("days")
    if once and not days:
        days = []
    elif not isinstance(days, list) or not days or not all(
            isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
        raise ValueError(f"{where} needs at least one day (0 is Monday, 6 is Sunday)")
    # "alarm" rings (fading in, with Snooze); "switch" changes what's
    # playing at that time, as the radio schedule does.
    action = value.get("action") or "alarm"
    if action not in ("alarm", "switch"):
        raise ValueError(f"{where} must be an alarm or a schedule switch")
    source = value.get("source")
    if not (isinstance(source, dict) and source.get("kind") in ("playlist", "station")
            and isinstance(source.get("id"), str) and source["id"]):
        raise ValueError(f"{where} needs a playlist or station to play")
    name = source.get("name")
    label = value.get("label")
    if label is None:
        label = ""
    if not isinstance(label, str):
        raise ValueError(f"{where}'s label must be text")
    alarm_id = value.get("id")
    return {"id": alarm_id if isinstance(alarm_id, str) else "",
            "on": bool(value.get("on")), "time": when, "days": sorted(set(days)), "date": once, "action": action,
            "source": {"kind": source["kind"], "id": source["id"][:100],
                       "name": name[:200] if isinstance(name, str) else ""},
            "label": " ".join(label.split())[:MAX_ALARM_LABEL].strip()}


def _fix_ids(alarms: list[dict]) -> list[dict]:
    """Missing, malformed or repeated ids get new random ones."""
    seen: set[str] = set()
    for alarm in alarms:
        if not _ALARM_ID.fullmatch(alarm.get("id") or "") or alarm["id"] in seen:
            alarm["id"] = new_alarm_id()
            while alarm["id"] in seen or any(a.get("id") == alarm["id"] for a in alarms if a is not alarm):
                alarm["id"] = new_alarm_id()
        seen.add(alarm["id"])
    return alarms


def clean_alarm(value) -> dict | None:
    """A valid single alarm (with an id and label), or None. Raises ValueError."""
    if value is None:
        return None
    return _fix_ids([_clean_one_alarm(value)])[0]


def clean_alarms(value) -> list[dict]:
    """A valid list of alarms. Raises ValueError with a message for people."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("The alarms aren't set out properly (expected a list)")
    if len(value) > MAX_ALARMS:
        raise ValueError(f"You can have up to {MAX_ALARMS} alarms")
    many = len(value) > 1
    return _fix_ids([_clean_one_alarm(v, f"Alarm {i + 1}" if many else "The alarm")
                     for i, v in enumerate(value)])


def _alarms_from_json(data: dict) -> list[dict]:
    """Alarms from a settings file: malformed ones are dropped, and the
    single `alarm` of older versions becomes the first of a list."""
    if "alarms" in data:
        raw = data["alarms"] if isinstance(data["alarms"], list) else []
    elif data.get("alarm") is not None:
        raw = [{**data["alarm"], "id": None, "label": ""}] if isinstance(data["alarm"], dict) else []
    else:
        raw = []
    out = []
    for item in raw:
        try:
            out.append(_clean_one_alarm(item))
        except ValueError:
            continue
        if len(out) == MAX_ALARMS:
            break
    return _fix_ids(out)


def config_from_json(data: dict) -> Config:
    """Build a Config from parsed JSON, dropping anything malformed."""
    cfg = Config(
        playlist_sources=_str_list(data.get("playlist_sources")),
        music_folders=_str_list(data.get("music_folders")),
        path_remaps=_pairs(data.get("path_remaps"), ("from", "to")),
    )
    if "stations" in data:
        cfg.stations = _stations(data.get("stations"))
    offered = _str_list(data.get("defaults_offered")) if "defaults_offered" in data else list(_FIRST_DEFAULTS)
    for st in DEFAULT_STATIONS:
        if st["url"] in offered:
            continue
        if all(s["url"] != st["url"] for s in cfg.stations):
            cfg.stations.append(dict(st))
        offered.append(st["url"])
    cfg.defaults_offered = offered
    # A built-in station given a what's-on feed since: yours gets it too,
    # unless you took it off.
    for st in cfg.stations:
        default = next((d for d in DEFAULT_STATIONS if d["url"] == st["url"] and d.get("feed")), None)
        if default and "feed" not in st:
            st["feed"] = default["feed"]
    cfg.recent_stations = _stations(data.get("recent_stations"))[:MAX_RECENT]
    if isinstance(data.get("check_updates"), bool):
        cfg.check_updates = data["check_updates"]
    for key in ("own_window", "play_log", "remote", "studio"):
        if isinstance(data.get(key), bool):
            setattr(cfg, key, data[key])
    cfg.alarms = _alarms_from_json(data)
    token = data.get("remote_token")
    if isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token):
        cfg.remote_token = token
    port = data.get("remote_port")
    if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
        cfg.remote_port = port
    return cfg


class ConfigStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or get_home()
        self.path = self.home / "config.json"
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        self.config = self._read()
        if self._needs_save:
            try:  # e.g. new alarm ids: written now so they stay the same next time
                self.save()
            except OSError as exc:
                logger.warning("Could not update the settings file: %s", exc)

    def _read(self) -> Config:
        self._needs_save = False
        if not self.path.is_file():
            cfg = Config()
            exports = station_studio_exports()
            cfg.studio = exports.is_dir()
            if cfg.studio:
                cfg.playlist_sources.append(str(exports.resolve()))
            return cfg
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError) as exc:
            # Keep the broken file for the user to fix rather than
            # overwriting it with defaults on the next save.
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
        cfg = config_from_json(data)
        given = data.get("alarms") if isinstance(data.get("alarms"), list) else []
        given_ids = [a.get("id") if isinstance(a, dict) else None for a in given]
        self._needs_save = "alarms" not in data and data.get("alarm") is not None \
            or any(a["id"] not in given_ids for a in cfg.alarms) \
            or cfg.defaults_offered != data.get("defaults_offered")  # a new built-in station was added
        return cfg

    def save(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.config.to_json(), indent=2), encoding="utf-8")
            tmp.replace(self.path)
