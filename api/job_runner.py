"""§16.11 c2: in-memory job runner for asynchronous playlist generation.

Architecture (DESIGN.md §16.11 Decisions A1, B2, F, G):

    POST /jobs/generate  →  JobStore.start(...)  →  spawns threading.Thread
                            thread body: drain stream_factory(), update state
                            on Result: call on_result() + status="done"
                            on Exception: status="error"
    GET  /jobs/{id}      →  JobStore.get(id)     →  JobSnapshot (immutable)
    DELETE /jobs/{id}    →  JobStore.cancel(id)  →  sets cancelled flag;
                            thread observes between yields + bails

Concurrency model (Decision F's "in-memory implementation sketch" +
review finding 🟡 1):

* `_jobs` dict mutations under `self._lock` (threading.RLock —
  re-entrant for GC paths invoked while already holding the lock).
* `get()` returns a FROZEN snapshot. Pollers never read the mutable
  Job whose fields the worker thread is mid-write. Without
  snapshotting, a poller can observe torn state
  (e.g. `current=5, total=None`).
* `_Job.cancelled` is a single primitive bool — CPython atomic reads/
  writes. The worker checks it between yields without locking.
* The worker thread updates `phase/current/total/status` under the
  store lock — short critical sections (just the dict mutation).

GC policy (Decision F's GC bullets + review 🟡 2/3):

* LAZY — runs on every `start()` / `get()` call, under the lock.
  Avoids a second background thread + atomic "check-then-remove"
  comes for free inside the existing lock window.
* Cancelled jobs GC IMMEDIATELY (no 60s grace). Rationale: cancelled
  results aren't useful; live-preview's debounce-restart can spawn
  ~60 cancelled jobs/min — without immediate-GC they accumulate.
* Done / error jobs keep a 60s grace so the frontend can fetch final
  state after detecting completion (without a race against GC).
* Per-(slug, kind) eviction (review 🔴-1 + 🟡-2):
  - POLICY_REJECT: only a RUNNING job conflicts. Done/error jobs in
    their grace window do NOT block a new start — the natural
    workflow (generate, then immediately generate again) must not
    409 for 60s while the previous result lingers.
  - POLICY_CANCEL_EXISTING ("latest wins"): a RUNNING job is
    cancelled + immediate-GC'd; a terminal-in-grace job is silently
    evicted (popped without flipping its status — it really did
    finish, it's just being made room for).

Engine + DB agnosticism (review 🟡 8):

The store knows nothing about templates, pools, or the database.
Callers (the API endpoint in c3) pass a `stream_factory` closure
that captures all the DB / pool state + returns the engine iterator.
A second callback `on_result(GenerationResult) -> dict` does the
save_playlist + builds the JSON-ready result body. Both callbacks
run in the worker thread. This split keeps the store testable with
a fake fast engine (no DB fixture required).
"""
from __future__ import annotations

import copy
import logging
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from engine.generator import GenerationResult, ProgressEvent


logger = logging.getLogger(__name__)


# Job lifecycle (Decision G state machine). Strings rather than Enum
# for forward-compat: future statuses (e.g. "queued" if we add a real
# worker pool) can be added without breaking unknown-status handling
# on the frontend.
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_ERROR = "error"
JOB_STATUS_CANCELLED = "cancelled"
_TERMINAL_STATUSES = {JOB_STATUS_DONE, JOB_STATUS_ERROR, JOB_STATUS_CANCELLED}
# All status values — used as the find-helper's "match anything"
# filter under POLICY_CANCEL_EXISTING's "latest wins" rule.
_ALL_STATUSES = (
    JOB_STATUS_RUNNING, JOB_STATUS_DONE, JOB_STATUS_ERROR, JOB_STATUS_CANCELLED,
)


# Concurrency policy values (Decision F).
POLICY_REJECT = "reject"
POLICY_CANCEL_EXISTING = "cancel_existing"
_VALID_POLICIES = {POLICY_REJECT, POLICY_CANCEL_EXISTING}


