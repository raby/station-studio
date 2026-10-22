import json
import urllib.error

import pytest

from station_player.directory import Directory, DirectoryError, clean_station, is_public_host


def raw(name, url, **more):
    return {"stationuuid": f"uuid-{name}", "name": name, "url": url, "url_resolved": url, "favicon": "",
            "tags": "jazz,soul", "country": "United Kingdom", "countrycode": "gb", "codec": "MP3",
            "bitrate": 128, "votes": 10, "hls": 0, **more}


class FakeNet:
    """Stands in for the internet: answers by host, records what was asked."""

    def __init__(self, answers=None, down=()):
        self.answers = answers or {}
        self.down = set(down)
        self.asked = []

    def __call__(self, url, user_agent, limit):
        self.asked.append(url)
        host = url.split("/")[2]
        if host in self.down:
            raise urllib.error.URLError("down")
        for prefix, answer in self.answers.items():
            if url.startswith(prefix) or prefix == host:
                return answer() if callable(answer) else answer
        raise urllib.error.URLError("unknown")


def api_answer(items):
    return json.dumps(items).encode(), "application/json"


def test_clean_station_keeps_what_the_page_shows():
    st = clean_station(raw("Jazz FM", "https://stream.example/jazz", tags="jazz, smooth jazz,,soul",
                           favicon="https://img.example/j.png", homepage="javascript:alert(1)"))
    assert st["name"] == "Jazz FM"
    assert st["url"] == "https://stream.example/jazz"
    assert st["tags"] == ["jazz", "smooth jazz", "soul"]
    assert st["logo"] == "https://img.example/j.png"
    assert st["homepage"] == ""  # only web addresses
    assert st["countrycode"] == "GB"
    assert clean_station(raw("No link", "ftp://x/y")) is None
    assert clean_station(raw("", "https://x/y")) is None
    assert clean_station("nonsense") is None
    # Prefers the resolved stream address over a playlist link.
    st = clean_station(raw("P", "https://x/listen.pls", url_resolved="https://x/live"))
    assert st["url"] == "https://x/live"


def test_search_drops_duplicates_and_remembers_mirror():
    items = [raw("A", "https://s/a"), raw("A again", "https://s/a"), raw("HLS", "https://s/h.m3u8", hls=1),
             raw("B", "https://s/b")]
    net = FakeNet({"https://de1.api.radio-browser.info/": api_answer(items)},
                  down=["all.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info",
                        "nl1.api.radio-browser.info"])
    d = Directory(fetch=net)
    found = d.search(q="a", tag="Jazz", country="gb", order="popular")
    assert [s["name"] for s in found] == ["A", "HLS", "B"]  # HLS plays through the player
    assert d.listed("https://s/h.m3u8") and not d.listed("https://elsewhere/")
    asked = net.asked[-1]
    assert "/json/stations/search?" in asked
    assert "name=a" in asked and "tag=jazz" in asked and "countrycode=GB" in asked and "order=clickcount" in asked
    assert "hidebroken=true" in asked
    # The mirror that answered is asked first next time.
    net.asked.clear()
    d.search()
    assert net.asked[0].startswith("https://de1.api.radio-browser.info/")


def test_search_with_every_mirror_down_says_so():
    d = Directory(fetch=FakeNet(down=["all.api.radio-browser.info", "de1.api.radio-browser.info",
                                      "de2.api.radio-browser.info", "fi1.api.radio-browser.info",
                                      "nl1.api.radio-browser.info"]))
    with pytest.raises(DirectoryError, match="radio-browser.info"):
        d.search(q="x")


def test_search_rejects_an_odd_answer():
    d = Directory(fetch=FakeNet({"https://": (b'{"not": "a list"}', "application/json")}), servers=["one"])
    with pytest.raises(DirectoryError):
        d.search()


def soma_channel(cid, title, listeners, formats=("mp3", "aac"), **more):
    return {"id": cid, "title": title, "description": f"{title} description", "genre": "ambient|electronica",
            "image": f"https://api.somafm.com/img/{cid}120.png", "listeners": str(listeners),
            "playlists": [{"url": f"https://api.somafm.com/{cid}.pls", "format": f} for f in formats], **more}


