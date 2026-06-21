"""Sidecar grammar leaf module — types + constants + grammar walk + SQLAlchemy column type.

§16.2.5.t13 extracted this from `engine.editor_metadata` so
`engine.models.py` (loaded by every ORM consumer — every router,
every migration, every script) only pulls in the ~250-LOC leaf
needed for the `SidecarJSON` column type, not the 1.5K-LOC
`editor_metadata` module that owns the cross-document drift rules.

What lives here:
  * `EditorMetadataError` — the validation-failure exception (base
    `ValueError`; see §16.2.5.t14 for the AssertionError → ValueError
    promotion).
  * Allowlist + required-key constants for the sidecar grammar.
  * `validate_sidecar_grammar` — the structural-check entry point
    + its three private sub-validators (`_validate_section_grammar`,
    `_validate_block_grammar`, `_validate_macro_grammar`). Single-
    document — no cross-document references; those stay in
    `editor_metadata.validate_sidecar_semantics`.
  * `SidecarJSON` — SQLAlchemy `TypeDecorator` over
    `JSON(none_as_null=True)` that runs `validate_sidecar_grammar`
    on every ORM bind. `engine/models.py` imports this directly to
    avoid the `models → editor_metadata` reverse import that
    previously risked a circular-import landmine if any drift rule
    needed `Station` access.

What does NOT live here:
  * `parse_sidecar`, `validate_sidecar`, `validate_sidecar_semantics`
    — cross-document checks that need the station_template.
  * `synthesize_trivial_sidecar`, `derive_sidecar`, `lookup_macro`,
    `lookup_section`, `expand_section`, `is_system_section`, etc.
    — higher-level helpers atop the grammar.
  * `MacroPlaceholder`, `AtomicRef` — return shapes for the
    expand/lookup helpers.

`engine.editor_metadata` re-exports the symbols defined here for
backward-compat with existing call sites (api routers, tests,
scripts) — no churn at consumer sites.
"""
from __future__ import annotations

from sqlalchemy.types import TypeDecorator
from sqlalchemy import JSON as _SaJSON


# ---------- grammar allowlists ------------------------------------------

ALLOWED_TOP_LEVEL = {
    "schema_version", "sections", "macros", "layout_order", "_comment",
}
REQUIRED_TOP_LEVEL = {"schema_version", "sections", "layout_order"}

ALLOWED_SECTION_KEYS = {"id", "name", "blocks", "repeat_with_offsets"}
REQUIRED_SECTION_KEYS = {"id", "blocks"}

ALLOWED_MACRO_KEYS = {"id", "name", "spec", "repeat"}
REQUIRED_MACRO_KEYS = {"id", "spec", "repeat"}

# Atomic block: discriminated by which addressing fields are present.
ATOMIC_FLAT_KEYS = {"kind", "slot_index"}
ATOMIC_ROW_KEYS = {"kind", "row_idx", "block_idx"}
MACRO_BLOCK_KEYS = {"kind", "ref"}


# ---------- error type ---------------------------------------------------


class EditorMetadataError(ValueError):
    """Raised when sidecar validation or drift detection fails (§16.2.1).

    §16.2.5.t14: base changed from `AssertionError` → `ValueError`.
    The original rationale was "test contexts can use
    `pytest.raises(AssertionError, ...)`" — but no test in the suite
    actually relied on that (every editor_metadata test uses
    `pytest.raises(EditorMetadataError)` directly). After §16.2.5.t4
    the error crosses the SQL bind boundary on every commit; the
    `AssertionError` base risked infrastructure-level
    `except AssertionError:` clauses (FastAPI middleware, Alembic
    event hooks, defensive pytest assertion-rewriting) consuming
    validation failures as "something is structurally wrong with
    this assertion" rather than "data validation failure."
    `ValueError` is the convention for "bad data."
    """


# ---------- public grammar entry point ----------------------------------


