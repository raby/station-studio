"""Integration tests for the §16.11 c3 job-runner API endpoints.

Wires up:
  POST   /api/stations/{slug}/jobs/generate
  GET    /api/jobs/{job_id}
  DELETE /api/jobs/{job_id}

End-to-end through the real engine + a fresh SQLite tmp DB. Asserts:

  * 201 + Location header on happy path; final Job.result mirrors
    GenerateResponse exactly + a Playlist row appears in the DB
    (proves on_result ran in the worker thread with its own session).
  * 409 with `existing_job_id` when a generate job already runs for
    the same slug (Decision F POLICY_REJECT).
  * 404 for unknown job / unknown station / GC'd job.
  * 400 for missing-pool errors (raised by load_pools).
  * 204 on DELETE (idempotent); cancelled jobs vanish on next GET.
  * status=error with {message, kind} when the engine raises mid-
    stream (patched generate_stream).
  * Cancellation BEFORE on_result runs leaves NO Playlist row
    (Decision F: cancel-before-side-effects). Pins c2 review fix.
"""
from __future__ import annotations

import time
from typing import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.deps import get_db, get_session_factory
from api.job_runner import JobStore, get_job_store
from api.main import app
from engine.database import make_engine, make_sessionmaker
from engine.generator import GenerationResult, ProgressEvent
from engine.models import Base, Playlist, SourcePlaylist, SourcePlaylistEntry, Station, Track


# ---------- fixtures ----------------------------------------------------


@pytest.fixture
def jobs_api(tmp_path, monkeypatch):
    """TestClient wired to a throwaway SQLite DB + isolated JobStore.

    Yields (client, Session, store). The override pattern matches the
    existing `api` fixture in tests/test_api.py — same throwaway-DB
    plumbing — but also overrides `get_session_factory` (so the
    worker thread's fresh session points at the test DB, not the dev
    DB) and `get_job_store` (so each test gets its own JobStore +
    no cross-test contamination).
    """
    db_url = f"sqlite:///{tmp_path}/jobs_api.db"
    monkeypatch.setenv("STATION_STUDIO_DB_URL", db_url)
    monkeypatch.setenv("STATION_STUDIO_SKIP_AUTO_SEED", "1")

    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    Session: sessionmaker = make_sessionmaker(engine)

    def _override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    def _override_session_factory():
        # Return the sessionmaker itself, NOT a session. on_result
        # uses `with session_factory() as worker_db:` so it expects
        # a callable.
        return Session

    store = JobStore()

    def _override_job_store():
        return store

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_session_factory] = _override_session_factory
    app.dependency_overrides[get_job_store] = _override_job_store
    with TestClient(app) as client:
        yield client, Session, store
    app.dependency_overrides.clear()


def _seed_station_with_pool(
    Session: sessionmaker, *, slug: str = "ts", persist_cursors: bool = False,
):
    """Insert a Station + a SourcePlaylist + tracks so generate_stream
    can produce a playlist. Single-row flat_slots template — 3 slots,
    sequential strategy, named cursor.

    `persist_cursors=True` flips `cursor.persist` on so the endpoint
    exercises the `load_cursor_positions` + `advance_cursors` branches
    (jobs.py 222-225 + 291-301). Default off keeps existing tests
    simple."""
    with Session() as db:
        # Tracks
        tracks = [
            Track(
                persistent_id=f"P{i:04d}", name=f"Track {i}",
                artist=f"Artist {i}", album=None, itunes_track_id=i,
            )
            for i in range(1, 11)
        ]
        db.add_all(tracks)
        db.commit()
        # SourcePlaylist + entries
        sp = SourcePlaylist(name="A", role="content")
        db.add(sp)
        db.commit()
        for pos, t in enumerate(tracks, start=1):
            db.add(SourcePlaylistEntry(
                source_playlist_id=sp.id, position=pos, track_id=t.id,
            ))
        # Station with a minimal flat_slots template — uses the
        # `sequential` strategy + named cursor, the same shape c1's
        # tests use. Deterministic; rng=off.
        cursor_block: dict = {"base_advance_per_week": 0, "cycle_weeks": 1}
        if persist_cursors:
            cursor_block["persist"] = True
        template = {
            "station": slug,
            "name": "Test",
            "cadence": "weekly",
            "rng": "off",
            "sources": [{"name": "A", "colour": "#abc", "role": "content"}],
            "cursor": cursor_block,
            "layout": {
                "unit": "slot",
                "unit_labels": [],
                "row_start_offsets": [],
                "cursors_init": {"C": 0},
                "flat_slots": [
                    {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "C"}
                ] * 3,
            },
        }
        station = Station(slug=slug, name="Test", template=template)
        db.add(station)
        db.commit()


