import base64
import http.client
import json
import threading

import pytest

from station_player.config import ConfigStore
from station_player.directory import Directory
from station_player.logos import LogoStore, is_name, sniff
from station_player.server import PlayerApp, make_server

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201a0b2e1a10000000049454e44ae426082")
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 50


def test_sniff_and_names():
    assert sniff(PNG) == "image/png" and sniff(JPEG) == "image/jpeg"
    assert sniff(b"GIF89a...") == "image/gif" and sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff(b"<svg/>") is None and sniff(b"") is None
    assert is_name("0123456789abcdef.png")
    assert not is_name("../config.json") and not is_name("0123456789abcdef.svg") and not is_name(None)


def test_store_save_get_prune(tmp_path):
    logos = LogoStore(tmp_path / "logos")
    name = logos.save(PNG)
    assert name.endswith(".png") and logos.save(PNG) == name  # named by its contents
    assert logos.get(name) == (PNG, "image/png")
    assert logos.get("../../x") is None and logos.get("ffffffffffffffff.png") is None
    with pytest.raises(ValueError, match="isn't a PNG"):
        logos.save(b"<html>")
    with pytest.raises(ValueError, match="too big"):
        logos.save(PNG + b"0" * (300 * 1024))
    other = logos.save(JPEG)
    logos.prune(set())
    assert logos.has(name)  # just saved: a station may be about to take it
    logos.grace = 0
    logos.prune({other})
    assert not logos.has(name) and logos.has(other)


def test_config_keeps_logo_names_and_recent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({
        "stations": [{"name": "A", "url": "https://a/", "logo": "0123456789abcdef.png"},
                     {"name": "B", "url": "https://b/", "logo": "../../etc/passwd"}],
        "recent_stations": [{"name": f"R{n}", "url": f"https://r/{n}"} for n in range(12)] + ["junk"],
    }))
    cfg = ConfigStore(home).config
    assert cfg.stations == [{"name": "A", "url": "https://a/", "logo": "0123456789abcdef.png"},
                            {"name": "B", "url": "https://b/"}]
    assert len(cfg.recent_stations) == 8 and cfg.recent_stations[0]["name"] == "R0"


def raw(name, url, favicon=""):
    return {"stationuuid": f"u-{name}", "name": name, "url_resolved": url, "favicon": favicon, "tags": "",
            "countrycode": "GB", "codec": "MP3", "bitrate": 128, "votes": 1}


class Net:
    def __init__(self, stations=(), byurl=()):
        self.stations, self.byurl, self.asked = list(stations), list(byurl), []

    def __call__(self, url, ua, limit):
        self.asked.append(url)
        if url.startswith("https://logo.example/fake"):
            return b"<html>not a picture</html>", "image/png"
        if url.startswith("https://logo.example/"):
            return PNG, "image/png"
        if "/json/stations/byurl" in url:
            return json.dumps(self.byurl).encode(), "application/json"
        return json.dumps(self.stations).encode(), "application/json"


def test_search_pages():
    from urllib.parse import parse_qs, urlparse

    class Paged(Net):
        def __call__(self, url, ua, limit):
            self.asked.append(url)
            q = parse_qs(urlparse(url).query)
            start, count = int(q["offset"][0]), int(q["limit"][0])
            return json.dumps([raw(f"S{n}", f"https://s/{n % 95}") for n in range(start, min(100, start + count))]).encode(), "x"
    d = Directory(fetch=Paged(), servers=["one"])
    page = d.search_page(limit=10)
    assert [s["name"] for s in page["stations"]] == [f"S{n}" for n in range(10)] and page["next"] == 10
    page = d.search_page(limit=10, offset=90)  # the end of the list: S95-S99 repeat S0-S4's addresses
    assert page["next"] is None and len(page["stations"]) == 10
    assert d.search_page(limit=10, offset=10**9)["next"] is None  # offset capped


def test_find_logo_by_stream_address():
    net = Net(byurl=[raw("A", "https://s/a"), raw("A", "https://s/a", "https://logo.example/a.png")])
    d = Directory(fetch=net, servers=["one"], public=lambda h: True)
    assert d.find_logo("https://s/a") == (PNG, "image/png")
    assert "url=https%3A%2F%2Fs%2Fa" in [u for u in net.asked if "byurl" in u][0]
    assert Directory(fetch=Net(), servers=["one"]).find_logo("https://s/a") is None
    assert d.find_logo("file:///etc/passwd") is None
    # A "picture" that isn't one is passed over.
    fake = Net(byurl=[raw("A", "https://s/a", "https://logo.example/fake.png")])
    assert Directory(fetch=fake, servers=["one"], public=lambda h: True).find_logo("https://s/a") is None


