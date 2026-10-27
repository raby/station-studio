"""Settings › Internet: upload speed, listener caps, the router and DuckDNS (netcheck.py, upnp.py)."""
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from station_server import netcheck, upnp
from station_server.config import ConfigStore, clean_station
from station_server.onair import ServerApp


class Clock:
    def __init__(self, per_byte):
        self.t, self.per_byte, self.sent = 0.0, per_byte, []

    def __call__(self):
        return self.t

    def opener(self, req, timeout=None):
        if req.data is not None:
            self.sent.append(len(req.data))
            self.t += len(req.data) * self.per_byte
        return io.BytesIO(b"{}")


def test_upload_speed_grows_the_test_until_it_takes_a_while():
    clock = Clock(per_byte=8 / 10_000_000)  # 10 Mbit/s
    assert netcheck.upload_mbps(clock.opener, clock) == 10.0
    assert clock.sent == [250_000, 1_000_000, 4_000_000]  # 4 MB took 3.2 s: enough
    slow = Clock(per_byte=8 / 1_000_000)  # 1 Mbit/s: stops before it takes too long
    assert netcheck.upload_mbps(slow.opener, slow) == 1.0 and slow.sent == [250_000, 1_000_000]

    def offline(req, timeout=None):
        raise OSError("no route")
    with pytest.raises(netcheck.CheckError, match="couldn't reach Cloudflare"):
        netcheck.upload_mbps(offline)


def test_listeners_from_the_upload():
    assert netcheck.listeners_for(10, 128) == 54
    assert netcheck.listeners_for(0.05, 128) == 0
    both = netcheck.suggest(10, [{"id": "a", "name": "Radio", "port": 8000, "bitrate": 128, "max": 20},
                                 {"id": "b", "name": "Classics", "port": 8002, "bitrate": 64, "max": 20}])
    assert [s["suggested"] for s in both] == [36, 36]  # the upload shared by bitrate


def test_public_address():
    def site(trace, meta):
        def opener(req, timeout):
            body = trace if req.full_url == netcheck.TRACE_URL else meta
            if body is None:
                raise OSError("refused")
            return io.BytesIO(body)
        return opener
    trace = b"fl=123\nh=speed.cloudflare.com\nip=81.150.147.206\nts=1\n"
    assert netcheck.public_address(site(trace, None)) == "81.150.147.206"
    assert netcheck.public_address(site(None, b'{"clientIp": "81.150.147.206"}')) == "81.150.147.206"
    assert netcheck.public_address(site(b"ip=<b>\n", b'{"clientIp": "<b>"}')) is None
    assert netcheck.public_address(site(None, None)) is None


def test_duckdns_names_and_updates():
    assert netcheck.clean_duckdns("https://Rebirth.duckdns.org/") == "rebirth" and netcheck.clean_duckdns("") == ""
    with pytest.raises(ValueError):
        netcheck.clean_duckdns("rebirth radio")
    asked = []

    def opener(req, timeout=None):
        asked.append(req.full_url)
        return io.BytesIO(b"OK" if "token=good" in req.full_url else b"KO")
    netcheck.duckdns_update("rebirth", "good", opener)
    assert asked[0].startswith(netcheck.DUCKDNS_URL + "?domains=rebirth&token=good")
    with pytest.raises(netcheck.CheckError, match="DuckDNS said no"):
        netcheck.duckdns_update("rebirth", "bad", opener)


DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device><deviceList><device><deviceList><device>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList></device></deviceList></device></root>"""


class FakeRouter(BaseHTTPRequestHandler):
    mappings: dict = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._reply(200, DESCRIPTION)

    def do_POST(self):
        action = self.headers["SOAPAction"].strip('"').split("#")[1]
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        port = body.split("<NewExternalPort>")[1].split("<")[0] if "<NewExternalPort>" in body else None
        if action == "AddPortMapping":
            self.mappings[port] = body.split("<NewInternalClient>")[1].split("<")[0]
            return self._soap(action, "")
        if action == "DeletePortMapping":
            self.mappings.pop(port, None)
            return self._soap(action, "")
        if action == "GetExternalIPAddress":
            return self._soap(action, "<NewExternalIPAddress>81.150.147.206</NewExternalIPAddress>")
        if action == "GetSpecificPortMappingEntry":
            if port in self.mappings:
                return self._soap(action, f"<NewInternalClient>{self.mappings[port]}</NewInternalClient>")
            return self._reply(500, "<s:Envelope><s:Body><s:Fault><detail><UPnPError><errorCode>714</errorCode>"
                                    "<errorDescription>NoSuchEntryInArray</errorDescription></UPnPError></detail>"
                                    "</s:Fault></s:Body></s:Envelope>")
        self._reply(500, "")

    def _soap(self, action, inner):
        self._reply(200, f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                         f'<s:Body><u:{action}Response xmlns:u="x">{inner}</u:{action}Response></s:Body></s:Envelope>')

    def _reply(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def router():
    FakeRouter.mappings = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeRouter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/rootDesc.xml"
    srv.shutdown()
    srv.server_close()


def test_the_router_forwards_a_port(router):
    gw = upnp.Gateway(router)
    assert gw.control.endswith("/ctl/IPConn") and gw.external_address() == "81.150.147.206"
    assert gw.mapping(8000) is None
    assert gw.forward(8000, to="192.168.1.20") == "192.168.1.20"
    assert gw.mapping(8000) == "192.168.1.20"
    gw.unforward(8000)
    assert gw.mapping(8000) is None
    with pytest.raises(upnp.UpnpError, match="No router on this network"):
        upnp.find_gateway(discover=lambda: [])


def test_a_mac_that_blocks_the_local_network_says_how_to_allow_it(monkeypatch):
    import errno
    import urllib.error
    monkeypatch.setattr(upnp.sys, "platform", "darwin")

    class Blocked:
        def setsockopt(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, data, to):
            raise OSError(errno.EHOSTUNREACH, "No route to host")

        def close(self):
            pass
    with pytest.raises(upnp.UpnpError, match="Local Network"):
        upnp.discover(sock_factory=Blocked)

    def refused(url, timeout):
        raise urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))
    with pytest.raises(upnp.UpnpError, match="Local Network"):
        upnp.find_gateway(discover=lambda: ["http://192.168.1.1:5000/rootDesc.xml"], opener=refused)


def test_settings_internet_end_to_end(tmp_path, router):
    app = ServerApp(ConfigStore(tmp_path))
    st = app.save_station({"name": "Rebirth Classics", "server": "serve://:8000?max=80"})
    app.save_station({"name": "Rebirth Radio", "server": "icecast://h:8000/radio", "password": "x"})
    assert [s["name"] for s in app.internet()["stations"]] == ["Rebirth Classics"]
    assert any(c["name"] == "Internet" and c["status"] == "warn" for c in app.ready_check(st.id))
    app.check_internet(upload=lambda: 9.8, address=lambda: "81.150.147.206")
    info = app.internet()
    assert info["check"]["upload_mbps"] == 9.8 and info["stations"][0]["suggested"] == 53
    assert ConfigStore(tmp_path).config.net_check["address"] == "81.150.147.206"  # kept
    warn = next(c for c in app.ready_check(st.id) if c["name"] == "Internet")
    assert warn["status"] == "warn" and "suits about 53" in warn["detail"]
    app.set_listener_cap(st.id, 53)
    assert app.config.station(st.id).server == "serve://:8000?max=53"
    assert next(c for c in app.ready_check(st.id) if c["name"] == "Internet")["status"] == "ok"

    gw = upnp.Gateway(router)
    gw.local_address = lambda: "192.168.1.20"
    info = app.router("open", 8000, find=lambda: gw)
    assert info["router"]["ports"] == {"8000": "192.168.1.20"} and info["router"]["address"] == "81.150.147.206"
    assert app.router("close", 8000, find=lambda: gw)["router"]["ports"] == {"8000": None}
    with pytest.raises(ValueError):
        app.router("open", 22, find=lambda: gw)  # only a station's port

    def no_router():
        raise upnp.UpnpError("No router on this network offers UPnP port forwarding")
    assert app.router("look", find=no_router)["router"]["found"] is False

    updated = []
    app.duckdns._update = lambda name, token: updated.append(name)
    app.save_settings({"duckdns_name": "rebirth.duckdns.org", "duckdns_token": "secret-token"})
    state = app.state()["internet"]["duckdns"]
    assert state == {"name": "rebirth", "has_token": True, "host": "rebirth.duckdns.org", "last": state["last"]}
    assert "secret-token" not in json.dumps(app.state()) and "secret-token" not in app.diagnostics()
    with pytest.raises(ValueError, match="token"):
        ServerApp(ConfigStore(tmp_path / "other")).save_settings({"duckdns_name": "rebirth"})
    for _ in range(200):  # straight away, then every few minutes
        if updated:
            break
        time.sleep(0.01)
    assert updated[:1] == ["rebirth"]
    app.save_settings({"duckdns_name": ""})
    assert app.config.duckdns_token == "" and app.state()["internet"]["duckdns"]["last"] is None
    app.shutdown()
    assert clean_station({"name": "x", "server": "serve://:8000"}).server == "serve://:8000"


def test_the_router_search_also_asks_the_router_directly(monkeypatch):
    import socket
    answer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    answer.bind(("127.0.0.1", 0))
    port = answer.getsockname()[1]
    monkeypatch.setattr(upnp, "SSDP", ("239.255.255.250", port))

    def reply():
        data, sender = answer.recvfrom(4096)
        assert data.startswith(b"M-SEARCH")
        answer.sendto(b"HTTP/1.1 200 OK\r\nLOCATION: http://127.0.0.1:5000/rootDesc.xml\r\n\r\n", sender)
    threading.Thread(target=reply, daemon=True).start()
    # this computer at .9 on a network: the router is asked at .1, out of that network
    assert upnp.discover(timeout=2, addresses=["127.0.0.9"]) == ["http://127.0.0.1:5000/rootDesc.xml"]
    answer.close()
    assert all(upnp._private(ip) for ip in upnp.lan_addresses())


def test_internet_gives_the_home_network_address_for_a_phone_on_the_wifi(tmp_path):
    app = ServerApp(ConfigStore(tmp_path))
    assert app.internet()["lan"] is None  # nothing streams from here
    app.save_station({"name": "Rebirth Classics", "server": "serve://:8000"})
    calls = []
    assert app.lan_address(find=lambda: calls.append(1) or ["192.168.0.124"], wait=True) == "192.168.0.124"
    assert app.lan_address(find=lambda: calls.append(1) or ["10.0.0.9"], wait=True) == "192.168.0.124"  # kept a minute
    assert len(calls) == 1
    app.router_state = {"found": True, "here": "192.168.0.50"}
    assert app.lan_address() == "192.168.0.50"  # what the router saw wins
    assert app.internet()["lan"] == "192.168.0.50"


def test_a_duckdns_name_without_its_token_isnt_half_saved(tmp_path):
    app = ServerApp(ConfigStore(tmp_path))
    with pytest.raises(ValueError, match="token"):
        app.save_settings({"duckdns_name": "rebirth"})
    assert app.config.duckdns_name == ""
    app.save_settings({"check_updates": False})  # other settings still save


def test_a_new_duckdns_name_is_sent_straight_away(tmp_path):
    app = ServerApp(ConfigStore(tmp_path))
    sent = []
    app.duckdns._update = lambda name, token: sent.append(name)

    def wait_for(name):
        for _ in range(300):
            if name in sent and app.duckdns.last:
                return
            time.sleep(0.01)
        raise AssertionError(sent)
    app.save_settings({"duckdns_name": "oldname", "duckdns_token": "t"})
    wait_for("oldname")
    app.save_settings({"duckdns_name": "newname"})  # the updater is waiting its few minutes
    assert app.duckdns.last is None  # not "updated" for a name never sent
    wait_for("newname")
    app.save_settings({"duckdns_name": ""})
    app.save_settings({"duckdns_name": "again", "duckdns_token": "t"})  # straight back on
    wait_for("again")
    app.shutdown()


def test_use_never_offers_more_than_a_station_can_allow(tmp_path):
    assert netcheck.suggest(100, [{"id": "a", "name": "R", "port": 8000, "bitrate": 64, "max": 20}])[0]["suggested"] == 500
    app = ServerApp(ConfigStore(tmp_path))
    st = app.save_station({"name": "Rebirth Classics", "server": "serve://192.168.1.20:8000?max=20"})
    app.set_listener_cap(st.id, 54)
    assert app.config.station(st.id).server == "serve://192.168.1.20:8000?max=54"  # the host kept