def validate_sidecar_grammar(sidecar: dict) -> None:
    """Structural checks: types + allowlist + required keys.

    §16.2.5.t15 split: grammar is the only check needed at the SQL
    bind boundary (`SidecarJSON.process_bind_param`). Semantic checks
    need the template + don't run during ORM commit — see
    `engine.editor_metadata.validate_sidecar_semantics`.

    Single-document — does not require a station_template. Suitable
    for the TypeDecorator's bind-time gate AND for cheap top-level-
    shape validation when the caller doesn't have a template handy
    yet.

    §16.2.5.t14: canonical public name. The prior `_validate_grammar`
    private alias was folded — every former indirection routed
    through here anyway.
    """
    if not isinstance(sidecar, dict):
        raise EditorMetadataError(
            f"sidecar must be a JSON object, got {type(sidecar).__name__}"
        )
    missing = REQUIRED_TOP_LEVEL - set(sidecar.keys())
    if missing:
        raise EditorMetadataError(
            f"sidecar missing required keys: {sorted(missing)}. "
            f"Required: {sorted(REQUIRED_TOP_LEVEL)}."
        )
    unknown = set(sidecar.keys()) - ALLOWED_TOP_LEVEL
    if unknown:
        raise EditorMetadataError(
            f"sidecar has unknown top-level keys: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_TOP_LEVEL)}."
        )
    if sidecar.get("schema_version") != 1:
        raise EditorMetadataError(
            f"sidecar schema_version={sidecar.get('schema_version')!r}; "
            f"expected 1 (§16.2.0)."
        )

    sections = sidecar["sections"]
    if not isinstance(sections, list):
        raise EditorMetadataError(
            f"'sections' must be a list, got {type(sections).__name__}"
        )
    # Mid-slice review finding 2: reject empty sections. A sidecar
    # with zero sections renders an empty editor view; if the user
    # wants "no sections" they shouldn't ship a sidecar at all
    # (synthesize_trivial_sidecar handles the no-sidecar case).
    if not sections:
        raise EditorMetadataError(
            "'sections' must contain at least one section. An empty "
            "sidecar has nothing to render; remove the sidecar file "
            "(the editor falls back to a synthesized 'All slots' view) "
            "or add at least one section."
        )
    seen_section_ids: set[str] = set()
    for i, sec in enumerate(sections):
        _validate_section_grammar(sec, i)
        if sec["id"] in seen_section_ids:
            raise EditorMetadataError(
                f"sections[{i}]: duplicate id {sec['id']!r}; "
                f"section ids must be unique within the sidecar."
            )
        seen_section_ids.add(sec["id"])

    macros = sidecar.get("macros", [])
    if not isinstance(macros, list):
        raise EditorMetadataError(
            f"'macros' must be a list, got {type(macros).__name__}"
        )
    seen_macro_ids: set[str] = set()
    for i, m in enumerate(macros):
        _validate_macro_grammar(m, i)
        if m["id"] in seen_macro_ids:
            raise EditorMetadataError(
                f"macros[{i}]: duplicate id {m['id']!r}; "
                f"macro ids must be unique within the sidecar."
            )
        seen_macro_ids.add(m["id"])

    layout_order = sidecar["layout_order"]
    if not isinstance(layout_order, list):
        raise EditorMetadataError(
            f"'layout_order' must be a list, got {type(layout_order).__name__}"
        )
    for i, sid in enumerate(layout_order):
        if not isinstance(sid, str):
            raise EditorMetadataError(
                f"layout_order[{i}] must be a string, got {type(sid).__name__}"
            )


