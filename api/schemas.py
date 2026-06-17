"""Pydantic request/response schemas."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt


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


class PreviewRequest(BaseModel):
    """Body for §16.2.6 live-preview endpoint — `POST /api/stations/
    {slug}/jobs/preview`.

    Carries ONLY editor-mutated state (template, sidecar, week, seed).
    Server-recomputed inputs (`excluded_mix_ids`, `previous_playlist`,
    `alias_map`, named-cursor positions) are looked up fresh from DB
    per request — sending them would let a stale frontend snapshot
    override fresh server state, defeating the recompute-per-request
    guarantee the live-preview slice depends on. See DESIGN §16.2.6
    "API surface" for the rationale.

    `template`/`sidecar` are dicts on the wire — same shape as the
    stored Station columns. The route layer parses/validates both
    before spawning the job.
    """
    template: dict
    sidecar: dict
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

    `value` is typed `StrictInt | list[str]` (NOT plain `int`) so
    Pydantic doesn't coerce JSON booleans into 1/0 (§16.14.t10 c1
    review F1). Without StrictInt, a `{"value": true}` body lands
    as int=1 BEFORE the validator runs — the D6 bool-rejection
    contract that engine.rules.validate_int_rule_value enforces
    would be silently bypassed at the API surface. StrictInt
    rejects bool at the schema layer, returning a 422 (Pydantic
    validation error). The downstream validator then handles
    out-of-bounds + non-list-for-list-key + comma-in-entry +
    empty-entry cases that StrictInt can't express.
    """
    value: StrictInt | list[str]


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


class BlockRetypeRequest(BaseModel):
    """PATCH /api/stations/{slug}/blocks/retype body (§16.2.4 D2).

    Distinct endpoint from PATCH /blocks — the parameter-only guard
    on /blocks stays crisp; retyping has its own contract.

    * `address` — which block to retype (XOR slot_index / block_idx,
      same shape as BlockPatchRequest).
    * `new_strategy` — optional new strategy. When set, must be in
      ``engine.template.SUPPORTED_STRATEGIES``. The block's
      `strategy` field is overwritten.
    * `new_source` — optional new source. When set, must be a name
      declared in ``template.sources[]`` with a role compatible with
      the resulting strategy (parse_template's role cross-check
      enforces this; we don't duplicate here).
    * `new_fields` — optional dict of new field values to set ALONG
      WITH the retype, e.g. setting `position=1` when retyping to
      archive_ref. Same null-clears-the-key semantics as
      BlockPatchRequest. ALL strategy-parameter fields are valid
      here (no parameter-only guard) because the retype changes the
      valid-field-set — old fields incompatible with the new
      strategy are dropped server-side.
    * `expected_updated_at` — optimistic-concurrency token. 409 if
      stale.

    Server-side: applies new_strategy + new_source + new_fields to
    a deep-copy of the block; drops fields that are no longer
    valid for the new strategy (e.g. removing `genre` when retyping
    away from jingle/mix); re-`parse_template`s the whole document;
    400 on any TemplateError.
    """
    address: BlockAddress
    new_strategy: str | None = None
    new_source: str | None = None
    new_fields: dict[str, Any] = Field(default_factory=dict)
    expected_updated_at: str | None = None


class BlockRetypeResponse(BaseModel):
    """200 body for a successful block retype. Same shape as
    BlockPatchResponse — returns the updated block (with the new
    strategy/source/fields applied) + the refreshed updated_at
    token."""
    block: dict[str, Any]
    updated_at: str | None = None


class MacroPatchRequest(BaseModel):
    """PATCH /api/stations/{slug}/macros/{macro_id} body (§16.2.4 D1).

    Edit-once-applies-to-all: the server resolves the macro_id to
    every block expanded from it + applies `fields` to each.

    * `fields` — parameter fields to set; null clears a key. Subject
      to the same `EDITABLE_FIELDS_BY_STRATEGY` parameter-only guard
      as PATCH /blocks (Decision C). Strategy/source/size remain
      forbidden here too — a macro retype is conceptually different
      (it's `PATCH /blocks/retype` applied to each expanded block);
      this slice keeps the two semantics distinct.
    * `expected_updated_at` — optimistic-concurrency token. 409 if
      stale.

    Server-side: parses the sidecar to locate the macro + its
    expanded block addresses; for each block, applies `fields`
    (same null-clears + parameter-only guard); re-`parse_template`s
    the whole document once at the end; 400 on TemplateError.
    All-or-nothing: a failure on any block rolls back the whole
    transaction (no partial macro state).
    """
    fields: dict[str, Any]
    expected_updated_at: str | None = None