def test_somafm_channels():
    channels = [soma_channel("groovesalad", "Groove Salad", 900), soma_channel("dronezone", "Drone Zone", 1500),
                soma_channel("aaconly", "AAC Only", 10, formats=("aac",)), soma_channel("../x", "Bad id", 5),
                {"id": "notitle"}, "nonsense"]
    net = FakeNet({"https://somafm.com/channels.json": (json.dumps({"channels": channels}).encode(), "application/json")})
    d = Directory(fetch=net, servers=["one"])
    found = d.somafm()
    assert [s["name"] for s in found] == ["SomaFM Drone Zone", "SomaFM Groove Salad"]  # most listened first
    gs = found[1]
    assert gs["url"] == "https://ice1.somafm.com/groovesalad-128-mp3"
    assert gs["tags"] == ["ambient", "electronica"] and gs["codec"] == "MP3" and gs["bitrate"] == 128
    assert "https://api.somafm.com/img/groovesalad120.png" in d._logos  # its logo may be shown
    # Asked once, then kept for a while.
    d.somafm()
    assert len(net.asked) == 1
    with pytest.raises(DirectoryError, match="somafm.com"):
        Directory(fetch=FakeNet(), servers=["one"]).somafm()


def test_logo_only_for_addresses_the_directory_gave_out():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    net = FakeNet({
        "https://one/": api_answer([raw("A", "https://s/a", favicon="https://img.example/a.png"),
                                    raw("B", "https://s/b", favicon="https://img.example/big.png"),
                                    raw("C", "https://s/c", favicon="https://img.example/c.svg"),
                                    raw("D", "https://s/d", favicon="https://home.example/d.png")]),
        "https://img.example/a.png": (png, "image/png; charset=binary"),
        "https://img.example/big.png": (b"0" * (300 * 1024), "image/png"),
        "https://img.example/c.svg": (b"<svg/>", "image/svg+xml"),
        "https://home.example/d.png": (png, "image/png"),
    })
    d = Directory(fetch=net, servers=["one"], public=lambda host: host != "home.example")
    assert d.logo("https://img.example/a.png") is None  # not handed out yet
    d.search()
    assert d.logo("https://img.example/a.png") == (png, "image/png")
    assert d.logo("https://img.example/big.png") is None  # too big
    assert d.logo("https://img.example/c.svg") is None  # not a safe image type
    assert d.logo("https://home.example/d.png") is None  # on the local network
    assert d.logo("http://127.0.0.1/secret") is None
    # Cached: asked once.
    before = len(net.asked)
    d.logo("https://img.example/a.png")
    assert len(net.asked) == before


def test_is_public_host():
    def resolver(addrs):
        return lambda host, port: [(None, None, None, None, (a, 0)) for a in addrs]
    assert is_public_host("x", resolver(["93.184.216.34"]))
    assert not is_public_host("x", resolver(["127.0.0.1"]))
    assert not is_public_host("x", resolver(["192.168.1.10"]))
    assert not is_public_host("x", resolver(["93.184.216.34", "10.0.0.1"]))
    assert not is_public_host("x", resolver(["::1"]))
    assert not is_public_host("x", resolver([]))

    def fails(host, port):
        raise OSError("no such host")
    assert not is_public_host("x", fails)


def test_clicked_ignores_odd_ids():
    net = FakeNet({"https://": api_answer([])})
    d = Directory(fetch=net, servers=["one"])
    d.clicked("../../etc")
    d.clicked("")
    assert net.asked == []


