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
