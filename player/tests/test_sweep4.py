"""The play log, the alarm and the phone remote (server side)."""
import csv
import http.client
import io
import json
import threading
from datetime import date, datetime

import pytest

from conftest import write_itunes_xml, write_wav
from station_player import remote
from station_player.config import ConfigStore, clean_alarm, config_from_json
from station_player.playlog import PlayLog
from station_player.server import PlayerApp, make_server


@pytest.fixture
def running(tmp_path):
    song = write_wav(tmp_path / "music" / "song.wav", frames=4000)
    write_itunes_xml(tmp_path / "exports" / "001.xml",
                     [{"Track ID": 1, "Name": "=Song", "Artist": "Band", "Album": "LP",
                       "Persistent ID": "P1", "Location": song.as_uri()}],
                     [{"Name": "Week 1", "Playlist Items": [{"Track ID": 1}]}])
    store = ConfigStore()
    store.config.studio = True
    store.config.playlist_sources = [str(tmp_path / "exports")]
    app = PlayerApp(store)
    server = make_server(app, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield app, server.server_address[1]
    server.shutdown()
    server.server_close()
    if app.remote_server:
        app.remote_server.stop()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
        hdrs.setdefault("X-Station-Player", "1")
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=hdrs)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res.status, dict(res.getheaders()), data


# ---- play log ---------------------------------------------------------------

def test_play_log_export(running):
    app, port = running
    tid = next(iter(app.library.tracks))
    start = datetime(2026, 9, 14, 21, 5, 0)
    for seconds in (95, 4):  # the second was skipped: not reported
        status, _, _ = call(port, "POST", "/api/played",
                            {"id": tid, "started": start.timestamp() * 1000, "seconds": seconds})
        assert status == 200
    status, headers, body = call(port, "GET", "/api/playlog.csv?from=2026-09-01&to=2026-09-30")
    assert status == 200 and "attachment" in headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows[0][:4] == ["Date", "Time", "Title", "Artist"]
    assert rows[1][:5] == ["2026-09-14", "21:05:00", "'=Song", "Band", "LP"]  # never a formula
    assert rows[1][-1] == "1:35" and len(rows) == 2
    status, _, body = call(port, "GET", "/api/playlog.csv?from=2026-10-01&to=2026-10-31")
    assert len(body.decode("utf-8-sig").splitlines()) == 1  # just the header
    assert call(port, "GET", "/api/playlog.csv?from=junk&to=2026-10-31")[0] == 400
    # Off: nothing more is logged.
    call(port, "POST", "/api/settings", {"play_log": False})
    call(port, "POST", "/api/played", {"id": tid, "started": start.timestamp() * 1000, "seconds": 60})
    assert len(app.play_log.entries(date(2026, 9, 1), date(2026, 9, 30))) == 2
    assert call(port, "POST", "/api/played", {"id": tid, "started": "x", "seconds": 1})[0] == 400
    # NaN (which JSON parsing accepts) is refused, and never breaks the export.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/api/played", body='{"id": "%s", "started": 1, "seconds": NaN}' % tid,
                 headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-Station-Player": "1"})
    assert conn.getresponse().status == 400
    conn.close()
    with app.play_log.path.open("a") as fh:
        fh.write('{"at": "2026-09-15T10:00:00+00:00", "seconds": NaN, "duration_ms": NaN}\n'
                 '{"at": "2026-09-15T11:00:00+00:00", "seconds": 40, "duration_ms": NaN}\n')
    assert call(port, "GET", "/api/playlog.csv?from=2026-09-01&to=2026-09-30")[0] == 200


def test_play_log_reads_reporting_tags_and_survives_damage(tmp_path):
    log = PlayLog(tmp_path / "log.jsonl")
    song = tmp_path / "a.mp3"
    frames = b"TSRC" + (13).to_bytes(4, "big") + b"\x00\x00" + b"\x03GBAAA2400001"
    song.write_bytes(b"ID3\x04\x00\x00" + bytes([0, 0, 0, len(frames)]) + frames)
    log.add(datetime(2026, 1, 2, 3, 4, 5), 200, {"title": "T", "path": str(song), "duration_ms": 201000})
    with (tmp_path / "log.jsonl").open("a") as fh:
        fh.write('{"at": "not a time"}\n{"cut short')
    text, count = log.csv(date(2026, 1, 1), date(2026, 1, 31))
    row = list(csv.reader(io.StringIO(text)))[1]
    assert count == 1 and row[7] == "GBAAA2400001" and row[8] == "3:21" and row[9] == "3:20"
    assert PlayLog(tmp_path / "missing.jsonl").csv(date(2026, 1, 1), date(2026, 1, 2)) == (",".join(
        ["Date", "Time", "Title", "Artist", "Album", "Composer", "Label", "ISRC", "Duration", "Played"]) + "\r\n", 0)


