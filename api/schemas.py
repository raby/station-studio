"""Pydantic request/response schemas."""
from __future__ import annotations

from typing import Any
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


class ImportRequest(BaseModel):
    path: str
    name: str


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
