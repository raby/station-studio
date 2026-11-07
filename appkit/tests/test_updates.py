"""The new-version check (station_appkit/updates.py)."""
import io
import json

import pytest

from station_appkit import updates


def test_version_comparison():
    assert updates.is_newer("0.3.1", "0.3.0")
    assert updates.is_newer("1.0", "0.9.9")
    assert updates.is_newer("0.10.0", "0.9.0")
    assert not updates.is_newer("0.3.0", "0.3")
    assert not updates.is_newer("0.3.0", "0.3.0")
    assert not updates.is_newer("0.4.0rc1", "0.3.0")  # pre-releases aren't offered
    assert not updates.is_newer(None, "0.3.0")
    assert not updates.is_newer("garbage", "0.3.0")


def test_newest_release_is_the_apps_own_and_published():
    releases = [{"tag_name": t} for t in ("server-v9.0.0", "studio-v5.0.0", "test-v0.18.0", "test-v0.19.0")]
    releases += [{"tag_name": "test-v1.0.0", "draft": True}, {"tag_name": "test-v0.20.0", "prerelease": True},
                 {"tag_name": "test-v<b>"}, {"tag_name": None}, "junk"]
    assert updates.newest_release(releases, "test-v") == "0.19.0"
    assert updates.newest_release({"message": "rate limited"}, "test-v") is None


def test_fetch_asks_github_for_the_apps_releases(app):
    seen = []

    def opener(req, timeout):
        seen.append((req.full_url, req.get_header("User-agent")))
        return io.BytesIO(json.dumps([{"tag_name": "test-v2.0.0"}]).encode())

    assert updates.fetch_latest(app, opener) == "2.0.0"
    assert seen == [(app.releases_api, "StationTest")]


def test_asks_at_most_once_a_day_and_keeps_the_last_answer(app, tmp_path):
    cache = tmp_path / "update-check.json"
    calls = []

    def fetch():
        calls.append(1)
        return "0.4.0"

    assert updates.latest_version(app, cache, fetch, now=1000) == "0.4.0"
    assert updates.latest_version(app, cache, fetch, now=1000 + 3600) == "0.4.0"
    assert len(calls) == 1
    assert updates.latest_version(app, cache, fetch, now=1000 + 3600, force=True) == "0.4.0"
    assert len(calls) == 2
    cache.write_text("not json")
    assert updates.latest_version(app, cache, fetch, now=5) == "0.4.0"
    cache.write_text("[1, 2]")
    assert updates.latest_version(app, cache, fetch, now=5) == "0.4.0"


def test_offline_keeps_the_last_answer_and_tries_again_next_time(app, tmp_path):
    cache = tmp_path / "update-check.json"
    cache.write_text(json.dumps({"checked": 0, "latest": "0.4.0"}))

    def offline():
        raise OSError("no network")

    later = updates.CHECK_EVERY + 10
    assert updates.latest_version(app, cache, offline, now=later) == "0.4.0"
    # Not cached as a check, so the next look (an hour on) asks again.
    assert json.loads(cache.read_text())["checked"] == 0
    assert updates.latest_version(app, cache, lambda: "0.5.0", now=later + 3600) == "0.5.0"
    # Asked for by hand, it says it couldn't, rather than that all is up to date.
    with pytest.raises(OSError):
        updates.latest_version(app, cache, offline, now=later, force=True, quiet=False)


def test_the_version_is_the_same_in_both_places():
    import tomllib
    from pathlib import Path

    import station_appkit

    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == station_appkit.__version__