# ---- alarm -------------------------------------------------------------------

ALARM = {"on": True, "time": "07:30", "days": [0, 1, 2, 3, 4],
         "source": {"kind": "station", "id": "abc", "name": "Rebirth Radio"}}


def test_alarm_setting_is_checked():
    assert clean_alarm(ALARM)["days"] == [0, 1, 2, 3, 4]
    for bad in ({**ALARM, "time": "7:30"}, {**ALARM, "time": "24:00"}, {**ALARM, "days": []},
                {**ALARM, "days": [7]}, {**ALARM, "source": {"kind": "file", "id": "x"}}):
        with pytest.raises(ValueError):
            clean_alarm(bad)
    assert config_from_json({"alarm": {"time": "nope"}}).alarms == []
    assert config_from_json({"alarm": ALARM}).alarms[0]["time"] == "07:30"


def test_alarm_goes_off_once_on_its_days(running):
    app, port = running
    opened = []
    app.show_window = lambda: opened.append(1)
    assert call(port, "POST", "/api/settings", {"alarm": {**ALARM, "time": "25:00"}})[0] == 400
    assert call(port, "POST", "/api/settings", {"alarm": ALARM})[0] == 200
    monday = datetime(2026, 10, 12, 7, 29)
    assert not app.check_alarm(monday)                        # not yet
    assert app.check_alarm(monday.replace(minute=31))         # a minute late is fine
    assert not app.check_alarm(monday.replace(minute=32))     # once
    assert opened == [1]                                      # no window was open: one opens
    _, _, body = call(port, "GET", "/api/inbox")
    alarm = json.loads(body)["alarm"]
    assert alarm["source"]["id"] == "abc"
    # A restart doesn't reuse the number (the page remembers the last one it
    # played), and doesn't set off an alarm that has already gone off.
    again = PlayerApp(app.store, scan=False)
    assert not again.check_alarm(monday.replace(minute=33))
    assert again.check_alarm(datetime(2026, 10, 14, 7, 30))
    assert again.alarm_request["seq"] != alarm["seq"]
    assert not app.check_alarm(datetime(2026, 10, 13, 7, 40))  # ten minutes late: skipped
    assert not app.check_alarm(datetime(2026, 10, 17, 7, 30))  # Saturday
    assert app.check_alarm(datetime(2026, 10, 13, 7, 30))
    assert opened == [1]  # the page has checked in since: it plays there
    app.set_alarm({**ALARM, "on": False})
    assert not app.check_alarm(datetime(2026, 10, 14, 7, 30))


def test_alarm_in_the_hour_the_clocks_skip(monkeypatch):
    import os
    import time as _time
    if not hasattr(_time, "tzset"):
        pytest.skip("needs time.tzset")
    tz_before = os.environ.get("TZ")  # None when TZ isn't set (the usual case on a Mac)
    monkeypatch.setenv("TZ", "Europe/London")
    _time.tzset()
    try:
        app = PlayerApp(ConfigStore(), scan=False)
        # 29 March 2026: UK clocks go from 01:00 to 02:00 (a Sunday).
        app.store.config.alarms = [{**ALARM, "id": "0000000a", "time": "01:30", "days": [6]}]
        assert app.check_alarm(datetime(2026, 3, 29, 2, 0, 15))
        app.store.config.alarms = [{**ALARM, "id": "0000000b", "time": "06:30", "days": [6]}]
        assert not app.check_alarm(datetime(2026, 3, 29, 7, 0))  # an ordinary time: 30 minutes late is too late
    finally:
        monkeypatch.undo()
        _time.tzset()
    assert os.environ.get("TZ") == tz_before  # put back as it was, even when it was Europe/London


def test_alarm_just_before_midnight():
    app = PlayerApp(ConfigStore(), scan=False)
    app.store.config.alarms = [{**ALARM, "id": "0000000a", "time": "23:59", "days": [6]}]
    assert app.check_alarm(datetime(2026, 10, 19, 0, 1))  # Sunday 23:59, seen on Monday


# ---- phone remote ---------------------------------------------------------------

def test_remote_commands_are_checked():
    assert remote.clean_command({"cmd": "volume", "value": 0.5}) == {"cmd": "volume", "value": 0.5}
    assert remote.clean_command({"cmd": "sleep", "minutes": "track"})["minutes"] == "track"
    for bad in ({"cmd": "rm"}, {"cmd": "volume", "value": 2}, {"cmd": "volume", "value": True},
                {"cmd": "playlist"}, {"cmd": "sleep", "minutes": -1}, {"cmd": "sleep", "minutes": 1.5}):
        with pytest.raises(ValueError):
            remote.clean_command(bad)


