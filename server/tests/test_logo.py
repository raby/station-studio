"""A station's logo: kept by Station Server, on its stream's page and in its feed."""
import base64
import json
import urllib.request
from types import SimpleNamespace

from test_app import api, wait_for  # noqa: F401 (fixtures)
from test_feed import NOW, FakeGitHub, Runner, _Frozen, rows

from station_server import feed as feedmod
from station_server.config import clean_station
from station_server.stream import StreamServer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_only_a_kept_logo_name_is_taken():
    assert clean_station({"logo": "0123456789abcdef.png"}).logo == "0123456789abcdef.png"
    assert clean_station({"logo": "../../etc/passwd"}).logo == ""


def test_a_station_gets_a_logo(api):  # noqa: F811
    sid = api("/api/stations", {"name": "Digital Bluebird"})[1]["id"]
    assert api(f"/api/stations/{sid}/logo")[0] == 404
    status, body = api(f"/api/stations/{sid}/logo", {"image": base64.b64encode(PNG).decode()})
    assert status == 200
    name = body["stations"][0]["station"]["logo"]
    assert name.endswith(".png")
    assert api(f"/api/stations/{sid}/logo")[1] == PNG
    assert api.app.runners[sid].logo() == (PNG, "image/png")
    # Saving the rest of the station keeps it.
    api(f"/api/stations/{sid}", {"name": "Digital Bluebird", "genre": "Birdsong"})
    assert api.app.config.station(sid).logo == name
    # Not a picture, or too big.
    assert api(f"/api/stations/{sid}/logo", {"image": base64.b64encode(b"<html>").decode()})[0] == 400
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * 300_000
    assert api(f"/api/stations/{sid}/logo", {"image": base64.b64encode(big).decode()})[0] == 400
    assert api(f"/api/stations/{sid}/logo", {"image": "not base64!"})[0] == 400
    # No logo.
    api(f"/api/stations/{sid}/logo", {"image": None})
    assert api.app.config.station(sid).logo == "" and api(f"/api/stations/{sid}/logo")[0] == 404


def test_the_stream_page_shows_the_logo():
    logo = [None]
    srv = StreamServer(0, name="Digital Bluebird", logo=lambda: logo[0])
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        for path in ("/logo", "/favicon.ico"):
            try:
                urllib.request.urlopen(base + path, timeout=5)
                raise AssertionError("expected 404")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
        logo[0] = (PNG, "image/png")
        with urllib.request.urlopen(base + "/logo", timeout=5) as resp:
            assert resp.read() == PNG and resp.headers["Content-Type"] == "image/png"
        req = urllib.request.Request(base + "/", headers={"Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert "<img src='/logo'" in resp.read().decode()
    finally:
        srv.stop()


def test_the_feed_names_the_logo_and_publishes_it(tmp_path, monkeypatch):
    runner = Runner(tmp_path, order=rows(NOW, "Too Much Too Young"))
    assert feedmod.station_feed(runner, now=NOW)["logo"] is None
    runner.logo = lambda: (PNG, "image/png")
    assert feedmod.station_feed(runner, now=NOW)["logo"] == "rebirth-classics-logo.png"
    app = SimpleNamespace(config=SimpleNamespace(feed=True, feed_repo="raby/rebirth-now", feed_token="tok",
                                                 stations=[runner.station]), runners={"abc": runner})
    gh = FakeGitHub()
    pub = feedmod.FeedPublisher(app, pages=lambda repo, token: feedmod.GitHubPages(repo, token, opener=gh))
    pub.pages = pub._pages_cls("raby/rebirth-now", "tok")
    monkeypatch.setattr(feedmod, "datetime", _Frozen)
    _Frozen.now_ = NOW
    pub.tick()
    assert gh.files["rebirth-classics-logo.png"][0] == PNG
    assert json.loads(gh.files["rebirth-classics.json"][0])["logo"] == "rebirth-classics-logo.png"
    n = len(gh.puts)
    pub.tick()  # unchanged: not sent again
    assert len(gh.puts) == n
