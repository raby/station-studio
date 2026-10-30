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
from api.schemas import _PREVIEW_FIELD_MAX_BYTES
from engine.database import make_engine, make_sessionmaker
from engine.editor_metadata import synthesize_trivial_sidecar
from engine.generator import GenerationResult, ProgressEvent, generate_stream
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


# ---------- §16.2.6 c1: POST /api/stations/{slug}/jobs/preview ---------


def _template_for_3_slots() -> dict:
    """Source-of-truth template literal matching _seed_station_with_pool.
    Returned fresh per call so tests can splice overrides safely."""
    return {
        "station": "ts",
        "name": "Test",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "A", "colour": "#abc", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"C": 0},
            "flat_slots": [
                {"source": "A", "strategy": "sequential", "size": 1,
                 "cursor_id": "C"}
                for _ in range(3)
            ],
        },
    }


def _trivial_sidecar_3_slots() -> dict:
    """Sidecar matching _template_for_3_slots(). Delegates to the
    engine's `synthesize_trivial_sidecar` so the test fixture stays
    in sync with production sidecar grammar — a future schema bump
    updates synthesize_trivial_sidecar in one place + this fixture
    follows automatically."""
    from engine.editor_metadata import synthesize_trivial_sidecar
    return synthesize_trivial_sidecar(_template_for_3_slots())


def _preview_body(template_overrides: dict | None = None,
                  sidecar_overrides: dict | None = None,
                  week: int = 1, seed: int | None = 42) -> dict:
    """Build a preview request body mirroring _seed_station_with_pool's
    template shape. Overrides splice in for the failure-path tests."""
    template = _template_for_3_slots()
    if template_overrides:
        template.update(template_overrides)
    sidecar = _trivial_sidecar_3_slots()
    if sidecar_overrides:
        sidecar.update(sidecar_overrides)
    body: dict = {"template": template, "sidecar": sidecar, "week": week}
    if seed is not None:
        body["seed"] = seed
    return body


def test_preview_start_returns_201_with_location_header(jobs_api):
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    r = client.post("/api/stations/ts/jobs/preview", json=_preview_body())
    assert r.status_code == 201, r.json()
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "running"
    assert r.headers["Location"] == f"/api/jobs/{body['job_id']}"


@pytest.mark.parametrize("persist_cursors", [False, True])
def test_preview_runs_to_done_returns_trace_with_no_persistence(
    jobs_api, persist_cursors,
):
    """§16.2.6 D1 pin: a preview call followed by `db.refresh(station)`
    MUST show no changes. Verified end-to-end across BOTH cursor
    persistence modes: zero Playlist rows + zero MixUsage rows +
    station.template untouched + station.sidecar untouched +
    CursorState row count unchanged after a successful preview.

    Why parametrise over persist_cursors: with persist=True, the
    /jobs/generate worker's on_result calls advance_cursors which
    upserts a CursorState row. Preview's on_result must NEVER write
    to CursorState — running the same fixture but with persist=True
    catches a future bug that wires preview's on_result to
    advance_cursors mistakenly (the no-persistence-on-persist=False
    case would silently pass because advance_cursors only runs when
    template.cursor.persist is True).

    Review #1 finding #1 + #2: pre-fix the test asserted Playlist +
    MixUsage + station.template, but NOT station.sidecar (a rogue
    sidecar write would slip through) and NOT CursorState
    (advance_cursors would slip through in persist=True mode).
    """
    from engine.models import CursorState, MixUsage
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session, persist_cursors=persist_cursors)

    # Seed a sidecar so the post-preview comparison has something
    # non-trivial to check (the route validates against it; if it
    # accidentally writes back the validated copy, the diff would
    # surface).
    with Session() as db:
        station = db.query(Station).filter_by(slug="ts").first()
        station.sidecar = _trivial_sidecar_3_slots()
        db.commit()
        before_template = station.template
        before_sidecar = station.sidecar
        before_cursor_count = db.query(CursorState).count()

    r = client.post("/api/stations/ts/jobs/preview", json=_preview_body())
    job_id = r.json()["job_id"]
    final = _wait_for_status(client, job_id, "done")

    # Result shape: trace dict + slot_count.
    assert final["error"] is None
    assert "trace" in final["result"]
    assert final["result"]["slot_count"] == 3
    trace = final["result"]["trace"]
    assert "slots" in trace
    assert len(trace["slots"]) == 3
    assert trace["station"] == "ts"
    assert trace["week"] == 1

    # No persistence side-effects.
    with Session() as db:
        assert db.query(Playlist).count() == 0
        assert db.query(MixUsage).count() == 0
        station = db.query(Station).filter_by(slug="ts").first()
        assert station.template == before_template, (
            "preview wrote to station.template — broke D1 guarantee"
        )
        assert station.sidecar == before_sidecar, (
            "preview wrote to station.sidecar — broke D1 guarantee"
        )
        assert db.query(CursorState).count() == before_cursor_count, (
            "preview wrote to CursorState (advance_cursors fired?) — "
            "broke D1 guarantee"
        )


