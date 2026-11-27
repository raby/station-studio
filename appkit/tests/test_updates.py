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


def test_a_release_check_github_refuses_lets_the_error_go_closed():
    """An HTTP error is a response too: the check logs it and carries on,
    so it's closed as it's raised (3.13+ warns of one left to the gc)."""
    import gc
    import threading
    import urllib.error
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from station_appkit import selfupdate

    class NotFound(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(404)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), NotFound)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/releases"
    try:
        for fetch in (lambda: updates.urlopen(url, timeout=5), lambda: selfupdate.fetch_json(url)):
            with pytest.raises(urllib.error.HTTPError) as info:
                fetch()
            assert info.value.code == 404 and info.value.fp.closed
        del info
        gc.collect()  # would warn here if one weren't closed
    finally:
        httpd.shutdown()
        httpd.server_close()


def _notes(version: str, *items: str) -> str:
    """A release's notes on GitHub, as scripts/release_notes.py starts them."""
    bullets = "\n".join(f"- {i}" for i in items)
    return (f"## What's new in Station Test {version}\n\n### Fixed\n\n{bullets}\n\n---\n"
            f"Station Test {version}: **Download** below.\n\n- **Mac:** not a change\n")


RELEASES = [
    {"tag_name": "test-v1.3.0", "body": _notes("1.3.0", "Third."), "published_at": "2026-12-03T10:00:00Z"},
    {"tag_name": "test-v1.2.0", "body": _notes("1.2.0", "Second, **bold**."), "published_at": "2026-12-02T10:00:00Z"},
    {"tag_name": "test-v1.1.0", "body": "Station Test 1.1.0: from before notes said what changed."},
    {"tag_name": "test-v1.4.0", "body": _notes("1.4.0", "A draft."), "draft": True},
    {"tag_name": "server-v9.0.0", "body": _notes("9.0.0", "Another app's.")},
]


def test_one_request_brings_the_newest_and_what_recent_ones_change(app):
    asked = []

    def opener(req, timeout):
        asked.append(req.full_url)
        return io.BytesIO(json.dumps(RELEASES).encode())

    latest, notes = updates.fetch_latest_with_notes(app, opener)
    assert latest == "1.3.0" and len(asked) == 1
    assert list(notes) == ["1.3.0", "1.2.0"]  # published, this app's, and saying what changed
    assert notes["1.2.0"] == {"version": "1.2.0", "date": "2026-12-02", "intro": [],
                              "groups": [{"heading": "Fixed", "items": ["Second, bold."]}]}


def test_update_now_reads_what_changed_since_this_version(app, tmp_path):
    cache = tmp_path / "update-check.json"
    latest = updates.latest_version(app, cache, fetch=lambda: ("1.3.0", updates.release_notes(RELEASES, "test-v")),
                                    now=1000.0)
    assert latest == "1.3.0"
    assert [r["version"] for r in updates.notes_since(cache, "1.1.0", latest)] == ["1.3.0", "1.2.0"]
    assert [r["version"] for r in updates.notes_since(cache, "1.2.0", latest)] == ["1.3.0"]
    assert updates.notes_since(cache, "1.3.0", latest) == []  # up to date
    assert updates.notes_since(cache, "1.1.0", "1.2.0")[0]["version"] == "1.2.0"  # never past the newest offered
    # A check by hand that only got a version keeps no notes; nothing breaks.
    updates.latest_version(app, cache, fetch=lambda: "1.3.0", now=1000.0, force=True)
    assert updates.notes_since(cache, "1.1.0", "1.3.0") == []
    assert updates.notes_since(tmp_path / "missing.json", "1.1.0", "1.3.0") == []


def test_only_the_last_few_releases_notes_are_kept():
    many = [{"tag_name": f"test-v1.{i}.0", "body": _notes(f"1.{i}.0", "x")} for i in range(25)]
    kept = updates.release_notes(many, "test-v")
    assert len(kept) == updates.NOTES_KEPT and next(iter(kept)) == "1.24.0"


def test_the_notes_are_read_again_only_when_the_cache_changes(app, tmp_path, monkeypatch):
    cache = tmp_path / "update-check.json"
    updates.latest_version(app, cache, fetch=lambda: ("1.3.0", updates.release_notes(RELEASES, "test-v")), now=1.0)
    reads = []
    real = type(cache).read_text
    monkeypatch.setattr(type(cache), "read_text", lambda self, *a, **k: reads.append(self) or real(self, *a, **k))
    for _ in range(5):
        assert updates.notes_since(cache, "1.1.0", "1.3.0")[0]["version"] == "1.3.0"
    assert len(reads) <= 1  # once, then from memory
    import os
    updates.latest_version(app, cache, fetch=lambda: ("1.3.0", {}), now=2.0, force=True)
    os.utime(cache, ns=(cache.stat().st_atime_ns, cache.stat().st_mtime_ns + 1_000_000))
    assert updates.notes_since(cache, "1.1.0", "1.3.0") == []  # changed: read again
