"""Just-this-week changes through the API: made, listed and undone
(api/routers/week_changes.py), made again by every way a week is
generated, following the station's rules, and restored by Rollback.
"""
from __future__ import annotations

import copy
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db, get_session_factory
from api.job_runner import JobStore, get_job_store
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.editor_metadata import synthesize_trivial_sidecar
from engine.models import (
    Base, MixUsage, PlaylistEntry, SourcePlaylist,
    SourcePlaylistEntry, Station, Track, WeekChange,
)
from engine.persistence import generate_chain

SLUG = "wk"


def station_template() -> dict:
    return {
        "station": SLUG,
        "name": "Week changes",
        "cadence": "weekly",
        "rng": "on",
        "sources": [
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "flat_slots": [
                {"source": "A", "strategy": "weighted", "size": 3},
                {"source": "B", "strategy": "weighted", "size": 2},
                {"source": "A", "strategy": "weighted", "size": 2},
            ],
        },
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/wk.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    monkeypatch.setenv("STATION_STUDIO_SKIP_AUTO_SEED", "1")
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    Session: sessionmaker = make_sessionmaker(engine)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    store = JobStore()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory] = lambda: Session
    app.dependency_overrides[get_job_store] = lambda: store
    _seed(Session)
    with TestClient(app) as client:
        yield client, Session, store
    app.dependency_overrides.clear()


def _seed(Session) -> None:
    with Session() as db:
        for name, n in (("A", 30), ("B", 15)):
            sp = SourcePlaylist(name=name, role="content")
            db.add(sp)
            db.flush()
            for i in range(1, n + 1):
                t = Track(persistent_id=f"{name}{i:03d}", name=f"{name} song {i}",
                          artist=f"{name} artist {i % 9}")
                db.add(t)
                db.flush()
                db.add(SourcePlaylistEntry(source_playlist_id=sp.id, position=i, track_id=t.id))
        # A song that's in no pool of the station's.
        db.add(Track(persistent_id="X001", name="Elsewhere", artist="Nobody"))
        t = station_template()
        db.add(Station(slug=SLUG, name="Week changes", template=t,
                       sidecar=synthesize_trivial_sidecar(t)))
        db.commit()


def gen(client, week=1, seed=7):
    r = client.post(f"/api/stations/{SLUG}/generate", json={"week": week, "seed": seed})
    assert r.status_code == 201, r.text
    return r.json()


def trace(client, pid, v=None):
    r = client.get(f"/api/playlists/{pid}/trace" + (f"?v={v}" if v else ""))
    assert r.status_code == 200, r.text
    return r.json()


def track_id(Session, pid: str) -> int:
    with Session() as db:
        return db.query(Track).filter_by(persistent_id=pid).one().id


def swap(row=1, block=1, k=0, track=None, strategy="weighted", source="A"):
    return {"row": row, "block": block, "k": k, "kind": "swap", "track_id": track,
            "rule_strategy": strategy, "rule_source": source}


def rule(row=1, block=1, k=0, new=None, strategy="weighted", source="A"):
    return {"row": row, "block": block, "k": k, "kind": "rule", "rule": new,
            "rule_strategy": strategy, "rule_source": source}


def add(client, pid, change, version):
    return client.post(f"/api/playlists/{pid}/changes",
                       json={"expected_version": version, "change": change})


def entries(Session, playlist_id) -> list[int]:
    with Session() as db:
        return [e.track_id for e in db.query(PlaylistEntry)
                .filter_by(playlist_id=playlist_id).order_by(PlaylistEntry.position)]


# ---------- making, listing, undoing ------------------------------------


