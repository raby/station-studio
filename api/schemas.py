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


class StationSummary(BaseModel):
    """List-row shape for GET /api/stations (§16.12 stations list polish).

    Lighter than StationOut (no full template blob) + enriched with
    the per-station aggregates the landing page renders + sorts/filters
    on:

      * `cadence` — from template["cadence"]; drives the cadence
        filter chip.
      * `playlist_count` — number of generated playlists; drives the
        "has generations" vs "empty" filter.
      * `last_generated_week` / `last_generated_at` — the week + ISO
        timestamp of the MOST RECENTLY GENERATED playlist (by
        generated_at, NOT max week — a regen of an old week is the
        latest event). Powers the "Last generated: WEEKLY 026 (3
        days ago)" per-row line + the last-generation sort.
      * `updated_at` — Station.updated_at ISO; powers the
        "template last-modified" sort.

    The detail endpoint (GET /api/stations/{slug}) still returns the
    full StationOut with the template — this summary is list-only.
    """
    id: int
    slug: str
    name: str
    cadence: str
    playlist_count: int
    last_generated_week: int | None
    last_generated_at: str | None  # ISO 8601
    updated_at: str | None  # ISO 8601


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

    §16.14.t10 — broadened to cover both int + list rules:

      * ``kind`` (D1) is ``"int"`` for ``RULE_DEFAULTS`` keys,
        ``"list"`` for ``RULE_LIST_DEFAULTS`` keys. The frontend
        dispatches on this rather than inspecting value types.
      * ``value`` + ``default_value`` (D2) are ``int`` when
        ``kind == "int"``, ``list[str]`` when ``kind == "list"``.
        "list" today means list-of-string specifically; a future
        rule with different element types adds a new ``kind`` tag.
      * ``max_value`` is always ``None`` for list rules.

    Pre-t10 (the §14.29 + §16.10 surface) this schema was
    int-only. t10 keeps the int-rule path byte-identical when
    ``kind == "int"`` so the existing `<RulesPanel>` rendering
    keeps working; §16.14.t11 adds the `<ListRuleRow>` component
    for the list path.
    """
    key: str
    kind: str  # "int" | "list"
    value: int | list[str]
    default_value: int | list[str]
    description: str
    max_value: int | None
    is_override: bool


class RuleListResponse(BaseModel):
    """All known rules, sorted by key for deterministic UI rendering."""
    rules: list[RuleOut]


class RuleValueIn(BaseModel):
    """PUT body — `{value: int}` for int rules, `{value: [...]}` for
    list rules. The server dispatches by which registry the URL
    `{key}` belongs to (§16.14.t10).
    """
    value: int | list[str]


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
    # §16.2.3 — the station's `updated_at` at load time. The editor
    # carries it back in a block PATCH's `expected_updated_at` for
    # optimistic concurrency (Decision D2): the PATCH 409s if the row
    # moved since the editor loaded. ISO-8601 string.
    updated_at: str | None = None


# §16.19 — station detail richness (overview bundle)


class TemplateSummary(BaseModel):
    """At-a-glance template shape for the station detail page."""
    slot_count: int
    layout_form: Literal["flat_slots", "row_template"]
    # strategy → count of blocks using it (e.g. {"window": 2, "pick": 1}).
    strategy_counts: dict[str, int]
    cursor_persist: bool
    source_count: int


class PoolStatus(BaseModel):
    """One source the template references, joined to its imported pool
    (if any). `imported=False` surfaces a setup gap — a declared source
    with no SourcePlaylist row, which would 400 at generation time."""
    name: str
    role: str
    imported: bool
    track_count: int | None = None
    imported_at: str | None = None  # ISO-8601


class CursorPosition(BaseModel):
    """One persistent named cursor's current position (§14.30).
    Empty list for stateless templates."""
    name: str
    position: int


class GenerationsSummary(BaseModel):
    """Playlist-generation rollup for the station.

    `stale_count` = playlists generated BEFORE the last template edit
    (generated_at < station.updated_at) — they reflect a prior template
    version (§16.2.3 staleness hint, homed here)."""
    count: int
    last_generated_week: int | None = None
    last_generated_at: str | None = None
    stale_count: int


class StationOverview(BaseModel):
    """200 body for GET /api/stations/{slug}/overview (§16.19). One
    fetch bundles everything the rich station-detail page renders:
    identity + template summary + pool status + cursor positions +
    generation rollup. Mirrors the editor-sidecar one-fetch pattern."""
    slug: str
    name: str
    cadence: str
    rng: str
    updated_at: str | None = None
    template_summary: TemplateSummary
    pools: list[PoolStatus]
    cursors: list[CursorPosition]
    generations: GenerationsSummary


# §16.2.3 — atomic block editing (Decision B2: targeted PATCH).


class BlockAddress(BaseModel):
    """Storage address of a block in the template's layout. EITHER
    `slot_index` (flat_slots) OR `block_idx` (row_template). Mirrors
    the editor's AtomicRef, but `row_idx` is intentionally ABSENT —
    row_template stores ONE shared row template; editing block_idx j
    changes `layout.row_template[j]` for EVERY row. The render-time
    row_idx is irrelevant to storage (Decision B)."""
    slot_index: int | None = None
    block_idx: int | None = None


class BlockPatchRequest(BaseModel):
    """PATCH /api/stations/{slug}/blocks body.

    * `address` — which block to edit (XOR slot_index / block_idx).
    * `fields` — parameter fields to set; a null value CLEARS the key
      (removes an optional field). Only strategy-PARAMETER fields are
      accepted (Decision C); strategy/source/size are rejected 400.
    * `expected_updated_at` — optimistic-concurrency token from the
      sidecar GET. 409 if the station moved since (Decision D2).
    """
    address: BlockAddress
    fields: dict[str, Any]
    expected_updated_at: str | None = None


class BlockPatchResponse(BaseModel):
    """200 body for a successful block PATCH. Returns the updated
    block + the new `updated_at` so the editor can refresh its
    optimistic token for the next edit without a full reload."""
    block: dict[str, Any]
    updated_at: str | None = None


class TemplateExportResponse(BaseModel):
    """200 body for POST /api/stations/{slug}/export — the A3
    'commit to disk' affordance. Writes the DB template to
    `stations/{slug}.json` (the git-committable artifact)."""
    path: str
    bytes: int


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
