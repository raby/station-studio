"""Schema-parity guard: TypeScript `web/src/lib/editor.ts` mirrors of
the Python sidecar grammar must stay in sync (§16.2.2 mid-slice
review 🟡-5).

How the drift happens: TS types are hand-translated from
`engine/editor_metadata.py`. A field added to the Python grammar
(REQUIRED_*_KEYS / ALLOWED_*_KEYS) without mirroring in TS produces
silent runtime drift — the c1 review caught Macro's `spec` vs the
TS's wrong `blocks` field. This test fails LOUDLY on the next drift.

Approach: text-search `editor.ts` for every key in the Python
allowlists. Cheap, no JS parser needed. Trade-off: a key buried in
a comment counts as "present" — we accept that for the simplicity;
the next code reviewer catches misplacement.

ALSO guards `EngineBlock`: every strategy-specific BlockSpec field
in `engine/template.py` must have a matching `?: ...` field in TS
(otherwise `verboseStrategyLabel` silently can't surface it).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
EDITOR_TS = REPO_ROOT / "web" / "src" / "lib" / "editor.ts"


@pytest.fixture
def editor_ts_text() -> str:
    return EDITOR_TS.read_text()


# ---------- sidecar grammar keys ----------------------------------------


def test_top_level_required_keys_appear_in_ts(editor_ts_text):
    """Every key in `REQUIRED_TOP_LEVEL` must be non-optional in
    `Sidecar` (no `?:` after the field name on its line)."""
    from engine.editor_metadata import REQUIRED_TOP_LEVEL

    for key in REQUIRED_TOP_LEVEL:
        # Look for `  key: ` (required) NOT `  key?: ` (optional).
        # Anchored on `Sidecar` interface scope is overkill; the
        # interfaces share key names but the REQUIRED set is small
        # + each name is distinct enough.
        required_pattern = rf"\b{re.escape(key)}\s*:"
        optional_pattern = rf"\b{re.escape(key)}\?\s*:"
        has_required = re.search(required_pattern, editor_ts_text)
        has_only_optional = (
            re.search(optional_pattern, editor_ts_text)
            and not has_required
        )
        assert has_required, (
            f"REQUIRED top-level key {key!r} missing from editor.ts"
        )
        assert not has_only_optional, (
            f"REQUIRED top-level key {key!r} is optional (`{key}?:`) in "
            f"editor.ts — must be required"
        )


def test_top_level_allowed_keys_appear_in_ts(editor_ts_text):
    """Every key in `ALLOWED_TOP_LEVEL` must appear somewhere in
    `editor.ts` (required OR optional). Catches drift where Python
    adds a top-level field that TS hasn't mirrored."""
    from engine.editor_metadata import ALLOWED_TOP_LEVEL

    for key in ALLOWED_TOP_LEVEL:
        assert re.search(rf"\b{re.escape(key)}\??\s*:", editor_ts_text), (
            f"ALLOWED top-level key {key!r} not mirrored in editor.ts"
        )


def test_section_keys_appear_in_ts(editor_ts_text):
    """Section ALLOWED keys present; REQUIRED keys non-optional."""
    from engine.editor_metadata import (
        ALLOWED_SECTION_KEYS, REQUIRED_SECTION_KEYS,
    )

    for key in ALLOWED_SECTION_KEYS:
        assert re.search(rf"\b{re.escape(key)}\??\s*:", editor_ts_text), (
            f"ALLOWED section key {key!r} not mirrored in editor.ts"
        )
    for key in REQUIRED_SECTION_KEYS:
        # required: must have at least one non-`?:` declaration.
        assert re.search(rf"\b{re.escape(key)}\s*:", editor_ts_text), (
            f"REQUIRED section key {key!r} not non-optional in editor.ts"
        )


def test_macro_keys_appear_in_ts(editor_ts_text):
    """Macro ALLOWED keys present; REQUIRED keys non-optional. This
    is the test that would have CAUGHT the c2 review 🔴-1 drift —
    `spec` is in REQUIRED_MACRO_KEYS but my original TS used `blocks`
    instead of `spec`."""
    from engine.editor_metadata import (
        ALLOWED_MACRO_KEYS, REQUIRED_MACRO_KEYS,
    )

    for key in ALLOWED_MACRO_KEYS:
        assert re.search(rf"\b{re.escape(key)}\??\s*:", editor_ts_text), (
            f"ALLOWED macro key {key!r} not mirrored in editor.ts"
        )
    for key in REQUIRED_MACRO_KEYS:
        assert re.search(rf"\b{re.escape(key)}\s*:", editor_ts_text), (
            f"REQUIRED macro key {key!r} not non-optional in editor.ts"
        )


