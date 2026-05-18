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


SUPPORTED_STRATEGIES = {
    "window", "pick", "sequential", "jingle", "mix", "weighted", "prev_ref",
}
# v1 had {"window", "pick"} only. `sequential` lands in §14.22 (§17 Phase B
# first bit) as the Weekly-port generalisation: a slot that reads its start
# position from a NAMED CURSOR (declared in layout.cursors_init) instead of
# the row-offsets list. Same algorithm as `window`; different start-position
# source. Cursors advance by the same base_advance_per_week as the main
# cursor — see Template.cursor_position().
#
# `jingle` (§14.24) and `mix` (§14.25) both require a per-block `genre`
# string. `mix` optionally accepts an `exclusion_window` integer
# (count of prior playlists to exclude from); when absent (§14.29),
# the value falls back to the station's `mix_exclusion_weeks` rule,
# then to an engine constant.
#
# `weighted` (§14.27) is the two-tier commercial strategy. Optionally
# accepts a `window` integer (artist-dedup sliding-window size; defaults
# to 10 if absent). When sub-slice #13's Rule table lands, the missing-
# value default will be sourced from a station-wide rule; per-block
# `window` overrides the rule (keeping per-block flexibility for
# templates that want it).
#
# `prev_ref` (§14.28) mirrors a slot from the previous playlist.
# Required field: `position` (1-indexed lookup into the previous
# playlist's slot order). Optional field: `fallback_genre` (when set,
# the strategy falls back to a mix-style pick from the same source
# filtered to that genre; when absent, falls back via the weighted
# strategy — defensive code that weekly's templates never actually
# trigger but the port keeps for byte-equality fidelity).
#
# The strategy ↔ required-field coupling is enforced by
# _require_strategy_field; see that helper for the source of truth on
# which fields belong to which strategies.
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
    # genre is required for strategy='jingle' (§14.24) or 'mix' (§14.25),
    # forbidden otherwise. Filters the source pool to tracks whose `genre`
    # attribute equals this value (case-sensitive exact match).
    genre: str | None = None
    # exclusion_window is required for strategy='mix' only (§14.25). Count
    # of the station's most recent playlists whose MixUsage rows are
    # treated as excluded track ids. Persistence-time: the generator's
    # excluded_mix_ids parameter is populated from this window.
    exclusion_window: int | None = None
    # window is optional for strategy='weighted' (§14.27); forbidden for
    # any other strategy. Artist-dedup sliding-window size; defaults to
    # 10 when absent. Sub-slice #13's Rule table will provide a station-
    # wide default; per-block `window` overrides it.
    window: int | None = None
    # position is required for strategy='prev_ref' (§14.28); forbidden
    # for any other strategy. 1-indexed slot lookup into the previous
    # playlist for this station.
    position: int | None = None
    # fallback_genre is optional for strategy='prev_ref' (§14.28);
    # forbidden for any other strategy. When set, the mirror-miss
    # fallback picks a mix from the same source filtered to this
    # genre. When absent, the fallback uses the weighted strategy
    # (defensive — weekly's actual templates never trigger this).
    fallback_genre: str | None = None


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
        optional={
            "cursor_id",          # §14.22 — required when strategy='sequential'
            "genre",              # §14.24/§14.25 — required when strategy in {'jingle', 'mix'}
            "exclusion_window",   # §14.25 — required when strategy='mix'
            "window",             # §14.27 — optional when strategy='weighted' (default 10)
            "position",           # §14.28 — required when strategy='prev_ref'
            "fallback_genre",     # §14.28 — optional when strategy='prev_ref'
        },
    )
    if raw["source"] not in known_sources:
        raise TemplateError(f"block references unknown source '{raw['source']}'")
    if raw["size"] < 1:
        raise TemplateError("block size must be >= 1")

    strategy = raw["strategy"]
    cursor_id = raw.get("cursor_id")
    genre = raw.get("genre")
    exclusion_window = raw.get("exclusion_window")
    window = raw.get("window")
    position = raw.get("position")
    fallback_genre = raw.get("fallback_genre")

    # Strategy ↔ required-field coupling. Each strategy declares which extra
    # fields it requires; every other strategy forbids them. The helper
    # standardises the error messages and keeps the table of strategy-field
    # rules in one place.
    _require_strategy_field(
        strategy, "cursor_id", cursor_id,
        required_for={"sequential"},
        validator=lambda v: v if v in known_cursor_ids else _err(
            f"block cursor_id '{v}' not in layout.cursors_init "
            f"(declared: {sorted(known_cursor_ids) or '[]'})"
        ),
    )
    _require_strategy_field(
        strategy, "genre", genre,
        required_for={"jingle", "mix"},
        validator=lambda v: v if (isinstance(v, str) and v) else _err(
            f"block with strategy={strategy!r} must specify a non-empty genre"
        ),
    )
    # §14.29: exclusion_window is now OPTIONAL for mix (was required).
    # Weekly's port still sets it explicitly on every mix slot, so
    # byte-equality is unaffected. Other stations can omit the field
    # and inherit the per-station `mix_exclusion_weeks` rule default.
    _require_strategy_field(
        strategy, "exclusion_window", exclusion_window,
        required_for=set(),
        optional_for={"mix"},
        validator=lambda v: v if (isinstance(v, int) and not isinstance(v, bool) and v >= 0) else _err(
            f"block exclusion_window must be a non-negative integer, got {v!r}"
        ),
    )
    # `window` is the first "optional for X, forbidden for everyone else"
    # field. Uses optional_for= rather than required_for=; validator only
    # fires when a value is actually supplied.
    _require_strategy_field(
        strategy, "window", window,
        required_for=set(),
        optional_for={"weighted"},
        validator=lambda v: v if (isinstance(v, int) and not isinstance(v, bool) and v >= 0) else _err(
            f"block window must be a non-negative integer, got {v!r}"
        ),
    )
    # §14.28 — prev_ref couplings:
    #   `position`: required for prev_ref (1-indexed positive integer)
    #   `fallback_genre`: optional for prev_ref; when set, must be a
    #                     non-empty string (drives mix-fallback path)
    _require_strategy_field(
        strategy, "position", position,
        required_for={"prev_ref"},
        validator=lambda v: v if (isinstance(v, int) and not isinstance(v, bool) and v >= 1) else _err(
            f"block position must be a positive integer (1-indexed), got {v!r}"
        ),
    )
    _require_strategy_field(
        strategy, "fallback_genre", fallback_genre,
        required_for=set(),
        optional_for={"prev_ref"},
        validator=lambda v: v if (isinstance(v, str) and v) else _err(
            f"block fallback_genre must be a non-empty string, got {v!r}"
        ),
    )

    return BlockSpec(
        source=raw["source"],
        strategy=strategy,
        size=int(raw["size"]),
        cursor_id=cursor_id,
        genre=genre,
        exclusion_window=exclusion_window,
        window=window,
        position=position,
        fallback_genre=fallback_genre,
    )


