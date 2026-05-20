"""Persistence round-trip tests for the §14.25 mix strategy.

Verifies the DB-side half of the slice: MixUsage rows are written when a
mix block picks a track, and load_excluded_mix_ids assembles the correct
exclusion set for the next week's generation.

Each test uses a throwaway SQLite DB (tmp_path) so nothing leaks between
runs. Pattern mirrors tests/test_api.py's fixture style but skips the
TestClient layer — these are direct exercises of engine.persistence.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from engine.database import make_engine, make_sessionmaker
from engine.generator import generate
from engine.models import (
    Base, MixUsage, Playlist, SourcePlaylist, SourcePlaylistEntry,
    Station, Track,
)
from engine.persistence import (
    load_excluded_mix_ids, load_pools, max_mix_exclusion_window, save_playlist,
)
from engine.template import parse_template


# ---------- fixtures ----------------------------------------------------


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker:
    """A sessionmaker pointed at a throwaway SQLite file."""
    engine = make_engine(f"sqlite:///{tmp_path}/persist.db")
    Base.metadata.create_all(engine)
    return make_sessionmaker(engine)


def _seed_mix_station(Session: sessionmaker) -> tuple[int, dict]:
    """Insert a station with a single-block mix template + a House-tagged
    source pool. Returns (station_id, template_dict)."""
    template = {
        "station": "rb",
        "cadence": "weekly",
        "rng": "off",
        "sources": [
            {"name": "MIXES", "colour": "#0cf", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "MIXES", "strategy": "mix", "size": 1,
                 "genre": "House", "exclusion_window": 5},
            ],
        },
    }
    with Session() as db:
        station = Station(slug="rb", name="Rebirth", template=template)
        db.add(station)
        db.flush()
        station_id = station.id

        pool = SourcePlaylist(name="MIXES")
        db.add(pool)
        db.flush()

        for i, (pid, name) in enumerate([
            ("P-H1", "House-A"), ("P-H2", "House-B"), ("P-H3", "House-C"),
        ], start=1):
            t = Track(persistent_id=pid, name=name, genre="House")
            db.add(t)
            db.flush()
            db.add(SourcePlaylistEntry(
                source_playlist_id=pool.id, track_id=t.id, position=i,
            ))
        db.commit()
    return station_id, template


# ---------- tests --------------------------------------------------------


def test_save_playlist_records_mix_usage_rows(session_factory):
    Session = session_factory
    station_id, template_dict = _seed_mix_station(Session)
    tmpl = parse_template(template_dict)

    with Session() as db:
        station = db.query(Station).filter_by(id=station_id).one()
        pools = load_pools(db, tmpl)
        result = generate(tmpl, pools, week=1)
        save_playlist(db, station, result, week=1, template=tmpl)
        db.commit()

        rows = db.query(MixUsage).all()
        assert len(rows) == 1
        row = rows[0]
        # First eligible House track in the pool is House-A (position 1).
        assert row.genre == "House"
        assert row.track_id == pools["MIXES"][0].id


def test_save_playlist_without_template_skips_mix_usage(session_factory):
    """Legacy/Classics callers don't pass template; MixUsage stays empty
    even if the template has mix blocks (caller knows what they're doing)."""
    Session = session_factory
    station_id, template_dict = _seed_mix_station(Session)
    tmpl = parse_template(template_dict)

    with Session() as db:
        station = db.query(Station).filter_by(id=station_id).one()
        pools = load_pools(db, tmpl)
        result = generate(tmpl, pools, week=1)
        save_playlist(db, station, result, week=1)  # no template= kwarg
        db.commit()

        assert db.query(MixUsage).count() == 0


def test_load_excluded_mix_ids_assembles_from_recent_playlists(session_factory):
    Session = session_factory
    station_id, template_dict = _seed_mix_station(Session)
    tmpl = parse_template(template_dict)

    # Generate weeks 1, 2, 3 — each picks the only-non-excluded House track.
    # With exclusion_window=5 propagating + window of 3 generated, each new
    # week's exclusion set should grow by one track.
    with Session() as db:
        station = db.query(Station).filter_by(id=station_id).one()
        pools = load_pools(db, tmpl)
        previously_excluded: set[int] = set()
        picked_ids: list[int] = []
        for week in (1, 2, 3):
            excluded = load_excluded_mix_ids(db, station_id=station.id, window=5)
            assert excluded == previously_excluded
            r = generate(tmpl, pools, week=week, excluded_mix_ids=excluded)
            save_playlist(db, station, r, week=week, template=tmpl)
            db.commit()
            previously_excluded.add(r.picks[0].id)
            picked_ids.append(r.picks[0].id)

        # All three picks distinct — the exclusion window forced rotation
        # through all three House tracks in the pool.
        assert len(set(picked_ids)) == 3
        # After week 3, MixUsage carries one row per week.
        assert db.query(MixUsage).count() == 3


def test_load_excluded_mix_ids_zero_window_returns_empty(session_factory):
    Session = session_factory
    station_id, _ = _seed_mix_station(Session)
    with Session() as db:
        assert load_excluded_mix_ids(db, station_id=station_id, window=0) == set()


def test_load_excluded_mix_ids_no_playlists_returns_empty(session_factory):
    Session = session_factory
    station_id, _ = _seed_mix_station(Session)
    with Session() as db:
        # No playlists generated yet.
        assert load_excluded_mix_ids(db, station_id=station_id, window=10) == set()


def test_load_excluded_mix_ids_window_limits_lookback(session_factory):
    """If window=1 only the most recent playlist's MixUsage feeds the set."""
    Session = session_factory
    station_id, template_dict = _seed_mix_station(Session)
    tmpl = parse_template(template_dict)

    with Session() as db:
        station = db.query(Station).filter_by(id=station_id).one()
        pools = load_pools(db, tmpl)
        for week in (1, 2):
            excluded = load_excluded_mix_ids(db, station_id=station.id, window=5)
            r = generate(tmpl, pools, week=week, excluded_mix_ids=excluded)
            save_playlist(db, station, r, week=week, template=tmpl)
            db.commit()

        # Two MixUsage rows total; window=1 should only see the week-2 row.
        narrow = load_excluded_mix_ids(db, station_id=station_id, window=1)
        assert len(narrow) == 1
        wide = load_excluded_mix_ids(db, station_id=station_id, window=5)
        assert len(wide) == 2
        assert narrow.issubset(wide)


# ---------- max_mix_exclusion_window (multi-mix templates) --------------


def _template_with_mix_blocks(*windows: int) -> dict:
    """Build a template carrying one mix block per supplied exclusion_window."""
    return {
        "station": "x",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "M", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": ["1"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "M", "strategy": "mix", "size": 1,
                 "genre": f"G{i}", "exclusion_window": w}
                for i, w in enumerate(windows, start=1)
            ],
        },
    }


