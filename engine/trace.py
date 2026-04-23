"""Trace dataclasses.

Every generation emits a Trace. Each slot records which source pool supplied it,
which strategy decided it, which pool index was consulted, and a human-readable
reason. A trace_id (UUID) is attached so the trace can be persisted and
retrieved later — answering "why did slot N pick that track in week W?".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


@dataclass
class TraceSlot:
    slot: int               # 1-indexed position in the generated playlist
    unit: str               # e.g. "Mon"; from template.layout.unit_labels
    row: int                # 1-indexed row within the week
    block: int              # 1-indexed block within the row
    source: str             # source pool name
    strategy: str           # "window" | "pick" | ...
    pool_index: int         # 1-indexed position in the source pool
    track_id: int | None    # resolved track id (None if dry-run)
    track_name: str
    track_artist: str | None
    reason: str             # free-text rationale

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trace:
    station: str
    week: int
    rng: str = "off"
    seed: int | None = None
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    slots: list[TraceSlot] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "station": self.station,
            "week": self.week,
            "rng": self.rng,
            "seed": self.seed,
            "generated_at": self.generated_at,
            "slots": [s.to_dict() for s in self.slots],
        }