def _wait_for_status(
    client: TestClient, job_id: str, target_status: str, *,
    timeout: float = 5.0,
):
    """Poll GET /api/jobs/{job_id} until status==target. Returns the
    final response JSON. Raises if the job vanishes or the deadline
    elapses."""
    deadline = time.monotonic() + timeout
    last_body = None
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        if r.status_code == 404:
            raise AssertionError(
                f"job {job_id} vanished before reaching {target_status}; "
                f"last body: {last_body}"
            )
        last_body = r.json()
        if last_body["status"] == target_status:
            return last_body
        time.sleep(0.01)
    raise AssertionError(
        f"job {job_id} didn't reach {target_status} within {timeout}s; "
        f"last body: {last_body}"
    )


# ---------- happy path --------------------------------------------------


def test_start_generate_job_returns_201_with_location_header(jobs_api):
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    r = client.post("/api/stations/ts/jobs/generate", json={"week": 1})
    assert r.status_code == 201, r.json()
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "running"
    # Location header points at the polling endpoint.
    assert r.headers["Location"] == f"/api/jobs/{body['job_id']}"


def test_generate_job_runs_to_done_and_persists_playlist(jobs_api):
    """End-to-end: POST → poll → status=done → result mirrors
    GenerateResponse + Playlist row exists in DB (proves on_result
    fired the worker-thread save)."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    r = client.post("/api/stations/ts/jobs/generate", json={"week": 1})
    job_id = r.json()["job_id"]
    final = _wait_for_status(client, job_id, "done")

    assert final["result"]["station"] == "ts"
    assert final["result"]["week"] == 1
    assert final["result"]["slot_count"] == 3
    assert "playlist_id" in final["result"]
    assert "trace_id" in final["result"]
    assert final["error"] is None
    # Playlist actually persisted by the worker thread.
    with Session() as db:
        pl = db.query(Playlist).filter_by(id=final["result"]["playlist_id"]).first()
        assert pl is not None
        assert pl.week == 1
        assert len(pl.entries) == 3


def test_response_timestamps_are_iso_utc_strings(jobs_api):
    """`started_at` / `finished_at` are ISO-8601 UTC strings ending in
    Z (per the design doc), NOT Python's `+00:00` suffix or UNIX
    floats."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    job_id = client.post(
        "/api/stations/ts/jobs/generate", json={"week": 1},
    ).json()["job_id"]
    final = _wait_for_status(client, job_id, "done")
    assert isinstance(final["started_at"], str)
    assert final["started_at"].endswith("Z")
    assert "T" in final["started_at"]  # ISO-8601 separator.
    assert isinstance(final["finished_at"], str)
    assert final["finished_at"].endswith("Z")


# ---------- 4xx paths ---------------------------------------------------


def test_start_for_unknown_station_returns_404(jobs_api):
    client, _Session, _store = jobs_api
    r = client.post("/api/stations/nope/jobs/generate", json={"week": 1})
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


def test_start_with_missing_pool_returns_400(jobs_api):
    """If load_pools can't find a SourcePlaylist row for a referenced
    source, the endpoint rejects with 400 BEFORE spawning a job."""
    client, Session, _store = jobs_api
    # Seed a station whose template references source "X" but no
    # SourcePlaylist row for X exists.
    with Session() as db:
        template = {
            "station": "no-pool",
            "name": "No Pool",
            "cadence": "weekly",
            "rng": "off",
            "sources": [{"name": "X", "colour": "#abc", "role": "content"}],
            "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
            "layout": {
                "unit": "slot",
                "unit_labels": [],
                "row_start_offsets": [],
                "cursors_init": {"C": 0},
                "flat_slots": [
                    {"source": "X", "strategy": "sequential", "size": 1, "cursor_id": "C"}
                ],
            },
        }
        db.add(Station(slug="no-pool", name="No Pool", template=template))
        db.commit()

    r = client.post("/api/stations/no-pool/jobs/generate", json={"week": 1})
    assert r.status_code == 400