def test_max_mix_exclusion_window_no_mix_blocks_returns_zero():
    # Plain window-only template — no mix blocks.
    tmpl = parse_template({
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot", "unit_labels": ["1"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "window", "size": 1}],
        },
    })
    assert max_mix_exclusion_window(tmpl) == 0


def test_max_mix_exclusion_window_single_mix_block_returns_its_window():
    tmpl = parse_template(_template_with_mix_blocks(7))
    assert max_mix_exclusion_window(tmpl) == 7


def test_max_mix_exclusion_window_multiple_blocks_returns_max():
    # The wider window dominates — strategy only sees one exclusion set per
    # generation, and wider = stronger constraint.
    tmpl = parse_template(_template_with_mix_blocks(3, 11, 5))
    assert max_mix_exclusion_window(tmpl) == 11


def test_max_mix_exclusion_window_zero_windows_returns_zero():
    tmpl = parse_template(_template_with_mix_blocks(0, 0))
    assert max_mix_exclusion_window(tmpl) == 0


# ---------- §14.29: per-block None falls back to default ----------------


def _template_with_mix_block_no_window() -> dict:
    """Mix block without exclusion_window (§14.29 grammar relaxation)."""
    return {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "M", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot", "unit_labels": ["1"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "M", "strategy": "mix", "size": 1, "genre": "House"},
            ],
        },
    }