def test_atomic_block_addressing_keys_appear_in_ts(editor_ts_text):
    """`slot_index` (flat) and `row_idx` / `block_idx` (row_template)
    are the two XOR addressing modes for atomic blocks — both must be
    typed optionally on the TS atomic-block interface (the XOR
    invariant is runtime-enforced by Python's parser + the TS
    `expandSection` helper)."""
    from engine.editor_metadata import ATOMIC_FLAT_KEYS, ATOMIC_ROW_KEYS

    for key in (ATOMIC_FLAT_KEYS | ATOMIC_ROW_KEYS):
        # All addressing fields are optional in TS (XOR enforced at
        # runtime). `kind` is the shared discriminator.
        assert re.search(rf"\b{re.escape(key)}\??\s*:", editor_ts_text), (
            f"atomic-block addressing key {key!r} not mirrored in editor.ts"
        )


# ---------- EngineBlock strategy-specific fields ------------------------


def test_block_spec_strategy_fields_mirrored_in_ts(editor_ts_text):
    """Every optional field on `engine.template.BlockSpec` must have
    a matching `?: ...` field in the TS `EngineBlock` interface.
    Otherwise `verboseStrategyLabel` silently can't surface it +
    `compactStrategyLabel` extensions miss it."""
    from dataclasses import fields

    from engine.template import BlockSpec

    optional_field_names = [
        f.name for f in fields(BlockSpec)
        if f.name not in {"source", "strategy", "size"}
    ]

    for name in optional_field_names:
        # Must appear as `name?:` in the TS EngineBlock.
        assert re.search(rf"\b{re.escape(name)}\?\s*:", editor_ts_text), (
            f"BlockSpec optional field {name!r} missing from "
            f"`EngineBlock` in editor.ts (mid-slice review 🟡-4: "
            f"this is the guard for that class of drift)"
        )


def test_engine_block_strategy_union_covers_all_supported_strategies(editor_ts_text):
    """§16.14.t6 slice-review SR1+SR2+SR3: every entry in
    ``engine.template.SUPPORTED_STRATEGIES`` must appear as a
    quoted literal in the TS ``EngineBlock.strategy`` union.

    Why this exists: archive_ref was added to SUPPORTED_STRATEGIES
    in §16.14.t6 c1 but slipped past the TS union because the
    parity test pinned only optional BlockSpec FIELDS — not the
    strategy union itself. A template with archive_ref blocks
    would fail TS validation on load. This guard catches the
    next strategy addition (e.g. §16.14.t7 allowlist enforcement)
    at test time instead of at first-template-load.

    Also pins ``compactStrategyLabel``'s switch statement: TS's
    exhaustiveness check (`_exhaustive: never`) compile-errors
    when a strategy is in the union but unhandled. The pin keeps
    the runtime + the compile-time guard aligned with the
    backend's source of truth.
    """
    from engine.template import SUPPORTED_STRATEGIES

    for strategy in SUPPORTED_STRATEGIES:
        # Must appear as `'strategy'` (single-quoted, as TS union
        # member). Allow `| 'strategy'` or first-position
        # `strategy:\n    | 'strategy'`.
        assert re.search(rf"'{re.escape(strategy)}'", editor_ts_text), (
            f"SUPPORTED_STRATEGIES entry {strategy!r} missing from "
            f"the `EngineBlock.strategy` union in editor.ts. Add it "
            f"to the union + handle it in `compactStrategyLabel` "
            f"(the exhaustiveness check will compile-error otherwise)."
        )


# ---------- structural sanity ------------------------------------------


def test_editor_ts_file_exists():
    """File-presence smoke — if the file moves the other tests would
    fail with a confusing FileNotFoundError; this is a clearer
    failure."""
    assert EDITOR_TS.is_file(), (
        f"web/src/lib/editor.ts not found at {EDITOR_TS} — has the "
        f"file moved? Update tests/test_editor_ts_schema_parity.py."
    )
