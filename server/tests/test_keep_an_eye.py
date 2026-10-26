"""Keeping an eye on it: Listen, listener counts, alerts and phone access."""
import base64
import json
import socket
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
from conftest import needs_ffmpeg
from test_app import api, exports, wait_for  # noqa: F401 (fixtures)

from station_server import alerts
from station_server.config import Alerts, ConfigStore, clean_alerts
from station_server.listen import Listeners, Tap, TooManyListeners
from station_server.senders import parse_7html, parse_icestats

# ---- listener counts ------------------------------------------------------

def test_listener_counts_from_shoutcast_and_icecast():
    assert parse_7html("<html><body>12,1,40,32,10,128,Artist - Song, with a comma</body></html>") == {
        "listeners": 12, "peak": 40, "max": 32}
    with pytest.raises(ValueError):
        parse_7html("<html><body>Invalid resource</body></html>")
    stats = {"icestats": {"source": [
        {"listenurl": "http://radio.example:8000/radio", "listeners": 7, "listener_peak": 9},
        {"listenurl": "http://radio.example:8000/classics", "listeners": 2, "listener_peak": 4},
    ]}}
    assert parse_icestats(stats, "/classics") == {"listeners": 2, "peak": 4, "max": None}
    with pytest.raises(ValueError):
        parse_icestats(stats, "/other")


# ---- Listen ---------------------------------------------------------------

def test_listeners_hear_what_is_sent_and_slow_ones_are_dropped():
    sent = []
    hub = Listeners(most=2)
    tap = Tap(SimpleNamespace(send=sent.append, connected=True, describe="server"), hub)
    a = hub.open()
    b = hub.open()
    with pytest.raises(TooManyListeners):
        hub.open()
    tap.send(b"one")
    assert sent == [b"one"] and tap.connected and tap.describe == "server"
    assert a.get_nowait() == b"one" and b.get_nowait() == b"one"
    for _ in range(300):  # `a` stops reading
        tap.send(b"x")
        b.get_nowait()
    assert hub.count == 1
    hub.end()
    assert b.get_nowait() is None and hub.count == 0


# ---- alerts ---------------------------------------------------------------

def runner(name="Rebirth Classics", on_air=True, state="on", connected=True, error=None):
    return SimpleNamespace(
        station=SimpleNamespace(name=name, on_air=on_air), state=state, error=error,
        sender=SimpleNamespace(connected=connected, describe="SHOUTcast 81.150.147.206:8000"),
        channel=SimpleNamespace(stuck=False), playing_fallback=lambda: False)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def test_an_alert_after_a_few_minutes_off_air_and_another_when_back():
    sent = []
    r = runner()
    app = SimpleNamespace(config=SimpleNamespace(alerts=Alerts(after_minutes=3, ntfy="https://ntfy.sh/x")),
                          runners={"a": r})
    clock = Clock()
    watcher = alerts.Watcher(app, send=lambda cfg, title, msg: sent.append((title, msg)) or [], clock=clock)
    watcher.check()
    # A short drop that puts itself right tells nobody.
    r.sender.connected = False
    watcher.check()
    clock.now += 60
    r.sender.connected = True
    watcher.check()
    assert sent == []
    # A longer one does, once.
    r.state, r.error = "waiting", "the server can't be reached (Connection refused)"
    watcher.check()
    clock.now += 179
    watcher.check()
    assert sent == []
    clock.now += 2
    watcher.check()
    clock.now += 600
    watcher.check()
    assert len(sent) == 1
    assert sent[0][0] == "Rebirth Classics is off air" and "Connection refused" in sent[0][1]
    r.state, r.error = "on", None
    watcher.check()
    assert sent[1][0] == "Rebirth Classics is back on air" and "13 minutes" in sent[1][1]
    assert watcher.last["title"] == "Rebirth Classics is back on air"


def test_no_alert_for_a_station_taken_off_air_or_with_nowhere_to_send():
    sent = []
    r = runner(on_air=False, state="off")
    app = SimpleNamespace(config=SimpleNamespace(alerts=Alerts(after_minutes=1)), runners={"a": r})
    clock = Clock()
    watcher = alerts.Watcher(app, send=lambda *a: sent.append(a) or [], clock=clock)
    watcher.check()
    clock.now += 600
    watcher.check()
    r.on_air, r.state = True, "error"  # a problem, but no phone or email set up
    r.station.on_air = True
    watcher.check()
    clock.now += 600
    watcher.check()
    assert sent == [] and watcher.last is None


def test_silence_and_the_fallback_are_problems_too():
    r = runner()
    r.channel.stuck = True
    assert alerts.problem(r)[0] == "is sending silence"
    r.channel.stuck = False
    r.playing_fallback = lambda: True
    assert alerts.problem(r)[0] == "is playing its fallback"


def test_phone_notification_and_email_are_sent():
    got = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def opener(req, timeout):
        got["url"], got["body"] = req.full_url, json.loads(req.data)
        return Resp()

    alerts.send_ntfy("https://ntfy.sh/rebirth-x1", "Rebirth Classics is off air", "Since 03:12", opener=opener)
    assert got["url"] == "https://ntfy.sh/" and got["body"]["topic"] == "rebirth-x1"
    assert got["body"]["title"] == "Rebirth Classics is off air"

    class Smtp:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            self.calls.append("starttls")

        def login(self, user, password):
            self.calls.append(("login", user, password))

        def send_message(self, msg):
            self.calls.append(("send", msg["To"], msg["Subject"]))

    smtp = Smtp()
    cfg = Alerts(email_to="rob@example.com", smtp_host="smtp.example.com", smtp_user="me@example.com",
                 smtp_password="app-pass")
    alerts.send_email(cfg, "Rebirth Classics is off air", "Since 03:12", smtp=smtp)
    assert smtp.calls == ["starttls", ("login", "me@example.com", "app-pass"),
                          ("send", "rob@example.com", "Rebirth Classics is off air")]