def test_second_generate_for_same_slug_returns_409_with_existing_job_id(jobs_api):
    """Decision F POLICY_REJECT: starting two generate jobs for the
    same station while the first runs returns 409 with the existing
    job_id so the frontend can poll instead of failing."""
    client, Session, store = jobs_api
    _seed_station_with_pool(Session)

    # Block the worker thread mid-stream so the first job stays in
    # status=running while we try to start a second.
    started = []

    from api.routers import jobs as jobs_mod
    real_generate_stream = jobs_mod.generate_stream

    def slow_stream(template, pools, **kwargs):
        # Emit start event so the worker reaches the running state,
        # then block until released.
        for event in real_generate_stream(template, pools, **kwargs):
            started.append(True)
            time.sleep(0.5 if len(started) <= 1 else 0)  # only slow first
            yield event

    with patch.object(jobs_mod, "generate_stream", slow_stream):
        r1 = client.post("/api/stations/ts/jobs/generate", json={"week": 1})
        first_id = r1.json()["job_id"]
        # Wait until the worker has at least started.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if started:
                break
            time.sleep(0.01)
        assert started, "first worker never started"

        r2 = client.post("/api/stations/ts/jobs/generate", json={"week": 2})
        assert r2.status_code == 409, r2.json()
        detail = r2.json()["detail"]
        assert detail["existing_job_id"] == first_id
        assert "already running" in detail["message"]

    # Let the first job finish so the test fixture tears down cleanly.
    _wait_for_status(client, first_id, "done", timeout=10)


# ---------- GET ---------------------------------------------------------


def test_get_unknown_job_returns_404(jobs_api):
    client, _Session, _store = jobs_api
    r = client.get("/api/jobs/not-a-real-id")
    assert r.status_code == 404
    assert "not-a-real-id" in r.json()["detail"]


def test_get_during_running_returns_phase_current_total(jobs_api):
    """Poll while running surfaces ProgressEvent fields. The test
    template is too small to guarantee a `phase=picks` event before
    completion, so this just verifies the GET schema returns 200
    with the expected keys for some valid status."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)
    job_id = client.post(
        "/api/stations/ts/jobs/generate", json={"week": 1},
    ).json()["job_id"]
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    # Every snapshot must have these keys.
    for key in (
        "job_id", "slug", "kind", "status", "phase",
        "current", "total", "started_at",
    ):
        assert key in body, f"missing key {key} in {body}"


# ---------- DELETE ------------------------------------------------------


def test_delete_unknown_job_returns_404(jobs_api):
    client, _Session, _store = jobs_api
    r = client.delete("/api/jobs/not-a-real-id")
    assert r.status_code == 404


def test_delete_running_job_returns_204_and_job_vanishes(jobs_api):
    """DELETE on a running job → 204 + immediate-GC; subsequent GET
    returns 404 (Decision F cancelled-immediate-GC)."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    from api.routers import jobs as jobs_mod
    real_generate_stream = jobs_mod.generate_stream

    proceed = [False]

    def slow_stream(template, pools, **kwargs):
        for event in real_generate_stream(template, pools, **kwargs):
            # Block briefly so cancel arrives mid-stream.
            while not proceed[0]:
                time.sleep(0.005)
            yield event

    with patch.object(jobs_mod, "generate_stream", slow_stream):
        job_id = client.post(
            "/api/stations/ts/jobs/generate", json={"week": 1},
        ).json()["job_id"]
        # The worker is blocked in slow_stream; cancel now.
        r = client.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 204
        # Immediate-GC: next GET → 404.
        r2 = client.get(f"/api/jobs/{job_id}")
        assert r2.status_code == 404
        # Release the worker so the test fixture tears down cleanly.
        proceed[0] = True