def test_swap_makes_a_version_and_regenerating_keeps_it(env):
    client, Session, _ = env
    g = gen(client)
    pid = g["playlist_id"]
    assert g["week_changes_applied"] == 0 and g["week_changes_dropped"] == []
    v1 = trace(client, pid)
    assert v1["version_no"] == 1 and v1["note"] is None
    mine = track_id(Session, "A030")

    r = add(client, pid, swap(block=3, k=1, track=mine), 1)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version_no"] == 2
    assert body["change"]["position"] == 7 and body["change"]["track"]["name"] == "A song 30"
    assert body["slot"]["track_id"] == mine and body["slot"]["change"]["kind"] == "swap"

    v2 = trace(client, pid)
    assert v2["version_no"] == 2
    assert v2["note"] == "Just this week: song 7 swapped for A song 30"
    assert [s["track_id"] for s in v2["slots"]][:6] == [s["track_id"] for s in v1["slots"]][:6]
    assert v2["slots"][6]["track_id"] == mine
    assert v2["slots"][6]["source"] == "A" and v2["slots"][6]["strategy"] == "weighted"
    assert entries(Session, pid)[6] == mine
    versions = client.get(f"/api/playlists/{pid}/versions").json()["versions"]
    assert [v["note"] for v in versions] == [None, v2["note"]]

    listed = client.get(f"/api/playlists/{pid}/changes").json()
    assert listed["version_no"] == 2 and listed["max_rule_changes"] == 20
    assert [(c["kind"], c["position"], c["track_id"]) for c in listed["changes"]] == \
        [("swap", 7, mine)]

    # Regenerating (same seed) gives the same week, the swap made again.
    g2 = gen(client)
    assert g2["week_changes_applied"] == 1 and g2["week_changes_dropped"] == []
    v3 = trace(client, pid)
    assert [s["track_id"] for s in v3["slots"]] == [s["track_id"] for s in v2["slots"]]
    assert v3["slots"][6]["change"] == v2["slots"][6]["change"]


