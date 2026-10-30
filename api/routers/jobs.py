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
same station catastrophic). /jobs/preview (added in §16.2.6 c1
`b5314b7`) uses POLICY_CANCEL_EXISTING for the debounce-restart UX —
a new preview supersedes any running preview without the frontend
needing to explicitly DELETE the old job.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from engine.editor_metadata import EditorMetadataError, validate_sidecar
from engine.generator import GenerationResult, generate_stream
from engine.models import Playlist, Station
from engine.persistence import (
    CursorPlan, finish_cursors, load_alias_map, plan_cursors, regeneration_seed,
    load_excluded_mix_ids, load_excluded_track_pids, load_pools,
    load_previous_playlist, max_mix_exclusion_window, save_playlist,
)
from engine.rules import get_rule_value
from engine.template import parse_template, TemplateError
from api.deps import get_db, get_session_factory
from api.job_runner import (
    JobSnapshot,
    JobStartConflict,
    JobStore,
    KIND_GENERATE,
    KIND_PREVIEW,
    POLICY_CANCEL_EXISTING,
    POLICY_REJECT,
    get_job_store,
)
from api.schemas import (
    GenerateRequest,
    JobErrorBody,
    JobStartResponse,
    JobStatusResponse,
    PreviewRequest,
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


@dataclass
class _EngineInputs:
    """The engine kwargs both `/jobs/generate` and `/jobs/preview`
    need. §16.2.6 c1 review #1 finding #4 extracted from the
    duplicated input-loading regions; §16.2.6.t7 promoted
    `excluded_track_pids` into the dataclass after the
    `/jobs/generate` alignment landed (closing the pre-existing
    divergence flagged in c1 review #1 batch B).

    EXCLUDED on purpose:
      - `cursor_advance_step` / `cursor_reset_cycle` — only used by
        `advance_cursors` in /jobs/generate's `on_result`; preview
        never advances cursors so loading them is wasted.
    """
    pools: dict[str, list[Any]]
    artist_dedup_window: int | None
    excluded_mix_ids: Any
    alias_map: dict[str, str]
    # Where this week's persistent cursors start, and whether a
    # generation of it moves the live cursors on (plan_cursors).
    cursor_plan: CursorPlan
    previous_playlist: list[Any] | None
    named_cursor_positions: dict[str, int] | None
    # §16.2.6.t7: cross-pool dedup exclusion set. Loaded for both
    # endpoints so /jobs/generate produces the same playlist as the
    # sync /generate endpoint for the same template + rules. The
    # /jobs/preview endpoint was already loading this inline (since
    # c1); the helper now centralizes both load sites.
    excluded_track_pids: frozenset[str]


def _load_engine_inputs(
    db: Session, station: Station, template: Any, week: int,
) -> _EngineInputs:
    """Load the engine kwargs shared by /jobs/generate + /jobs/preview.

    `template` is the PARSED Template (not the raw dict). Returns
    `_EngineInputs` — the closure captures the dataclass instead of
    7 individual locals so future per-station-rule additions land in
    one site.

    Raises HTTPException(400) on `load_pools` ValueError OR on
    `load_excluded_track_pids` ValueError / TemplateError — the
    operator's edited template / rules referenced a pool or
    reference source that doesn't exist server-side. Same surface
    area as both endpoints had inline pre-extraction.
    """
    try:
        pools = load_pools(db, template)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # §14.29: per-station rule defaults. Resolved BEFORE the mix
    # exclusion set so a station that omits BlockSpec.exclusion_window
    # on some mix slots still feeds the right lookback into the query.
    artist_dedup_window = get_rule_value(
        db, station_id=station.id, key="artist_dedup_window",
    )
    mix_exclusion_weeks = get_rule_value(
        db, station_id=station.id, key="mix_exclusion_weeks",
    )

    excluded_mix_ids = load_excluded_mix_ids(
        db,
        station_id=station.id,
        window=max_mix_exclusion_window(template, default=mix_exclusion_weeks),
    )
    alias_map = load_alias_map(db)
    previous_playlist = load_previous_playlist(
        db, station_id=station.id, current_week=week,
    )
    # A past week starts from the positions it was generated with, so
    # its preview and its regeneration match; see plan_cursors.
    cursor_plan = plan_cursors(
        db, station_id=station.id, template=template, week=week,
    )
    named_cursor_positions = cursor_plan.positions

    # §16.2.6.t7: cross_pool_dedup_sources → frozenset of excluded
    # Track.persistent_ids. ValueError / TemplateError translate to
    # 400 with the operator-readable message (same shape sync
    # /generate uses at generate.py:108-115).
    try:
        excluded_track_pids = load_excluded_track_pids(
            db, station_id=station.id,
        )
    except (ValueError, TemplateError) as e:
        raise HTTPException(400, str(e))

    return _EngineInputs(
        pools=pools,
        artist_dedup_window=artist_dedup_window,
        excluded_mix_ids=excluded_mix_ids,
        alias_map=alias_map,
        previous_playlist=previous_playlist,
        named_cursor_positions=named_cursor_positions,
        excluded_track_pids=excluded_track_pids,
        cursor_plan=cursor_plan,
    )


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
    except TemplateError as e:  # pragma: no cover
        # Stored templates are validated at creation (POST /api/stations
        # rejects invalid templates with 400). This branch fires only on
        # data corruption — the row was written invalid somehow (manual
        # DB edit, restore from a corrupted backup, schema drift). Not
        # reachable from any normal API path; the 500 surfaces the
        # corruption to the operator.
        raise HTTPException(500, f"stored template is invalid: {e}")

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

    inputs = _load_engine_inputs(db, station, template, payload.week)

    # advance_cursors-only rule values (NOT in _EngineInputs since
    # they're /jobs/generate-specific — preview never advances).
    cursor_advance_step = get_rule_value(
        db, station_id=station.id, key="cursor_advance_step",
    )
    cursor_reset_cycle = get_rule_value(
        db, station_id=station.id, key="cursor_reset_cycle",
    )

    # Capture identity values that the worker will need but that we
    # can't safely read off the ORM-bound `station` instance after
    # the request session closes. Captured by closure below.
    station_id = station.id
    station_slug = station.slug
    week = payload.week
    seed = payload.seed
    # rng='on' needs a seed. Regenerating a week without one reuses
    # the week's own, so only songs the rules changed should move (the
    # week page's Regenerate does this). A new week still needs one.
    if seed is None and template.rng == "on":
        seed = regeneration_seed(db, station_id=station.id, week=week)

    # ----- Closures -------------------------------------------------
    # stream_factory must be parameterless (matches JobStore.start's
    # signature). Capturing the loaded inputs into the closure keeps
    # the runner engine-agnostic.

    def stream_factory():
        return generate_stream(
            template, inputs.pools,
            week=week, seed=seed,
            excluded_mix_ids=inputs.excluded_mix_ids,
            alias_map=inputs.alias_map,
            previous_playlist=inputs.previous_playlist,
            weighted_window_default=inputs.artist_dedup_window,
            named_cursor_positions=inputs.named_cursor_positions,
            # §16.2.6.t7: align with sync /generate + /jobs/preview.
            excluded_track_pids=inputs.excluded_track_pids,
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
            if worker_station is None:  # pragma: no cover
                # Station was deleted between job start + completion.
                # Requires the operator (or a script) to DELETE the
                # station mid-generation; not reachable from normal
                # API paths without thread-racing the test fixture.
                # The runner surfaces this as status=error so the
                # frontend has SOMETHING to render rather than a
                # silent worker-thread crash.
                raise ValueError(
                    f"station id={station_id} was deleted before job completed"
                )
            try:
                playlist = save_playlist(
                    worker_db, worker_station, result,
                    week=week, template=template,
                    cursor_positions=inputs.cursor_plan.positions,
                )
                worker_db.commit()
            except IntegrityError:  # pragma: no cover
                # Concurrent (station_id, week) generations racing the
                # unique constraint. POLICY_REJECT (Decision F) prevents
                # the common case at the API layer; this only fires if
                # the same station+week is being generated via the SYNC
                # endpoint AND the async job simultaneously, or two
                # processes share a DB (no longer the v1 deployment
                # shape — see README single-worker caveat). Translated
                # to ValueError so error.kind is meaningful on the wire.
                worker_db.rollback()
                raise ValueError(
                    "a playlist for this station and week was concurrently "
                    "generated; retry"
                )

            # §14.30: advance persistent named cursors AFTER the
            # playlist commits. Same generation_count convention as
            # the synchronous endpoint.
            # Only a generation of the latest week moves them; a past
            # week's leaves them where the next new week continues.
            if inputs.cursor_plan.moves_live:
                finish_cursors(
                    worker_db,
                    plan=inputs.cursor_plan,
                    station_id=worker_station.id,
                    template=template,
                    pools=dict(inputs.pools),
                    advance_step=cursor_advance_step,
                    reset_cycle=cursor_reset_cycle,
                    week=week,
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


def _preview_seed(db: Session, station: Station, week: int) -> int:
    """The seed for an rng='on' preview sent without one.

    The week's own seed, so a preview of the saved template reproduces
    the stored week and an edited preview differs from it only where
    the edit does. A week never generated gets the week number: any
    fixed value keeps repeated previews of that week comparable with
    each other.
    """
    playlist = (
        db.query(Playlist)
        .filter(Playlist.station_id == station.id, Playlist.week == week)
        .first()
    )
    if playlist is not None:
        version = playlist.current_version()
        for seed in (version.seed if version is not None else None, playlist.seed):
            if seed is not None:
                return seed
    return week


@router.post(
    "/api/stations/{slug}/jobs/preview",
    response_model=JobStartResponse,
    status_code=201,
)
def start_preview_job(
    slug: str,
    payload: PreviewRequest,
    response: Response,
    db: Session = Depends(get_db),
    job_store: JobStore = Depends(get_job_store),
):
    """Start a §16.2.6 live-preview job. Runs the engine against the
    operator's IN-MEMORY editor state (template + sidecar in the
    request body) + returns the resulting trace via the §16.11 job-id
    transport. NO persistence side-effects.

    Synchronous validation (runs in the request thread; 4xx errors
    surface before any job exists):

      * 404 — station slug doesn't exist
      * 400 — template fails to parse (operator edited into an
              invalid state)
      * 400 — sidecar fails grammar OR semantics validation
              (operator edit + template combo invalid)
      * 400 — pools missing for some source the in-memory template
              references

    Concurrent policy is `POLICY_CANCEL_EXISTING` (§16.2.6 D6 latest-
    wins): a new preview job for the same (slug, KIND_PREVIEW)
    supersedes any running preview job. Implements the DESIGN's
    "cancel in-flight on new edit" — the frontend doesn't even
    need to explicitly DELETE; firing the next preview cancels the
    last automatically. The explicit DELETE remains supported for
    operator-initiated cancellation (Discard mid-PREVIEWING).

    No session_factory dep: preview's on_result is a pure
    transformation of the GenerationResult — no DB writes.

    Inputs the server recomputes from DB (NOT from the request body,
    per the design's recompute-per-request guarantee):

      * `excluded_mix_ids` — from current MixUsage; reflects any
        intervening mix-block exclusion_window edits
      * `previous_playlist` — looked up by `week - 1`
      * `alias_map`, `excluded_track_pids` — current DB rules
      * `named_cursor_positions` — current cursors (preview READS;
        the on_result deliberately does NOT advance them)
      * per-station rule defaults (artist_dedup_window,
        mix_exclusion_weeks)
    """
    # ----- Synchronous validation + engine input loading -------------
    station = db.query(Station).filter(Station.slug == slug).first()
    if not station:
        raise HTTPException(404, f"station '{slug}' not found")

    # Parse the operator's in-memory template. 400 on invalid input —
    # operator can fix the edit + retry; not 500 (no data corruption).
    # The variable is named `parsed_template` (review #1 #9) to make
    # the dict-vs-parsed distinction obvious to future editors:
    # `payload.template` is the raw dict (Pydantic field), while
    # `parsed_template` is the engine's structured Template.
    try:
        parsed_template = parse_template(payload.template)
    except TemplateError as e:
        raise HTTPException(400, f"invalid template: {e}")

    # Validate the operator's in-memory sidecar. Uses the full wrapper
    # (grammar + semantics) since the request body is operator-edited
    # + not pre-validated. §16.2.5.t15's _persist_sidecar optimization
    # (semantics-only at route layer) does NOT apply here — preview's
    # input doesn't traverse the SidecarJSON TypeDecorator (no commit),
    # so grammar must run explicitly. validate_sidecar takes the RAW
    # dict template (`payload.template`), not the parsed one.
    try:
        validate_sidecar(payload.sidecar, payload.template)
    except EditorMetadataError as e:
        raise HTTPException(400, f"invalid sidecar: {e}")

    # ⚠ Detached-pool caveat (mid-slice review 🟡-4):
    # `inputs.pools` is `dict[str, list[Track]]` where Track is a
    # SQLAlchemy ORM instance bound to `db`. The worker thread
    # iterates them after the request's `db` closes. Today's
    # scalar-only access path is safe; future ORM-relationship
    # access would DetachedInstanceError.

    inputs = _load_engine_inputs(db, station, parsed_template, payload.week)

    # §16.2.6.t7: `excluded_track_pids` moved INTO _EngineInputs
    # (the helper now loads it for both endpoints). The pre-t7
    # inline load + try/except here is gone — `inputs.excluded_track_pids`
    # carries the same value the sync /generate endpoint loads.

    # Capture identity values that the worker closure needs.
    week = payload.week
    seed = payload.seed
    if seed is None and parsed_template.rng == "on":
        # The engine refuses rng='on' without a seed; the editor and the
        # week page don't send one. See _preview_seed.
        seed = _preview_seed(db, station, week)

    # ----- Closures -------------------------------------------------
    def stream_factory():
        return generate_stream(
            parsed_template, inputs.pools,
            week=week, seed=seed,
            excluded_mix_ids=inputs.excluded_mix_ids,
            alias_map=inputs.alias_map,
            previous_playlist=inputs.previous_playlist,
            weighted_window_default=inputs.artist_dedup_window,
            named_cursor_positions=inputs.named_cursor_positions,
            excluded_track_pids=inputs.excluded_track_pids,
        )

    def on_result(result: GenerationResult) -> dict[str, Any]:
        # NO persistence side-effects. Pure transformation: the trace
        # dict the frontend renders + a slot_count summary for the
        # JobSnapshot's UI.
        return {
            "trace": result.trace.to_dict(),
            "slot_count": len(result.picks),
        }

    # ----- Spawn the job --------------------------------------------
    # POLICY_CANCEL_EXISTING (§16.2.6 D6): a running preview job for
    # this same slug gets cancelled + replaced. No JobStartConflict
    # to catch here (the policy never raises one).
    job_id = job_store.start(
        slug=slug,
        kind=KIND_PREVIEW,
        stream_factory=stream_factory,
        on_result=on_result,
        concurrent_policy=POLICY_CANCEL_EXISTING,
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
