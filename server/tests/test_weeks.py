"""Which week goes on air: a new week waits for its week to begin."""
import os
from datetime import datetime, timedelta

from station_server.schedule import WeekStart
from station_server.weeks import WeekPicker

MONDAY = WeekStart()  # Monday 00:00


def at(day: int, hour: int = 12) -> datetime:
    """A local time in the week of Monday 2 Nov 2026 (day 0 = that Monday)."""
    return (datetime(2026, 11, 2, hour) + timedelta(days=day)).astimezone()


def export(folder, name, when: datetime):
    p = folder / name
    p.write_text("<plist/>")
    os.utime(p, (when.timestamp(), when.timestamp()))
    return p


def test_a_week_exported_ahead_waits_for_monday(tmp_path):
    export(tmp_path, "012.xml", at(-3))
    picker = WeekPicker(tmp_path / "weeks.json")
    assert picker.pick("s", tmp_path, MONDAY, at(1))[0].name == "012.xml"
    export(tmp_path, "013.xml", at(3))  # Thursday
    playing, upcoming = picker.pick("s", tmp_path, MONDAY, at(3, 13))
    assert playing.name == "012.xml"
    assert upcoming[0].name == "013.xml" and upcoming[1] == at(7, 0)
    assert picker.pick("s", tmp_path, MONDAY, at(6, 23))[0].name == "012.xml"
    playing, upcoming = picker.pick("s", tmp_path, MONDAY, at(7, 0))
    assert playing.name == "013.xml" and upcoming is None


def test_a_week_exported_late_goes_on_at_once(tmp_path):
    export(tmp_path, "012.xml", at(-10))
    picker = WeekPicker(tmp_path / "weeks.json")
    assert picker.pick("s", tmp_path, MONDAY, at(-1))[0].name == "012.xml"
    # Monday comes with no new week; it's exported on Tuesday.
    assert picker.pick("s", tmp_path, MONDAY, at(0, 9))[0].name == "012.xml"
    export(tmp_path, "013.xml", at(1))
    assert picker.pick("s", tmp_path, MONDAY, at(1, 13))[0].name == "013.xml"


def test_starting_afresh_skips_a_week_exported_ahead(tmp_path):
    export(tmp_path, "011.xml", at(-10))
    export(tmp_path, "012.xml", at(-4))
    export(tmp_path, "013.xml", at(2))  # this week, ahead of next
    playing, upcoming = WeekPicker(tmp_path / "weeks.json").pick("s", tmp_path, MONDAY, at(3))
    assert playing.name == "012.xml" and upcoming[0].name == "013.xml"


def test_remembered_across_a_restart_and_off_for_two_weeks(tmp_path):
    for n, day in ((12, -4), (13, 2), (14, 9)):
        export(tmp_path, f"{n:03d}.xml", at(day))
    assert WeekPicker(tmp_path / "weeks.json").pick("s", tmp_path, MONDAY, at(3))[0].name == "012.xml"
    # Restarted the same week: still 012.
    assert WeekPicker(tmp_path / "weeks.json").pick("s", tmp_path, MONDAY, at(4))[0].name == "012.xml"
    # Switched off for two weeks: back on with the week that's due, not the next one.
    assert WeekPicker(tmp_path / "weeks.json").pick("s", tmp_path, MONDAY, at(15))[0].name == "014.xml"


def test_other_files_and_stations(tmp_path):
    export(tmp_path, "012.xml", at(-4))
    export(tmp_path, "012-v2.xml", at(2))  # an archived version, not a week
    picker = WeekPicker(tmp_path / "weeks.json")
    assert picker.pick("a", tmp_path, MONDAY, at(3)) == (tmp_path / "012.xml", None)
    assert picker.pick("b", tmp_path / "missing", MONDAY, at(3)) == (None, None)
    picker.forget("a")
    assert "a" not in WeekPicker(tmp_path / "weeks.json")._state
