"""The new-version check (station_player/updates.py). The work is
station_appkit.updates' (appkit/tests/test_updates.py); these check the
player's part."""
import io
import json

from station_player import updates
from station_player.config import ConfigStore, config_from_json


def test_asks_at_most_once_a_day_and_keeps_the_last_answer(tmp_path):
    cache = tmp_path / "update-check.json"
    calls = []

    def fetch():
        calls.append(1)
        return "0.4.0"

    assert updates.latest_version(cache, fetch, now=1000) == "0.4.0"
    assert updates.latest_version(cache, fetch, now=1000 + 3600) == "0.4.0"
    assert len(calls) == 1
    # Offline a day later: the last known answer stands, and the next look
    # (an hour on) asks again rather than a day after.
    assert updates.latest_version(cache, lambda: 1 / 0, now=1000 + updates.CHECK_EVERY) == "0.4.0"
    assert json.loads(cache.read_text())["checked"] == 1000
    assert updates.latest_version(cache, lambda: "0.5.0", now=1000 + updates.CHECK_EVERY + 3600) == "0.5.0"


def test_reads_the_newest_player_release_from_github():
    releases = [{"tag_name": t} for t in ("server-v9.0.0", "studio-v5.0.0", "player-v0.18.0", "player-v0.19.0")]
    seen = []

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header("User-agent")))
        return io.BytesIO(json.dumps(releases).encode())

    assert updates._updates.fetch_latest(updates.APP, opener) == "0.19.0"
    assert seen == [("https://api.github.com/repos/raby/station-studio/releases?per_page=100", "StationPlayer")]
    assert updates.newest_release(releases) == "0.19.0"


def test_check_updates_setting_defaults_on():
    assert ConfigStore().config.check_updates is True
    assert config_from_json({"check_updates": False}).check_updates is False
    assert config_from_json({"check_updates": "no"}).check_updates is True