def _validate_section_grammar(sec: dict, i: int) -> None:
    if not isinstance(sec, dict):
        raise EditorMetadataError(
            f"sections[{i}] must be an object, got {type(sec).__name__}"
        )
    missing = REQUIRED_SECTION_KEYS - set(sec.keys())
    if missing:
        raise EditorMetadataError(
            f"sections[{i}] missing required keys: {sorted(missing)}."
        )
    unknown = set(sec.keys()) - ALLOWED_SECTION_KEYS
    if unknown:
        raise EditorMetadataError(
            f"sections[{i}] has unknown keys: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_SECTION_KEYS)}."
        )
    if not isinstance(sec["id"], str) or not sec["id"]:
        raise EditorMetadataError(
            f"sections[{i}].id must be a non-empty string."
        )
    if not isinstance(sec["blocks"], list):
        raise EditorMetadataError(
            f"sections[{i}].blocks must be a list."
        )
    if "name" in sec and not isinstance(sec["name"], str):
        raise EditorMetadataError(
            f"sections[{i}].name must be a string when present."
        )
    if "repeat_with_offsets" in sec:
        rwo = sec["repeat_with_offsets"]
        if not isinstance(rwo, list):
            raise EditorMetadataError(
                f"sections[{i}].repeat_with_offsets must be a list."
            )
        for j, v in enumerate(rwo):
            if not isinstance(v, int):
                raise EditorMetadataError(
                    f"sections[{i}].repeat_with_offsets[{j}] must be int, "
                    f"got {type(v).__name__}"
                )

    # Validate each block in the section.
    has_rwo = "repeat_with_offsets" in sec
    for j, b in enumerate(sec["blocks"]):
        _validate_block_grammar(b, i, j)
        if has_rwo:
            # Mid-slice review finding 4: macros inside a
            # repeat_with_offsets section have ambiguous semantics
            # (does the macro repeat per-row or globally?). v1
            # rejects them; §16.2.4 may relax if a real need emerges.
            if b.get("kind") == "macro":
                raise EditorMetadataError(
                    f"sections[{i}].blocks[{j}]: macro blocks aren't "
                    f"allowed inside a section with repeat_with_offsets. "
                    f"The semantic (does the macro repeat per-row or "
                    f"globally?) isn't settled in v1 — restructure to "
                    f"either drop repeat_with_offsets OR move the macro "
                    f"into a separate section."
                )
            # Mid-slice review finding 1: atomic blocks' `row_idx`
            # field is OVERRIDDEN by the iteration index when the
            # section uses repeat_with_offsets. The block's authored
            # row_idx is template-only; require it to be 0 so the
            # contract is explicit + the user doesn't silently lose
            # an authored value.
            if b.get("kind") == "atomic" and b.get("row_idx") not in (None, 0):
                raise EditorMetadataError(
                    f"sections[{i}].blocks[{j}]: atomic blocks inside a "
                    f"repeat_with_offsets section must use row_idx=0 "
                    f"(the iteration index overrides it). Got row_idx="
                    f"{b['row_idx']}; the value would be silently lost."
                )


def _validate_block_grammar(b: dict, section_idx: int, block_idx: int) -> None:
    if not isinstance(b, dict):
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}] must be an object."
        )
    kind = b.get("kind")
    if kind not in {"atomic", "macro"}:
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}].kind must be "
            f"'atomic' or 'macro'; got {kind!r}."
        )
    if kind == "macro":
        unknown = set(b.keys()) - MACRO_BLOCK_KEYS
        if unknown:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (macro): "
                f"unknown keys {sorted(unknown)}. "
                f"Allowed: {sorted(MACRO_BLOCK_KEYS)}."
            )
        if "ref" not in b:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (macro): "
                f"missing 'ref'."
            )
        if not isinstance(b["ref"], str) or not b["ref"]:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (macro): "
                f"'ref' must be a non-empty string."
            )
        return

    # atomic block — exactly one addressing form
    keys = set(b.keys())
    is_flat = "slot_index" in keys
    is_row = "row_idx" in keys or "block_idx" in keys
    if is_flat and is_row:
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}] (atomic): "
            f"both slot_index AND row_idx/block_idx present; pick one."
        )
    if not is_flat and not is_row:
        raise EditorMetadataError(
            f"sections[{section_idx}].blocks[{block_idx}] (atomic): "
            f"missing addressing — need either 'slot_index' OR "
            f"'row_idx'+'block_idx'."
        )
    if is_flat:
        if keys != ATOMIC_FLAT_KEYS:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic flat): "
                f"unexpected keys {sorted(keys - ATOMIC_FLAT_KEYS)}. "
                f"Required: {sorted(ATOMIC_FLAT_KEYS)}."
            )
        if not isinstance(b["slot_index"], int):
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}].slot_index "
                f"must be int, got {type(b['slot_index']).__name__}"
            )
    else:
        if keys != ATOMIC_ROW_KEYS:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic row): "
                f"unexpected keys {sorted(keys - ATOMIC_ROW_KEYS)}. "
                f"Required: {sorted(ATOMIC_ROW_KEYS)}."
            )
        if "row_idx" not in b or "block_idx" not in b:
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic row): "
                f"both row_idx AND block_idx required."
            )
        if not isinstance(b["row_idx"], int) or not isinstance(b["block_idx"], int):
            raise EditorMetadataError(
                f"sections[{section_idx}].blocks[{block_idx}] (atomic row): "
                f"row_idx and block_idx must both be int."
            )


