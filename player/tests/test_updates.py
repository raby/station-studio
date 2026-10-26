import json

from station_player import updates
from station_player.config import ConfigStore, config_from_json
from station_player.updates import _fetch_latest as real_fetch  # conftest stubs the module's


def test_version_comparison():
    assert updates.is_newer("0.3.1", "0.3.0")
    assert updates.is_newer("1.0", "0.9.9")
    assert updates.is_newer("0.10.0", "0.9.0")
    assert not updates.is_newer("0.3.0", "0.3")
    assert not updates.is_newer("0.3.0", "0.3.0")
    assert not updates.is_newer("0.4.0rc1", "0.3.0")  # pre-releases aren't offered
    assert not updates.is_newer(None, "0.3.0")
    assert not updates.is_newer("garbage", "0.3.0")


def test_asks_at_most_once_a_day_and_keeps_the_last_answer(tmp_path):
    cache = tmp_path / "update-check.json"
    calls = []

    def fetch():
        calls.append(1)
        return "0.4.0"

    assert updates.latest_version(cache, fetch, now=1000) == "0.4.0"
    assert updates.latest_version(cache, fetch, now=1000 + 3600) == "0.4.0"
    assert len(calls) == 1
    # A day later GitHub is asked again; if it can't be reached, the last
    # known answer stands and the next try is a day after that.
    assert updates.latest_version(cache, lambda: 1 / 0, now=1000 + updates.CHECK_EVERY) == "0.4.0"
    assert json.loads(cache.read_text())["checked"] == 1000 + updates.CHECK_EVERY
    cache.write_text("not json")
    assert updates.latest_version(cache, fetch, now=5) == "0.4.0"


def test_reads_the_newest_player_release_from_github(monkeypatch):
    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self, n):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    releases = [{"tag_name": t} for t in ("server-v9.0.0", "studio-v5.0.0", "player-v0.18.0", "player-v0.19.0")]
    releases += [{"tag_name": "player-v1.0.0", "draft": True}, {"tag_name": "player-v0.20.0", "prerelease": True},
                 {"tag_name": "player-v<b>"}]
    seen = []
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda req, timeout: seen.append(req.full_url) or Resp(json.dumps(releases).encode()))
    assert real_fetch() == "0.19.0"
    assert seen == [updates.RELEASES_API]
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda req, timeout: Resp(b'{"message": "rate limited"}'))
    assert real_fetch() is None


def test_check_updates_setting_defaults_on():
    assert ConfigStore().config.check_updates is True
    assert config_from_json({"check_updates": False}).check_updates is False
    assert config_from_json({"check_updates": "no"}).check_updates is True