def test_saving_the_same_picture_at_once(tmp_path):
    logos = LogoStore(tmp_path / "logos")
    names, errors = [], []

    def save():
        try:
            names.append(logos.save(PNG))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=save) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(set(names)) == 1 and logos.get(names[0]) == (PNG, "image/png")
    assert [p.name for p in (tmp_path / "logos").iterdir()] == names[:1]  # no temporary files left


@pytest.fixture
def served(tmp_path):
    app = PlayerApp(ConfigStore(tmp_path / "home"), scan=False)
    app.logos.grace = 0
    net = Net(stations=[raw("Listed", "https://s/listed", "https://logo.example/l.png")],
              byurl=[raw("Rebirth", "https://sh.uk.audio-stream.com/proxy/adgerdes?mp=/stream", "https://logo.example/r.png")])
    app.directory = Directory(fetch=net, servers=["one"], public=lambda h: True)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def call(path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}", "X-Station-Player": "1", "Content-Type": "application/json"}
        conn.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None,
                     headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data
    yield app, call
    server.shutdown()
    server.server_close()


def test_recently_played_only_known_stations(served):
    app, call = served
    resp, _ = call("/api/stations/played", {"name": "X", "url": "http://127.0.0.1:22/"})
    assert resp.status == 400  # not saved, not listed
    assert not app.can_preview("http://127.0.0.1:22/")
    app.directory.search()
    resp, data = call("/api/stations/played", {"name": "Listed", "url": "https://s/listed",
                                                "logo_url": "https://logo.example/l.png"})
    assert resp.status == 200
    recent = json.loads(data)["recent_stations"]
    assert recent[0]["url"] == "https://s/listed" and not recent[0]["saved"] and is_name(recent[0]["logo"])
    # Still playable after the finder forgets it (a restart).
    app.directory._streams.clear()
    assert app.can_preview("https://s/listed")
    # The logo is served by the player, by name only.
    resp, body = call(f"/logo/{recent[0]['logo']}")
    assert resp.status == 200 and body == PNG and resp.getheader("Content-Type") == "image/png"
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert call("/logo/..%2Fconfig.json")[0].status == 404
    # Saved stations count, newest first, without repeats; at most 8.
    call("/api/stations/played", {"name": "Rebirth", "url": app.store.config.stations[0]["url"]})
    call("/api/stations/played", {"name": "Listed", "url": "https://s/listed"})
    urls = [s["url"] for s in app.store.config.recent_stations]
    assert urls == ["https://s/listed", app.store.config.stations[0]["url"]]
    call("/api/stations/recent/clear", {})
    assert app.store.config.recent_stations == []
    assert not any((app.store.home / "logos").iterdir())  # its logo went too


def test_add_with_a_logo_and_set_one(served, monkeypatch):
    app, call = served
    monkeypatch.setattr("station_player.server.resolve_stream_url", lambda u: u)
    app.directory.search()
    resp, data = call("/api/stations", {"name": "Listed", "url": "https://s/listed", "logo_url": "https://logo.example/l.png"})
    st = next(s for s in json.loads(data)["stations"] if s["url"] == "https://s/listed")
    assert is_name(st["logo"])
    # A logo address the finder never listed isn't fetched.
    call("/api/stations", {"name": "Other", "url": "https://s/other", "logo_url": "https://logo.example/x.png"})
    assert "logo" not in next(s for s in app.store.config.stations if s["url"] == "https://s/other")
    # Find the default station's logo in Radio Browser; choose one; remove it.
    rebirth = app.store.config.stations[0]["url"]
    assert call("/api/stations/logo", {"url": rebirth, "find": True})[0].status == 200
    assert is_name(app.store.config.stations[0].get("logo"))
    assert call("/api/stations/logo", {"url": rebirth, "image": base64.b64encode(JPEG).decode()})[0].status == 200
    assert app.store.config.stations[0]["logo"].endswith(".jpg")
    resp, data = call("/api/stations/logo", {"url": rebirth, "image": base64.b64encode(b"<svg/>").decode()})
    assert resp.status == 400 and "isn't a PNG" in json.loads(data)["error"]
    assert call("/api/stations/logo", {"url": rebirth, "image": "not base64!"})[0].status == 400
    assert call("/api/stations/logo", {"url": "https://nope/", "find": True})[0].status == 404
    call("/api/stations/logo", {"url": rebirth})
    assert "logo" not in app.store.config.stations[0]
    # Removing a station removes its logo file.
    call("/api/stations/remove", {"url": "https://s/listed"})
    assert not any((app.store.home / "logos").iterdir())