class MacroPatchResponse(BaseModel):
    """200 body for a successful macro PATCH. Returns the count of
    blocks updated + the refreshed updated_at token. The frontend
    refetches the sidecar to re-render the updated chips."""
    blocks_updated: int
    updated_at: str | None = None


# ---- §16.2.5 c1: section composition + structural block edits -------


class SectionCreateRequest(BaseModel):
    """POST /api/stations/{slug}/sections body (§16.2.5 D4).

    Creates an EMPTY section at the end of layout_order. The
    operator moves blocks into it via subsequent
    PATCH /blocks/move calls. `id` is operator-supplied (must be a
    non-empty string, not already in use, NOT starting with `_`
    — leading underscore is reserved for system bins like
    `_unsorted`).
    """
    id: str
    name: str | None = None
    expected_updated_at: str | None = None


class SectionRenameRequest(BaseModel):
    """PATCH /api/stations/{slug}/sections/{id} body (§16.2.5 D4).

    Renames the section's `name` (the user-visible label). The
    section's `id` is part of the URL and immutable here — changing
    an id would invalidate every layout_order reference + every
    block's section membership, which is a different operation
    (queued; not in §16.2.5).
    """
    name: str
    expected_updated_at: str | None = None


class SectionsOrderRequest(BaseModel):
    """PUT /api/stations/{slug}/sections/order body (§16.2.5 D4).

    Full replacement of `layout_order`. The server validates that
    the new list is a permutation of the existing section ids
    (no add/drop allowed here — those are POST + DELETE).
    """
    layout_order: list[str]
    expected_updated_at: str | None = None


class BlockMoveRequest(BaseModel):
    """PATCH /api/stations/{slug}/blocks/move body (§16.2.5 D4).

    Move an ATOMIC block from its current section to
    `target_section_id`, optionally at a specific `target_index`
    within the target's blocks list (default = append). Sidecar-
    only mutation — the engine TEMPLATE is unchanged.

    Address is XOR — exactly one of slot_index / block_idx.

    SCOPE — ATOMIC BLOCKS ONLY (c1 review #2). The prior docstring
    said "atomic or macro" but moving a MACRO between sections is
    semantically broken by the sidecar's position-math:
    `resolve_macro_addresses` derives a macro's slot range from
    its section's first atomic anchor + cumulative widths, so
    relocating the macro element changes its computed slot range
    → mis-aligned ↔ slot_indices the macro is supposed to cover.
    Macros stay anchored where derive_sidecar (or the operator's
    hand-authored grouping) placed them; to reorganise macros,
    the operator hand-edits via the wizard-edit PUT until
    §16.2.6's macro-relocation affordance lands.
    """
    address: BlockAddress
    target_section_id: str
    target_index: int | None = None
    expected_updated_at: str | None = None


class StructuralBlockEdit(BaseModel):
    """One structural edit step — discriminated by `op`:
      * `op="size"`   — change a block's `size` field; no slot-count
        change.
      * `op="add"`    — INSERT a new block at `slot_index`; every
        downstream atomic-ref shifts by +1.
      * `op="remove"` — DELETE the block at `slot_index`; every
        downstream atomic-ref shifts by -1.
    """
    op: Literal["size", "add", "remove"]
    slot_index: int
    # For `op="size"`: required, new size value.
    # For `op="add"`: required, the new block dict (must include
    #   strategy, source, size + any required parameter fields).
    # For `op="remove"`: ignored.
    new_size: int | None = None
    new_block: dict[str, Any] | None = None


class BlockStructureRequest(BaseModel):
    """PATCH /api/stations/{slug}/blocks/structure body (§16.2.5 D4).

    Applies one structural edit. v1 ships single-edit semantics
    (one op per request) — bulk edits would compound the
    index-shift math (D5) + complicate optimistic concurrency.
    Future slices may relax to a list[StructuralBlockEdit].
    """
    edit: StructuralBlockEdit
    expected_updated_at: str | None = None


# §16.2.6.t2 c1 — transactional batch endpoint envelopes. One
# envelope per Edit kind (mirrors the frontend `Edit` discriminated
# union in `web/src/lib/edit-ops.ts`). The batch endpoint loops the
# `edits` list inside a single transaction; any error rolls back
# the whole batch.

class _BatchPatchBlockEnv(BaseModel):
    kind: Literal["patchBlock"]
    address: BlockAddress
    fields: dict[str, Any]


class _BatchPatchMacroEnv(BaseModel):
    kind: Literal["patchMacro"]
    macro_id: str = Field(..., alias="macroId")
    fields: dict[str, Any]
    model_config = ConfigDict(populate_by_name=True)


