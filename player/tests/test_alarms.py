"""Several alarms, snooze and stop, and the phone remote's alarm controls."""
import http.client
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import pytest

from station_player import remote
from station_player.config import ConfigStore, clean_alarms, config_from_json
from station_player.server import AlarmNotRinging, PlayerApp, make_server

SOURCE = {"kind": "station", "id": "abc", "name": "Rebirth Radio"}
WEEKDAYS = [0, 1, 2, 3, 4]


def alarm(time_="07:30", days=WEEKDAYS, **extra):
    return {"on": True, "time": time_, "days": days, "source": dict(SOURCE), "label": "", **extra}


@pytest.fixture
def app():
    return PlayerApp(ConfigStore(), scan=False)


@pytest.fixture
def running(app):
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
    return res.status, json.loads(data) if data else None


# ---- settings -------------------------------------------------------------------

def test_alarms_are_checked():
    out = clean_alarms([alarm(label="  Work   days  "), alarm("08:00", [6, 5, 5], id="0123abcd")])
    assert re.fullmatch(r"[0-9a-f]{8}", out[0]["id"]) and out[0]["label"] == "Work days"
    assert out[1]["id"] == "0123abcd" and out[1]["days"] == [5, 6]
    assert set(out[0]) == {"id", "on", "time", "days", "source", "label"}
    assert clean_alarms([alarm(label="x" * 99)])[0]["label"] == "x" * 60
    assert clean_alarms(None) == [] and clean_alarms([]) == []
    # Malformed or repeated ids get new ones.
    out = clean_alarms([alarm(id="ABCDEF12"), alarm(id="0123abcd"), alarm(id="0123abcd"), alarm(id=5)])
    ids = [a["id"] for a in out]
    assert len(set(ids)) == 4 and ids[1] == "0123abcd" and ids[0] != "ABCDEF12"
    assert all(re.fullmatch(r"[0-9a-f]{8}", i) for i in ids)
    for bad, words in ((alarm("7:30"), "HH:MM"), (alarm(days=[]), "day"), (alarm(days=[True]), "day"),
                       (alarm(days=[7]), "day"), ({**alarm(), "source": {"kind": "file", "id": "x"}}, "playlist"),
                       (alarm(label=3), "label"), ("x", "set out")):
        with pytest.raises(ValueError, match=words):
            clean_alarms([bad])
    with pytest.raises(ValueError, match="Alarm 2 needs a time"):
        clean_alarms([alarm(), alarm("25:00")])
    with pytest.raises(ValueError, match="up to 10"):
        clean_alarms([alarm()] * 11)
    with pytest.raises(ValueError):
        clean_alarms({"time": "07:00"})


def test_old_single_alarm_is_migrated(tmp_path):
    store = ConfigStore()
    store.home.mkdir(parents=True, exist_ok=True)
    old = {k: v for k, v in alarm().items() if k != "label"}
    store.path.write_text(json.dumps({"stations": [], "alarm": old}))
    (store.home / "alarm-fired.txt").write_text("2026-10-12 07:30")
    again = ConfigStore()
    (migrated,) = again.config.alarms
    assert migrated["label"] == "" and migrated["time"] == "07:30" and re.fullmatch(r"[0-9a-f]{8}", migrated["id"])
    saved = json.loads(again.path.read_text())  # the new id is kept
    assert "alarm" not in saved and saved["alarms"] == again.config.alarms
    assert ConfigStore().config.alarms[0]["id"] == migrated["id"]
    # 0.6's record of the time it went off still counts after the upgrade.
    app = PlayerApp(again, scan=False)
    assert not app.check_alarm(datetime(2026, 10, 12, 7, 31))
    assert app.check_alarm(datetime(2026, 10, 13, 7, 30))
    # Malformed alarms in a file are dropped; at most 10 are kept.
    cfg = config_from_json({"alarms": [alarm("nope")] + [alarm()] * 12})
    assert len(cfg.alarms) == 10 and len({a["id"] for a in cfg.alarms}) == 10
    assert config_from_json({"alarms": "x"}).alarms == []


