"""Regenerating past weeks of a station with persistent cursors.

Each generation records the cursor positions it started from
(PlaylistVersion.cursors). Regenerating a week starts from that week's
recorded positions, and only the latest week moves the station's live
cursors, so regenerating weeks 2-4 of four, in order, gives the same
weeks (rules unchanged) and leaves the live cursors where week 5
expects them. Before, a regeneration started from the live cursors
(where the latest week left them) and moved them on again.
"""
from __future__ import annotations

import pytest

from engine.editor_metadata import synthesize_trivial_sidecar

from engine.models import CursorState, Playlist, PlaylistVersion
from tests.test_api import _seed_station_and_pool
from tests.test_jobs_api import _wait_for_status, jobs_api  # noqa: F401  (fixture)


def _template(slug: str, *, persist: bool = True) -> dict:
    # Two sequential songs per week from one cursor; the default rules
    # advance the cursor 120 a week, wrapping on the 100-song pool.
    return {
        "station": slug, "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1, "persist": persist},
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "cursors_init": {"C1": 0},
            "row_template": [
                {"source": "A", "strategy": "sequential", "size": 2, "cursor_id": "C1"},
            ],
        },
    }


def _tracks(Session, station_id: int, week: int) -> list[int]:
    with Session() as db:
        pl = db.query(Playlist).filter_by(station_id=station_id, week=week).one()
        return [e.track_id for e in pl.entries]


def _live(Session, station_id: int) -> int:
    with Session() as db:
        return db.query(CursorState).filter_by(station_id=station_id, name="C1").one().position


def _generate(client, slug: str, week: int, *, job: bool) -> None:
    if not job:
        r = client.post(f"/api/stations/{slug}/generate", json={"week": week})
        assert r.status_code == 201, r.text
        return
    r = client.post(f"/api/stations/{slug}/jobs/generate", json={"week": week})
    assert r.status_code == 201, r.text
    _wait_for_status(client, r.json()["job_id"], "done")


@pytest.fixture
def four_weeks(jobs_api):  # noqa: F811
    client, Session, _store = jobs_api
    station_id, _ = _seed_station_and_pool(
        Session, slug="rb", pool_name="A", pool_tracks=100, template=_template("rb"),
    )
    for week in range(1, 5):
        _generate(client, "rb", week, job=False)
    before = {w: _tracks(Session, station_id, w) for w in range(1, 5)}
    return client, Session, station_id, before


def test_each_generation_records_where_its_cursors_started(four_weeks):
    _, Session, station_id, _ = four_weeks
    with Session() as db:
        rows = (
            db.query(Playlist.week, PlaylistVersion.cursors)
            .join(PlaylistVersion, PlaylistVersion.playlist_id == Playlist.id)
            .filter(Playlist.station_id == station_id)
            .order_by(Playlist.week)
            .all()
        )
    # 0, then +120 a week wrapping on 100 songs.
    assert rows == [(1, {"C1": 0}), (2, {"C1": 20}), (3, {"C1": 40}), (4, {"C1": 60})]
    assert _live(Session, station_id) == 80


@pytest.mark.parametrize("job", [False, True], ids=["sync", "job"])
def test_regenerating_a_past_week_gives_the_same_week_and_leaves_live_cursors(four_weeks, job):
    client, Session, station_id, before = four_weeks
    _generate(client, "rb", 2, job=job)
    assert _tracks(Session, station_id, 2) == before[2]
    assert _live(Session, station_id) == 80  # week 5 still continues from here


@pytest.mark.parametrize("job", [False, True], ids=["sync", "job"])
def test_regenerating_this_and_later_weeks_in_order(four_weeks, job):
    client, Session, station_id, before = four_weeks
    for week in (2, 3, 4):
        _generate(client, "rb", week, job=job)
    assert {w: _tracks(Session, station_id, w) for w in range(1, 5)} == before
    assert _live(Session, station_id) == 80

    # The next new week carries on as if nothing had been regenerated.
    _generate(client, "rb", 5, job=job)
    with Session() as db:
        v5 = db.query(PlaylistVersion).filter_by(week=5).one()
    assert v5.cursors == {"C1": 80}


