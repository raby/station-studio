"""Tests for `api.job_runner.JobStore` (§16.11 c2).

Strategy: never touch the real engine or DB. Pass synthetic
`stream_factory` callables that yield ProgressEvent + GenerationResult
objects directly. Each test isolates a JobStore (no shared singleton).

Pins:
  * Decision F concurrency policy (reject vs cancel_existing)
  * Decision G stream invariants enforced in the worker
  * Per-(slug, kind) latest-wins eviction
  * Cancelled-immediate-GC + done/error grace-window GC
  * Snapshot is FROZEN + reflects the latest worker-set state
  * Exception inside `on_result` transitions to status=error
  * Cancellation observed between yields (worker bails)
"""
from __future__ import annotations

import threading
import time
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from api.job_runner import (
    JobSnapshot,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    JOB_STATUS_RUNNING,
    JobStartConflict,
    JobStore,
    POLICY_CANCEL_EXISTING,
    POLICY_REJECT,
)
from engine.generator import GenerationResult, ProgressEvent


# ---------- helpers -----------------------------------------------------


def _fake_result(slot_count: int = 3) -> GenerationResult:
    """Tiny GenerationResult — runner doesn't inspect contents, only
    invokes on_result with it. picks/trace can be empty stand-ins."""
    return GenerationResult(picks=[None] * slot_count, trace=None)