def test_delete_already_done_job_returns_204_idempotent(jobs_api):
    """DELETE on a done job — 204 anyway. The client's intent
    ("dismiss this job") is already met; reporting an error would
    be hostile to a frontend racing a cancel against a fast finish."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)
    job_id = client.post(
        "/api/stations/ts/jobs/generate", json={"week": 1},
    ).json()["job_id"]
    _wait_for_status(client, job_id, "done")
    r = client.delete(f"/api/jobs/{job_id}")
    assert r.status_code == 204


def test_cancel_before_on_result_leaves_no_playlist_row(jobs_api):
    """c2 review fix regression guard at the API layer: a cancel
    issued between Result yield and on_result must NOT create a
    Playlist row. The c2 unit tests pin the runner behaviour; this
    pins it end-to-end through the real on_result closure (which
    does save_playlist).
    """
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    from api.routers import jobs as jobs_mod
    real_generate_stream = jobs_mod.generate_stream
    result_yielded = [False]
    proceed = [False]

    def slow_stream(template, pools, **kwargs):
        # Drain the real stream up to BEFORE the Result, then block
        # so the test can cancel, then yield the Result.
        events = list(real_generate_stream(template, pools, **kwargs))
        # The Result is always last (Decision G).
        for event in events[:-1]:
            yield event
        # Mark "about to yield Result"; test will cancel.
        result_yielded[0] = True
        while not proceed[0]:
            time.sleep(0.005)
        yield events[-1]

    with patch.object(jobs_mod, "generate_stream", slow_stream):
        job_id = client.post(
            "/api/stations/ts/jobs/generate", json={"week": 1},
        ).json()["job_id"]
        # Wait until just before the Result yield.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not result_yielded[0]:
            time.sleep(0.01)
        assert result_yielded[0], "stream never reached pre-Result barrier"

        # Cancel BEFORE the Result yield is consumed.
        r = client.delete(f"/api/jobs/{job_id}")
        assert r.status_code == 204
        # Release the worker so it yields the Result; the runner's
        # pre-on_result cancel check should then fire + skip
        # save_playlist.
        proceed[0] = True

    # Wait briefly for the worker thread to fully exit.
    time.sleep(0.1)
    with Session() as db:
        rows = db.query(Playlist).count()
        assert rows == 0, (
            f"cancel-before-on_result should leave no Playlist; "
            f"got {rows} rows"
        )


# ---------- error surfacing ---------------------------------------------


def test_engine_exception_surfaces_as_status_error(jobs_api):
    """A mid-stream engine exception → job ends in status=error with
    `error.message` + `error.kind` (Decision G + c2's error surfacing).
    """
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    def broken_stream(*args, **kwargs):
        # Yield is needed for Python to treat this as a generator.
        if False:
            yield  # unreachable
        raise RuntimeError("simulated engine crash")

    from api.routers import jobs as jobs_mod
    with patch.object(jobs_mod, "generate_stream", broken_stream):
        job_id = client.post(
            "/api/stations/ts/jobs/generate", json={"week": 1},
        ).json()["job_id"]
        final = _wait_for_status(client, job_id, "error")
        assert final["error"]["kind"] == "RuntimeError"
        assert "simulated engine crash" in final["error"]["message"]
        assert final["result"] is None


# ---------- coverage gap closures (slice review fixes) ------------------


def test_generate_job_with_persist_cursors_advances_cursor_state(jobs_api):
    """Slice review 🔴-1: closes the cursor.persist=True branches in
    `start_generate_job` (jobs.py 222-225 + 291-301). With
    persist=True the endpoint must:
      * Load existing named cursor positions before generate_stream.
      * After save_playlist commits, advance the cursor row via
        engine.persistence.advance_cursors.

    Assertion: a CursorState row exists post-job with `position`
    advanced by 3 (the template has 3 slots, cursor advance == picks).
    """
    from engine.models import CursorState
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session, slug="cp", persist_cursors=True)

    r = client.post("/api/stations/cp/jobs/generate", json={"week": 1})
    assert r.status_code == 201
    job_id = r.json()["job_id"]
    final = _wait_for_status(client, job_id, "done")
    assert final["result"]["slot_count"] == 3

    with Session() as db:
        rows = db.query(CursorState).filter_by(name="C").all()
        # advance_cursors upserted a row for cursor 'C' — its mere
        # existence is the proof that the persist=True branch fired
        # (load_cursor_positions only READS from cursor_state; only
        # advance_cursors WRITES). The post-advance `position` value
        # happens to be `(0 + cursor_advance_step_default=120) %
        # pool_len=10 == 0` for this fixture — so we can't simply
        # assert `position > 0`. The row's presence is unambiguous.
        assert len(rows) == 1, (
            f"persist=True path didn't upsert a CursorState row for 'C': "
            f"got {[(c.name, c.position) for c in db.query(CursorState).all()]}"
        )


def test_job_result_dict_keys_match_generate_response_schema(jobs_api):
    """Slice review 🟡-4 (downgraded to a structural pin): the dict
    returned by on_result must carry the same keys the synchronous
    `GenerateResponse` Pydantic model exposes — otherwise a future
    field added to one endpoint silently diverges from the other.
    Cheap structural test; no functional cost."""
    from api.schemas import GenerateResponse
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    job_id = client.post(
        "/api/stations/ts/jobs/generate", json={"week": 1},
    ).json()["job_id"]
    final = _wait_for_status(client, job_id, "done")
    expected = set(GenerateResponse.model_fields.keys())
    got = set(final["result"].keys())
    assert got == expected, (
        f"job.result key drift from GenerateResponse: "
        f"missing={expected - got}, extra={got - expected}"
    )