# Default grace window — done/error jobs linger this long after
# completion so the frontend's final-status poll (which fires once
# after the previous "running" response) can still find them. 60s is
# generous; even a paused-tab user resuming after 30s should still see
# their result. Configurable via JobStore(grace_seconds=…) for tests.
_DEFAULT_GRACE_SECONDS = 60.0


# §16.11 c3 — job kinds. Module-level constants so the API endpoints
# (and §16.2.6 preview, future) share a single source of truth.
KIND_GENERATE = "generate"
KIND_PREVIEW = "preview"


class JobStartConflict(Exception):
    """Raised by `JobStore.start(..., concurrent_policy=POLICY_REJECT)`
    when a job for the same (slug, kind) is currently RUNNING (mid-
    slice review 🔴-1: NOT triggered by done/error jobs in their
    grace window — those don't conflict, they're just lingering for
    the frontend's final-status poll).

    The API layer catches this and returns 409 Conflict with the
    `existing_job_id` so the frontend can poll the in-flight job
    rather than failing.
    """
    def __init__(self, existing_job_id: str):
        super().__init__(
            f"a job for this (slug, kind) is currently running: "
            f"{existing_job_id}"
        )
        self.existing_job_id = existing_job_id


@dataclass
class _Job:
    """Mutable internal Job state. Accessed under `JobStore._lock`
    except for `cancelled` (CPython-atomic bool) and `thread` (set
    once at construction).

    NEVER returned directly to API consumers — `get()` returns a
    frozen `JobSnapshot` instead.
    """
    job_id: str
    slug: str
    kind: str
    status: str = JOB_STATUS_RUNNING
    # §16.11 review 🟢-6: initial phase BEFORE the worker pulls its
    # first event from the engine. The engine's first yield is
    # `phase="start"` (then "picks"); any GET that arrives between
    # `start()` returning and the worker pulling the first event sees
    # "queued". Frontends should treat phase ∈ {"queued", "start"} as
    # "indeterminate progress, show generic spinner".
    phase: str = "queued"
    current: int = 0
    total: int = 0
    label: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    # Wall-clock UNIX seconds for the JSON payload (human-readable).
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # Monotonic clock for GC grace timing — immune to system clock
    # skew (NTP corrections, DST etc.). Tests freeze + advance this
    # via the `grace_seconds` constructor arg + manipulating
    # `time.monotonic` via a clock override hook.
    finished_monotonic: float | None = None
    # Atomic bool — read by the worker thread between yields. Reads
    # and writes of a single Python attribute are atomic in CPython,
    # so this needs no lock.
    cancelled: bool = False
    thread: threading.Thread | None = None


@dataclass(frozen=True)
class JobSnapshot:
    """Immutable Job view. Returned by `JobStore.get(...)` — the API
    layer serialises this directly into the GET /jobs/{id} response.

    Frozen so pollers can't accidentally mutate state belonging to
    the store; constructed under the store lock so all fields are
    coherent (no torn `current=5, total=None`).
    """
    job_id: str
    slug: str
    kind: str
    status: str
    phase: str
    current: int
    total: int
    label: str | None
    result: dict[str, Any] | None
    error: dict[str, str] | None
    started_at: float
    finished_at: float | None