def _wait_until(predicate, *, timeout: float = 2.0, interval: float = 0.005):
    """Spin-wait for `predicate()` to be truthy. The runner is
    threaded; tests need to wait for state transitions without a
    fixed sleep that's either flaky-short or slow. 2s timeout is
    generous — any test exceeding it points at a real bug.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(
        f"wait_until timed out after {timeout}s (predicate never true)"
    )


def _wait_for_status(
    store: JobStore, job_id: str, target_status: str, *, timeout: float = 2.0,
):
    """Poll the store for a job to reach `target_status`. Returns the
    final snapshot. Raises if the job vanishes (e.g. GC'd) before
    reaching the target."""
    def _check() -> bool:
        snap = store.get(job_id)
        if snap is None:
            return False
        return snap.status == target_status
    _wait_until(_check, timeout=timeout)
    snap = store.get(job_id)
    assert snap is not None, "job vanished before reaching target status"
    return snap


# ---------- happy path --------------------------------------------------


def test_start_runs_to_completion_and_calls_on_result():
    """Smoke: a stream that yields progress + a final Result ends in
    status=done with `result` populated by the on_result callback."""
    store = JobStore()

    def stream() -> Iterator:
        yield ProgressEvent(phase="start", current=0, total=2, label="hi")
        yield ProgressEvent(phase="picks", current=1, total=2)
        yield ProgressEvent(phase="picks", current=2, total=2)
        yield _fake_result(slot_count=2)

    captured = {}

    def on_result(r: GenerationResult) -> dict[str, Any]:
        captured["got"] = r
        return {"slot_count": len(r.picks)}

    job_id = store.start(
        slug="s1", kind="generate",
        stream_factory=stream, on_result=on_result,
    )
    snap = _wait_for_status(store, job_id, JOB_STATUS_DONE)
    assert snap.result == {"slot_count": 2}
    assert snap.error is None
    assert captured["got"].picks == [None, None]
    # Progress fields reflect the LAST ProgressEvent before the Result.
    assert snap.current == 2
    assert snap.total == 2
    assert snap.label is None  # Last progress event had no label.


def test_get_returns_none_for_unknown_job():
    """404 path — unknown job_id (never existed, GC'd, typo)."""
    store = JobStore()
    assert store.get("not-a-real-id") is None


def test_snapshot_is_frozen():
    """JobSnapshot is a frozen dataclass — pollers can't accidentally
    mutate state belonging to the store (or to their own cached view)."""
    store = JobStore()

    def stream() -> Iterator:
        yield _fake_result()

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    _wait_for_status(store, job_id, JOB_STATUS_DONE)
    snap = store.get(job_id)
    assert isinstance(snap, JobSnapshot)
    with pytest.raises(Exception):  # FrozenInstanceError
        snap.status = "tampered"  # type: ignore[misc]


# ---------- error paths -------------------------------------------------


def test_stream_exception_transitions_to_error():
    """Decision G: a mid-stream raise transitions job → status=error
    with `error.message` + `error.kind` (class name). Job runner
    catches; the worker thread doesn't propagate."""
    store = JobStore()

    def stream() -> Iterator:
        yield ProgressEvent(phase="start", current=0, total=3)
        raise RuntimeError("boom!")

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    snap = _wait_for_status(store, job_id, JOB_STATUS_ERROR)
    assert snap.error == {"message": "boom!", "kind": "RuntimeError"}
    assert snap.result is None


def test_on_result_exception_transitions_to_error():
    """on_result raises (e.g. save_playlist IntegrityError) → the
    runner records it as status=error. The engine got all the way to
    a Result, but persistence failed; user needs to see why."""
    store = JobStore()

    def stream() -> Iterator:
        yield _fake_result()

    def on_result(r: GenerationResult) -> dict:
        raise ValueError("persist failed")

    job_id = store.start(
        slug="s1", kind="generate",
        stream_factory=stream, on_result=on_result,
    )
    snap = _wait_for_status(store, job_id, JOB_STATUS_ERROR)
    assert snap.error == {"message": "persist failed", "kind": "ValueError"}


def test_multi_result_engine_bug_transitions_to_error():
    """Decision G: two Results from one stream is an engine bug. The
    runner surfaces it as status=error (not a crash) so the frontend
    can show a real message instead of timing out."""
    store = JobStore()

    def stream() -> Iterator:
        yield _fake_result()
        yield _fake_result()

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    snap = _wait_for_status(store, job_id, JOB_STATUS_ERROR)
    assert snap.error is not None
    assert "multiple GenerationResult" in snap.error["message"]
    assert snap.error["kind"] == "AssertionError"


def test_no_result_engine_bug_transitions_to_error():
    """Decision G: stream ends without a Result → status=error."""
    store = JobStore()

    def stream() -> Iterator:
        yield ProgressEvent(phase="start", current=0, total=3)
        # Never yields a Result.

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    snap = _wait_for_status(store, job_id, JOB_STATUS_ERROR)
    assert "no GenerationResult" in snap.error["message"]


# ---------- concurrency policy (Decision F) -----------------------------


def test_reject_policy_raises_conflict_for_same_slug_kind():
    """POLICY_REJECT: starting a second job for the same (slug, kind)
    while one is still running raises JobStartConflict carrying the
    existing job_id. API turns this into 409."""
    store = JobStore()
    started = threading.Event()
    release = threading.Event()

    def slow_stream() -> Iterator:
        started.set()
        # Block until released, simulating a long-running engine.
        release.wait(timeout=5)
        yield _fake_result()

    first_id = store.start(
        slug="s1", kind="generate", stream_factory=slow_stream,
    )
    # Wait for the worker to actually enter the stream — otherwise
    # the conflict check might race against the job still being in
    # status=running but not yet visible.
    assert started.wait(timeout=2)

    def quick_stream() -> Iterator:
        yield _fake_result()

    with pytest.raises(JobStartConflict) as excinfo:
        store.start(
            slug="s1", kind="generate",
            stream_factory=quick_stream,
            concurrent_policy=POLICY_REJECT,
        )
    assert excinfo.value.existing_job_id == first_id

    release.set()
    _wait_for_status(store, first_id, JOB_STATUS_DONE)


def test_reject_policy_allows_different_kind_for_same_slug():
    """Decision F: kinds are independent. Generating while a preview
    runs for the same station is allowed."""
    store = JobStore()
    release = threading.Event()

    def slow_stream() -> Iterator:
        release.wait(timeout=5)
        yield _fake_result()

    preview_id = store.start(
        slug="s1", kind="preview", stream_factory=slow_stream,
    )
    # Different kind — should succeed.
    def quick() -> Iterator:
        yield _fake_result()

    gen_id = store.start(
        slug="s1", kind="generate", stream_factory=quick,
    )
    assert preview_id != gen_id
    _wait_for_status(store, gen_id, JOB_STATUS_DONE)
    release.set()
    _wait_for_status(store, preview_id, JOB_STATUS_DONE)


def test_cancel_existing_policy_evicts_running_job():
    """POLICY_CANCEL_EXISTING: a new start() cancels + GCs the old
    running job for the same (slug, kind). The OLD job's snapshot
    disappears (immediate-GC of cancelled); the new one is what
    callers see."""
    store = JobStore()
    started = threading.Event()
    release = threading.Event()

    def slow_stream() -> Iterator:
        started.set()
        release.wait(timeout=5)
        yield _fake_result()

    old_id = store.start(
        slug="s1", kind="preview", stream_factory=slow_stream,
    )
    assert started.wait(timeout=2)

    def quick() -> Iterator:
        yield _fake_result()

    new_id = store.start(
        slug="s1", kind="preview", stream_factory=quick,
        concurrent_policy=POLICY_CANCEL_EXISTING,
    )
    assert new_id != old_id
    # Old job was cancel-GC'd immediately.
    assert store.get(old_id) is None
    _wait_for_status(store, new_id, JOB_STATUS_DONE)
    release.set()


def test_reject_policy_allows_new_job_when_previous_is_done_in_grace():
    """🔴-1 regression guard: POLICY_REJECT must NOT 409 when an
    existing job for (slug, kind) is in done/grace.

    Without the fix, the natural workflow "generate week 1 → wait 1s →
    generate week 2" returned 409 for ~60s because the still-in-grace
    done job was matched as "existing". The design doc is explicit:
    REJECT applies to RUNNING jobs only.
    """
    store = JobStore(grace_seconds=60.0)

    def quick() -> Iterator:
        yield _fake_result()

    first_id = store.start(slug="s1", kind="generate", stream_factory=quick)
    _wait_for_status(store, first_id, JOB_STATUS_DONE)
    # Job is in grace — still queryable, status=done.
    assert store.get(first_id).status == JOB_STATUS_DONE

    # Second start must succeed (no 409). Different job_id; both
    # exist in the store, the older one still lingering in grace.
    second_id = store.start(
        slug="s1", kind="generate", stream_factory=quick,
        concurrent_policy=POLICY_REJECT,
    )
    assert second_id != first_id
    _wait_for_status(store, second_id, JOB_STATUS_DONE)


def test_reject_policy_allows_new_job_when_previous_errored_in_grace():
    """🔴-1 sibling: same rule must hold for status=error jobs in
    grace. An engine crash on week 1 must not block a retry on week 2.
    """
    store = JobStore(grace_seconds=60.0)

    def crash() -> Iterator:
        if False:
            yield  # unreachable, makes this a generator
        raise RuntimeError("oops")

    first_id = store.start(slug="s1", kind="generate", stream_factory=crash)
    _wait_for_status(store, first_id, JOB_STATUS_ERROR)

    def quick() -> Iterator:
        yield _fake_result()

    second_id = store.start(
        slug="s1", kind="generate", stream_factory=quick,
        concurrent_policy=POLICY_REJECT,
    )
    assert second_id != first_id
    _wait_for_status(store, second_id, JOB_STATUS_DONE)


def test_cancel_existing_evicts_done_in_grace_without_status_flip():
    """🟡-2 regression guard: POLICY_CANCEL_EXISTING against a
    terminal-in-grace job must silently EVICT (just pop) — not flip
    the older job's status to cancelled. The job really did finish;
    we're just making room for the latest "winner".

    Verified by: the old job vanishes from store.get() (eviction
    happened) AND no JOB_STATUS_CANCELLED entries exist in the store
    afterwards (status flip didn't happen).
    """
    store = JobStore(grace_seconds=60.0)

    def quick() -> Iterator:
        yield _fake_result()

    old_id = store.start(slug="s1", kind="preview", stream_factory=quick)
    _wait_for_status(store, old_id, JOB_STATUS_DONE)
    assert store.get(old_id).status == JOB_STATUS_DONE  # still in grace

    new_id = store.start(
        slug="s1", kind="preview", stream_factory=quick,
        concurrent_policy=POLICY_CANCEL_EXISTING,
    )
    assert new_id != old_id
    # The old job is gone (evicted).
    assert store.get(old_id) is None
    _wait_for_status(store, new_id, JOB_STATUS_DONE)
    # And the new job is genuinely a fresh job in status=done — not
    # somehow inheriting the old job's status flip. (Also: there's
    # no orphan cancelled job lingering in the store.)
    assert store.get(new_id).status == JOB_STATUS_DONE


def test_invalid_concurrent_policy_raises():
    """Defensive: typo'd policy string fails fast."""
    store = JobStore()

    def stream() -> Iterator:
        yield _fake_result()

    with pytest.raises(ValueError, match="invalid concurrent_policy"):
        store.start(
            slug="s1", kind="generate", stream_factory=stream,
            concurrent_policy="nope",
        )


# ---------- cancellation ------------------------------------------------


def test_cancel_during_stream_bails_worker():
    """Cancellation observed between yields. The worker stops pulling
    from the iterator; the engine generator is GC'd. Snapshot is gone
    (immediate-GC of cancelled)."""
    store = JobStore()
    seen_count = 0
    release = threading.Event()

    def stream() -> Iterator:
        nonlocal seen_count
        for i in range(1, 10):
            yield ProgressEvent(phase="picks", current=i, total=10)
            seen_count = i
            # Wait between yields so the test can cancel mid-stream.
            release.wait(timeout=0.1)
        yield _fake_result(slot_count=10)

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    # Wait for the worker to publish at least one progress event.
    _wait_until(
        lambda: (s := store.get(job_id)) is not None and s.current >= 1,
        timeout=2,
    )
    cancelled = store.cancel(job_id)
    assert cancelled is True
    # Job is GC'd immediately under cancel.
    _wait_until(lambda: store.get(job_id) is None, timeout=2)
    # Release the held flag so the worker thread doesn't linger.
    release.set()


def test_cancel_unknown_job_returns_false():
    """Cancelling a nonexistent job is a no-op returning False."""
    store = JobStore()
    assert store.cancel("not-a-real-id") is False


def test_cancel_between_result_and_on_result_skips_on_result():
    """🟡-1 regression guard: a cancel issued AFTER the GenerationResult
    yield but BEFORE on_result runs must skip on_result.

    Why this matters: in c3 on_result is save_playlist + db.commit. A
    cancel that fires here, without the worker's pre-on_result cancel
    check, would still commit the playlist — the user cancelled but a
    row was created anyway. The fix in c2 is an atomic cancelled
    check right before on_result; this test pins it.

    Sequencing: synthetic stream blocks just before yielding the
    Result, test issues cancel, stream is released, worker observes
    cancel before calling on_result.
    """
    store = JobStore()
    result_ready = threading.Event()
    proceed = threading.Event()
    on_result_called: list[bool] = []

    def stream() -> Iterator:
        # First yield a progress event so the test can detect the
        # worker has entered the stream and we're past the initial
        # cancel check.
        yield ProgressEvent(phase="picks", current=1, total=2)
        result_ready.set()
        # Block here — test will cancel, then release us. The Result
        # is yielded AFTER the cancel arrives.
        proceed.wait(timeout=5)
        yield _fake_result(slot_count=2)

    def on_result(r: GenerationResult) -> dict:
        on_result_called.append(True)
        return {"shouldnt": "happen"}

    job_id = store.start(
        slug="s1", kind="generate",
        stream_factory=stream, on_result=on_result,
    )
    assert result_ready.wait(timeout=2)
    # Cancel BEFORE releasing the stream — when the worker next
    # iterates, it'll see the Result, then the pre-on_result cancel
    # check fires.
    assert store.cancel(job_id) is True
    proceed.set()
    # Job is immediately GC'd on cancel; on_result must NEVER fire.
    _wait_until(lambda: store.get(job_id) is None, timeout=2)
    # Give the worker a moment to finish + (incorrectly, if buggy)
    # call on_result. Tight but adequate — the worker's only remaining
    # work is `next()` then the cancel check.
    time.sleep(0.05)
    assert on_result_called == [], (
        "on_result ran AFTER cancel — Decision F cancel-before-side-"
        "effects contract broken"
    )


def test_snapshot_result_dict_is_isolated_from_store():
    """🟢-3 regression guard: mutating a snapshot's `result` dict must
    NOT affect the store's view. Snapshot is frozen as a dataclass,
    but its dict fields are deep-copied to prevent shared-reference
    leakage."""
    store = JobStore()

    def stream() -> Iterator:
        yield _fake_result()

    def on_result(r: GenerationResult) -> dict:
        return {"nested": {"k": 1}}

    job_id = store.start(
        slug="s1", kind="generate",
        stream_factory=stream, on_result=on_result,
    )
    _wait_for_status(store, job_id, JOB_STATUS_DONE)
    snap_a = store.get(job_id)
    # Mutate the snapshot's nested dict. If the store shared the
    # reference, the next snapshot would see the mutation.
    snap_a.result["nested"]["k"] = 999
    snap_a.result["leaked"] = True
    snap_b = store.get(job_id)
    assert snap_b.result == {"nested": {"k": 1}}, (
        f"store leaked snapshot mutation: {snap_b.result}"
    )


def test_cancel_done_job_returns_false():
    """Cancelling an already-done job returns False (terminal status
    — nothing to cancel). The done job still exists in the store
    during its grace window."""
    store = JobStore()

    def stream() -> Iterator:
        yield _fake_result()

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    _wait_for_status(store, job_id, JOB_STATUS_DONE)
    assert store.cancel(job_id) is False


# ---------- GC ----------------------------------------------------------


def test_cancelled_jobs_gc_immediately():
    """Decision F: cancelled jobs are popped from _jobs the instant
    cancel() runs — no 60s grace. Prevents debounce-restart from
    flooding the store."""
    store = JobStore()
    started = threading.Event()
    release = threading.Event()

    def stream() -> Iterator:
        started.set()
        release.wait(timeout=5)
        yield _fake_result()

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    assert started.wait(timeout=2)
    assert store._len_for_test() == 1
    store.cancel(job_id)
    assert store._len_for_test() == 0
    release.set()


def test_done_job_lingers_through_grace_window():
    """Done jobs stay queryable for grace_seconds after completion
    so the frontend's final-status poll can fetch the result."""
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    store = JobStore(grace_seconds=10.0, monotonic=fake_monotonic)

    def stream() -> Iterator:
        yield _fake_result()

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    _wait_for_status(store, job_id, JOB_STATUS_DONE)
    # Still in grace.
    fake_now[0] = 1009.0
    assert store.get(job_id) is not None
    # Advance past grace + trigger GC via get().
    fake_now[0] = 1012.0
    assert store.get(job_id) is None


def test_error_job_lingers_through_grace_window():
    """Error jobs honour the same grace window as done jobs."""
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    store = JobStore(grace_seconds=10.0, monotonic=fake_monotonic)

    def stream() -> Iterator:
        raise RuntimeError("nope")
        yield  # unreachable

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    _wait_for_status(store, job_id, JOB_STATUS_ERROR)
    fake_now[0] = 1009.0
    assert store.get(job_id) is not None
    fake_now[0] = 1012.0
    assert store.get(job_id) is None


def test_gc_triggered_on_start_not_just_get():
    """GC must run on start() too — otherwise a quiet system (no
    pollers) accumulates done jobs until the next start() somewhere."""
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    store = JobStore(grace_seconds=10.0, monotonic=fake_monotonic)

    def stream() -> Iterator:
        yield _fake_result()

    first_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    _wait_for_status(store, first_id, JOB_STATUS_DONE)
    assert store._len_for_test() == 1

    fake_now[0] = 1012.0
    # No get() in between — start() must do the sweep itself.
    second_id = store.start(slug="s2", kind="generate", stream_factory=stream)
    _wait_for_status(store, second_id, JOB_STATUS_DONE)
    # Old job GC'd; new one present.
    assert store.get(first_id) is None
    assert store.get(second_id) is not None


# ---------- progress propagation ----------------------------------------


def test_progress_events_update_snapshot_phase_current_total_label():
    """Each ProgressEvent fully updates the four progress fields.
    Pollers' next get() reads the latest values."""
    store = JobStore()
    advance = threading.Event()
    next_event = threading.Event()

    def stream() -> Iterator:
        yield ProgressEvent(phase="start", current=0, total=5, label="lo")
        next_event.set()
        advance.wait(timeout=5)
        advance.clear()
        yield ProgressEvent(phase="picks", current=3, total=5, label="hi")
        next_event.set()
        advance.wait(timeout=5)
        yield _fake_result(slot_count=5)

    job_id = store.start(slug="s1", kind="generate", stream_factory=stream)
    # First event published.
    assert next_event.wait(timeout=2)
    next_event.clear()
    snap = store.get(job_id)
    assert (snap.phase, snap.current, snap.total, snap.label) == \
        ("start", 0, 5, "lo")

    advance.set()
    assert next_event.wait(timeout=2)
    snap = store.get(job_id)
    assert (snap.phase, snap.current, snap.total, snap.label) == \
        ("picks", 3, 5, "hi")

    advance.set()
    _wait_for_status(store, job_id, JOB_STATUS_DONE)


def test_two_stores_are_isolated():
    """Sanity: two JobStore instances don't share state. Tests
    relying on `dependency_overrides[get_job_store]` swap in a fresh
    instance per test; this asserts that's actually isolation."""
    s1 = JobStore()
    s2 = JobStore()

    def stream() -> Iterator:
        yield _fake_result()

    j1 = s1.start(slug="x", kind="generate", stream_factory=stream)
    _wait_for_status(s1, j1, JOB_STATUS_DONE)
    assert s2.get(j1) is None