def test_phone_remote_end_to_end(running, monkeypatch):
    app, port = running
    app.store.config.remote_port = 0
    monkeypatch.setattr(remote, "lan_address", lambda: "192.168.1.20")
    real = remote.RemoteServer
    monkeypatch.setattr(remote, "RemoteServer", lambda a, p: real(a, p, host="127.0.0.1"))
    opened = []
    app.show_window = lambda: opened.append(1)

    assert json.loads(call(port, "GET", "/api/state")[2])["remote"]["url"] is None
    assert call(port, "GET", "/api/remote/qr.svg")[0] == 404
    state = json.loads(call(port, "POST", "/api/settings", {"remote": True})[2])
    token = app.store.config.remote_token
    rport = app.remote_server.port
    assert state["remote"]["url"] == f"http://192.168.1.20:{rport}/r/{token}/"
    status, headers, svg = call(port, "GET", "/api/remote/qr.svg")
    assert status == 200 and headers["Content-Type"] == "image/svg+xml" and svg.startswith(b"<svg")

    base = f"/r/{token}/"
    status, headers, page = call(rport, "GET", base, headers={"Host": "192.168.1.20"})
    assert status == 200 and b"remote.js" in page and "script-src 'self'" in headers["Content-Security-Policy"]
    assert call(rport, "GET", base + "remote.js")[0] == 200
    # Wrong or no link, the player's own API, or no header: nothing.
    for path in ("/r/wrong/", "/", "/api/state", f"/r/{token}/../../api/state", f"/r/{token}/api/inbox"):
        assert call(rport, "GET", path)[0] == 404, path
    assert call(rport, "POST", base + "api/command", {"cmd": "next"}, {"X-Station-Player": "0"})[0] == 404
    assert call(rport, "POST", base + "api/command", {"cmd": "nope"})[0] == 400
    assert call(rport, "POST", base + "api/command", {"cmd": "next"}, {"Content-Length": "-1"})[0] == 400
    assert remote.RemoteServer.__init__ and app.remote_server.httpd.RequestHandlerClass.timeout == 10

    view = json.loads(call(rport, "GET", base + "api/status")[2])
    assert view["page_open"] is False and view["playlists"][0]["name"] == "Week 1"
    assert view["stations"][0]["name"] == "Rebirth Radio"
    # A command with no window open opens one; the page gets it when it checks in.
    assert call(rport, "POST", base + "api/command", {"cmd": "volume", "value": 0.3})[0] == 200
    assert opened == [1]
    box = json.loads(call(port, "GET", "/api/inbox")[2])
    assert [(c["cmd"], c["value"]) for c in box["commands"]] == [("volume", 0.3)]
    assert json.loads(call(port, "GET", "/api/inbox")[2])["commands"] == []  # handed over once
    # The page reports what's playing; the phone sees it.
    call(port, "POST", "/api/remote/status", {"mode": "track", "title": "Song", "playing": True,
                                              "volume": 0.3, "secret": "x"})
    view = json.loads(call(rport, "GET", base + "api/status")[2])
    assert view["status"] == {"mode": "track", "title": "Song", "playing": True, "volume": 0.3}
    tid = next(iter(app.library.tracks))
    assert call(rport, "GET", base + f"art/{tid}")[0] == 404  # no art in this file

    # A new link: the old one stops working.
    call(port, "POST", "/api/remote/new-link", {})
    assert app.store.config.remote_token != token
    assert call(rport, "GET", base + "api/status")[0] == 404
    # Off: the remote's server stops.
    call(port, "POST", "/api/settings", {"remote": False})
    assert app.remote_server is None
    with pytest.raises(OSError):
        call(rport, "GET", f"/r/{app.store.config.remote_token}/api/status")


def test_remote_port_in_use_is_reported(running, monkeypatch):
    app, port = running

    def busy(a, p):
        raise OSError("Address already in use")

    monkeypatch.setattr(remote, "RemoteServer", busy)
    state = json.loads(call(port, "POST", "/api/settings", {"remote": True})[2])
    assert "couldn't start" in state["remote"]["error"] and state["remote"]["url"] is None


def test_remote_settings_survive_a_restart(tmp_path):
    store = ConfigStore()
    store.config.remote = True
    store.config.remote_token = remote.new_token()
    store.save()
    again = ConfigStore()
    assert again.config.remote and again.config.remote_token == store.config.remote_token
    assert config_from_json({"remote_token": "short"}).remote_token == ""
