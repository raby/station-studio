"""Settings › Station Server and sending weeks to it (engine/stationserver.py,
api/routers/stationserver.py), against a stand-in for Station Server's
link (server/station_server/studiolink.py)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from engine.models import Config
from engine.stationserver import LinkError, base_url
from tests.test_api import _seed_station_and_pool, api  # noqa: F401 (fixture)

CODE = "k7m2-9qxr-4hpt"


@pytest.fixture
def fake_server():
    """A stand-in Station Server link: keeps what was sent, answers like it."""
    got: list[tuple[str, str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _ok(self):
            if self.headers.get("Authorization") != f"Bearer {CODE}":
                self._json({"error": "That isn't the link code"}, 401)
                return False
            return True

        def do_GET(self):
            if self._ok():
                self._json({"server": "Station Server", "version": "0.13.0", "stations": [
                    {"id": "abc123", "name": "Rebirth Classics", "state": "on", "week": "001.xml",
                     "next": None, "weeks": ["001.xml"], "now": {"title": "Track 1", "artist": "A"}}]})

        def do_PUT(self):
            if not self._ok():
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append(("PUT", self.path, body))
            if not self.path.startswith("/api/link/stations/abc123/"):
                return self._json({"error": "There's no such station in Station Server"}, 404)
            self._json({"station": "Rebirth Classics", "week": self.path.rsplit("/", 1)[1], "tracks": 1,
                        "missing_count": 0, "missing": [], "when": "next", "goes_on": "2026-11-02T00:00"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.got = got
    server.address = f"127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


def test_addresses_people_type():
    assert base_url("192.168.1.20") == "http://192.168.1.20:8772"
    assert base_url(" 192.168.1.20:9000 ") == "http://192.168.1.20:9000"
    assert base_url("http://station-pc.local:8772/") == "http://station-pc.local:8772"
    for bad in ("", "http://", "ftp://x", "host:notaport"):
        with pytest.raises(LinkError):
            base_url(bad)


def test_settings_keep_the_code_but_never_show_it(api):  # noqa: F811
    client, Session = api
    assert client.get("/api/station-server").json() == {"address": "", "has_code": False, "stations": {}}
    assert client.get("/api/station-server/status").json()["configured"] is False
    r = client.put("/api/station-server", json={"address": "192.168.1.20", "code": CODE})
    assert r.json() == {"address": "192.168.1.20", "has_code": True, "stations": {}}
    assert CODE not in r.text and CODE not in client.get("/api/station-server").text
    # Changing the address keeps the code.
    assert client.put("/api/station-server", json={"address": "10.0.0.5:8772"}).json()["has_code"]
    assert client.put("/api/station-server", json={"address": "http://"}).status_code == 400


def test_status_says_why_it_cant_connect(api, fake_server):  # noqa: F811
    client, _ = api
    client.put("/api/station-server", json={"address": fake_server.address, "code": "wrong"})
    got = client.get("/api/station-server/status").json()
    assert not got["connected"] and "link code" in got["error"]
    client.put("/api/station-server", json={"code": CODE})
    got = client.get("/api/station-server/status").json()
    assert got["connected"] and got["stations"][0]["name"] == "Rebirth Classics"
    # Nothing listening there.
    fake_server.shutdown()
    fake_server.server_close()
    got = client.get("/api/station-server/status").json()
    assert not got["connected"] and "Couldn't reach Station Server" in got["error"]


def test_send_a_week_and_send_on_export(api, fake_server, tmp_path):  # noqa: F811
    client, Session = api
    _seed_station_and_pool(Session, slug="cls", name="Classics")
    with Session() as db:
        db.add(Config(key="playlist_output_dir", value=str(tmp_path)))
        db.commit()
    pl_id = client.post("/api/stations/cls/generate", json={"week": 13}).json()["playlist_id"]
    client.put("/api/station-server", json={"address": fake_server.address, "code": CODE})

    # Not yet told which station it feeds.
    r = client.post(f"/api/playlists/{pl_id}/send-to-station-server")
    assert r.status_code == 400 and "Choose which Station Server station" in r.json()["detail"]
    assert client.put("/api/station-server/stations/nosuch", json={"station": "abc123"}).status_code == 404

    client.put("/api/station-server/stations/cls", json={"station": "abc123"})
    r = client.post(f"/api/playlists/{pl_id}/send-to-station-server")
    assert r.status_code == 200 and r.json()["when"] == "next"
    method, path, body = fake_server.got[-1]
    assert path == "/api/link/stations/abc123/weeks/013.xml" and body.startswith(b"<?xml")
    assert body == client.get(f"/api/playlists/{pl_id}/export").content

    # Export to disk sends only when the station is set to.
    r = client.post(f"/api/playlists/{pl_id}/export-to-disk").json()
    assert "station_server" not in r and len(fake_server.got) == 1
    client.put("/api/station-server/stations/cls", json={"station": "abc123", "auto": True})
    r = client.post(f"/api/playlists/{pl_id}/export-to-disk").json()
    assert r["station_server"]["week"] == "013.xml" and len(fake_server.got) == 2
    # An old version written to disk isn't sent.
    client.post("/api/stations/cls/generate", json={"week": 13})
    client.post(f"/api/playlists/{pl_id}/export-to-disk?v=1")
    assert len(fake_server.got) == 2

    # Station Server's own reason comes through.
    client.put("/api/station-server/stations/cls", json={"station": "gone", "auto": True})
    r = client.post(f"/api/playlists/{pl_id}/export-to-disk").json()
    assert r["path"] and "no such station" in r["station_server_error"]
    r = client.post(f"/api/playlists/{pl_id}/send-to-station-server")
    assert r.status_code == 404 and "no such station" in r.json()["detail"]

    # Clearing the target forgets it.
    assert client.put("/api/station-server/stations/cls", json={"station": ""}).json()["stations"] == {}
