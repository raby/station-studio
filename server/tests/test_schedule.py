from datetime import datetime, timedelta

from station_player.library import Track

from station_server.schedule import GUESS_SECONDS, WeekStart, position, running_order, track_seconds, where_now


def tracks(*seconds):
    return [Track(id=str(i), name=f"Song {i}", artist="A", duration_ms=int(s * 1000))
            for i, s in enumerate(seconds)]


def test_week_start_parse_falls_back_to_monday_midnight():
    assert WeekStart.parse({"day": 4, "time": "18:30"}) == WeekStart(4, 18, 30)
    for bad in (None, "x", {"day": 9}, {"time": "25:00"}, {"time": "noon"}):
        assert WeekStart.parse(bad) == WeekStart()
    assert WeekStart(4, 18, 30).to_json() == {"day": 4, "time": "18:30"}
    assert WeekStart(4, 18, 30).label() == "Friday 18:30"


def test_latest_week_start():
    wed = datetime(2026, 10, 21, 9, 0)  # a Wednesday
    assert WeekStart().latest(wed) == datetime(2026, 10, 19, 0, 0)
    # Friday 18:30 hasn't come yet this week: last Friday's.
    assert WeekStart(4, 18, 30).latest(wed) == datetime(2026, 10, 16, 18, 30)
    # Exactly on the start counts as this week.
    assert WeekStart(2, 9, 0).latest(wed) == wed


def test_position_loops_a_short_playlist():
    assert position([10, 20, 30], 0) == (0, 0)
    assert position([10, 20, 30], 15) == (1, 5)
    assert position([10, 20, 30], 65) == (0, 5)
    assert position([], 100) == (0, 0.0)


def test_where_now_counts_from_the_week_start():
    week = WeekStart()
    monday = datetime(2026, 10, 19).astimezone()
    assert where_now(tracks(60, 60, 60), week, monday + timedelta(seconds=90)) == (1, 30)


def test_track_seconds_guesses_without_a_length():
    assert track_seconds(Track(id="1", name="x")) == GUESS_SECONDS


def test_running_order_times_and_loop():
    start = datetime(2026, 10, 19, 12, 0)
    rows = running_order(tracks(60, 120), 1, start, count=3)
    assert [r["index"] for r in rows] == [1, 0, 1]
    assert [r["at"][11:16] for r in rows] == ["12:00", "12:02", "12:03"]
    assert len(running_order(tracks(60, 120), 1, start, count=5, loop=False)) == 1


from station_server.schedule import Show, plan  # noqa: E402


def test_show_parse_and_label():
    show = Show.parse({"name": " Saturday Night Mix ", "source": "/m/mix.mp3", "days": [5], "time": "20:00"})
    assert show.name == "Saturday Night Mix" and show.on_time and show.id
    assert show.label() == "Sat 20:00"
    assert Show.parse({**show.to_json()}) == show  # keeps its id
    assert Show.parse({"source": "x", "days": list(range(7)), "time": "6:05"}).label() == "Every day 06:05"
    for bad, msg in (({"days": [5], "time": "20:00"}, "Choose the recording"),
                     ({"source": "x", "days": [], "time": "20:00"}, "days"),
                     ({"source": "x", "days": [5], "time": "25:00"}, "time like")):
        try:
            Show.parse(bad)
        except ValueError as exc:
            assert msg in str(exc)
        else:
            raise AssertionError(bad)


def test_show_starts_on_its_days():
    show = Show.parse({"source": "x", "days": [5], "time": "20:00"})  # Saturdays
    mon = datetime(2026, 10, 19).astimezone()
    starts = show.starts(mon, mon + timedelta(days=14))
    assert [s.strftime("%a %d %H:%M") for s in starts] == ["Sat 24 20:00", "Sat 31 20:00"]
    # Wall-clock time, also after the clocks go back (25 October 2026 in the UK).
    assert all(s.hour == 20 for s in starts)


def _clock(tracks, week_start):
    def resume(at, interrupted):
        return position([60.0] * len(tracks), (at - week_start).total_seconds())
    return resume


def test_running_order_cuts_for_a_show_on_time():
    start = datetime(2026, 10, 19, 12, 0).astimezone()
    tr = tracks(60, 60, 60, 60, 60, 60, 60, 60, 60, 60)
    show = Show("n", "News", "x", (0,), 12, 2, True)  # Monday 12:02, on the dot
    rows = plan(tr, 0, start, start, [(show, 150.0)], _clock(tr, start), count=5)
    assert [(r.get("show") or r["title"], r["at"][11:19]) for r in rows] == [
        ("Song 0", "12:00:00"), ("Song 1", "12:01:00"), ("News", "12:02:00"),
        ("Song 4", "12:04:30"), ("Song 5", "12:05:00")]  # back by the clock, part-way into Song 4
    assert rows[3]["from"] == 30 and rows[3]["seconds"] == 30  # joins Song 4 part-way, by the clock


def test_running_order_after_the_song():
    start = datetime(2026, 10, 19, 12, 0).astimezone()
    tr = tracks(60, 60, 60, 60, 60, 60)
    show = Show("s", "Mix", "x", (0,), 12, 1, False)  # Monday 12:01, after the song
    rows = plan(tr, 0, start, start + timedelta(seconds=30), [(show, 90.0)], lambda at, i: (i + 1, 0.0), count=4)
    assert [(r.get("show") or r["title"], r["at"][11:19]) for r in rows] == [
        ("Song 0", "12:00:00"), ("Song 1", "12:01:00"), ("Mix", "12:02:00"), ("Song 2", "12:03:30")]


def test_running_order_with_a_show_on_now():
    start = datetime(2026, 10, 19, 20, 0).astimezone()
    tr = tracks(60, 60, 60)
    show = Show("s", "Mix", "x", (0,), 20, 0, True)
    rows = plan(tr, 0, start, start + timedelta(minutes=5), [(show, 600.0)], lambda at, i: (2, 15.0),
                count=2, show_playing=(show, start, 600.0))
    assert rows[0] == {"show": "Mix", "at": start.isoformat(timespec="seconds"), "seconds": 600.0}
    assert rows[1]["title"] == "Song 2" and rows[1]["from"] == 15 and rows[1]["at"][11:16] == "20:10"