def _require_strategy_field(
    strategy: str,
    field_name: str,
    value,
    *,
    required_for: set[str],
    optional_for: set[str] | None = None,
    validator,
) -> None:
    """Enforce the "this field belongs only to these strategies" rule.

    Three buckets per strategy/field combination:

    - In ``required_for``: value MUST be present (non-None); validator
      runs.
    - In ``optional_for``: value MAY be present; if supplied, validator
      runs; if absent, no error (caller fills in the default).
    - Otherwise (forbidden): value MUST be None.

    Shared by sequential ↔ cursor_id, jingle/mix ↔ genre, mix ↔
    exclusion_window (required-or-forbidden shape) and weighted ↔
    window (optional-or-forbidden shape).
    """
    allowed_for = required_for | (optional_for or set())
    if strategy in required_for:
        if value is None:
            raise TemplateError(
                f"block with strategy={strategy!r} must specify {field_name}"
            )
        validator(value)
    elif strategy in (optional_for or set()):
        if value is not None:
            validator(value)
    else:
        if value is not None:
            raise TemplateError(
                f"block {field_name} is only valid when strategy in "
                f"{sorted(allowed_for)} (got strategy={strategy!r}, "
                f"{field_name}={value!r})"
            )


def _err(msg: str):
    """Helper for lambda validators: raise TemplateError with a custom message."""
    raise TemplateError(msg)


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
