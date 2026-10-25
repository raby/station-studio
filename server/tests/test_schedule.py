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
