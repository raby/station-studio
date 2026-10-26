"""What's on a station, from its Station Server's feed (whatson.py)."""
import io
import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from station_player import config as configmod
from station_player.config import ConfigStore, config_from_json
from station_player.whatson import Feeds, clean_feed_url, view
from test_server import request, running  # noqa: F401 (fixture)

NOW = datetime(2026, 10, 26, 18, 0, 0).astimezone()


def feed(start=NOW - timedelta(minutes=1), songs=("Ghost Town", "Too Much Too Young", "Gangsters", "A Message"),
         **over):
    items, at = [], start
    for title in songs:
        items.append({"at": at.isoformat(timespec="seconds"), "seconds": 240, "title": title, "artist": "The Specials"})
        at += timedelta(minutes=4)
    return {"station": "Rebirth Classics", "slug": "rebirth-classics", "updated": NOW.isoformat(), "on_air": True,
            "live": None, "items": items,
            "recent": [{"at": (NOW - timedelta(minutes=9)).isoformat(), "title": "Rat Race", "artist": "The Specials"}],
            **over}


def test_whats_on_comes_from_the_clock():
    v = view(feed(), NOW)
    assert v["state"] == "on" and v["now"]["title"] == "Ghost Town"
    assert [n["title"] for n in v["next"]] == ["Too Much Too Young", "Gangsters", "A Message"]
    assert [r["title"] for r in v["recent"]] == ["Rat Race"]
    later = view(feed(), NOW + timedelta(minutes=8))
    assert later["now"]["title"] == "Gangsters" and [r["title"] for r in later["recent"]][:2] == [
        "Too Much Too Young", "Ghost Town"]
    assert view(feed(), NOW + timedelta(hours=2))["state"] == "unknown"  # past the end of the feed
    assert view(feed(), NOW + timedelta(hours=1, minutes=31))["recent"] == []  # an old feed: its computer is off


def test_live_off_air_and_shows():
    assert view(feed(live={"presenter": "Dave"}), NOW) == {
        "state": "live", "now": {"live": "Dave"}, "next": [], "recent": feed()["recent"]}
    assert view(feed(on_air=False), NOW)["state"] == "off" and view(None, NOW)["state"] == "off"
    f = feed()
    f["items"][1] = {"at": f["items"][1]["at"], "seconds": 3600, "show": "Saturday Night Mix"}
    assert view(f, NOW)["next"][0] == {"at": f["items"][1]["at"], "show": "Saturday Night Mix"}
    f["items"].append({"at": "not a time"})
    assert view(f, NOW)["state"] == "on"  # a damaged item is left out


def test_feeds_are_fetched_once_a_minute_and_kept_when_a_fetch_fails():
    clock = [0.0]
    calls = []

    def opener(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 2:
            raise OSError("offline")
        return io.BytesIO(json.dumps(feed()).encode())

    feeds = Feeds(opener=opener, clock=lambda: clock[0])
    url = "https://raby.github.io/rebirth-now/rebirth-classics.json"
    assert feeds.get(url)["station"] == "Rebirth Classics"
    assert feeds.get(url) and len(calls) == 1
    clock[0] = 61
    assert feeds.get(url)["station"] == "Rebirth Classics" and len(calls) == 2  # failed: the last one kept
    assert calls[0].startswith(url + "?t=")


def test_feed_addresses():
    assert clean_feed_url(" https://x/y.json ") == "https://x/y.json" and clean_feed_url(None) == ""
    with pytest.raises(ValueError):
        clean_feed_url("ftp://x/y.json")


def test_a_built_in_station_given_a_feed_passes_it_on(monkeypatch):
    rebirth = dict(configmod.DEFAULT_STATIONS[1], feed="https://raby.github.io/rebirth-now/rebirth-classics.json")
    monkeypatch.setattr(configmod, "DEFAULT_STATIONS", [configmod.DEFAULT_STATIONS[0], rebirth])
    saved = {"stations": [{"name": "Classics", "url": rebirth["url"]}, {"name": "Jazz", "url": "https://j/x"}],
             "defaults_offered": [s["url"] for s in configmod.DEFAULT_STATIONS]}
    cfg = config_from_json(saved)
    assert cfg.stations[0]["feed"] == rebirth["feed"] and "feed" not in cfg.stations[1]
    saved["stations"][0]["feed"] = ""  # taken off: stays off
    assert config_from_json(saved).stations[0]["feed"] == ""


def test_whats_on_through_the_player(running):  # noqa: F811
    app, port, _, tmp = running
    served = feed(start=datetime.now().astimezone() - timedelta(minutes=1), updated=datetime.now().astimezone().isoformat())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(served).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    site = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    h = {"X-Station-Player": "1"}
    request(port, "POST", "/api/stations", {"name": "Classics", "url": "https://classics.example/live"}, h)
    resp, data = request(port, "POST", "/api/stations/update",
                         {"url": "https://classics.example/live", "feed": f"http://127.0.0.1:{site.server_address[1]}/c.json"}, h)
    st = next(s for s in json.loads(data)["stations"] if s["name"] == "Classics")
    assert st["feed"].endswith("/c.json")
    resp, data = request(port, "GET", f"/api/whatson?station={st['id']}")
    assert resp.status == 200 and json.loads(data)["now"]["title"] == "Ghost Town"
    assert request(port, "POST", "/api/stations/update", {"url": "https://classics.example/live", "feed": "nope"},
                   h)[0].status == 400
    assert ConfigStore(app.store.home).config.stations[-1]["feed"].endswith("/c.json")
    other = next(s for s in json.loads(request(port, "GET", "/api/state")[1])["stations"] if not s.get("feed"))
    assert request(port, "GET", f"/api/whatson?station={other['id']}")[0].status == 404
    site.shutdown()
    site.server_close()
