"""The now/next feed for the station's website (feed.py)."""
import base64
import io
import json
import urllib.error
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from station_player.playlog import PlayLog
from station_server import feed as feedmod
from station_server.feed import (FeedError, FeedPublisher, GitHubPages, clean_repo, on_now, site_url, slug,
                                 station_feed)
from station_server.web import _state
from test_app import api  # noqa: F401 (fixture)

NOW = datetime(2026, 10, 26, 18, 0, 0).astimezone()


def rows(start: datetime, *songs, show=None):
    """A running order (schedule.plan rows) of 4-minute songs from `start`."""
    out, at = [], start
    for i, title in enumerate(songs):
        out.append({"index": i, "at": at.isoformat(timespec="seconds"), "title": title, "artist": "The Specials",
                    "seconds": 240.0})
        at += timedelta(minutes=4)
    if show:
        out.append({"show": show, "at": at.isoformat(timespec="seconds"), "seconds": 3600.0})
    return out


class Runner:
    def __init__(self, tmp_path, name="Rebirth Classics", order=(), state="on", live=None):
        self.station = SimpleNamespace(name=name, id="abc")
        self.state, self.rehearsal = state, False
        self.channel = SimpleNamespace(live=live)
        self.order = list(order)
        self.playlog = PlayLog(tmp_path / f"{name}.jsonl")

    def coming(self, count):
        return self.order[:count]


def test_names_and_addresses():
    assert slug("Rebirth Classics!") == "rebirth-classics" and slug("***") == "station"
    assert clean_repo(" https://github.com/raby/rebirth-now.git/ ") == "raby/rebirth-now"
    assert clean_repo("raby/rebirth-now") == "raby/rebirth-now" and clean_repo("") == ""
    with pytest.raises(ValueError):
        clean_repo("not a repo")
    assert site_url("raby/rebirth-now") == "https://raby.github.io/rebirth-now/"
    assert site_url("Raby/raby.github.io") == "https://raby.github.io/"


def test_a_feed_holds_the_coming_hours_and_what_just_played(tmp_path):
    order = rows(NOW - timedelta(minutes=1), *[f"Song {i}" for i in range(200)])
    r = Runner(tmp_path, order=order)
    r.playlog.add(NOW - timedelta(minutes=9), 240, {"title": "Ghost Town", "artist": "The Specials"})
    r.playlog.add(NOW - timedelta(minutes=5), 5, {"title": "Skipped", "artist": "X"})
    r.playlog.add(NOW - timedelta(minutes=5), 240, {"title": "Too Much Too Young", "artist": "The Specials",
                                                    "path": "/Users/rob/Music/secret.mp3"})
    f = station_feed(r, hours=1, now=NOW)
    assert (f["station"], f["slug"], f["on_air"], f["live"]) == ("Rebirth Classics", "rebirth-classics", True, None)
    assert [i["title"] for i in f["items"]][:2] == ["Song 0", "Song 1"] and len(f["items"]) == 16  # an hour's worth
    assert [e["title"] for e in f["recent"]] == ["Too Much Too Young", "Ghost Town"]  # newest first, no skips
    assert "secret" not in json.dumps(f)  # no file paths
    assert on_now(f, NOW) == ("Song 0", "The Specials")
    assert on_now(f, NOW + timedelta(minutes=4)) == ("Song 1", "The Specials")
    assert on_now(f, NOW + timedelta(hours=3)) is None


def test_off_air_live_and_shows(tmp_path):
    off = station_feed(Runner(tmp_path, state="off"), now=NOW)
    assert not off["on_air"] and off["items"] == [] and on_now(off, NOW) is None
    rehearsing = Runner(tmp_path, order=rows(NOW, "A"))
    rehearsing.rehearsal = True
    assert not station_feed(rehearsing, now=NOW)["on_air"]  # a rehearsal isn't on air for listeners
    live = SimpleNamespace(presenter="Dave", since=NOW.timestamp(), address="10.0.0.9")
    f = station_feed(Runner(tmp_path, order=rows(NOW, "A"), live=live), now=NOW)
    assert f["live"]["presenter"] == "Dave" and f["items"] == [] and "10.0.0.9" not in json.dumps(f)
    assert on_now(f, NOW) == ("live", "Dave")
    f = station_feed(Runner(tmp_path, order=rows(NOW - timedelta(minutes=2), "A", show="Saturday Night Mix")),
                     now=NOW)
    assert f["items"][1] == {"at": f["items"][1]["at"], "seconds": 3600.0, "show": "Saturday Night Mix"}
    assert on_now(f, NOW + timedelta(minutes=10)) == ("show", "Saturday Night Mix")


class FakeGitHub:
    """Enough of GitHub's Contents API: files by path, with shas."""

    def __init__(self, refuse=None):
        self.files, self.puts, self.refuse = {}, [], refuse

    def __call__(self, req, timeout=None):
        if self.refuse:
            raise urllib.error.HTTPError(req.full_url, self.refuse, "No", {},
                                         io.BytesIO(b'{"message": "Bad credentials"}'))
        assert req.headers["Authorization"] == "Bearer tok"
        path = req.full_url.split("/contents/", 1)[1].split("?")[0]
        if req.get_method() == "GET":
            if path not in self.files:
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
            content, sha = self.files[path]
            return _Resp({"sha": sha, "content": base64.b64encode(content).decode()})
        body = json.loads(req.data)
        if path in self.files:
            assert body["sha"] == self.files[path][1]  # GitHub wants the sha of the file it replaces
        sha = f"sha{len(self.puts)}"
        self.files[path] = (base64.b64decode(body["content"]), sha)
        self.puts.append(path)
        return _Resp({"content": {"sha": sha}})


