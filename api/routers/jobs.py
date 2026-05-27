"""§16.11 c3 — asynchronous generate endpoints.

Three endpoints (Decision A1):

    POST   /api/stations/{slug}/jobs/generate
           → 201 JobStartResponse{job_id, status="running"}
             Location: /api/jobs/{job_id}
           → 404 if station not found
           → 400 if pools missing for the station's sources
           → 409 if a generate job for this slug is already running
                 body: {detail: {message, existing_job_id}}

    GET    /api/jobs/{job_id}
           → 200 JobStatusResponse
           → 404 if unknown / GC'd / wrong id

    DELETE /api/jobs/{job_id}
           → 204 (idempotent)
           → 404 if unknown / GC'd / wrong id

Concurrency policy is hard-coded to POLICY_REJECT for /jobs/generate
(Decision F: persistence side-effects make racing two saves on the
same station catastrophic). /jobs/preview (§16.2.6, future) will use
POLICY_CANCEL_EXISTING for the debounce-restart UX.

Engine seam (Decision B2): the endpoint validates synchronously (404 /
400 / 422 paths fire BEFORE the job is started), then constructs two
closures:

  * `stream_factory()` — invokes `generate_stream(template, pools, …)`,
    capturing all the loaded engine inputs. The runner pulls events
    from this iterator in the worker thread.
  * `on_result(GenerationResult) -> dict` — opens a FRESH DB session
    (via get_session_factory; the request's `db` is closed by the time
    the worker reaches here), runs save_playlist + advance_cursors,
    builds the response-body dict that becomes `Job.result`.

The on_result body mirrors `GenerateResponse` exactly so the frontend's
job-done handler can treat the result identically to the synchronous
endpoint's response — Decision E1's "both endpoints keep working".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from engine.generator import GenerationResult, generate_stream
from engine.models import Station
from engine.persistence import (
    advance_cursors, load_alias_map, load_cursor_positions,
    load_excluded_mix_ids, load_pools, load_previous_playlist,
    max_mix_exclusion_window, save_playlist,
)
from engine.rules import get_rule_value
from engine.template import parse_template, TemplateError
from api.deps import get_db, get_session_factory
from api.job_runner import (
    JobSnapshot,
    JobStartConflict,
    JobStore,
    KIND_GENERATE,
    POLICY_REJECT,
    get_job_store,
)
from api.schemas import (
    GenerateRequest,
    JobErrorBody,
    JobStartResponse,
    JobStatusResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])


# §16.11 mid-slice review 🟡-5: error response shapes ARE NOT uniform
# across this router. Pinning the shapes here so frontend (c4) can
# match against documented contracts rather than guessing:
#
#   404 (station / job not found):
#       {"detail": "<human message>"}              # FastAPI default
#   400 (load_pools failed):
#       {"detail": "<human message>"}              # FastAPI default
#   422 (pydantic validation, e.g. week < 1):
#       {"detail": [{"loc": ..., "msg": ..., ...}]}  # FastAPI default
#   409 (POLICY_REJECT conflict):
#       {"detail": {"message": str, "existing_job_id": str}}
#
# c4's API helpers should branch on `response.status` and read the
# appropriate shape. Standardising into a single ErrorBody schema is a
# defer-later item (would touch every existing endpoint).


def _iso_utc(ts: float | None) -> str | None:
    """Convert UNIX-seconds timestamp to ISO-8601 UTC with a trailing
    Z. Matches the design doc's example "2026-05-27T14:32:01Z" rather
    than Python's default `+00:00` suffix. None passes through.
    """
    if ts is None:
        return None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Drop sub-second precision to keep poller responses small +
    # human-readable. Sub-second timing only matters for benchmarks,
    # which read the float field internally.
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_to_response(snap: JobSnapshot) -> JobStatusResponse:
    """Project a `JobSnapshot` (runner-internal) onto the public
    `JobStatusResponse`. Just timestamp conversion + Pydantic wrap.
    """
    return JobStatusResponse(
        job_id=snap.job_id,
        slug=snap.slug,
        kind=snap.kind,
        status=snap.status,
        phase=snap.phase,
        current=snap.current,
        total=snap.total,
        label=snap.label,
        result=snap.result,
        error=JobErrorBody(**snap.error) if snap.error is not None else None,
        started_at=_iso_utc(snap.started_at) or "",  # never None in practice
        finished_at=_iso_utc(snap.finished_at),
    )


@router.post(
    "/api/stations/{slug}/jobs/generate",
    response_model=JobStartResponse,
    status_code=201,
)
def start_generate_job(
    slug: str,
    payload: GenerateRequest,
    response: Response,
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
    job_store: JobStore = Depends(get_job_store),
):
    """Start a background playlist-generation job for the given station
    + week. Returns 201 with `{job_id, status:"running"}` + a Location
    header pointing at the polling URL.

    Synchronous validation paths (run before the job is spawned):

      * 404 — station slug doesn't exist
      * 500 — stored template fails to parse (data corruption)
      * 400 — pools missing for some source the template references
      * 409 — a `generate` job for the same slug is already running /
              in grace; body carries `existing_job_id` so the frontend
              can poll the in-flight job rather than failing

    Decision A1 keeps polling simple: this endpoint returns 201
    immediately; the frontend immediately begins polling
    `GET /api/jobs/{job_id}`.
    """
    # ----- Synchronous validation + engine input loading -------------
    # Runs in the request thread so 4xx errors surface before any job
    # exists. The existing synchronous /generate endpoint does the same
    # work; only difference here is what we do with the loaded inputs
    # afterwards (capture for worker vs. call generate directly).
    station = db.query(Station).filter(Station.slug == slug).first()
    if not station:
        raise HTTPException(404, f"station '{slug}' not found")

    try:
        template = parse_template(station.template)
    except TemplateError as e:
        raise HTTPException(500, f"stored template is invalid: {e}")

    try:
        pools = load_pools(db, template)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # ⚠ Detached-pool caveat (mid-slice review 🟡-4):
    # `pools` is `dict[str, list[Track]]` where Track is a SQLAlchemy
    # ORM instance bound to `db`. When the request returns, FastAPI's
    # generator-dep teardown closes `db`, leaving those Tracks DETACHED.
    # The worker thread then iterates `pools` inside generate_stream.
    # Today this works because the engine reads only scalar columns
    # (id/name/artist/album) which were eager-loaded via joinedload in
    # `engine.persistence.load_pools`. A future strategy that traverses
    # an ORM relationship (e.g. lazy-loading track.genre_tags) would
    # DetachedInstanceError. If/when that lands, pre-materialise pools
    # here into plain dataclasses before capturing into the closure.
    # The c3 byte-equality tests pin today's scalar-only behaviour.

    # §14.29: per-station rule defaults. Resolved BEFORE the mix
    # exclusion set so a station that omits BlockSpec.exclusion_window
    # on some mix slots still feeds the right lookback into the query.
    artist_dedup_window = get_rule_value(
        db, station_id=station.id, key="artist_dedup_window",
    )
    mix_exclusion_weeks = get_rule_value(
        db, station_id=station.id, key="mix_exclusion_weeks",
    )
    cursor_advance_step = get_rule_value(
        db, station_id=station.id, key="cursor_advance_step",
    )
    cursor_reset_cycle = get_rule_value(
        db, station_id=station.id, key="cursor_reset_cycle",
    )

    excluded_mix_ids = load_excluded_mix_ids(
        db,
        station_id=station.id,
        window=max_mix_exclusion_window(template, default=mix_exclusion_weeks),
    )
    alias_map = load_alias_map(db)
    previous_playlist = load_previous_playlist(
        db, station_id=station.id, current_week=payload.week,
    )
    named_cursor_positions = None
    if template.cursor.persist:
        named_cursor_positions = load_cursor_positions(
            db, station_id=station.id, template=template,
        )

    # Capture identity values that the worker will need but that we
    # can't safely read off the ORM-bound `station` instance after
    # the request session closes. Captured by closure below.
    station_id = station.id
    station_slug = station.slug
    week = payload.week
    seed = payload.seed
    persist_cursors = template.cursor.persist

    # ----- Closures -------------------------------------------------
    # stream_factory must be parameterless (matches JobStore.start's
    # signature). Capturing the loaded inputs into the closure keeps
    # the runner engine-agnostic.

    def stream_factory():
        return generate_stream(
            template, pools,
            week=week, seed=seed,
            excluded_mix_ids=excluded_mix_ids,
            alias_map=alias_map,
            previous_playlist=previous_playlist,
            weighted_window_default=artist_dedup_window,
            named_cursor_positions=named_cursor_positions,
        )

    # on_result runs inside the worker thread. The request's `db` is
    # CLOSED by then (FastAPI's generator-dep mechanism closes it as
    # soon as the endpoint returns). Open a fresh session via the
    # injected factory so the worker writes to the same DB the request
    # was reading (in tests this is the throwaway tmp DB; in prod
    # _SessionLocal). Concurrent-generation safety: save_playlist's
    # unique constraint on (station_id, week) catches the race +
    # converts to a ValueError so the job surfaces as status=error
    # with a meaningful message.

    def on_result(result: GenerationResult) -> dict[str, Any]:
        with session_factory() as worker_db:
            # Re-fetch the station in the worker's session — `station`
            # from the request is bound to a closed session.
            worker_station = worker_db.query(Station).filter_by(id=station_id).first()
            if worker_station is None:
                # Station was deleted between job start + completion.
                # Surface as a ValueError so it lands in error.kind.
                raise ValueError(
                    f"station id={station_id} was deleted before job completed"
                )
            try:
                playlist = save_playlist(
                    worker_db, worker_station, result,
                    week=week, template=template,
                )
                worker_db.commit()
            except IntegrityError:
                worker_db.rollback()
                # Translate to a ValueError so error.kind is meaningful
                # ("IntegrityError" wouldn't tell a frontend much).
                raise ValueError(
                    "a playlist for this station and week was concurrently "
                    "generated; retry"
                )

            # §14.30: advance persistent named cursors AFTER the
            # playlist commits. Same generation_count convention as
            # the synchronous endpoint.
            if persist_cursors:
                advance_cursors(
                    worker_db,
                    station_id=worker_station.id,
                    template=template,
                    pools=dict(pools),
                    advance_step=cursor_advance_step,
                    reset_cycle=cursor_reset_cycle,
                    generation_count=week - 1,
                )
                worker_db.commit()

            return {
                "playlist_id": playlist.id,
                "trace_id": result.trace.trace_id,
                "station": station_slug,
                "week": week,
                "slot_count": len(result.picks),
            }

    # ----- Spawn the job --------------------------------------------
    try:
        job_id = job_store.start(
            slug=slug,
            kind=KIND_GENERATE,
            stream_factory=stream_factory,
            on_result=on_result,
            concurrent_policy=POLICY_REJECT,
        )
    except JobStartConflict as e:
        # 409 with a structured detail so the frontend can route to
        # the existing job rather than failing the user's click.
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"a generate job for station '{slug}' is already running; "
                    f"poll the existing job"
                ),
                "existing_job_id": e.existing_job_id,
            },
        )

    response.headers["Location"] = f"/api/jobs/{job_id}"
    return JobStartResponse(job_id=job_id, status="running")


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
):
    """Poll a job's current state. The frontend hits this every ~500ms
    until `status != "running"`. Decision F: a 404 mid-poll (job GC'd,
    or process restarted) lets the frontend show a friendly "couldn't
    find that job — refresh and retry" toast rather than a perpetual
    spinner.
    """
    snap = job_store.get(job_id)
    if snap is None:
        raise HTTPException(404, f"job '{job_id}' not found")
    return _snapshot_to_response(snap)


@router.delete("/api/jobs/{job_id}", status_code=204)
def cancel_job(
    job_id: str,
    job_store: JobStore = Depends(get_job_store),
):
    """Cancel a running job. Idempotent at the HTTP level:

      * Running job → cancelled + immediately GC'd; returns 204.
      * Already-terminal (done/error/cancelled in grace) → 204 anyway
        (the client's intent is "make this job go away"; reporting an
        error for a job that's already gone is hostile).
      * Unknown / never-existed / past-grace job → 404 so a typo
        surfaces vs being silently absorbed.

    Cancellation is observed by the worker thread between yields; for
    multi-second strategies the worker may take a moment to actually
    bail. The store entry vanishes immediately though, so subsequent
    polls return 404 — UI clears the indicator at that point.
    """
    # The store's cancel() returns False both for "unknown id" and
    # "job exists but is already terminal". We want different HTTP
    # codes for these, so do an explicit existence check first.
    snap = job_store.get(job_id)
    if snap is None:
        raise HTTPException(404, f"job '{job_id}' not found")
    job_store.cancel(job_id)
    # Even if cancel() returned False (job was terminal), respond 204:
    # the client wanted the job dismissed; that goal is already met.
    return Response(status_code=204)
