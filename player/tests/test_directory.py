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


def test_search_drops_hls_and_duplicates_and_remembers_mirror():
    items = [raw("A", "https://s/a"), raw("A again", "https://s/a"), raw("HLS", "https://s/h.m3u8", hls=1),
             raw("B", "https://s/b")]
    net = FakeNet({"https://de1.api.radio-browser.info/": api_answer(items)},
                  down=["all.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info",
                        "nl1.api.radio-browser.info"])
    d = Directory(fetch=net)
    found = d.search(q="a", tag="Jazz", country="gb", order="popular")
    assert [s["name"] for s in found] == ["A", "B"]
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


def test_tags():
    d = Directory(fetch=FakeNet({"https://": api_answer([{"name": "pop", "stationcount": 900}, {"name": ""}, "x"])}),
                  servers=["one"])
    assert d.tags() == [{"name": "pop", "stations": 900}]


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
                   "https://one/json/tags": api_answer([{"name": "pop", "stationcount": 3}]),
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
        resp, data = get("/api/directory/tags")
        assert json.loads(data) == {"tags": [{"name": "pop", "stations": 3}]}
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