def test_server_endpoints(tmp_path):
    import http.client
    import threading

    from station_player.config import ConfigStore
    from station_player.server import PlayerApp, make_server

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    net = FakeNet({"https://one/json/stations": api_answer([raw("A", "https://s/a", favicon="https://img.example/a.png")]),
                   "https://somafm.com/channels.json": (json.dumps({"channels": [soma_channel("gs", "Groove Salad", 1)]}).encode(),
                                                        "application/json"),
                   "https://img.example/a.png": (png, "image/png")})
    app = PlayerApp(ConfigStore(tmp_path / "home"), scan=False)
    app.directory = Directory(fetch=net, servers=["one"], public=lambda host: True)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(path, method="GET", body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    try:
        resp, data = get("/api/directory/search?q=a&country=gb")
        assert resp.status == 200 and json.loads(data)["stations"][0]["name"] == "A"
        resp, data = get("/api/directory/somafm")
        assert json.loads(data)["stations"][0]["name"] == "SomaFM Groove Salad"
        resp, data = get("/api/directory/logo?u=" + "https%3A%2F%2Fimg.example%2Fa.png")
        assert resp.status == 200 and data == png and resp.getheader("Content-Type") == "image/png"
        resp, _ = get("/api/directory/logo?u=http%3A%2F%2F127.0.0.1%2F")
        assert resp.status == 404
        resp, _ = get("/api/directory/clicked", "POST", b'{"uuid": "abc"}', {"Content-Type": "application/json"})
        assert resp.status == 403  # needs the player's header
        resp, _ = get("/api/directory/clicked", "POST", b'{"uuid": "abc"}',
                      {"Content-Type": "application/json", "X-Station-Player": "1"})
        assert resp.status == 200
        resp, _ = get("/api/directory/search", headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status == 403
        app.directory = Directory(fetch=FakeNet(), servers=["one"])
        resp, data = get("/api/directory/search?q=x")
        assert resp.status == 502 and "radio-browser.info" in json.loads(data)["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_logos_connect_only_to_the_address_that_was_checked():
    import http.server
    import threading

    from station_player.directory import _fetch_public

    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"] = self.headers.get("Host")
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"PNGDATA")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    looked_up = []

    def resolve(host, port, type=0):
        looked_up.append(host)
        return [(None, None, None, None, ("127.0.0.1", port))]
    try:
        # A local address is refused before anything is sent.
        with pytest.raises(OSError, match="public internet"):
            _fetch_public(f"http://logo.example:{port}/a.png", "UA", 1000, resolve=resolve)
        assert seen == {}
        # Allowed (as a public one would be): it connects to that address,
        # asking for the station's own host name, and looks it up once.
        looked_up.clear()
        body, ctype = _fetch_public(f"http://logo.example:{port}/a.png", "UA", 1000, resolve=resolve,
                                    allowed=lambda ip: True)
        assert (body, ctype) == (b"PNGDATA", "image/png")
        assert seen["host"] == f"logo.example:{port}" and looked_up == ["logo.example"]
    finally:
        server.shutdown()
        server.server_close()


def test_odd_answers_dont_break_logos():
    import http.client

    def broken(url, ua, limit):
        raise http.client.IncompleteRead(b"")
    d = Directory(fetch=FakeNet(), servers=["one"], logo_fetch=broken, public=lambda h: True)
    d._logos["https://img.example/a.png"] = None
    assert d.logo("https://img.example/a.png") is None
    with pytest.raises(DirectoryError):
        Directory(fetch=broken, servers=["one"]).search()


def test_a_genre_button_can_stand_for_several_tags():
    from station_player.directory import genre_tags
    assert genre_tags("Techno / Trance") == ["techno", "trance"]
    assert genre_tags("afrobeats") == ["afrobeat"]
    assert genre_tags("asian / bhangra") == ["asian", "bhangra", "punjabi"]
    assert genre_tags("jazz") == ["jazz"] and genre_tags("") == []


def test_search_for_either_of_two_tags_merges_them_by_votes():
    from urllib.parse import parse_qs, urlparse

    class ByTag(FakeNet):
        def __call__(self, url, user_agent, limit):
            self.asked.append(url)
            q = parse_qs(urlparse(url).query)
            tag = q["tag"][0]
            items = {"techno": [raw("T1", "https://s/t1", votes=50), raw("Both", "https://s/both", votes=5)],
                     "trance": [raw("Tr1", "https://s/tr1", votes=80), raw("Both", "https://s/both", votes=5)]}[tag]
            return api_answer(items[int(q["offset"][0]):][:int(q["limit"][0])])
    net = ByTag()
    d = Directory(fetch=net, servers=["one"])
    page = d.search_page(tag="techno / trance", limit=2)
    assert [s["name"] for s in page["stations"]] == ["Tr1", "T1", "Both"]
    assert page["next"] == 2  # a tag filled its page, so there may be more
    assert sorted(parse_qs(urlparse(u).query)["tag"][0] for u in net.asked) == ["techno", "trance"]
    assert d.search_page(tag="techno / trance", limit=2, offset=2)["next"] is None
