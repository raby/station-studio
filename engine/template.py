"""Station template — load, validate, expose.

A template is a JSON document. See DESIGN.md §5.7 and §4.3 for rationale.

Validation is deliberately strict: unknown fields raise. We want the canonical
JSON artifact to be self-describing, so sloppy extra keys that look meaningful
but are silently ignored are a footgun we reject up-front.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


SUPPORTED_STRATEGIES = {"window", "pick", "sequential"}
# v1 had {"window", "pick"} only. `sequential` lands in §14.22 (§17 Phase B
# first bit) as the Weekly-port generalisation: a slot that reads its start
# position from a NAMED CURSOR (declared in layout.cursors_init) instead of
# the row-offsets list. Same algorithm as `window`; different start-position
# source. Cursors advance by the same base_advance_per_week as the main
# cursor — see Template.cursor_position().
SUPPORTED_RNG = {"off", "on"}
SUPPORTED_ROLES = {"content", "reference"}
# Cadence is purely metadata for the engine — `weekly` is the only value
# in use today, and the validator doesn't reject other strings. Listed
# here for the GET /api/meta endpoint (§14.19) so the frontend dropdowns
# stay in sync with what the project intends to support. Engine does not
# (yet) close-set-validate this; if a future cadence needs scheduling
# semantics, add the validation alongside the scheduler punt (§11 v1.1).
SUGGESTED_CADENCES = {"weekly"}

# Hex colour: #RGB, #RRGGBB, or #RRGGBBAA. Case-insensitive.
_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3}([0-9a-fA-F]{2})?)?$")


class TemplateError(ValueError):
    """Raised when a template fails validation."""


@dataclass
class SourceSpec:
    name: str
    colour: str
    role: str  # "content" | "reference"


@dataclass
class CursorSpec:
    base_advance_per_week: int
    cycle_weeks: int


@dataclass
class BlockSpec:
    source: str
    strategy: str
    size: int
    # cursor_id is required for strategy='sequential' (Weekly-port style) and
    # forbidden for 'window' / 'pick' (the row-offset style). Validated by the
    # parser. None = "no named cursor; use row_offset[row_idx]" (existing).
    cursor_id: str | None = None


@dataclass
class LayoutSpec:
    unit: str
    unit_labels: list[str]
    row_start_offsets: list[int]
    row_template: list[BlockSpec]
    # Named-cursor initial positions, one entry per cursor referenced by any
    # sequential block. Optional — Classics-style templates leave this empty.
    # See Template.cursor_position() for the per-week computation.
    cursors_init: dict[str, int] = field(default_factory=dict)


@dataclass
class Template:
    station: str
    cadence: str
    rng: str
    sources: list[SourceSpec]
    cursor: CursorSpec
    layout: LayoutSpec
    raw: dict = field(repr=False)

    # --- convenience -------------------------------------------------------

    @property
    def content_source_names(self) -> list[str]:
        return [s.name for s in self.sources if s.role == "content"]

    def source_by_name(self, name: str) -> SourceSpec:
        for s in self.sources:
            if s.name == name:
                return s
        raise TemplateError(f"Unknown source '{name}'")

    def slots_per_week(self) -> int:
        per_row = sum(b.size for b in self.layout.row_template)
        return per_row * len(self.layout.row_start_offsets)

    def base_cursor_for_week(self, week: int) -> int:
        """Week 1 = 0; advances base_advance_per_week each week.

        Wrap policy:
          - cycle_weeks >= 1: wrap every cycle_weeks (Classics, §5.4)
          - cycle_weeks == 0: no wrap; position grows linearly until the
            source pool would underflow at slot-fill time (§14.22 / Weekly
            style — pool-size becomes the implicit ceiling; engine fails
            loud per §5.5 when a window runs off the end).
        """
        if week < 1:
            raise TemplateError(f"week must be >= 1, got {week}")
        if self.cursor.cycle_weeks == 0:
            return (week - 1) * self.cursor.base_advance_per_week
        return ((week - 1) % self.cursor.cycle_weeks) * self.cursor.base_advance_per_week

    def cursor_position(self, cursor_id: str, week: int) -> int:
        """Compute a named cursor's 0-indexed position for a given week.

        Used by `sequential` slots (§14.22). Each named cursor has an
        initial position declared in `layout.cursors_init`; the per-week
        advance is shared with the main cursor (base_advance_per_week)
        and respects the same cycle_weeks wrap policy.

        Raises TemplateError if the cursor_id is unknown.
        """
        if cursor_id not in self.layout.cursors_init:
            raise TemplateError(
                f"unknown cursor_id '{cursor_id}'. "
                f"Declared: {sorted(self.layout.cursors_init)}"
            )
        return self.layout.cursors_init[cursor_id] + self.base_cursor_for_week(week)


# ---------- loading + validation -----------------------------------------


def load_template(path: Path | str) -> Template:
    path = Path(path)
    with open(path) as f:
        raw = json.load(f)
    return parse_template(raw)


def parse_template(raw: dict) -> Template:
    _require_keys(
        raw,
        {"station", "cadence", "rng", "sources", "cursor", "layout"},
        where="template",
        optional={"name"},  # UI-only display name; ignored by the engine
    )

    rng = raw["rng"]
    if rng not in SUPPORTED_RNG:
        raise TemplateError(f"rng must be one of {SUPPORTED_RNG}, got {rng!r}")

    sources = [_parse_source(s) for s in raw["sources"]]
    names = [s.name for s in sources]
    if len(names) != len(set(names)):
        raise TemplateError("duplicate source names")

    cursor = _parse_cursor(raw["cursor"])
    layout = _parse_layout(raw["layout"], known_sources=set(names))

    tmpl = Template(
        station=raw["station"],
        cadence=raw["cadence"],
        rng=rng,
        sources=sources,
        cursor=cursor,
        layout=layout,
        raw=raw,
    )

    # Cross-field checks
    if len(layout.unit_labels) != len(layout.row_start_offsets):
        raise TemplateError(
            f"unit_labels length ({len(layout.unit_labels)}) must match "
            f"row_start_offsets length ({len(layout.row_start_offsets)})"
        )

    for b in layout.row_template:
        if b.strategy not in SUPPORTED_STRATEGIES:
            raise TemplateError(
                f"strategy '{b.strategy}' not supported. Supported: {sorted(SUPPORTED_STRATEGIES)}"
            )
        src = tmpl.source_by_name(b.source)
        if src.role != "content":
            raise TemplateError(
                f"block uses source '{b.source}' with role '{src.role}'; "
                "only 'content' sources can be referenced by the layout"
            )

    return tmpl


def _parse_source(raw: dict) -> SourceSpec:
    _require_keys(raw, {"name", "colour", "role"}, where="source")
    if raw["role"] not in SUPPORTED_ROLES:
        raise TemplateError(
            f"source role must be one of {sorted(SUPPORTED_ROLES)}, got {raw['role']!r}"
        )
    if not _COLOUR_RE.match(raw["colour"]):
        raise TemplateError(
            f"source colour must be a hex colour (#RGB, #RRGGBB, or #RRGGBBAA), "
            f"got {raw['colour']!r}"
        )
    return SourceSpec(name=raw["name"], colour=raw["colour"], role=raw["role"])


def _parse_cursor(raw: dict) -> CursorSpec:
    _require_keys(raw, {"base_advance_per_week", "cycle_weeks"}, where="cursor")
    if raw["base_advance_per_week"] < 0:
        raise TemplateError("cursor.base_advance_per_week must be >= 0")
    # cycle_weeks=0 is valid post-§14.22 — means "no cycle wrap, position
    # grows until the pool underflows" (Weekly-style). cycle_weeks>=1 keeps
    # the Classics behaviour (wrap every N weeks).
    if raw["cycle_weeks"] < 0:
        raise TemplateError("cursor.cycle_weeks must be >= 0 (0 = no cycle)")
    return CursorSpec(
        base_advance_per_week=int(raw["base_advance_per_week"]),
        cycle_weeks=int(raw["cycle_weeks"]),
    )


def _parse_layout(raw: dict, *, known_sources: set[str]) -> LayoutSpec:
    _require_keys(
        raw,
        {"unit", "unit_labels", "row_start_offsets", "row_template"},
        where="layout",
        optional={"cursors_init"},  # §14.22 — for sequential strategy
    )

    # cursors_init: dict[str, int] for named cursors used by sequential blocks.
    # Each value must be >= 0 (cursor positions are 0-indexed into pools).
    cursors_init_raw = raw.get("cursors_init", {})
    if not isinstance(cursors_init_raw, dict):
        raise TemplateError(
            f"layout.cursors_init must be an object, got {type(cursors_init_raw).__name__}"
        )
    cursors_init: dict[str, int] = {}
    for cid, pos in cursors_init_raw.items():
        if not isinstance(cid, str) or not cid:
            raise TemplateError(f"layout.cursors_init keys must be non-empty strings, got {cid!r}")
        if not isinstance(pos, int) or pos < 0:
            raise TemplateError(
                f"layout.cursors_init['{cid}'] must be a non-negative integer, got {pos!r}"
            )
        cursors_init[cid] = pos

    row_template = [
        _parse_block(b, known_sources=known_sources, known_cursor_ids=set(cursors_init))
        for b in raw["row_template"]
    ]
    return LayoutSpec(
        unit=raw["unit"],
        unit_labels=list(raw["unit_labels"]),
        row_start_offsets=list(raw["row_start_offsets"]),
        row_template=row_template,
        cursors_init=cursors_init,
    )


def _parse_block(
    raw: dict,
    *,
    known_sources: set[str],
    known_cursor_ids: set[str],
) -> BlockSpec:
    _require_keys(
        raw,
        {"source", "strategy", "size"},
        where="block",
        optional={"cursor_id"},  # §14.22 — required when strategy='sequential'
    )
    if raw["source"] not in known_sources:
        raise TemplateError(f"block references unknown source '{raw['source']}'")
    if raw["size"] < 1:
        raise TemplateError("block size must be >= 1")

    # Strategy ↔ cursor_id coupling (§14.22):
    #   strategy='sequential' requires cursor_id; the cursor must be declared
    #   strategy='window' / 'pick' forbid cursor_id (row-offset addressing only)
    strategy = raw["strategy"]
    cursor_id = raw.get("cursor_id")
    if strategy == "sequential":
        if cursor_id is None:
            raise TemplateError(
                "block with strategy='sequential' must specify cursor_id"
            )
        if cursor_id not in known_cursor_ids:
            raise TemplateError(
                f"block cursor_id '{cursor_id}' not in layout.cursors_init "
                f"(declared: {sorted(known_cursor_ids) or '[]'})"
            )
    else:
        if cursor_id is not None:
            raise TemplateError(
                f"block cursor_id is only valid when strategy='sequential' "
                f"(got strategy={strategy!r}, cursor_id={cursor_id!r})"
            )

    return BlockSpec(
        source=raw["source"],
        strategy=strategy,
        size=int(raw["size"]),
        cursor_id=cursor_id,
    )


def _require_keys(
    raw: dict,
    keys: set[str],
    *,
    where: str,
    optional: set[str] | None = None,
) -> None:
    missing = keys - set(raw.keys())
    if missing:
        raise TemplateError(f"{where}: missing keys {sorted(missing)}")
    allowed = keys | (optional or set())
    unknown = set(raw.keys()) - allowed
    if unknown:
        raise TemplateError(f"{where}: unknown keys {sorted(unknown)}")