def test_settings_take_the_list_or_the_old_single_alarm(running):
    app, port = running
    status, state = call(port, "POST", "/api/settings",
                         {"alarms": [alarm(label="Work"), alarm("09:00", [5, 6], id="0000beef", on=False)]})
    assert status == 200
    alarms = state["config"]["alarms"]
    assert [a["label"] for a in alarms] == ["Work", ""] and alarms[1]["id"] == "0000beef"
    assert "alarm" not in state["config"] and app.store.config.alarms == alarms
    status, state = call(port, "POST", "/api/settings", {"alarms": [alarm("24:00")]})
    assert status == 400 and "HH:MM" in state["error"] and len(app.store.config.alarms) == 2
    # The old single alarm replaces the list, keeping its id.
    status, state = call(port, "POST", "/api/settings", {"alarm": alarm("06:00", id="1234abcd")})
    assert status == 200 and [(a["id"], a["time"]) for a in state["config"]["alarms"]] == [("1234abcd", "06:00")]
    status, state = call(port, "POST", "/api/settings", {"alarm": None})
    assert status == 200 and state["config"]["alarms"] == []
    assert json.loads(app.store.path.read_text())["alarms"] == []


# ---- going off ---------------------------------------------------------------------

def test_every_alarm_goes_off_once(app):
    app.store.config.alarms = clean_alarms([
        alarm("07:30", label="Work", id="0000000a"),
        alarm("07:30", [0], id="0000000b", label="Same minute"),
        alarm("09:00", [5, 6], id="0000000c", label="Weekend"),
        alarm("10:00", id="0000000d", on=False),
    ])
    monday = datetime(2026, 10, 12, 7, 30, 20)
    assert app.check_alarm(monday)
    req = app.inbox()["alarm"]
    assert (req["alarm_id"], req["label"], req["snoozed"]) == ("0000000a", "Work", False)
    assert req["source"] == SOURCE and req["age"] >= 0 and "seq" in req
    assert not app.check_alarm(monday.replace(minute=31))  # the other at the same minute doesn't ring
    assert not app.check_alarm(datetime(2026, 10, 12, 10, 0))  # off
    assert app.check_alarm(datetime(2026, 10, 17, 9, 1))  # Saturday
    assert app.inbox()["alarm"]["label"] == "Weekend"
    # A restart remembers each alarm's times.
    again = PlayerApp(app.store, scan=False)
    assert not again.check_alarm(datetime(2026, 10, 17, 9, 2))
    assert not again.check_alarm(monday.replace(minute=32))
    assert again.check_alarm(datetime(2026, 10, 13, 7, 30))
    fired = json.loads((app.store.home / "alarm-fired.json").read_text())
    assert set(fired) <= {"0000000a", "0000000b", "0000000c", "0000000d"}
    assert all(k >= "2026-10-11" for keys in fired.values() for k in keys)  # only recent times kept


def test_state_shows_the_next_alarm_and_what_is_ringing(running):
    app, port = running
    _, state = call(port, "GET", "/api/state")
    assert state["next_alarm"] is None
    assert state["alarm_state"] == {"ringing": None, "snoozed_until": None}
    app.store.config.alarms = clean_alarms([alarm("07:30", label="Work", id="0000000a"),
                                            alarm("06:00", [5], id="0000000b")])
    nxt = app.next_alarm(datetime(2026, 10, 15, 8, 0))  # Thursday, after 07:30
    assert nxt["alarm_id"] == "0000000a" and nxt["label"] == "Work" and nxt["source_name"] == "Rebirth Radio"
    at = datetime.fromisoformat(nxt["at"])
    assert at.utcoffset() is not None and at.replace(tzinfo=None) == datetime(2026, 10, 16, 7, 30)
    nxt = app.next_alarm(datetime(2026, 10, 16, 8, 0))  # Friday: Saturday 06:00 is next
    assert nxt["alarm_id"] == "0000000b" and datetime.fromisoformat(nxt["at"]).day == 17
    _, state = call(port, "GET", "/api/state")
    assert state["next_alarm"]["alarm_id"] in ("0000000a", "0000000b")
    assert app.check_alarm(datetime(2026, 10, 12, 7, 30))
    _, state = call(port, "GET", "/api/state")
    ringing = state["alarm_state"]["ringing"]
    assert ringing["alarm_id"] == "0000000a" and ringing["label"] == "Work" and ringing["snoozed"] is False
    assert ringing["source"] == SOURCE and abs(ringing["at"] - time.time()) < 5 and ringing["seq"]


