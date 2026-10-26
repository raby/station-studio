"""The Station Studio link: weeks sent straight from Station Studio, and what's on air."""
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from conftest import station_export, write_wav
from station_server.schedule import WeekStart
from station_server.studiolink import new_code, same_code
from station_server.web import _state
from test_app import api, exports  # noqa: F401 (fixtures)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def link(api, code=None):  # noqa: F811
    """Turn the link on; returns a caller for its port, sending the code."""
    port = free_port()
    status, state = api("/api/settings", {"studio_link": True, "studio_port": port})
    assert status == 200
    studio = api("/api/state")[1]["studio"]
    assert studio["running"] and studio["port"] == port
    code = studio["code"] if code is None else code

    def call(path, body=None, given=None, method=None):
        headers = {"Authorization": f"Bearer {code if given is None else given}"}
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers,
                                     method=method or ("PUT" if body is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    call.code = studio["code"]
    return call


def week(tmp_path: Path, n: int, found: int = 2, missing: int = 0) -> bytes:
    files = [write_wav(tmp_path / "studio" / f"{n}-{i}.wav", seconds=0.5, freq=400 + 50 * i) for i in range(found)]
    files += [tmp_path / "elsewhere" / f"gone-{i}.mp3" for i in range(missing)]
    folder = tmp_path / f"w{n}"
    folder.mkdir(parents=True, exist_ok=True)
    return station_export(folder, files, name=f"Rebirth Classics - Week {n}").read_bytes()


def test_codes_are_easy_to_type():
    code = new_code()
    assert len(code) == 14 and code.count("-") == 2 and not set(code) & set("l1o0i")
    assert same_code(code.upper().replace("-", " "), code) and same_code(code.replace("-", ""), code)
    assert not same_code("", code) and not same_code(code, "") and not same_code("abcd-efgh-jkmn", code)


def test_the_link_is_off_until_turned_on_and_needs_the_code(api):  # noqa: F811
    assert not api("/api/state")[1]["studio"]["running"]
    call = link(api)
    status, body = call("/api/link")
    assert status == 200 and body["server"] == "Station Server" and body["stations"] == []
    status, body = call("/api/link", given="wrong-code-here")
    assert status == 401 and "link code" in body["error"]
    # A new code: the old one stops working.
    api("/api/settings", {"studio_new_code": True})
    assert api("/api/state")[1]["studio"]["code"] != call.code
    assert call("/api/link")[0] == 401


def test_wrong_codes_lock_an_address_out(api):  # noqa: F811
    call = link(api)
    for _ in range(5):
        assert call("/api/link", given="nope")[0] == 401
    assert call("/api/link")[0] == 429


def test_a_week_sent_waits_for_its_week_or_goes_on_now(api, exports, tmp_path):  # noqa: F811
    sid = api("/api/stations", {"name": "Rebirth Classics", "playlist": str(exports)})[1]["id"]
    api.app.weeks.pick(sid, exports, WeekStart())  # week 12 is on air (as when it went on air)
    call = link(api)
    row = call("/api/link")[1]["stations"][0]
    assert (row["name"], row["state"], row["week"], row["weeks"]) == ("Rebirth Classics", "off", "012.xml",
                                                                       ["011.xml", "012.xml"])
    # Next week, sent early: it waits for the start of its week.
    status, got = call(f"/api/link/stations/{sid}/weeks/013.xml", week(tmp_path, 13, found=2, missing=1))
    assert status == 200, got
    assert (got["week"], got["tracks"], got["missing_count"], got["when"]) == ("013.xml", 2, 1, "next")
    assert got["goes_on"] and not got["replaced"] and got["missing"][0]["title"] == "Song 3"
    assert (exports / "013.xml").is_file() and not list(exports.glob(".incoming*"))
    row = call("/api/link")[1]["stations"][0]
    assert row["next"]["week"] == "013.xml" and row["week"] == "012.xml"
    # This week again, changed: it replaces the one on air.
    got = call(f"/api/link/stations/{sid}/weeks/012.xml", week(tmp_path, 12, found=3))[1]
    assert (got["when"], got["replaced"], got["tracks"]) == ("now", True, 3)
    assert call(f"/api/link/stations/{sid}/weeks/014.xml", week(tmp_path, 14))[1]["when"] == "later"
    assert call(f"/api/link/stations/{sid}/weeks/010.xml", week(tmp_path, 10))[1]["when"] == "old"
    assert api("/api/state")[1]["studio"]["last"]["week"] == "010.xml"


def test_a_station_with_no_playlist_gets_a_folder_of_weeks(api, tmp_path):  # noqa: F811
    sid = api("/api/stations", {"name": "Rebirth Radio"})[1]["id"]
    call = link(api)
    assert call("/api/link")[1]["stations"][0]["weeks"] == []
    status, got = call(f"/api/link/stations/{sid}/weeks/013.xml", week(tmp_path, 13))
    assert status == 200 and got["when"] == "now"
    folder = Path(api.app.config.station(sid).playlist)
    assert folder == api.app.store.home / "weeks" / sid and (folder / "013.xml").is_file()


def test_weeks_that_cant_be_taken_say_why(api, exports, tmp_path):  # noqa: F811
    one_file = exports / "012.xml"
    sid = api("/api/stations", {"name": "Classics", "playlist": str(one_file)})[1]["id"]
    call = link(api)
    status, body = call(f"/api/link/stations/{sid}/weeks/013.xml", week(tmp_path, 13))
    assert status == 409 and "one playlist file" in body["error"]
    assert "one playlist file" in call("/api/link")[1]["stations"][0]["problem"]
    api(f"/api/stations/{sid}", {"playlist": str(exports)})
    status, body = call(f"/api/link/stations/{sid}/weeks/013.xml", b"not a playlist")
    assert status == 400 and "can read" in body["error"] and not (exports / "013.xml").exists()
    assert call(f"/api/link/stations/{sid}/weeks/week13.txt", week(tmp_path, 13))[0] == 400
    assert call("/api/link/stations/nosuch/weeks/013.xml", week(tmp_path, 13))[0] == 404
    assert not list(exports.glob(".incoming*"))


def test_another_device_isnt_told_the_code_and_diagnostics_hide_it(api):  # noqa: F811
    call = link(api)
    assert "code" not in _state(api.app, remote=True)["studio"]
    assert _state(api.app, remote=False)["studio"]["code"] == call.code
    text = api.app.diagnostics()
    assert call.code not in text and "Station Studio link: on" in text
    # Turned off: the link stops answering.
    api("/api/settings", {"studio_link": False})
    assert not api("/api/state")[1]["studio"]["running"]