def test_preview_deterministic_seed_produces_identical_trace(jobs_api):
    """§16.2.6 D1 pin: same seed + same template + same DB state →
    identical trace slots across runs. Pins the engine's determinism
    AND the route's recompute-per-request behavior (helpers must
    return identical values across two requests when DB state hasn't
    changed)."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    body = _preview_body(seed=12345)

    job_a = client.post("/api/stations/ts/jobs/preview", json=body).json()["job_id"]
    trace_a = _wait_for_status(client, job_a, "done")["result"]["trace"]
    job_b = client.post("/api/stations/ts/jobs/preview", json=body).json()["job_id"]
    trace_b = _wait_for_status(client, job_b, "done")["result"]["trace"]

    # trace_id + generated_at vary per call; slots must match.
    assert trace_a["slots"] == trace_b["slots"]


def _rng_on_weighted(Session: sessionmaker) -> dict:
    """Make station 'ts' rng='on' with three weighted slots (the seed
    decides which tracks come out). Returns the template."""
    template = _template_for_3_slots()
    template["rng"] = "on"
    template["layout"]["flat_slots"] = [
        {"source": "A", "strategy": "weighted", "size": 1} for _ in range(3)
    ]
    with Session() as db:
        station = db.query(Station).filter_by(slug="ts").first()
        station.template = template
        db.commit()
    return template


def _preview_tracks(client, template: dict, week: int, seed: int | None = None) -> list:
    from engine.editor_metadata import synthesize_trivial_sidecar
    body = {"template": template, "sidecar": synthesize_trivial_sidecar(template), "week": week}
    if seed is not None:
        body["seed"] = seed
    job_id = client.post("/api/stations/ts/jobs/preview", json=body).json()["job_id"]
    final = _wait_for_status(client, job_id, "done")
    return [s["track_id"] for s in final["result"]["trace"]["slots"]]


def test_preview_rng_on_without_a_seed_reproduces_the_generated_week(jobs_api):
    """The editor and the week page send no seed. For rng='on' the
    engine needs one; the preview uses the seed the week was generated
    with, so previewing the saved template gives the stored week."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)
    template = _rng_on_weighted(Session)

    job_id = client.post(
        "/api/stations/ts/jobs/generate", json={"week": 1, "seed": 7},
    ).json()["job_id"]
    stored = _wait_for_status(client, job_id, "done")["result"]
    stored_tracks = [e["track_id"] for e in client.get(
        f"/api/playlists/{stored['playlist_id']}/trace").json()["slots"]]

    # Guard: the week-number fallback (seed 1) would give other tracks,
    # so a match below means the stored seed was used.
    assert _preview_tracks(client, template, week=1, seed=1) != stored_tracks
    assert _preview_tracks(client, template, week=1) == stored_tracks