def test_snooze_and_stop(running, caplog):
    app, port = running
    caplog.set_level(logging.INFO, logger="station_player.server")
    app.store.config.alarms = clean_alarms([alarm("07:30", label="Work", id="0000000a")])
    status, body = call(port, "POST", "/api/alarm/snooze", {})
    assert status == 409 and body["error"]
    assert call(port, "POST", "/api/alarm/snooze", {}, {"X-Station-Player": "0"})[0] == 403
    assert app.check_alarm(datetime(2026, 10, 12, 7, 30))
    first = app.alarm_request["seq"]
    # (Off from here, without cancelling the snooze, so the real clock can't set it off.)
    app.store.config.alarms[0]["on"] = False
    assert call(port, "POST", "/api/alarm/snooze", {"minutes": 0})[0] == 400
    assert call(port, "POST", "/api/alarm/snooze", {"minutes": 61})[0] == 400
    assert call(port, "POST", "/api/alarm/snooze", {"minutes": "5"})[0] == 400
    before = time.time()
    status, state = call(port, "POST", "/api/alarm/snooze", {})
    assert status == 200 and state["alarm_state"]["ringing"] is None
    until = state["alarm_state"]["snoozed_until"]
    assert before + 9 * 60 - 1 <= until <= time.time() + 9 * 60 + 1
    # The snooze is the next alarm.
    assert state["next_alarm"]["alarm_id"] == "0000000a" and state["next_alarm"]["label"] == "Work"
    assert abs(datetime.fromisoformat(state["next_alarm"]["at"]).timestamp() - until) < 1
    assert call(port, "POST", "/api/alarm/snooze", {})[0] == 409  # not ringing any more
    assert not app.check_alarm(datetime.fromtimestamp(until - 5))
    assert app.check_alarm(datetime.fromtimestamp(until + 1))
    req = app.inbox()["alarm"]
    assert req["snoozed"] is True and req["alarm_id"] == "0000000a" and req["seq"] > first
    _, state = call(port, "GET", "/api/state")
    assert state["alarm_state"]["ringing"]["snoozed"] is True and state["alarm_state"]["snoozed_until"] is None
    # Snooze again for a minute, then stop while snoozed.
    _, state = call(port, "POST", "/api/alarm/snooze", {"minutes": 1})
    assert state["alarm_state"]["snoozed_until"] <= time.time() + 61
    status, state = call(port, "POST", "/api/alarm/stop", {})
    assert status == 200 and state["alarm_state"] == {"ringing": None, "snoozed_until": None}
    assert not app.check_alarm(datetime.fromtimestamp(time.time() + 120))
    assert call(port, "POST", "/api/alarm/stop", {})[0] == 200  # nothing to stop: fine
    # Ringing stops by itself after half an hour.
    app.store.config.alarms[0]["on"] = True
    assert app.check_alarm(datetime(2026, 10, 13, 7, 30))
    app._ringing["at"] -= 30 * 60
    assert app.alarm_state()["ringing"] is None
    with pytest.raises(AlarmNotRinging):
        app.snooze_alarm()
    messages = [r.getMessage() for r in caplog.records]
    assert any("snoozed for 9 minutes" in m for m in messages)
    assert any(m == "Alarm stopped" for m in messages)


def test_deleting_a_snoozed_alarm_cancels_the_snooze(app):
    app.store.config.alarms = clean_alarms([alarm("07:30", id="0000000a")])
    assert app.check_alarm(datetime(2026, 10, 12, 7, 30))
    app.snooze_alarm(5)
    app.set_alarms([alarm("07:30", id="0000000a")])  # still there and on: kept
    assert app.alarm_state()["snoozed_until"]
    app.set_alarms([])
    assert app.alarm_state()["snoozed_until"] is None


# ---- phone remote -----------------------------------------------------------------

