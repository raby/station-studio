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
class ExcludedCandidate:
    """A track the strategy *considered* but did not pick, with rationale.

    Populated by strategies that evaluate multiple candidates (weighted,
    dedup-aware, etc.). Empty for deterministic strategies (window, pick
    in sequential mode, sequential). Recording these makes the otherwise
    opaque "why didn't it pick X?" question answerable from the trace
    alone, without re-running the generator. See DESIGN.md §14.23.
    """
    pool_index: int         # 1-indexed position in the source pool
    track_id: int | None
    track_name: str
    track_artist: str | None
    reason: str             # why this candidate was rejected

    def to_dict(self) -> dict:
        return asdict(self)


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
    # Candidates considered but rejected (§14.23). Empty for the current
    # deterministic strategies; populated by future weighted/dedup-aware
    # strategies. `asdict` includes this in to_dict; legacy traces persisted
    # before §14.23 lack the field and round-trip back without it (the
    # frontend treats missing as empty).
    excluded: list[ExcludedCandidate] = field(default_factory=list)

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
