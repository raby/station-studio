"""Pydantic request/response schemas."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class MetaResponse(BaseModel):
    """Response shape for GET /api/meta — see DESIGN.md §14.19.

    Lists are server-side sorted for deterministic dropdown order. The
    frontend treats this as cacheable static data (constants don't
    change without a deploy); see web/src/lib/meta.ts.
    """

    strategies: list[str]
    rng: list[str]
    roles: list[str]
    cadences: list[str]


class StationCreate(BaseModel):
    # Slug is immutable after creation. Pattern mirrors the wizard's client-side
    # validation: lowercase alphanumeric + hyphens, must start with [a-z0-9].
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    template: dict[str, Any]


class StationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    template: dict[str, Any]


class SourceOut(BaseModel):
    id: int
    name: str
    track_count: int
    # §14.35: "content" (default — engine samples from this pool) or
    # "reference" (declared for cross-reference but never sampled).
    role: Literal["content", "reference"] = "content"


class ImportRequest(BaseModel):
    path: str
    name: str
    # §14.35: optional; defaults to "content". Pass "reference" for
    # pools imported only for cross-reference (e.g. weekly's MASTER).
    # Literal so Pydantic rejects invalid values at 422 (validation
    # layer) before the engine's defense-in-depth check fires.
    role: Literal["content", "reference"] = "content"


class GenerateRequest(BaseModel):
    week: int = Field(ge=1)
    seed: int | None = None


class GenerateResponse(BaseModel):
    playlist_id: int
    trace_id: str
    station: str
    week: int
    slot_count: int


class TrackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    artist: str | None
    album: str | None


class PlaylistEntryOut(BaseModel):
    position: int
    source_name: str | None
    # §14.31 — strategy tag (window/pick/sequential/jingle/mix/weighted/
    # prev_ref). Null for pre-§14.31 entries that haven't been backfilled.
    slot_type: str | None
    track: TrackOut


class PlaylistOut(BaseModel):
    id: int
    station: str
    week: int
    trace_id: str
    entries: list[PlaylistEntryOut]


class PlaylistSummary(BaseModel):
    """Lightweight row for station-playlist lists (no entries)."""
    id: int
    week: int
    trace_id: str
    generated_at: str | None
    entry_count: int


# §14.29 — per-station rules CRUD


class RuleOut(BaseModel):
    """One rule's effective state for a station.

    `value` is what the engine will see; `default_value` is the
    engine's fallback when no override exists; `is_override` tells
    a UI whether to render "set" vs "default" controls.
    """
    key: str
    value: int
    default_value: int
    description: str
    max_value: int | None
    is_override: bool


class RuleListResponse(BaseModel):
    """All known rules, sorted by key for deterministic UI rendering."""
    rules: list[RuleOut]


class RuleValueIn(BaseModel):
    """PUT body — just the new integer value."""
    value: int


# §16.11 c3 — job runner endpoints


class JobStartResponse(BaseModel):
    """201 body for `POST /api/stations/{slug}/jobs/generate`. Mirrors
    DESIGN.md §16.11 Decision A's "starts a background job, returns
    {job_id, status: running}" — the frontend immediately begins
    polling `GET /api/jobs/{job_id}`.
    """
    job_id: str
    status: Literal["running"] = "running"


class JobErrorBody(BaseModel):
    """Shape inside `JobStatusResponse.error` when status=error.

    `kind` is the Python exception class name (RuntimeError, ValueError,
    AssertionError for engine-bug paths). Frontends can show
    `kind`-specific messaging or treat as a generic failure.
    """
    message: str
    kind: str


# §16.2.2 c1 — visual block editor (read-only canvas)


class EditorSidecarResponse(BaseModel):
    """200 body for `GET /api/stations/{slug}/sidecar`. Per DESIGN.md
    §16.2.2 Decision E: the frontend NEVER handles "no sidecar" —
    always renders the canvas against a valid sidecar. The server
    synthesizes a trivial sidecar when the committed `.editor.json`
    is absent OR malformed.

    Fields:
      * `template` — the station's parsed-from-DB template dict.
        Same shape `StationOut.template` carries.
      * `sidecar` — either the committed `stations/{slug}.editor.json`
        content (after `validate_sidecar` passes) OR the result of
        `synthesize_trivial_sidecar(template)`. Always non-null +
        always valid.
      * `sidecar_error` — null on the happy path. Populated with the
        validator's error message (a string) when a committed sidecar
        exists but failed parse/validation, in which case `sidecar`
        carries the trivial fallback. The frontend renders a warning
        banner so the hobbyist can keep working while they figure out
        what broke their sidecar.

    `template` and `sidecar` are typed as `dict` for v1: the grammar
    is validated at the `engine.editor_metadata` layer; Pydantic isn't
    a second validation surface (and the structural variation —
    flat_slots vs row_template, optional macros, etc. — would need a
    discriminated-union model that buys little over the existing
    engine-side parser).
    """
    template: dict[str, Any]
    sidecar: dict[str, Any]
    sidecar_error: str | None = None


class JobStatusResponse(BaseModel):
    """200 body for `GET /api/jobs/{job_id}`. Mirrors `JobSnapshot`
    in api/job_runner.py with one transformation: monotonic-correlated
    UNIX timestamps are converted to ISO-8601 strings per the design
    doc's example ("started_at": "2026-05-27T14:32:01Z"). The
    `started_at`/`finished_at` floats are stored internally for clock-
    skew immunity but humans want ISO at the API boundary.

    `result` is whatever the endpoint's `on_result` callback returned —
    for the generate endpoint that's the same shape as
    `GenerateResponse` (playlist_id / trace_id / station / week /
    slot_count). Pydantic doesn't type-check this nested dict; trust
    the endpoint to populate consistently.
    """
    job_id: str
    slug: str
    kind: str
    status: Literal["running", "done", "error", "cancelled"]
    phase: str
    current: int
    total: int
    label: str | None = None
    result: dict[str, Any] | None = None
    error: JobErrorBody | None = None
    started_at: str  # ISO 8601 UTC
    finished_at: str | None = None