class _Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_github_pages_writes_only_what_changed():
    gh = FakeGitHub()
    pages = GitHubPages("raby/rebirth-now", "tok", opener=gh)
    assert pages.put("a.json", b"1", "m") and gh.puts == ["a.json"]
    assert not pages.put("a.json", b"1", "m")  # unchanged
    assert pages.put("a.json", b"2", "m") and gh.files["a.json"][0] == b"2"
    # A fresh start finds the file there already, unchanged.
    again = GitHubPages("raby/rebirth-now", "tok", opener=gh)
    assert not again.put("a.json", b"2", "m") and again.put("a.json", b"3", "m")
    with pytest.raises(FeedError, match="token"):
        GitHubPages("raby/rebirth-now", "tok", opener=FakeGitHub(refuse=401)).put("a.json", b"1", "m")


def test_publishing_follows_whats_on_air(tmp_path, monkeypatch):
    clock = [1000.0]
    runner = Runner(tmp_path, order=rows(NOW, *[f"Song {i}" for i in range(100)]))
    app = SimpleNamespace(config=SimpleNamespace(feed=True, feed_repo="raby/rebirth-now", feed_token="tok",
                                                 stations=[runner.station]),
                          runners={"abc": runner})
    gh = FakeGitHub()
    pub = FeedPublisher(app, pages=lambda repo, token: GitHubPages(repo, token, opener=gh), clock=lambda: clock[0])
    pub.pages = pub._pages_cls("raby/rebirth-now", "tok")
    monkeypatch.setattr(feedmod, "datetime", _Frozen)
    _Frozen.now_ = NOW

    assert pub.tick() and set(gh.puts) >= {"index.html", "widget.html", "now.js", "now.css", ".nojekyll",
                                            "stations.json", "rebirth-classics.json"}
    assert json.loads(gh.files["stations.json"][0]) == {"stations": [{"name": "Rebirth Classics",
                                                                      "slug": "rebirth-classics"}]}
    n = len(gh.puts)
    # As the clock says: nothing to publish.
    _Frozen.now_ = NOW + timedelta(minutes=10)
    clock[0] += 600
    assert pub.tick() == 0 and len(gh.puts) == n
    # A skip: the song on air isn't the one published, so it's published.
    runner.order = rows(NOW + timedelta(minutes=9), *[f"Song {i}" for i in range(4, 100)])
    assert pub.tick() == 1 and len(gh.puts) == n + 1
    # Another straight after waits a little, so a run of skips is one publish.
    runner.order = rows(NOW + timedelta(minutes=9), *[f"Song {i}" for i in range(7, 100)])
    clock[0] += 30
    assert pub.tick() == 0
    clock[0] += 100
    assert pub.tick() == 1 and len(gh.puts) == n + 2
    # Off air: straight away.
    runner.state = "off"
    clock[0] += 5
    assert pub.tick() == 1
    assert json.loads(gh.files["rebirth-classics.json"][0])["on_air"] is False
    # Back on air an hour later: straight away, then hourly.
    runner.state = "on"
    clock[0] += 20
    assert pub.tick() == 1
    clock[0] += 3600
    _Frozen.now_ += timedelta(minutes=1)
    assert pub.tick() == 1
    assert pub.error is None and pub.last["files"] == 1


class _Frozen(datetime):
    now_ = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.now_


def test_settings_keep_the_token_secret(api, tmp_path):  # noqa: F811
    status, body = api("/api/settings", {"feed": True})
    assert status == 400 and "repository" in body["error"]
    status, body = api("/api/settings", {"feed_repo": "github.com/raby/rebirth-now", "feed_token": "ghp_secret123"})
    assert status == 200
    f = body["feed"]
    assert (f["repo"], f["has_token"], f["url"]) == ("raby/rebirth-now", True, "https://raby.github.io/rebirth-now/")
    assert "ghp_secret123" not in json.dumps(body) and "ghp_secret123" not in json.dumps(_state(api.app, True))
    # A blank token keeps the one there.
    api("/api/settings", {"feed_repo": "raby/rebirth-now", "feed_token": ""})
    assert api.app.config.feed_token == "ghp_secret123"
    assert "ghp_secret123" not in api.app.diagnostics()
    assert api("/api/settings", {"feed_repo": "not a repo"})[0] == 400


def test_turned_off_and_straight_back_on_keeps_publishing():
    cfg = SimpleNamespace(feed=True, feed_repo="raby/rebirth-now", feed_token="tok", stations=[])
    pub = FeedPublisher(SimpleNamespace(config=cfg, runners={}), pages=lambda r, t: SimpleNamespace(repo=r, token=t))
    pub.apply()
    cfg.feed = False
    pub.apply()
    cfg.feed = True
    pub.apply()  # before the thread has noticed it was stopped
    assert pub.pages is not None and not pub._stop.is_set()
    pub.stop()