class _BatchRenameSectionEnv(BaseModel):
    kind: Literal["renameSection"]
    section_id: str = Field(..., alias="sectionId")
    name: str
    model_config = ConfigDict(populate_by_name=True)


class _BatchSectionCreateEnv(BaseModel):
    kind: Literal["sectionCreate"]
    section_id: str = Field(..., alias="sectionId")
    name: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class _BatchSectionDeleteEnv(BaseModel):
    kind: Literal["sectionDelete"]
    section_id: str = Field(..., alias="sectionId")
    model_config = ConfigDict(populate_by_name=True)


class _BatchReorderSectionsEnv(BaseModel):
    kind: Literal["reorderSections"]
    layout_order: list[str] = Field(..., alias="layoutOrder")
    model_config = ConfigDict(populate_by_name=True)


class _BatchMoveBlockEnv(BaseModel):
    kind: Literal["moveBlock"]
    address: BlockAddress
    target_section_id: str = Field(..., alias="targetSectionId")
    target_index: int | None = Field(default=None, alias="targetIndex")
    model_config = ConfigDict(populate_by_name=True)


class _BatchRetypeBlockEnv(BaseModel):
    kind: Literal["retypeBlock"]
    address: BlockAddress
    new_strategy: str | None = Field(default=None, alias="newStrategy")
    new_source: str | None = Field(default=None, alias="newSource")
    new_fields: dict[str, Any] = Field(default_factory=dict, alias="newFields")
    model_config = ConfigDict(populate_by_name=True)


class _BatchStructuralBlockEditEnv(BaseModel):
    kind: Literal["structuralBlockEdit"]
    edit: StructuralBlockEdit


BatchEdit = Annotated[
    (
        _BatchPatchBlockEnv
        | _BatchPatchMacroEnv
        | _BatchRenameSectionEnv
        | _BatchSectionCreateEnv
        | _BatchSectionDeleteEnv
        | _BatchReorderSectionsEnv
        | _BatchMoveBlockEnv
        | _BatchRetypeBlockEnv
        | _BatchStructuralBlockEditEnv
    ),
    Field(discriminator="kind"),
]


class SidecarBatchRequest(BaseModel):
    """POST /api/stations/{slug}/sidecar/batch body (§16.2.6.t2).

    All edits apply inside a single DB transaction — any failure
    rolls back the whole batch (operator picks Retry or Drop in the
    UI; server stays consistent).

    `expected_updated_at` is checked ONCE at the start (against the
    station row); the inner ops do not re-check (the transaction
    guarantees the row can't move under us).
    """
    edits: list[BatchEdit]
    expected_updated_at: str | None = None


class SidecarBatchErrorResponse(BaseModel):
    """4xx body for the batch endpoint. `failed_at_idx` is 1-indexed
    so the operator's "Save 5 edits" UI can say "edit 3 of 5 failed."
    `kind` echoes the failing edit's discriminator so frontend can
    surface op-specific hints without re-parsing the input."""
    failed_at_idx: int
    kind: str
    error: str


class SidecarMutationResponse(BaseModel):
    """200 body for the 6 §16.2.5 sidecar-mutation endpoints:
    section CRUD (POST/PATCH/DELETE /sections), reorder
    (PUT /sections/order), block move (PATCH /blocks/move), and
    structural edits (PATCH /blocks/structure).

    Returns the new sidecar + live template + refreshed updated_at;
    frontend re-renders without a separate GET round-trip.

    §16.2.5.t5 + c1 review #3 finding #6 — `template` is ALWAYS
    populated with the live `station.template` (post-refresh). The
    earlier "optional, only set for structural edits" shape created
    a two-tab drift hazard: between mutation N (sidecar-only,
    template=None) and N+1 a parallel writer's structural edit could
    leave the frontend rendering the new sidecar against its stale
    local template (atomic refs pointing at since-removed slots →
    confusing 404s on the next save). Sending the live template on
    every response is ~5KB gzipped for Rebirth Radio's 2,196-slot
    station; acceptable for the rare operator-triggered edit-rate.

    NOT used by the §16.2.4 macro endpoint — that returns
    `MacroPatchResponse` ({blocks_updated, updated_at}, no sidecar).
    The §16.2.3 `patchBlock` + §16.2.4 `retypeBlock` endpoints also
    use their own response models. Slice-review c1-rescue #4
    corrected this docstring; an earlier version misled future
    contributors into thinking macro edits used SidecarMutationResponse.
    """
    sidecar: dict[str, Any]
    template: dict[str, Any]
    updated_at: str | None = None


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