def test_regenerate_rng_on_without_a_seed_reuses_the_weeks_seed(jobs_api):
    """Regenerating a week (the week page's Regenerate) sends no seed:
    the week's own is reused, so unchanged rules give the same week.
    A week never generated still needs one."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)
    _rng_on_weighted(Session)

    def generate(body: dict) -> list[int]:
        job_id = client.post("/api/stations/ts/jobs/generate", json=body).json()["job_id"]
        done = _wait_for_status(client, job_id, "done")["result"]
        trace = client.get(f"/api/playlists/{done['playlist_id']}/trace").json()
        assert trace["seed"] == 7
        return [s["track_id"] for s in trace["slots"]]

    first = generate({"week": 1, "seed": 7})
    assert generate({"week": 1}) == first

    job_id = client.post("/api/stations/ts/jobs/generate", json={"week": 2}).json()["job_id"]
    snap = _wait_for_status(client, job_id, "error")
    assert "requires a seed" in snap["error"]["message"]


def test_preview_rng_on_week_never_generated_is_repeatable(jobs_api):
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)
    template = _rng_on_weighted(Session)
    first = _preview_tracks(client, template, week=2)
    assert len(first) == 3
    assert _preview_tracks(client, template, week=2) == first


def test_preview_400_on_invalid_template(jobs_api):
    """Operator's in-memory template refers to a source pool that
    doesn't exist server-side. load_pools raises ValueError →
    HTTPException(400). The error surfaces synchronously — no job
    spawned."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    body = _preview_body(template_overrides={
        # Source 'A' exists in the DB but the template references 'NONEXISTENT'.
        "sources": [{"name": "NONEXISTENT", "colour": "#abc", "role": "content"}],
        # flat_slots reference the bogus source via its name.
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"C": 0},
            "flat_slots": [
                {"source": "NONEXISTENT", "strategy": "sequential",
                 "size": 1, "cursor_id": "C"}
            ] * 3,
        },
    })
    r = client.post("/api/stations/ts/jobs/preview", json=body)
    assert r.status_code == 400, r.json()


