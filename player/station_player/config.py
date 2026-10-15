"""Player settings, stored as JSON under the player's home folder.

Resolution: `$STATION_PLAYER_HOME`, else `~/.station-player/`. The file
is small and human-editable; the UI writes it through the API.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_VAR = "STATION_PLAYER_HOME"

DEFAULT_STATIONS = [
    {
        "name": "Rebirth Radio",
        "url": "https://sh.uk.audio-stream.com/proxy/adgerdes?mp=/stream",
    },
]


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
    # [{"name": ..., "url": ...}]
    stations: list[dict] = field(default_factory=lambda: [dict(s) for s in DEFAULT_STATIONS])
    # ask PyPI once a day whether a newer Station Player is out
    check_updates: bool = True
    # open in an app window of its own (Chrome/Edge/Brave) rather than a tab
    own_window: bool = True

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


def config_from_json(data: dict) -> Config:
    """Build a Config from parsed JSON, dropping anything malformed."""
    cfg = Config(
        playlist_sources=_str_list(data.get("playlist_sources")),
        music_folders=_str_list(data.get("music_folders")),
        path_remaps=_pairs(data.get("path_remaps"), ("from", "to")),
    )
    if "stations" in data:
        cfg.stations = _pairs(data.get("stations"), ("name", "url"))
    if isinstance(data.get("check_updates"), bool):
        cfg.check_updates = data["check_updates"]
    if isinstance(data.get("own_window"), bool):
        cfg.own_window = data["own_window"]
    return cfg


class ConfigStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or get_home()
        self.path = self.home / "config.json"
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        self.config = self._read()

    def _read(self) -> Config:
        if not self.path.is_file():
            cfg = Config()
            exports = station_studio_exports()
            if exports.is_dir():
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
        return config_from_json(data)

    def save(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.config.to_json(), indent=2), encoding="utf-8")
            tmp.replace(self.path)