def test_alert_settings_are_checked_kept_and_hidden(tmp_path):
    with pytest.raises(ValueError):
        clean_alerts({"ntfy": "ntfy.sh/no-scheme"})
    with pytest.raises(ValueError):
        clean_alerts({"email_to": "rob@example.com"})  # no mail server
    with pytest.raises(ValueError):
        clean_alerts({"after_minutes": 0})
    a = clean_alerts({"email_to": "rob@example.com", "smtp_host": "smtp.example.com", "smtp_password": "secret"})
    assert clean_alerts({"smtp_password": ""}, a).smtp_password == "secret"  # blank keeps it
    store = ConfigStore(tmp_path)
    store.config.alerts = a
    store.save()
    again = ConfigStore(tmp_path)
    assert again.config.alerts == a and not again.warnings
    hidden = a.to_json(secrets=False)
    assert hidden["smtp_password"] == "" and hidden["has_smtp_password"]


def test_settings_page_saves_alerts_and_tests_them(api):  # noqa: F811
    status, body = api("/api/alerts/test", {})
    assert status == 400 and "first" in body["error"]
    status, state = api("/api/settings", {"alerts": {"ntfy": "https://ntfy.sh/rebirth-test", "after_minutes": 5}})
    assert status == 200 and state["alerts"]["ntfy"] == "https://ntfy.sh/rebirth-test"
    assert state["alerts"]["after_minutes"] == 5
    assert "rebirth-test" not in api.app.diagnostics()


# ---- phone access ---------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port, path, data=None, password=None, host=None):
    headers = {"Host": host or f"192.168.1.20:{port}"}
    if data is not None:
        headers.update({"X-Station-Server": "1", "Content-Type": "application/json"})
    if password is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(f"phone:{password}".encode()).decode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_phone_access_needs_its_password_and_only_watches_and_switches(api, monkeypatch):  # noqa: F811
    api.app.remote.host = "127.0.0.1"  # the test stays on this computer
    port = free_port()
    status, body = api("/api/settings", {"remote": True})
    assert status == 400 and "password" in body["error"]
    assert api("/api/settings", {"remote_password": "short"})[0] == 400
    status, state = api("/api/settings", {"remote": True, "remote_password": "rebirth-2026", "remote_port": port})
    assert status == 200 and state["remote"]["running"] and state["remote"]["port"] == port
    assert api("/api/state")[1]["remote"]["has_password"]
    assert "rebirth-2026" not in json.dumps(api("/api/state")[1])

    status, _ = call(port, "/api/state")
    assert status == 401
    status, state = call(port, "/api/state", password="rebirth-2026")
    assert status == 200 and state["remote_view"] is True
    status, sid = api("/api/stations", {"name": "Rebirth Classics"})
    sid = sid["id"]
    # Settings, editing and quitting stay on the station's computer.
    assert call(port, "/api/settings", {"remote": False}, password="rebirth-2026")[0] == 403
    assert call(port, f"/api/stations/{sid}", {"name": "x"}, password="rebirth-2026")[0] == 403
    assert call(port, "/api/quit", {}, password="rebirth-2026")[0] == 403
    status, state = call(port, f"/api/stations/{sid}/on", {"on": True}, password="rebirth-2026")
    assert status == 200 and state["stations"][0]["state"] in ("starting", "error", "waiting", "on")
    call(port, f"/api/stations/{sid}/on", {"on": False}, password="rebirth-2026")
    # Wrong guesses are slowed down.
    for _ in range(5):
        assert call(port, "/api/state", password="guess")[0] == 401
    assert call(port, "/api/state", password="rebirth-2026")[0] == 429

    assert api("/api/settings", {"remote": False})[1]["remote"]["running"] is False
    with pytest.raises(OSError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2)


# ---- on air: Listen and the count, end to end ------------------------------

@needs_ffmpeg
def test_listen_plays_the_broadcast_and_the_count_shows(api, exports, icecast, monkeypatch):  # noqa: F811
    from station_server import onair
    monkeypatch.setattr(onair, "COUNT_SECONDS", 0.5)
    status, body = api("/api/stations", {
        "name": "Rebirth Classics", "playlist": str(exports), "start_mode": "top",
        "server": f"icecast://localhost:{icecast.port}/classics", "password": "hackme",
    })
    sid = body["id"]
    assert api(f"/api/stations/{sid}/listen.mp3")[0] == 409  # not on air
    api(f"/api/stations/{sid}/on", {"on": True})
    assert wait_for(lambda: api("/api/state")[1]["stations"][0]["state"] == "on")
    got = bytearray()
    with urllib.request.urlopen(f"{api.base}/api/stations/{sid}/listen.mp3", timeout=10) as resp:
        assert resp.headers["Content-Type"] == "audio/mpeg"
        assert wait_for(lambda: api("/api/state")[1]["stations"][0]["listening"] == 1)
        while len(got) < 4000:
            got += resp.read(1000)
    assert got[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xfa") or b"\xff\xfb" in got  # MP3 frames
    assert wait_for(lambda: (api("/api/state")[1]["stations"][0]["audience"] or {}).get("listeners") == 3)
    threading.Thread(target=lambda: api(f"/api/stations/{sid}/on", {"on": False})).start()
    assert wait_for(lambda: api("/api/state")[1]["stations"][0]["state"] == "off")
    assert api("/api/state")[1]["stations"][0]["audience"] is None