def test_rule_change_song_is_the_one_regenerating_gives(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    r = add(client, pid, rule(block=1, k=2, new={"source": "B", "strategy": "weighted", "size": 9}), 1)
    assert r.status_code == 201, r.text
    slot = r.json()["slot"]
    assert slot["change"]["source"] == "B" and slot["change"]["fields"] == {"source": "B"}
    assert slot["change"]["rule"] == {"source": "B", "strategy": "weighted", "size": 9}
    assert slot["source"] == "A"  # the rule's, still
    with Session() as db:
        assert db.get(Track, slot["track_id"]).persistent_id.startswith("B")
    assert trace(client, pid)["note"] == "Just this week: song 3 picked by a changed rule"
    gen(client)
    assert trace(client, pid)["slots"][2] == slot


def test_undo_puts_the_rules_song_back_exactly(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    v1 = trace(client, pid)
    change = add(client, pid, swap(block=2, k=0, track=track_id(Session, "B015"), source="B"), 1).json()
    r = client.delete(f"/api/playlists/{pid}/changes/{change['change']['id']}?expected_version=2")
    assert r.status_code == 200, r.text
    assert r.json()["version_no"] == 3
    v3 = trace(client, pid)
    assert v3["slots"] == v1["slots"]
    assert v3["note"] == "Just this week: song 4 back to its rule's song"
    assert client.get(f"/api/playlists/{pid}/changes").json()["changes"] == []
    assert entries(Session, pid) == [s["track_id"] for s in v1["slots"]]


def test_a_second_change_to_a_song_replaces_the_first(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    v1 = trace(client, pid)
    add(client, pid, swap(track=track_id(Session, "A029")), 1)
    r = add(client, pid, swap(track=track_id(Session, "A028")), 2)
    assert r.status_code == 201
    slot = r.json()["slot"]
    # "was" is still the rule's own song, not the first swap.
    assert slot["change"]["was"]["track_id"] == v1["slots"][0]["track_id"]
    changes = client.get(f"/api/playlists/{pid}/changes").json()["changes"]
    assert [c["track"]["name"] for c in changes] == ["A song 28"]


def test_stale_version_and_running_generation_are_refused(env):
    client, Session, store = env
    pid = gen(client)["playlist_id"]
    mine = track_id(Session, "A030")
    r = add(client, pid, swap(track=mine), 2)
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 1
    store.running_job_id = lambda slug, kind: "job123"  # type: ignore[method-assign]
    r = add(client, pid, swap(track=mine), 1)
    assert r.status_code == 409 and r.json()["detail"]["existing_job_id"] == "job123"
    r = client.delete(f"/api/playlists/{pid}/changes/1?expected_version=1")
    assert r.status_code == 409


@pytest.mark.parametrize("change, message", [
    (swap(block=1, k=3, track=1), "the week has no such song"),
    (swap(block=4, k=0, track=1), "the week has no such song"),
    (swap(block=1, k=0, track=1, source="B"), "that song's rule isn't the one the change was made for"),
    (swap(block=1, k=0, track=99999), "track 99999 not found"),
    (rule(new={"source": "Nope", "strategy": "weighted"}), "the changed rule isn't a valid rule"),
    (rule(new={"source": "A", "strategy": "bogus"}), "the changed rule isn't a valid rule"),
])
def test_changes_that_cant_be_made_are_refused(env, change, message):
    client, _, _ = env
    pid = gen(client)["playlist_id"]
    r = add(client, pid, change, 1)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == message
    assert trace(client, pid)["version_no"] == 1


def test_a_song_from_outside_the_stations_pools_is_refused(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    r = add(client, pid, swap(track=track_id(Session, "X001")), 1)
    assert r.status_code == 400
    assert r.json()["detail"] == "that song isn't in any of the station's pools"


@pytest.mark.parametrize("change", [
    {"row": 1, "block": 1, "k": 0, "kind": "swap", "rule_strategy": "w", "rule_source": "A"},
    {"row": 1, "block": 1, "k": 0, "kind": "rule", "rule_strategy": "w", "rule_source": "A"},
    {"row": 1, "block": 1, "k": 0, "kind": "swap", "track_id": 1, "rule": {},
     "rule_strategy": "w", "rule_source": "A"},
    {"row": 0, "block": 1, "k": 0, "kind": "swap", "track_id": 1,
     "rule_strategy": "w", "rule_source": "A"},
    {"row": 1, "block": 1, "k": 0, "kind": "other", "track_id": 1,
     "rule_strategy": "w", "rule_source": "A"},
])
def test_malformed_changes_are_422(env, change):
    client, _, _ = env
    pid = gen(client)["playlist_id"]
    assert add(client, pid, change, 1).status_code == 422


def test_rule_changes_are_capped(env, monkeypatch):
    import api.routers.week_changes as wc
    monkeypatch.setattr(wc, "MAX_RULE_CHANGES", 1)
    client, _, _ = env
    pid = gen(client)["playlist_id"]
    new = {"source": "B", "strategy": "weighted"}
    assert add(client, pid, rule(k=0, new=new), 1).status_code == 201
    # Replacing the one rule change is fine; a second isn't.
    assert add(client, pid, rule(k=0, new=new), 2).status_code == 201
    r = add(client, pid, rule(k=1, new=new), 3)
    assert r.status_code == 400 and "at most 1 rule changes" in r.json()["detail"]


def test_changing_a_week_the_rules_have_moved_on_from_is_refused(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    with Session() as db:
        st = db.query(Station).filter_by(slug=SLUG).one()
        t = copy.deepcopy(st.template)
        t["layout"]["flat_slots"][0]["source"] = "B"
        st.template = t
        db.commit()
    r = add(client, pid, swap(track=track_id(Session, "A030")), 1)
    assert r.status_code == 400
    assert "rules have changed since this week was made" in r.json()["detail"]


def test_unknown_playlist_and_change(env):
    client, _, _ = env
    assert client.get("/api/playlists/999/changes").status_code == 404
    assert add(client, 999, swap(track=1), 1).status_code == 404
    pid = gen(client)["playlist_id"]
    assert client.delete(f"/api/playlists/{pid}/changes/5?expected_version=1").status_code == 404


# ---------- regenerating drops what can't be made -----------------------


def test_regenerating_drops_a_change_whose_rule_changed(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    add(client, pid, swap(block=2, k=1, track=track_id(Session, "B001"), source="B"), 1)
    # The JSON page replaces the template: its rule 2 is now another pool's.
    t = station_template()
    t["layout"]["flat_slots"][1]["source"] = "A"
    r = client.put(f"/api/stations/{SLUG}", json={"slug": SLUG, "name": "Week changes", "template": t})
    assert r.status_code == 200, r.text
    g = gen(client)
    assert g["week_changes_applied"] == 0
    assert [(d["row"], d["block"], d["k"], d["kind"], d["reason"]) for d in g["week_changes_dropped"]] == \
        [(1, 2, 1, "swap", "the rule for that song has changed since")]
    assert client.get(f"/api/playlists/{pid}/changes").json()["changes"] == []
    assert all("change" not in s for s in trace(client, pid)["slots"])


def test_a_deleted_song_drops_its_swap(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    mine = track_id(Session, "A030")
    add(client, pid, swap(track=mine), 1)
    with Session() as db:
        # Deleting the track leaves the stored change with no track
        # (ON DELETE SET NULL).
        db.query(PlaylistEntry).filter_by(track_id=mine).delete()
        db.query(SourcePlaylistEntry).filter_by(track_id=mine).delete()
        db.query(Track).filter_by(id=mine).delete()
        db.commit()
        assert db.query(WeekChange).one().track_id is None
    g = gen(client)
    assert [d["reason"] for d in g["week_changes_dropped"]] == \
        ["the song chosen is no longer in the library"]


# ---------- following the station's rules ------------------------------


def test_changes_follow_moved_rules(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    mine = track_id(Session, "A030")
    add(client, pid, swap(block=1, k=2, track=mine), 1)
    # Move rule 1 (slot 0) to the end.
    r = client.post(f"/api/stations/{SLUG}/sidecar/batch", json={"edits": [
        {"kind": "moveRules", "from": 0, "count": 1, "to": 3, "sectionId": "all-slots"},
    ]})
    assert r.status_code == 200, r.text
    with Session() as db:
        c = db.query(WeekChange).one()
        assert (c.row, c.block, c.k) == (1, 3, 2)
    gen(client)
    slots = trace(client, pid)["slots"]
    assert [s["block"] for s in slots] == [1, 1, 2, 2, 3, 3, 3]
    assert slots[6]["track_id"] == mine and slots[6]["change"]["kind"] == "swap"


def test_changes_of_a_removed_rule_are_deleted(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    add(client, pid, swap(block=2, k=0, track=track_id(Session, "B001"), source="B"), 1)
    add(client, pid, swap(block=3, k=0, track=track_id(Session, "A030")), 2)
    r = client.patch(f"/api/stations/{SLUG}/blocks/structure",
                     json={"edit": {"op": "remove", "slot_index": 1}})
    assert r.status_code == 200, r.text
    with Session() as db:
        assert [(c.block, c.k) for c in db.query(WeekChange)] == [(2, 0)]


def test_changes_past_a_smaller_rule_are_deleted(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    add(client, pid, swap(block=1, k=0, track=track_id(Session, "A029")), 1)
    add(client, pid, swap(block=1, k=2, track=track_id(Session, "A030")), 2)
    r = client.post(f"/api/stations/{SLUG}/sidecar/batch", json={"edits": [
        {"kind": "structuralBlockEdit", "edit": {"op": "size", "slot_index": 0, "new_size": 2}},
        {"kind": "structuralBlockEdit", "edit": {"op": "add", "slot_index": 0,
         "new_block": {"source": "B", "strategy": "weighted", "size": 1}}},
    ]})
    assert r.status_code == 200, r.text
    with Session() as db:
        assert [(c.block, c.k) for c in db.query(WeekChange)] == [(2, 0)]


# ---------- rollback ------------------------------------------------------


def test_rollback_restores_the_versions_changes(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    add(client, pid, swap(track=track_id(Session, "A030")), 1)
    add(client, pid, rule(block=3, k=0, new={"source": "B", "strategy": "weighted"}), 2)
    r = client.post(f"/api/playlists/{pid}/rollback?to=1")
    assert r.status_code == 200, r.text
    assert trace(client, pid)["note"] == "Rolled back to version 1"
    assert client.get(f"/api/playlists/{pid}/changes").json()["changes"] == []
    client.post(f"/api/playlists/{pid}/rollback?to=3")
    got = client.get(f"/api/playlists/{pid}/changes").json()["changes"]
    assert [(c["kind"], c["block"], c["k"], c["position"]) for c in got] == \
        [("swap", 1, 0, 1), ("rule", 3, 0, 6)]
    assert got[1]["rule"] == {"source": "B", "strategy": "weighted"}
    # And regenerating keeps them.
    before = trace(client, pid)["slots"]
    assert gen(client)["week_changes_applied"] == 2
    assert trace(client, pid)["slots"] == before


# ---------- jobs, the chain, mix usage ------------------------------------


def _wait(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "error", "cancelled"):
            return body
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} didn't finish")


def test_generate_job_makes_changes_and_reports_drops(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    mine = track_id(Session, "A030")
    add(client, pid, swap(track=mine), 1)
    add(client, pid, rule(block=3, k=1, new={"source": "B", "strategy": "weighted"}), 2)
    expected = trace(client, pid)["slots"]
    with Session() as db:
        db.add(WeekChange(playlist_id=pid, row=1, block=2, k=5, kind="swap", track_id=mine,
                          rule_strategy="weighted", rule_source="B"))
        db.commit()
    r = client.post(f"/api/stations/{SLUG}/jobs/generate", json={"week": 1, "seed": 7})
    assert r.status_code == 201, r.text
    body = _wait(client, r.json()["job_id"])
    assert body["status"] == "done", body
    assert body["result"]["week_changes_applied"] == 2
    assert [d["reason"] for d in body["result"]["week_changes_dropped"]] == \
        ["that song is no longer in the week"]
    assert trace(client, pid)["slots"] == expected
    with Session() as db:
        assert db.query(WeekChange).count() == 2


def test_preview_job_makes_the_changes_it_is_sent(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    with Session() as db:
        st = db.query(Station).filter_by(slug=SLUG).one()
        template, sidecar = st.template, st.sidecar
    mine = track_id(Session, "A030")
    r = client.post(f"/api/stations/{SLUG}/jobs/preview", json={
        "template": template, "sidecar": sidecar, "week": 1, "seed": 7,
        "week_changes": [swap(block=3, k=0, track=mine), swap(block=9, k=0, track=mine)],
    })
    assert r.status_code == 201, r.text
    body = _wait(client, r.json()["job_id"])
    assert body["status"] == "done", body
    slots = body["result"]["trace"]["slots"]
    assert slots[5]["track_id"] == mine and slots[5]["change"]["kind"] == "swap"
    assert [d["reason"] for d in body["result"]["week_changes_dropped"]] == \
        ["that song is no longer in the week"]
    # Nothing saved.
    assert trace(client, pid)["version_no"] == 1
    # Without changes, the preview is the week as the rules play it.
    r = client.post(f"/api/stations/{SLUG}/jobs/preview", json={
        "template": template, "sidecar": sidecar, "week": 1, "seed": 7,
    })
    body = _wait(client, r.json()["job_id"])
    assert body["result"]["week_changes_dropped"] == []
    assert [s["track_id"] for s in body["result"]["trace"]["slots"]] == \
        [s["track_id"] for s in trace(client, pid)["slots"]]


def test_generate_chain_keeps_changes(env):
    client, Session, _ = env
    pid = gen(client, seed=101)["playlist_id"]
    mine = track_id(Session, "A030")
    add(client, pid, swap(block=1, k=1, track=mine), 1)
    before = trace(client, pid)["slots"]
    with Session() as db:
        pids = generate_chain(db, station_slug=SLUG, weeks=[1, 2], seed_base=100)
    assert pids[1][1] == "A030"
    # The same week again (same seed), the swap made again.
    assert trace(client, pid)["slots"] == before
    with Session() as db:
        assert db.query(WeekChange).count() == 1


def test_mix_usage_follows_the_rules_picks(tmp_path):
    """A swapped mix song isn't recorded as used: the station's rotation
    follows what its rules picked, so later weeks don't shift."""
    from engine.generator import generate
    from engine.persistence import load_pools, save_playlist
    from engine.template import parse_template
    from engine.week_changes import Change, apply_week_changes

    engine = make_engine(f"sqlite:///{tmp_path}/mix.db")
    Base.metadata.create_all(engine)
    Session = make_sessionmaker(engine)
    template = {
        "station": "mx", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "M", "colour": "#0cf", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {"unit": "slot", "unit_labels": ["1"], "row_start_offsets": [0],
                   "row_template": [{"source": "M", "strategy": "mix", "size": 1,
                                     "genre": "House", "exclusion_window": 5}]},
    }
    with Session() as db:
        st = Station(slug="mx", name="Mix", template=template)
        sp = SourcePlaylist(name="M")
        db.add_all([st, sp])
        db.flush()
        tracks = [Track(persistent_id=f"H{i}", name=f"House {i}", genre="House") for i in range(3)]
        db.add_all(tracks)
        db.flush()
        for i, t in enumerate(tracks, start=1):
            db.add(SourcePlaylistEntry(source_playlist_id=sp.id, track_id=t.id, position=i))
        db.commit()
        tmpl = parse_template(template)
        result = generate(tmpl, load_pools(db, tmpl), week=1)
        own = result.picks[0]
        other = next(t for t in tracks if t.id != own.id)
        applied = apply_week_changes(result, [Change(
            row=1, block=1, k=0, kind="swap", rule_strategy="mix", rule_source="M", track=other,
        )], run=lambda *a: None)
        assert applied.result.picks[0].id == other.id
        save_playlist(db, st, applied.result, week=1, template=tmpl)
        db.commit()
        assert [u.track_id for u in db.query(MixUsage)] == [own.id]
        assert [e.track_id for e in db.query(PlaylistEntry)] == [other.id]


# ---------- a row station; a pool only a changed rule plays ----------------


def _row_station(Session, *, with_reference: bool = True) -> None:
    template = {
        "station": "rows", "name": "Rows", "cadence": "weekly", "rng": "on",
        "sources": [
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
            {"name": "R", "colour": "#ccc", "role": "reference"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day", "unit_labels": ["Mon", "Tue"], "row_start_offsets": [0, 5],
            "row_template": [
                {"source": "A", "strategy": "weighted", "size": 2},
                {"source": "B", "strategy": "weighted", "size": 1},
            ],
        },
    }
    with Session() as db:
        if with_reference:
            sp = SourcePlaylist(name="R", role="reference")
            db.add(sp)
            db.flush()
            for i in range(1, 4):
                t = Track(persistent_id=f"R{i:03d}", name=f"Archive {i}", artist="Arch")
                db.add(t)
                db.flush()
                db.add(SourcePlaylistEntry(source_playlist_id=sp.id, position=i, track_id=t.id))
        db.add(Station(slug="rows", name="Rows", template=template,
                       sidecar=synthesize_trivial_sidecar(template)))
        db.commit()


def test_row_station_rule_change_is_made_in_its_row_only(env):
    client, Session, _ = env
    _row_station(Session)
    r = client.post("/api/stations/rows/generate", json={"week": 1, "seed": 3})
    pid = r.json()["playlist_id"]
    v1 = trace(client, pid)
    assert [(s["row"], s["block"], s["unit"]) for s in v1["slots"]] == \
        [(1, 1, "Mon"), (1, 1, "Mon"), (1, 2, "Mon"), (2, 1, "Tue"), (2, 1, "Tue"), (2, 2, "Tue")]
    # Tue's second A song from the reference pool R, which no rule of the
    # station plays (so it isn't loaded for a generation).
    new = {"source": "R", "strategy": "archive_ref", "position": 2}
    r = add(client, pid, rule(row=2, block=1, k=1, new=new), 1)
    assert r.status_code == 201, r.text
    slot = r.json()["slot"]
    # archive_ref reads from `position`, one song per place: k=1 is 3.
    assert slot["track_name"] == "Archive 3" and slot["change"]["strategy"] == "archive_ref"
    assert trace(client, pid)["note"] == "Just this week: Tue song 5 picked by a changed rule"
    slots = trace(client, pid)["slots"]
    assert [s["track_id"] for i, s in enumerate(slots) if i != 4] == \
        [s["track_id"] for i, s in enumerate(v1["slots"]) if i != 4]
    # Regenerating keeps it; the rules' own week is unchanged.
    g = client.post("/api/stations/rows/generate", json={"week": 1, "seed": 3}).json()
    assert g["week_changes_applied"] == 1
    assert trace(client, pid)["slots"] == slots
    # A swap in Mon only.
    mine = track_id(Session, "B015")
    r = add(client, pid, swap(row=1, block=2, k=0, track=mine, source="B"), 3)
    assert r.status_code == 201
    assert [s["track_id"] == mine for s in trace(client, pid)["slots"]].index(True) == 2


def test_a_rule_whose_pool_isnt_imported_is_refused_and_dropped(env):
    client, Session, _ = env
    _row_station(Session, with_reference=False)
    pid = client.post("/api/stations/rows/generate", json={"week": 1, "seed": 3}).json()["playlist_id"]
    new = {"source": "R", "strategy": "archive_ref", "position": 2}
    r = add(client, pid, rule(row=1, block=1, k=0, new=new), 1)
    assert r.status_code == 400
    assert r.json()["detail"].startswith("the changed rule can't be played: ")
    # Stored anyway (an older version made it), it's dropped at generation.
    with Session() as db:
        db.add(WeekChange(playlist_id=pid, row=1, block=1, k=0, kind="rule", rule=new,
                          rule_strategy="weighted", rule_source="A"))
        db.commit()
    g = client.post("/api/stations/rows/generate", json={"week": 1, "seed": 3}).json()
    assert len(g["week_changes_dropped"]) == 1
    assert g["week_changes_dropped"][0]["reason"].startswith("the changed rule can't be played: ")


def test_undoing_a_change_the_week_doesnt_have_now_only_forgets_it(env):
    client, Session, _ = env
    pid = gen(client)["playlist_id"]
    c = add(client, pid, swap(block=1, k=2, track=track_id(Session, "A030")), 1).json()
    client.post(f"/api/stations/{SLUG}/sidecar/batch", json={"edits": [
        {"kind": "moveRules", "from": 0, "count": 1, "to": 3, "sectionId": "all-slots"},
    ]})
    # The change follows rule 1 to place 3; the week (not regenerated)
    # still has it at place 1.
    listed = client.get(f"/api/playlists/{pid}/changes").json()["changes"]
    assert [(x["block"], x["position"]) for x in listed] == [(3, None)]
    r = client.delete(f"/api/playlists/{pid}/changes/{c['change']['id']}?expected_version=2")
    assert r.status_code == 200 and r.json()["version_no"] == 2
    assert client.get(f"/api/playlists/{pid}/changes").json()["changes"] == []
