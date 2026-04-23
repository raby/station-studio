"""Station template — load, validate, expose.

A template is a JSON document. See DESIGN.md §5.7 and §4.3 for rationale.

Validation is deliberately strict: unknown fields raise. We want the canonical
JSON artifact to be self-describing, so sloppy extra keys that look meaningful
but are silently ignored are a footgun we reject up-front.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


SUPPORTED_STRATEGIES = {"window", "pick"}  # v1. Add more as new stations need them.
SUPPORTED_RNG = {"off", "on"}


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


@dataclass
class LayoutSpec:
    unit: str
    unit_labels: list[str]
    row_start_offsets: list[int]
    row_template: list[BlockSpec]


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
        """Week 1 = 0; advances base_advance_per_week each week; wraps at cycle_weeks."""
        if week < 1:
            raise TemplateError(f"week must be >= 1, got {week}")
        return ((week - 1) % self.cursor.cycle_weeks) * self.cursor.base_advance_per_week


# ---------- loading + validation -----------------------------------------


def load_template(path: Path | str) -> Template:
    path = Path(path)
    with open(path) as f:
        raw = json.load(f)
    return parse_template(raw)


def parse_template(raw: dict) -> Template:
    _require_keys(raw, {"station", "cadence", "rng", "sources", "cursor", "layout"}, where="template")

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
    if raw["role"] not in {"content", "reference"}:
        raise TemplateError(f"source role must be 'content' or 'reference', got {raw['role']!r}")
    return SourceSpec(name=raw["name"], colour=raw["colour"], role=raw["role"])


def _parse_cursor(raw: dict) -> CursorSpec:
    _require_keys(raw, {"base_advance_per_week", "cycle_weeks"}, where="cursor")
    if raw["base_advance_per_week"] < 0:
        raise TemplateError("cursor.base_advance_per_week must be >= 0")
    if raw["cycle_weeks"] < 1:
        raise TemplateError("cursor.cycle_weeks must be >= 1")
    return CursorSpec(
        base_advance_per_week=int(raw["base_advance_per_week"]),
        cycle_weeks=int(raw["cycle_weeks"]),
    )


def _parse_layout(raw: dict, *, known_sources: set[str]) -> LayoutSpec:
    _require_keys(raw, {"unit", "unit_labels", "row_start_offsets", "row_template"}, where="layout")
    row_template = [_parse_block(b, known_sources=known_sources) for b in raw["row_template"]]
    return LayoutSpec(
        unit=raw["unit"],
        unit_labels=list(raw["unit_labels"]),
        row_start_offsets=list(raw["row_start_offsets"]),
        row_template=row_template,
    )


def _parse_block(raw: dict, *, known_sources: set[str]) -> BlockSpec:
    _require_keys(raw, {"source", "strategy", "size"}, where="block")
    if raw["source"] not in known_sources:
        raise TemplateError(f"block references unknown source '{raw['source']}'")
    if raw["size"] < 1:
        raise TemplateError("block size must be >= 1")
    return BlockSpec(source=raw["source"], strategy=raw["strategy"], size=int(raw["size"]))


def _require_keys(raw: dict, keys: set[str], *, where: str) -> None:
    missing = keys - set(raw.keys())
    if missing:
        raise TemplateError(f"{where}: missing keys {sorted(missing)}")
    unknown = set(raw.keys()) - keys
    if unknown:
        raise TemplateError(f"{where}: unknown keys {sorted(unknown)}")
