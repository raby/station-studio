"""Player settings, stored as JSON under the player's home folder.

Resolution: `$STATION_PLAYER_HOME`, else `~/.station-player/`. The file
is small and human-editable; the UI writes it through the API.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

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

    def to_json(self) -> dict:
        return asdict(self)


class ConfigStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or get_home()
        self.path = self.home / "config.json"
        self._lock = threading.Lock()
        self.config = self._read()

    def _read(self) -> Config:
        if not self.path.is_file():
            cfg = Config()
            exports = station_studio_exports()
            if exports.is_dir():
                cfg.playlist_sources.append(str(exports))
            return cfg
        data = json.loads(self.path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
        return Config(**known)

    def save(self) -> None:
        with self._lock:
            self.home.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.config.to_json(), indent=2), encoding="utf-8")
            tmp.replace(self.path)