def test_regenerating_the_latest_week_moves_live_cursors_from_its_own_start(four_weeks):
    client, Session, station_id, before = four_weeks
    _generate(client, "rb", 4, job=False)
    _generate(client, "rb", 4, job=False)  # twice: no drift
    assert _tracks(Session, station_id, 4) == before[4]
    assert _live(Session, station_id) == 80


def test_a_past_week_made_before_positions_were_recorded_leaves_live_cursors(four_weeks):
    """Weeks generated before 1.12 have no record: they start from the
    live cursors as before, but no longer move them."""
    client, Session, station_id, _ = four_weeks
    with Session() as db:
        db.query(PlaylistVersion).update({PlaylistVersion.cursors: None})
        db.commit()
    _generate(client, "rb", 2, job=False)
    assert _live(Session, station_id) == 80
    with Session() as db:
        newest = (
            db.query(PlaylistVersion).filter_by(week=2)
            .order_by(PlaylistVersion.version_no.desc()).first()
        )
    assert newest.cursors == {"C1": 80}  # recorded from now on


def test_preview_of_a_past_week_matches_the_week(four_weeks):
    client, Session, station_id, before = four_weeks
    template = _template("rb")
    r = client.post(
        "/api/stations/rb/jobs/preview",
        json={"template": template, "sidecar": synthesize_trivial_sidecar(template), "week": 2},
    )
    assert r.status_code == 201, r.text
    snap = _wait_for_status(client, r.json()["job_id"], "done")
    assert [s["track_id"] for s in snap["result"]["trace"]["slots"]] == before[2]


def test_stateless_templates_record_nothing(jobs_api):  # noqa: F811
    client, Session, _store = jobs_api
    station_id, _ = _seed_station_and_pool(
        Session, slug="cls", pool_name="A", pool_tracks=20, template=_template("cls", persist=False),
    )
    _generate(client, "cls", 1, job=False)
    _generate(client, "cls", 1, job=False)
    with Session() as db:
        assert db.query(PlaylistVersion.cursors).filter(PlaylistVersion.cursors.isnot(None)).count() == 0
        assert db.query(CursorState).count() == 0


def test_rolling_back_a_week_keeps_where_its_cursors_started(four_weeks):
    client, Session, station_id, before = four_weeks
    _generate(client, "rb", 2, job=False)  # v2 of week 2
    with Session() as db:
        pid = db.query(Playlist.id).filter_by(station_id=station_id, week=2).scalar()
    r = client.post(f"/api/playlists/{pid}/rollback", params={"to": 1})
    assert r.status_code in (200, 201), r.text
    with Session() as db:
        newest = (
            db.query(PlaylistVersion).filter_by(week=2)
            .order_by(PlaylistVersion.version_no.desc()).first()
        )
    assert newest.version_no == 3 and newest.cursors == {"C1": 20}
    # Regenerating it again still starts from there.
    _generate(client, "rb", 2, job=False)
    assert _tracks(Session, station_id, 2) == before[2]
    assert _live(Session, station_id) == 80


def test_deleting_the_latest_week_then_regenerating_the_one_before_rewinds_the_cursors(four_weeks):
    """Week 4 deleted: week 3 is now the latest, so regenerating it puts
    the live cursors back where a new week 4 should start."""
    client, Session, station_id, before = four_weeks
    with Session() as db:
        pid = db.query(Playlist.id).filter_by(station_id=station_id, week=4).scalar()
    assert client.delete(f"/api/playlists/{pid}").status_code == 204
    _generate(client, "rb", 3, job=False)
    assert _live(Session, station_id) == 60  # week 3 started at 40, +120 wraps to 60
    _generate(client, "rb", 4, job=False)
    assert _tracks(Session, station_id, 4) == before[4]