def test_remote_alarm_commands(app):
    assert remote.clean_command({"cmd": "snooze", "minutes": 3}) == {"cmd": "snooze"}
    assert remote.clean_command({"cmd": "stop_alarm"}) == {"cmd": "stop_alarm"}
    opened = []
    app.show_window = lambda: opened.append(1)
    app.store.config.alarms = clean_alarms([alarm("07:30", label="Work", id="0000000a")])
    view = app.remote_view()["alarm"]
    assert view["ringing"] is False and view["label"] == "" and view["snoozed_until"] is None
    assert view["next"]["alarm_id"] == "0000000a"
    # Nothing ringing: nothing happens.
    app.remote_command({"cmd": "snooze"})
    app.remote_command({"cmd": "stop_alarm"})
    assert app.inbox()["commands"] == [] and opened == []
    app.page_seen = time.time()
    assert app.check_alarm(datetime(2026, 10, 12, 7, 30))
    assert app.remote_view()["alarm"]["ringing"] is True and app.remote_view()["alarm"]["label"] == "Work"
    app.remote_command({"cmd": "snooze"})
    view = app.remote_view()["alarm"]
    assert view["ringing"] is False and view["label"] == "Work"
    assert 9 * 60 - 5 < view["snoozed_until"] - time.time() <= 9 * 60
    assert [c["cmd"] for c in app.inbox()["commands"]] == ["snooze"]
    app.remote_command({"cmd": "stop_alarm"})  # stops the snooze too
    assert app.remote_view()["alarm"]["snoozed_until"] is None
    assert [c["cmd"] for c in app.inbox()["commands"]] == ["stop_alarm"]
    assert opened == []


# ---- the remote page in a browser -------------------------------------------------

def test_remote_page_alarm_panel(running, monkeypatch):
    if os.environ.get("STATION_PLAYER_REQUIRE_BROWSER") == "1":
        import playwright.sync_api as sync_api
    else:
        sync_api = pytest.importorskip("playwright.sync_api")
    app, port = running
    app.store.config.remote_port = 0
    monkeypatch.setattr(remote, "lan_address", lambda: "127.0.0.1")
    real = remote.RemoteServer
    monkeypatch.setattr(remote, "RemoteServer", lambda a, p: real(a, p, host="127.0.0.1"))
    app.set_remote(True)
    url = f"http://127.0.0.1:{app.remote_server.port}/r/{app.store.config.remote_token}/"
    app.store.config.alarms = clean_alarms([alarm("07:00", [1], label="Gym", id="0000000a")])
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:
            if os.environ.get("STATION_PLAYER_REQUIRE_BROWSER") == "1":
                raise
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url)
        page.wait_for_function("() => !document.querySelector('#next-alarm').hidden")
        assert page.locator("#next-alarm").inner_text() == "Next alarm: Tue 07:00 · Gym"
        assert page.locator("#alarm").is_hidden()

        assert app.check_alarm(datetime(2026, 10, 13, 7, 0))
        page.wait_for_function("() => !document.querySelector('#alarm').hidden")
        assert page.locator("#alarm-head").inner_text() == "Alarm"
        assert page.locator("#alarm-label").inner_text() == "Gym"
        assert page.locator("#next-alarm").is_hidden()
        for name in ("Snooze 9 min", "Stop"):
            assert page.get_by_role("button", name=name).bounding_box()["height"] >= 44
        page.get_by_role("button", name="Snooze 9 min").click()
        page.wait_for_function("() => document.querySelector('#alarm-snooze').hidden")
        until = app.alarm_state()["snoozed_until"]
        assert until and page.locator("#alarm-head").inner_text() == \
            "Snoozed until " + datetime.fromtimestamp(until).strftime("%H:%M")
        page.get_by_role("button", name="Stop").click()
        page.wait_for_function("() => document.querySelector('#alarm').hidden")
        assert app.alarm_state() == {"ringing": None, "snoozed_until": None}
        assert [c["cmd"] for c in app.inbox()["commands"]] == ["snooze", "stop_alarm"]
        assert not [e for e in errors if "Content Security Policy" in e]
        browser.close()