def _snapshot(job: _Job) -> JobSnapshot:
    """Build a JobSnapshot from a mutable _Job. Caller must hold the
    store lock — otherwise the read of result/error/etc. can race the
    worker's mutation.

    `result` and `error` are deep-copied: the snapshot is frozen as a
    dataclass, but its dict fields are shared by reference unless we
    copy. A caller that mutates `snap.result["foo"] = ...` would
    otherwise mutate the store's view too. For trusted callers
    (FastAPI's serialiser) this never bites, but cheap defence keeps
    the "snapshot is a snapshot" contract honest.
    """
    return JobSnapshot(
        job_id=job.job_id,
        slug=job.slug,
        kind=job.kind,
        status=job.status,
        phase=job.phase,
        current=job.current,
        total=job.total,
        label=job.label,
        result=copy.deepcopy(job.result),
        error=copy.deepcopy(job.error),
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


class JobStore:
    """In-memory job store + worker-thread orchestrator (§16.11 c2).

    Singleton lifecycle: instantiated once at app startup, attached
    to the FastAPI app state, looked up via `get_job_store()` (c3).
    A process restart loses all in-flight jobs — Decision A1 accepts
    this for the solo-user OSS scope. Multi-process SaaS deployments
    would replace this with a DB / queue backend (punted to §16.21+).
    """

    def __init__(
        self,
        *,
        grace_seconds: float = _DEFAULT_GRACE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        """
        Parameters
        ----------
        grace_seconds : float
            How long completed (done/error) jobs linger after
            `finished_monotonic` before lazy GC reaps them.
            Cancelled jobs ignore this — they GC immediately.
        monotonic : Callable[[], float]
            Clock injection for tests. Default `time.monotonic`;
            tests can pass a controllable clock to drive GC paths
            deterministically.
        """
        self._jobs: dict[str, _Job] = {}
        # RLock so a method that runs GC while already holding the
        # lock (e.g. start() calls _gc_under_lock() then more mutations)
        # doesn't self-deadlock.
        self._lock = threading.RLock()
        self._grace = grace_seconds
        self._monotonic = monotonic

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        slug: str,
        kind: str,
        stream_factory: Callable[[], Iterator["ProgressEvent | GenerationResult"]],
        on_result: Callable[[GenerationResult], dict[str, Any]] | None = None,
        concurrent_policy: str = POLICY_REJECT,
    ) -> str:
        """Spawn a new background job.

        Parameters
        ----------
        slug : str
            Station slug. Used for the per-(slug, kind) eviction.
        kind : str
            Job kind — "generate" for §16.11, "preview" for §16.2.6.
            Different kinds CAN run concurrently for the same slug.
        stream_factory : Callable[[], Iterator[ProgressEvent | GenerationResult]]
            Zero-arg closure that returns the engine event stream.
            Called once inside the worker thread; should capture all
            DB / pool state via closure variables (the store stays
            DB-agnostic). MUST yield exactly one GenerationResult per
            Decision G; the runner enforces this with an assertion.
        on_result : Callable[[GenerationResult], dict] | None
            Optional callback invoked in the worker thread once the
            GenerationResult arrives. Receives the result; returns a
            JSON-ready dict that becomes `Job.result`. Use this to
            persist the playlist + build the response body. Raising
            from here transitions the job to status="error".
            If None, Job.result is set to an empty dict.
        concurrent_policy : str
            "reject" (default): raise JobStartConflict ONLY if a job
            for (slug, kind) is currently RUNNING (review 🔴-1 — done/
            error jobs in their grace window don't conflict; the
            user clicking Generate right after a successful generate
            should start a new job, not get 409'd for 60 seconds).
            "cancel_existing": "latest wins". A running job for the
            same (slug, kind) is cancelled + immediate-GC'd; a done/
            error job in grace is silently evicted (popped from the
            store without flipping its status — it isn't cancelled,
            it's just being made room for). Used by preview (§16.2.6)
            for the debounce-restart UX.

        Returns
        -------
        str
            The new job's job_id (UUID4 hex).

        Raises
        ------
        JobStartConflict
            When `concurrent_policy=POLICY_REJECT` and a RUNNING job
            for the same (slug, kind) exists.
        ValueError
            On invalid `concurrent_policy`.
        """
        if concurrent_policy not in _VALID_POLICIES:
            raise ValueError(
                f"invalid concurrent_policy={concurrent_policy!r}; "
                f"expected one of {sorted(_VALID_POLICIES)}"
            )

        with self._lock:
            # Lazy GC first — clears expired jobs so the (slug, kind)
            # existence check below doesn't fire on stale entries.
            self._gc_under_lock()

            if concurrent_policy == POLICY_REJECT:
                # Review 🔴-1: REJECT only conflicts with RUNNING jobs.
                # Done/error jobs in their grace window are lingering
                # for the frontend's final-status poll, not occupying
                # the "slot" — a new generate may start.
                running = self._find_for_slug_kind_under_lock(
                    slug, kind, statuses=(JOB_STATUS_RUNNING,),
                )
                if running is not None:
                    raise JobStartConflict(running.job_id)
            else:
                # POLICY_CANCEL_EXISTING — "latest wins" (§16.2.6).
                # Find ANY existing job for (slug, kind). If running,
                # cancel + immediate-GC. If terminal-in-grace, just
                # pop (review 🟡-2: don't retroactively flip a done
                # job's status to "cancelled" — it really did finish).
                existing = self._find_for_slug_kind_under_lock(
                    slug, kind, statuses=_ALL_STATUSES,
                )
                if existing is not None:
                    if existing.status == JOB_STATUS_RUNNING:
                        self._cancel_under_lock(existing)
                    else:
                        # Silent eviction — the older terminal job
                        # is making room for the new one.
                        self._jobs.pop(existing.job_id, None)

            job_id = uuid.uuid4().hex
            job = _Job(job_id=job_id, slug=slug, kind=kind)
            self._jobs[job_id] = job

            thread = threading.Thread(
                target=self._run,
                args=(job, stream_factory, on_result),
                name=f"job-{kind}-{slug}-{job_id[:8]}",
                daemon=True,
            )
            job.thread = thread

        # Start the thread OUTSIDE the lock. start() can block briefly
        # on OS thread creation; we don't want to hold the store lock
        # while that runs. The Job is already in self._jobs so callers
        # racing to GET its state see status=running immediately.
        thread.start()
        logger.info(
            "started job %s kind=%s slug=%s thread=%s",
            job_id, kind, slug, thread.name,
        )
        return job_id

    def get(self, job_id: str) -> JobSnapshot | None:
        """Return an immutable snapshot of the job's current state,
        or None if no such job exists (never existed, GC'd, or wrong
        id). The frontend treats None as 404.
        """
        with self._lock:
            # Lazy GC on every read — keeps the store small without
            # a background sweep thread.
            self._gc_under_lock()
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return _snapshot(job)

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of a running job. Returns True if a
        cancellation flag was set (job was found + was still running),
        False otherwise (job not found, or already in a terminal
        status). Cancellation is observed by the worker thread
        BETWEEN yields — strategies that take seconds to compute a
        single pick may not bail until that yield completes.

        Cancelled jobs are GC'd immediately under the lock. The
        worker thread continues running until its current yield
        completes + the next iteration's check sees `cancelled=True`,
        but its state is already gone from the store — subsequent
        get() returns None. The Python GIL means the worker can't
        observe its own `_jobs` deletion; that's fine.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status in _TERMINAL_STATUSES:
                # Already done / errored / cancelled — nothing to do.
                # Still report False so callers can distinguish
                # "actually cancelled the work" from "no-op".
                return False
            self._cancel_under_lock(job)
            return True

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _run(
        self,
        job: _Job,
        stream_factory: Callable[[], Iterator["ProgressEvent | GenerationResult"]],
        on_result: Callable[[GenerationResult], dict[str, Any]] | None,
    ) -> None:
        """Worker-thread body. Drains the engine stream + updates job
        state. Catches every exception the engine + on_result might
        raise; records as status=error.

        Decision G invariants are enforced here:
        * Exactly ONE GenerationResult per stream. Multi-Result yields
          → status=error with a synthetic engine-bug message.
        * No GenerationResult by stream end → status=error.
        * Mid-stream exceptions → status=error with class name + msg.
        """
        result_obj: GenerationResult | None = None
        try:
            for event in stream_factory():
                # Decision F cancellation point. Observed BETWEEN
                # yields — the engine doesn't itself check; we just
                # stop pulling from the iterator. Python GCs the
                # generator + its frame state when the for-loop exits.
                if job.cancelled:
                    logger.info(
                        "job %s cancelled mid-stream (after %d events)",
                        job.job_id, job.current,
                    )
                    return

                if isinstance(event, ProgressEvent):
                    with self._lock:
                        # Re-check under lock so a cancel() racing the
                        # update doesn't silently overwrite the
                        # cancelled status.
                        if job.status == JOB_STATUS_CANCELLED:
                            return
                        job.phase = event.phase
                        job.current = event.current
                        job.total = event.total
                        job.label = event.label
                elif isinstance(event, GenerationResult):
                    if result_obj is not None:
                        # Engine bug per Decision G; surface as error.
                        raise AssertionError(
                            "engine bug: generate_stream yielded "
                            "multiple GenerationResult objects"
                        )
                    result_obj = event
                else:
                    raise AssertionError(
                        f"unexpected event type from stream: {type(event)!r}"
                    )

            if result_obj is None:
                raise AssertionError(
                    "engine bug: generate_stream yielded no GenerationResult"
                )

            # Decision F cancellation point — observed AFTER the Result
            # yield but BEFORE on_result runs. Without this check, a
            # cancel issued between the Result yield and on_result still
            # commits save_playlist + advance_cursors (c3's on_result
            # closure has DB side effects). The user clicks Cancel,
            # indicator vanishes, but the playlist row was created
            # anyway → stale state requiring §16.3 cleanup. The atomic
            # bool read is cheap; the (microscopic) race between this
            # check and on_result actually starting is acceptable —
            # the alternative (holding the store lock across on_result)
            # would block every poller for the duration of save_playlist.
            if job.cancelled:
                logger.info(
                    "job %s cancelled after Result yield; skipping on_result",
                    job.job_id,
                )
                return

            # Persistence + result-body construction in the worker
            # thread. Engine work is done; this is the post-generation
            # save_playlist + advance_cursors path (composed by the
            # endpoint via the on_result closure).
            body = on_result(result_obj) if on_result is not None else {}

        except BaseException as e:  # noqa: BLE001 — runner catches all
            # KeyboardInterrupt would normally propagate; in a worker
            # thread it can't reach the main thread anyway. Treat as
            # error like everything else.
            logger.warning(
                "job %s failed: %s: %s",
                job.job_id, type(e).__name__, e,
            )
            logger.debug("job %s traceback:\n%s", job.job_id, traceback.format_exc())
            with self._lock:
                # Don't overwrite a cancellation that won the race.
                if job.status == JOB_STATUS_CANCELLED:
                    return
                job.status = JOB_STATUS_ERROR
                job.error = {
                    "message": str(e) or type(e).__name__,
                    "kind": type(e).__name__,
                }
                self._mark_finished_under_lock(job)
            return

        # Success path.
        with self._lock:
            if job.status == JOB_STATUS_CANCELLED:
                # Cancelled after the result yielded but before we
                # took the lock — honour the cancellation.
                return
            job.status = JOB_STATUS_DONE
            job.result = body
            self._mark_finished_under_lock(job)
        logger.info("job %s completed (slug=%s kind=%s)", job.job_id, job.slug, job.kind)

    # ------------------------------------------------------------------
    # Lock-held helpers (callers must hold self._lock)
    # ------------------------------------------------------------------

    def _find_for_slug_kind_under_lock(
        self, slug: str, kind: str, *,
        statuses: tuple[str, ...],
    ) -> _Job | None:
        """Return the existing job for the (slug, kind) tuple whose
        status is in `statuses`, if any. Used by start():

        * POLICY_REJECT passes `statuses=(JOB_STATUS_RUNNING,)` — only
          a currently-running job conflicts with a new start (review
          🔴-1: done/error jobs in grace don't conflict).
        * POLICY_CANCEL_EXISTING passes `_ALL_STATUSES` — any existing
          entry (running OR terminal-in-grace) needs to be evicted to
          make room for the new "latest" job.

        Iterates the dict — O(N) but N is the number of in-flight or
        recently-completed jobs, expected ≪ 100 for typical OSS use.
        If this becomes a hotspot, add a (slug, kind) → job_id index.
        """
        for j in self._jobs.values():
            if j.slug == slug and j.kind == kind and j.status in statuses:
                return j
        return None

    def _cancel_under_lock(self, job: _Job) -> None:
        """Mark the job cancelled + GC it immediately (no grace).
        Caller holds self._lock.

        Worker thread observes `cancelled=True` on its next yield
        and bails — but the Job is already gone from self._jobs, so
        get() will start returning None right away. That's the
        contract: cancellation is "fire and forget"; the frontend
        sees the indicator vanish on the next poll's 404.
        """
        job.cancelled = True
        job.status = JOB_STATUS_CANCELLED
        self._mark_finished_under_lock(job)
        # Decision F's "immediate GC of cancelled jobs" — pop NOW.
        # Without this, debounce-restart spawns ~60 jobs/min in
        # cancelled-but-not-yet-GC'd state.
        self._jobs.pop(job.job_id, None)

    def _mark_finished_under_lock(self, job: _Job) -> None:
        """Stamp `finished_at` + `finished_monotonic`. Caller holds
        the lock. Used by all three terminal transitions (done /
        error / cancel)."""
        # Avoid double-stamp if we somehow take this path twice
        # (defensive; shouldn't happen given the lock).
        if job.finished_monotonic is None:
            job.finished_at = time.time()
            job.finished_monotonic = self._monotonic()

    def _gc_under_lock(self) -> None:
        """Sweep terminal jobs whose grace window elapsed. Caller
        holds self._lock. Cancelled jobs are already gone (immediate
        GC in `_cancel_under_lock`); this only reaps done/error
        jobs whose `finished_monotonic + grace_seconds` is in the
        past.
        """
        now = self._monotonic()
        expired = [
            jid for jid, j in self._jobs.items()
            if j.status in (JOB_STATUS_DONE, JOB_STATUS_ERROR)
            and j.finished_monotonic is not None
            and now - j.finished_monotonic > self._grace
        ]
        for jid in expired:
            self._jobs.pop(jid, None)
        if expired:
            logger.debug("GC'd %d expired job(s): %s", len(expired), expired)

    # ------------------------------------------------------------------
    # Test introspection
    # ------------------------------------------------------------------

    def _len_for_test(self) -> int:
        """How many jobs are currently in the store. Tests use this
        to assert GC behaviour. NOT public API — leading underscore.
        """
        with self._lock:
            return len(self._jobs)


# ----------------------------------------------------------------------
# App-state binding (consumed by c3's API endpoints)
# ----------------------------------------------------------------------

def get_job_store() -> JobStore:
    """Lazy module-level singleton accessor. The FastAPI app calls
    this on first request (or pre-init in main.py if we want to
    eagerly create the store). Tests can patch this with a fresh
    JobStore per test via the dependency-override mechanism.

    Why a module-level singleton (not app.state) for v1: simplicity.
    The store is process-wide stateless-ish — its only state is the
    in-flight jobs themselves. A test that swaps in a fresh instance
    via FastAPI's `app.dependency_overrides[get_job_store] = ...`
    gets full isolation without us threading the store through every
    layer. If we later want multiple stores (e.g. per-tenant in a
    multi-tenant SaaS), revisit.
    """
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = JobStore()
    return _SINGLETON


_SINGLETON: JobStore | None = None


def _reset_singleton_for_tests() -> None:
    """Reset the module singleton. Tests call this between cases when
    they don't use `dependency_overrides` (e.g. unit tests that
    `get_job_store()` directly). NOT public API.
    """
    global _SINGLETON
    _SINGLETON = None
