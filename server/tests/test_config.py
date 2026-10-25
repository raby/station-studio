import json
import os
import time

import pytest

from station_server.config import ConfigStore, Station, clean_station, newest_week


def test_clean_station_checks_fields():
    st = clean_station({"name": "  Rebirth  ", "server": "shoutcast://1.2.3.4:8000", "bitrate": "128",
                        "remaps": [{"from": "/a", "to": "/b"}, {"from": "", "to": "x"}],
                        "start_mode": "odd", "week_start": {"day": 12}})
    assert st.name == "Rebirth" and st.bitrate == 128 and st.id
    assert st.remaps == [{"from": "/a", "to": "/b"}]
    assert st.start_mode == "clock" and st.week_start == {"day": 0, "time": "00:00"}
    with pytest.raises(ValueError, match="between 32 and 320"):
        clean_station({"bitrate": 1000})
    with pytest.raises(ValueError, match="shoutcast://"):
        clean_station({"server": "http://x:8000"})


def test_blank_password_keeps_the_saved_one():
    old = clean_station({"password": "secret"})
    new = clean_station({"password": "", "name": "Renamed"}, old)
    assert new.password == "secret" and new.id == old.id and new.name == "Renamed"
    shown = new.to_json(secrets=False)
    assert shown["password"] == "" and shown["has_password"] is True


def test_store_round_trip_is_private(tmp_path):
    store = ConfigStore(tmp_path)
    store.config.stations.append(clean_station({"name": "A", "password": "pw"}))
    store.save()
    if os.name == "posix":
        assert (tmp_path / "config.json").stat().st_mode & 0o777 == 0o600
    again = ConfigStore(tmp_path)
    assert again.config.stations[0].name == "A" and again.config.stations[0].password == "pw"


def test_broken_settings_are_kept_aside(tmp_path):
    (tmp_path / "config.json").write_text("{nope")
    store = ConfigStore(tmp_path)
    assert store.config.stations == [] and store.warnings
    assert list(tmp_path.glob("config.broken-*.json"))


def test_newest_week(tmp_path):
    assert newest_week(tmp_path) is None
    for name in ("011.xml", "012.xml", "013-v2.xml", "009.xml"):
        (tmp_path / name).write_text("x")
    assert newest_week(tmp_path).name == "012.xml"
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.xml").write_text("x")
    time.sleep(0.01)
    (other / "b.xml").write_text("x")
    os.utime(other / "a.xml", (1, 1))
    assert newest_week(other).name == "b.xml"