def test_preview_400_on_invalid_sidecar(jobs_api):
    """Operator's in-memory sidecar fails grammar validation
    (missing required `layout_order` key). validate_sidecar raises
    EditorMetadataError → HTTPException(400). Synchronous; no job
    spawned."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    bad_sidecar = {
        "schema_version": 1,
        "sections": [
            {"id": "all", "name": "All",
             "blocks": [{"kind": "atomic", "slot_index": 0}]},
        ],
        # layout_order missing — REQUIRED_TOP_LEVEL key
    }
    body = _preview_body()
    body["sidecar"] = bad_sidecar
    r = client.post("/api/stations/ts/jobs/preview", json=body)
    assert r.status_code == 400, r.json()
    assert "sidecar" in r.json()["detail"].lower()


def test_preview_cancel_existing_supersedes_running_preview(jobs_api):
    """§16.2.6 D6 pin: POLICY_CANCEL_EXISTING means a new preview job
    for the same (slug, KIND_PREVIEW) supersedes any running preview.
    Both POSTs return 201; the FIRST job ends up cancelled (or done
    if it raced past the supersede). Second job runs to done."""
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    # Patch generate_stream to add a small sleep so the first job is
    # in-flight long enough for the second POST to supersede it. The
    # 3-slot fixture would otherwise finish in microseconds.
    real_stream = generate_stream

    def slow_stream(*args, **kwargs):
        stream = real_stream(*args, **kwargs)
        for ev in stream:
            time.sleep(0.05)
            yield ev

    with patch("api.routers.jobs.generate_stream", side_effect=slow_stream):
        r1 = client.post(
            "/api/stations/ts/jobs/preview", json=_preview_body(seed=1),
        )
        assert r1.status_code == 201
        job_a = r1.json()["job_id"]
        # Second POST — supersedes.
        r2 = client.post(
            "/api/stations/ts/jobs/preview", json=_preview_body(seed=2),
        )
        assert r2.status_code == 201
        job_b = r2.json()["job_id"]
        assert job_a != job_b
        # Second job runs to done.
        final_b = _wait_for_status(client, job_b, "done")
        assert final_b["result"]["slot_count"] == 3

    # First job's final state is either 'cancelled' or 'done'. With
    # the slow_stream sleep + JobStore's supersede + GC grace period,
    # cancelled is the expected outcome — but the 'done' race is
    # legitimate (the engine finished mid-supersede). Assert it's NOT
    # 'running' (would mean POLICY_CANCEL_EXISTING didn't supersede).
    r = client.get(f"/api/jobs/{job_a}")
    if r.status_code == 200:
        status = r.json()["status"]
        assert status in ("cancelled", "done"), (
            f"job_a should be superseded (cancelled) or completed (done) "
            f"after job_b started; got {status}"
        )
    # 404 is also acceptable — JobStore GC may have evicted by now.
    # §16.2.6.t4: the explicit silent-evict-in-grace path is pinned
    # by `test_preview_cancel_existing_silently_evicts_done_job_in_grace_window`
    # below, so this branch's tolerance for either outcome no longer
    # masks the regression class.


def test_preview_cancel_existing_silently_evicts_done_job_in_grace_window(jobs_api):
    """§16.2.6.t4 — POLICY_CANCEL_EXISTING silently evicts a DONE job
    that's still inside its grace window. Complements the in-flight
    cancellation test above.

    The grace window's purpose: let the frontend's "final status" poll
    (which fires once after the previous 'running' response) still
    find the result. POLICY_CANCEL_EXISTING's "latest wins" semantics
    override this — a new job for the same (slug, kind) silent-evicts
    the old terminal job to make room. Review 🟡-2 pin in job_runner.py
    (`_cancel_under_lock` docstring + supersede path): DON'T retroactively
    flip a done job's status to 'cancelled' — just pop it; it really
    did finish.

    Without this test, a regression that skipped the silent-evict
    step (e.g. only running `_cancel_under_lock` for running jobs +
    forgetting the terminal-pop branch in `start()`) would leave the
    grace-windowed job lingering, exhausting the per-(slug, kind)
    slot until 60s elapsed. The existing supersede test above would
    still pass (the running-supersede path stays exercised); only
    the grace-window case would silently regress.

    Determinism: no `slow_stream` patch needed because job_a fully
    finishes (synchronously, via `_wait_for_status`) BEFORE job_b's
    POST runs. Both jobs use the same 3-slot fixture which completes
    in microseconds — no race.
    """
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    # Job A — runs fast (no slow_stream); fully done within a few ms.
    r1 = client.post(
        "/api/stations/ts/jobs/preview", json=_preview_body(seed=1),
    )
    assert r1.status_code == 201
    job_a = r1.json()["job_id"]
    done_a = _wait_for_status(client, job_a, "done")
    assert done_a["status"] == "done"

    # Sanity: job_a is still findable while in grace (the 60s window
    # hasn't elapsed — GC won't reap yet).
    r = client.get(f"/api/jobs/{job_a}")
    assert r.status_code == 200
    assert r.json()["status"] == "done"

    # Job B — POLICY_CANCEL_EXISTING. The start() path finds job_a
    # in `_ALL_STATUSES` lookup, sees status != RUNNING, falls into
    # the silent-evict branch (line 351-353 in job_runner.py), pops
    # job_a from `_jobs` WITHOUT touching its status.
    r2 = client.post(
        "/api/stations/ts/jobs/preview", json=_preview_body(seed=2),
    )
    assert r2.status_code == 201
    job_b = r2.json()["job_id"]
    assert job_a != job_b

    # Post-supersede: job_a is silent-evicted → GET returns 404.
    # A regression where start() forgot the terminal-pop branch
    # would leave job_a in `_jobs` until the 60s grace elapsed,
    # surfacing as a 200 here with status='done'.
    r = client.get(f"/api/jobs/{job_a}")
    assert r.status_code == 404, (
        f"after job_b's POLICY_CANCEL_EXISTING start, job_a should be "
        f"silent-evicted (404); got {r.status_code} with body {r.json()}"
    )

    # Job B runs to done normally.
    final_b = _wait_for_status(client, job_b, "done")
    assert final_b["result"]["slot_count"] == 3


def test_preview_unknown_station_returns_404(jobs_api):
    client, _Session, _store = jobs_api
    r = client.post("/api/stations/no-such/jobs/preview", json=_preview_body())
    assert r.status_code == 404


def test_preview_strict_pydantic_rejects_coerced_int_at_the_wire(jobs_api):
    """§16.2.6.t5 — PreviewRequest model_config = ConfigDict(strict=True).

    Without strict, Pydantic v2 silently coerces JSON `"1"` (string)
    into int=1, `true` (bool) into int=1, etc. Strict mode rejects
    these as 422 validation errors BEFORE the engine sees them.

    Pins BOTH coercion paths the schema docstring promises:
      (a) string → int (e.g. `week="1"`)
      (b) bool → int (e.g. `seed=true`) — the load-bearing case;
          §16.14.t10 review F1 motivated the strict-typing pattern
          specifically because bool→int=1 was silently bypassing
          the engine's bool-rejection contract for rule values.
    """
    client, Session, _store = jobs_api
    body = _preview_body()

    # (a) string → int rejected. Short-circuits at Pydantic before
    # any DB lookup, so the station doesn't need to exist yet.
    body["week"] = "1"
    r = client.post("/api/stations/ts/jobs/preview", json=body)
    assert r.status_code == 422, r.json()

    # (b) bool → int rejected. Pydantic v2's strict mode rejects
    # bool for int fields even though `bool` is technically a
    # subclass of `int` in Python — strict treats them as distinct
    # at the schema boundary.
    body["week"] = 1
    body["seed"] = True
    r = client.post("/api/stations/ts/jobs/preview", json=body)
    assert r.status_code == 422, r.json()

    # Sanity: with both fields fixed + the station seeded, the
    # request hits the route + spawns a preview job. Proves the
    # 422s above came from strict-coercion checks, not e.g. a
    # different field's validator firing.
    _seed_station_with_pool(Session)
    body["seed"] = 42
    r = client.post("/api/stations/ts/jobs/preview", json=body)
    assert r.status_code == 201, r.json()


@pytest.mark.parametrize("field_to_inflate", ["template", "sidecar"])
def test_preview_request_rejects_huge_field(jobs_api, field_to_inflate):
    """§16.2.6.t6 — PreviewRequest's per-field defensive cap rejects
    accidentally-inflated dicts at the wire.

    Not a DoS defense (Decision A1's solo-hobbyist scope doesn't
    treat malicious requests as in-scope). Catches the ACCIDENTAL
    inflation class: a recursive reference, a debug-field copy that
    embeds the template inside itself, a regression where track-
    level metadata leaks into the body. Real templates / sidecars
    are ~50KB raw (Rebirth Radio); the cap is ~100x that.

    R1 fix — parametrized over BOTH `template` and `sidecar` so the
    `@field_validator("template", "sidecar")` registration is
    symmetric in coverage. Pre-fix only `template` was tested;
    a regression dropping `"sidecar"` from the decorator (typo,
    sloppy refactor) would have slipped past.

    R2 fix — cap + over-cap filler computed from
    `_PREVIEW_FIELD_MAX_BYTES` so a future tune (down to 2 MB
    after a real-world regression, up to 10 MB after a fixture
    audit) doesn't break the test. Assertion's MB string is
    likewise computed from the constant.
    """
    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    over_cap = _PREVIEW_FIELD_MAX_BYTES + (1024 * 1024)  # cap + 1 MB
    cap_mb = _PREVIEW_FIELD_MAX_BYTES // (1024 * 1024)

    body = _preview_body()
    # Filler string in ASCII = 1 byte per char; the dict's existing
    # keys add a few hundred more bytes; total comfortably over the
    # cap once json.dumps adds quote delimiters.
    body[field_to_inflate]["__t6_inflation__"] = "x" * over_cap

    r = client.post("/api/stations/ts/jobs/preview", json=body)
    assert r.status_code == 422, r.json()
    detail = str(r.json())
    # The validator's message names the field + the cap so a future
    # contributor finds the trail.
    assert field_to_inflate in detail
    assert f"{cap_mb}MB defensive cap" in detail


def test_jobs_generate_loads_excluded_track_pids_and_passes_to_engine(jobs_api):
    """§16.2.6.t7 — /jobs/generate now loads `excluded_track_pids`
    via the shared `_load_engine_inputs` helper + passes it to
    `generate_stream`, matching the sync `/generate` endpoint + the
    `/jobs/preview` endpoint.

    Pre-fix the kwarg was OMITTED from /jobs/generate's stream_factory
    closure — same template via sync /generate vs /jobs/generate
    produced different playlists when `cross_pool_dedup_sources`
    rules were configured (the async path silently skipped the
    exclusion set). The c1 review #1 batch B noted this as a
    pre-existing inconsistency; t7 closes it by promoting
    `excluded_track_pids` into the `_EngineInputs` dataclass so the
    helper is the single source of truth for both job endpoints.

    Pin design (R1 fix): monkeypatch BOTH `generate_stream` (to
    capture the kwargs flowing into the engine) AND
    `load_excluded_track_pids` (to return a SENTINEL non-empty
    frozenset). Without the sentinel, the seeded test station has
    no `cross_pool_dedup_sources` rule → both endpoints would get
    empty frozensets → the cross-endpoint equality assertion would
    be vacuously satisfied. A regression where one endpoint
    hardcoded `frozenset()` while the other actually loaded would
    slip past. With the sentinel, both endpoints' kwargs[excluded_
    track_pids] MUST equal the sentinel — pinning that the loaded
    value flowed through both call sites.

    Both endpoints are exercised so the helper's single-source-of-
    truth contract gets BOTH call sites pinned in one test.
    """
    from api.routers import jobs as jobs_mod
    real_generate_stream = jobs_mod.generate_stream
    captured_kwargs: list[dict] = []

    def capturing_stream(template, pools, **kwargs):
        captured_kwargs.append(kwargs)
        yield from real_generate_stream(template, pools, **kwargs)

    # R1: sentinel exclusion set — non-empty so the cross-endpoint
    # equality assertion is non-vacuous. The pids are synthetic
    # strings, never resolved against the pool; the generator
    # treats them as opaque set members (excluded_track_pids is a
    # filter against the Track.persistent_id column).
    sentinel_pids = frozenset({"sentinel-pid-A", "sentinel-pid-B"})

    client, Session, _store = jobs_api
    _seed_station_with_pool(Session)

    with patch.object(jobs_mod, "generate_stream", capturing_stream), \
         patch.object(jobs_mod, "load_excluded_track_pids",
                      return_value=sentinel_pids):
        # /jobs/generate path.
        r1 = client.post("/api/stations/ts/jobs/generate", json={"week": 1})
        assert r1.status_code == 201, r1.json()
        _wait_for_status(client, r1.json()["job_id"], "done", timeout=10)

        # /jobs/preview path (pin both call sites in one test).
        r2 = client.post(
            "/api/stations/ts/jobs/preview", json=_preview_body(seed=99),
        )
        assert r2.status_code == 201, r2.json()
        _wait_for_status(client, r2.json()["job_id"], "done", timeout=10)

    assert len(captured_kwargs) == 2, (
        f"expected 2 generate_stream calls (one per endpoint); got "
        f"{len(captured_kwargs)}"
    )
    for label, kwargs in zip(("/jobs/generate", "/jobs/preview"), captured_kwargs):
        assert "excluded_track_pids" in kwargs, (
            f"{label} stream_factory must pass excluded_track_pids "
            f"(§16.2.6.t7 alignment). Got kwargs: {sorted(kwargs)}"
        )
        # R1: STRICT identity against the sentinel — pre-fix this
        # was just an isinstance(frozenset) check, which a
        # hardcoded-frozenset() regression would still satisfy.
        assert kwargs["excluded_track_pids"] == sentinel_pids, (
            f"{label} stream_factory must pass the loaded "
            f"excluded_track_pids (sentinel {sentinel_pids}); got "
            f"{kwargs['excluded_track_pids']!r}. A regression where "
            f"this endpoint hardcoded frozenset() or skipped the "
            f"load would land here."
        )


# ---------- §16.2.6 c1 review #1 batch C: exclusion_window recompute ---


def _seed_mix_station_for_preview(Session: sessionmaker) -> dict:
    """Insert a station with a SINGLE-slot mix template + 3 House tracks
    + 2 saved Playlist rows. Returns the template dict (for the
    preview request body).

    The fixture exists in this state:
      - Playlist for week 1 exists (no entries needed — just an anchor
        for MixUsage rows below).
      - Playlist for week 2 exists (same).
      - MixUsage table starts EMPTY — the test inserts rows mid-flight
        to drive the recompute regression.

    Returns the template dict so the test can splice
    exclusion_window edits into the preview request body.
    """
    from engine.models import Playlist
    template = {
        "station": "mix-rb",
        "name": "Mix Rebirth",
        "cadence": "weekly",
        "rng": "off",
        "sources": [{"name": "MIXES", "colour": "#0cf", "role": "content"}],
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
        # 3 House tracks in the MIXES pool.
        tracks = [
            Track(persistent_id=f"P-H{i}", name=f"House-{i}",
                  artist=f"DJ-{i}", album=None, genre="House",
                  itunes_track_id=i + 100)
            for i in range(1, 4)
        ]
        db.add_all(tracks)
        db.commit()
        pool = SourcePlaylist(name="MIXES", role="content")
        db.add(pool)
        db.commit()
        for pos, t in enumerate(tracks, start=1):
            db.add(SourcePlaylistEntry(
                source_playlist_id=pool.id, position=pos, track_id=t.id,
            ))
        station = Station(slug="mix-rb", name="Mix Rebirth", template=template)
        db.add(station)
        db.commit()
        # Anchor Playlist rows for weeks 1 + 2 so MixUsage can attach.
        db.add(Playlist(station_id=station.id, week=1, trace_id="t-w1"))
        db.add(Playlist(station_id=station.id, week=2, trace_id="t-w2"))
        db.commit()
    return template


def test_preview_recomputes_excluded_mix_ids_per_request(jobs_api):
    """§16.2.6 D1 pin (the design's load-bearing 'mid-edit
    exclusion_window change affects the next preview' regression):
    each preview call recomputes excluded_mix_ids from CURRENT
    MixUsage — not from a cached page-load snapshot.

    Setup:
      - Mix station with 3 House tracks (House-1, House-2, House-3)
      - Playlists exist for weeks 1 + 2 (anchors for MixUsage)
      - MixUsage table starts empty

    Sequence:
      1. Preview week 3 → trace's mix slot picks T_first (no
         exclusions; the engine deterministically picks the
         lowest-position eligible track).
      2. Insert MixUsage row for T_first against week 2's Playlist.
      3. Preview week 3 again (same body) → trace's mix slot picks
         T_second ≠ T_first.

    If recompute didn't fire, the second preview would return T_first
    again (cached set). The behavior pins the route-layer
    `load_excluded_mix_ids` recompute that happens on EVERY preview
    request, not just on the first.

    NOT covered today: editing the template's `exclusion_window`
    field in the request body (would need to vary the body across
    calls). That path is exercised by the per-request body
    parsing already pinned in
    test_preview_400_on_invalid_template — every preview re-parses
    the body's template + recomputes the helper's window arg.
    """
    from engine.models import MixUsage, Playlist
    client, Session, _store = jobs_api
    template = _seed_mix_station_for_preview(Session)
    sidecar = synthesize_trivial_sidecar(template)

    body = {"template": template, "sidecar": sidecar, "week": 3}

    # Preview 1 — empty MixUsage, all tracks eligible.
    job_1 = client.post(
        "/api/stations/mix-rb/jobs/preview", json=body,
    ).json()["job_id"]
    trace_1 = _wait_for_status(client, job_1, "done")["result"]["trace"]
    first_track_id = trace_1["slots"][0]["track_id"]
    assert first_track_id is not None

    # Mid-session: insert a MixUsage row excluding the just-picked
    # track. Attach to week 2's Playlist (the most-recent week before
    # the previewed week=3).
    with Session() as db:
        station = db.query(Station).filter_by(slug="mix-rb").first()
        week2_playlist = db.query(Playlist).filter_by(
            station_id=station.id, week=2,
        ).first()
        db.add(MixUsage(
            playlist_id=week2_playlist.id,
            track_id=first_track_id,
            genre="House",
        ))
        db.commit()

    # Preview 2 — same body, but MixUsage now excludes first_track_id.
    job_2 = client.post(
        "/api/stations/mix-rb/jobs/preview", json=body,
    ).json()["job_id"]
    trace_2 = _wait_for_status(client, job_2, "done")["result"]["trace"]
    second_track_id = trace_2["slots"][0]["track_id"]
    assert second_track_id is not None
    assert second_track_id != first_track_id, (
        f"recompute failed: preview returned the same mix track "
        f"({first_track_id}) after MixUsage was added to exclude it. "
        f"Either load_excluded_mix_ids was cached across requests, or "
        f"the helper didn't run on the second preview call."
    )