def _validate_macro_grammar(m: dict, i: int) -> None:
    if not isinstance(m, dict):
        raise EditorMetadataError(
            f"macros[{i}] must be an object, got {type(m).__name__}"
        )
    missing = REQUIRED_MACRO_KEYS - set(m.keys())
    if missing:
        raise EditorMetadataError(
            f"macros[{i}] missing required keys: {sorted(missing)}."
        )
    unknown = set(m.keys()) - ALLOWED_MACRO_KEYS
    if unknown:
        raise EditorMetadataError(
            f"macros[{i}] has unknown keys: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_MACRO_KEYS)}."
        )
    if not isinstance(m["id"], str) or not m["id"]:
        raise EditorMetadataError(f"macros[{i}].id must be a non-empty string.")
    if not isinstance(m["spec"], dict):
        raise EditorMetadataError(f"macros[{i}].spec must be an object.")
    if not isinstance(m["repeat"], int) or m["repeat"] < 1:
        raise EditorMetadataError(
            f"macros[{i}].repeat must be a positive int (>= 1); "
            f"got {m['repeat']!r}."
        )


# ---------- SQLAlchemy column type --------------------------------------

# c2 review #3 finding #2: NO `try / except ImportError` fallback on the
# SQLAlchemy import. SQLAlchemy is a hard requirement (every consumer of
# this module's `SidecarJSON` already needs SQLAlchemy installed via
# `engine/models.py`). The fallback silently downgraded to plain JSON
# with no log + no warning if any future ImportError reached the import
# — exactly the opposite of defense in depth. Fail loud at import time
# instead.


class SidecarJSON(TypeDecorator):
    """SQLAlchemy column type wrapping `JSON(none_as_null=True)` that
    runs `validate_sidecar_grammar` on every ORM-attribute bind. See
    DESIGN.md §16.2.5.t4 for the rationale; the docstring here pins
    the coverage contract.

    **Coverage:** writes via the ORM Station attribute
    (`station.sidecar = ...; db.commit()` — fixtures, endpoints,
    scripts using the ORM).

    **Bypass (intentional):** any future alembic data migration that
    constructs an ad-hoc `sa.table('station', sa.column('sidecar',
    sa.JSON()))` to avoid importing engine code bypasses the
    validator by design — the runtime read path degrades via
    `_resolve_sidecar(strict=False)` warning banner.

    **Reads:** pass-through (no `process_result_value` override —
    SQLAlchemy's impl-chain handles JSON deserialisation, lenient
    `_resolve_sidecar(strict=False)` handles legacy/broken data).

    `none_as_null=True` is on the impl so Python None binds to SQL
    NULL (not the JSON 'null' string literal). Critical for
    `populate_db_sidecars`'s `Station.sidecar.is_(None)` filter.
    """
    impl = _SaJSON(none_as_null=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        validate_sidecar_grammar(value)
        return value

    # c2 review #3 finding #4: NO `process_result_value` override.
    # Defining a pass-through method flips SQLAlchemy's
    # `_has_result_processor` to True and forces every read through
    # a no-op closure. Letting the base class handle it (no override)
    # makes reads route through the impl's JSON deserialiser directly.
