"""Pydantic request/response schemas."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class StationCreate(BaseModel):
    slug: str
    name: str
    template: dict[str, Any]


class StationOut(BaseModel):
    id: int
    slug: str
    name: str
    template: dict[str, Any]

    class Config:
        from_attributes = True


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
    id: int
    name: str
    artist: str | None
    album: str | None

    class Config:
        from_attributes = True


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
