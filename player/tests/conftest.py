import plistlib
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def write_wav(path: Path, frames: int = 8000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x01" * frames)
    return path


def write_itunes_xml(path: Path, tracks: list[dict], playlists: list[dict]) -> Path:
    data = {
        "Major Version": 1,
        "Minor Version": 1,
        "Tracks": {str(t["Track ID"]): t for t in tracks},
        "Playlists": playlists,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(data, fh)
    return path


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch):
    monkeypatch.setenv("STATION_PLAYER_HOME", str(tmp_path / "player-home"))
    monkeypatch.setenv("STATION_STUDIO_HOME", str(tmp_path / "studio-home"))