def test_max_mix_exclusion_window_uses_default_when_block_field_is_none():
    """A mix block without exclusion_window inherits the `default` kwarg
    (sourced from the station's `mix_exclusion_weeks` rule at the API)."""
    tmpl = parse_template(_template_with_mix_block_no_window())
    assert max_mix_exclusion_window(tmpl, default=20) == 20
    assert max_mix_exclusion_window(tmpl, default=0) == 0


def test_max_mix_exclusion_window_walks_flat_slots():
    """Regression test for the §14.32 review-fix sweep: pre-fix, this
    function only walked `row_template`, so flat_slots templates with
    mix blocks silently returned 0 → load_excluded_mix_ids saw no
    lookback → cross-week mix exclusion never fired. Rebirth Radio's
    port is the primary class of flat_slots+mix templates.

    Same bug shape as §14.32's caught _save_mix_usages issue; this is
    the second instance in persistence.py the sweep audit caught.
    """
    template = {
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "M", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "flat_slots": [
                {"source": "M", "strategy": "mix", "size": 1,
                 "genre": "House", "exclusion_window": 7},
                {"source": "M", "strategy": "mix", "size": 1,
                 "genre": "Trance", "exclusion_window": 12},
            ],
        },
    }
    tmpl = parse_template(template)
    # Pre-fix: returned 0 (only walked row_template, which is empty for
    # flat templates). Post-fix: returns max(7, 12) = 12.
    assert max_mix_exclusion_window(tmpl, default=20) == 12
    # Same with explicit + missing exclusion_window via default kwarg.
    template["layout"]["flat_slots"][1] = {
        "source": "M", "strategy": "mix", "size": 1, "genre": "Trance",
        # exclusion_window omitted — should inherit default=20
    }
    tmpl = parse_template(template)
    assert max_mix_exclusion_window(tmpl, default=20) == 20  # max(7, 20)


def test_max_mix_exclusion_window_max_of_mixed_explicit_and_default():
    """One mix block sets exclusion_window=5; another omits it (inherits
    default=20). Max wins."""
    template = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "M", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot", "unit_labels": ["1"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "M", "strategy": "mix", "size": 1,
                 "genre": "G1", "exclusion_window": 5},
                {"source": "M", "strategy": "mix", "size": 1, "genre": "G2"},
            ],
        },
    }
    tmpl = parse_template(template)
    assert max_mix_exclusion_window(tmpl, default=20) == 20
    assert max_mix_exclusion_window(tmpl, default=3) == 5


def test_save_playlist_replacing_existing_clears_old_mix_usage(session_factory):
    """save_playlist's "replace existing playlist for (station, week)" path
    should ON DELETE CASCADE drop the prior MixUsage rows too — otherwise
    re-generating a week would orphan stale exclusion entries."""
    Session = session_factory
    station_id, template_dict = _seed_mix_station(Session)
    tmpl = parse_template(template_dict)

    with Session() as db:
        station = db.query(Station).filter_by(id=station_id).one()
        pools = load_pools(db, tmpl)

        # First generation.
        r1 = generate(tmpl, pools, week=1)
        save_playlist(db, station, r1, week=1, template=tmpl)
        db.commit()
        assert db.query(MixUsage).count() == 1
        first_track_id = r1.picks[0].id

        # Regenerate same week with a different exclusion that picks a
        # different track. The replace path drops the old playlist (and
        # via FK cascade, the old MixUsage row).
        r2 = generate(tmpl, pools, week=1, excluded_mix_ids={first_track_id})
        save_playlist(db, station, r2, week=1, template=tmpl)
        db.commit()
        rows = db.query(MixUsage).all()
        assert len(rows) == 1
        assert rows[0].track_id != first_track_id
