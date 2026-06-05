"""Parity guard: the editable-field maps in three places must agree
(§16.2.3 Decision C).

The same per-strategy "which parameter fields can the editor change"
table is hand-maintained in:

  1. api/routers/editor.py  — `EDITABLE_FIELDS_BY_STRATEGY` (the
     server-side parameter-only PATCH guard; source of truth).
  2. web/src/lib/editor.ts  — `EDITABLE_FIELDS` (drives the
     DetailPanel form).

Drift between them is the risk: a field editable in the UI but
rejected by the server (frustrating dead-end) or vice versa. This
test extracts the field sets from BOTH and asserts they're identical.

It ALSO cross-checks the Python map against the engine's actual
strategy-field coupling (engine/template.py `_require_strategy_field`
required_for / optional_for) so the editable set can't silently
include a field the engine doesn't accept (or omit one it does).
"""
from __future__ import annotations

import re
from pathlib import Path

from api.routers.editor import EDITABLE_FIELDS_BY_STRATEGY


REPO_ROOT = Path(__file__).resolve().parent.parent
EDITOR_TS = REPO_ROOT / "web" / "src" / "lib" / "editor.ts"


def _parse_ts_editable_fields() -> dict[str, set[str]]:
    """Extract `EDITABLE_FIELDS` from editor.ts into {strategy: {names}}.

    The const looks like:
        export const EDITABLE_FIELDS: Record<...> = {
          pick: [],
          sequential: [
            { name: 'cursor_id', ... },
          ],
          ...
        };
    Parse the object body, then per-strategy collect `name: '...'`.

    §16.2.4 c1 review #13 fragility note: the parser uses regex to
    pull `name: 'X'` literals from each strategy's array body. If
    a future refactor extracts the field spec into a named constant
    (e.g. `pick: [ALLOWLIST_SOURCES_SPEC]`), the constant reference
    is invisible to this regex → the parity test SILENTLY narrows
    the TS side's set + hides a real drift. The
    `test_ts_field_arrays_contain_only_object_literals` check below
    fails loudly on that pattern; do NOT remove it without replacing
    this parser with a real TS AST walker (tsc API / typescript
    Python bindings).
    """
    text = EDITOR_TS.read_text()
    m = re.search(
        r"export const EDITABLE_FIELDS[^=]*=\s*\{(.*?)\n\};",
        text,
        re.DOTALL,
    )
    assert m, "could not locate EDITABLE_FIELDS const in editor.ts"
    body = m.group(1)

    # Split into `strategy: [ ... ]` entries. Strategies are the known
    # engine strategy names; each maps to a [...] array literal.
    out: dict[str, set[str]] = {}
    # Match `<strategy>: [ <array contents> ]` (arrays may be empty or
    # span multiple lines).
    for sm in re.finditer(r"(\w+):\s*\[(.*?)\]", body, re.DOTALL):
        strategy = sm.group(1)
        arr = sm.group(2)
        names = set(re.findall(r"name:\s*'([^']+)'", arr))
        out[strategy] = names
    return out


def test_ts_field_arrays_contain_only_object_literals():
    """c1 review #13: anti-fragility guard against extracted-constant
    drift. The parity regex above only counts `name: 'X'` literals;
    an extracted constant like `[ALLOWLIST_SOURCES_SPEC]` would slip
    through invisibly and let the TS/Python sets disagree without
    the parity test catching it.

    This test walks each strategy's array body and asserts every
    non-whitespace, non-comma element is an object literal (starts
    with `{`). If the next big EDITABLE_FIELDS refactor wants to
    DRY up duplicated specs, this fires first — replace the regex
    parser with a real AST walker (tsc / TypeScript Python bindings)
    before extracting any constants.
    """
    text = EDITOR_TS.read_text()
    m = re.search(
        r"export const EDITABLE_FIELDS[^=]*=\s*\{(.*?)\n\};",
        text,
        re.DOTALL,
    )
    assert m, "could not locate EDITABLE_FIELDS const in editor.ts"
    body = m.group(1)
    for sm in re.finditer(r"(\w+):\s*\[(.*?)\]", body, re.DOTALL):
        strategy = sm.group(1)
        arr = sm.group(2).strip()
        if not arr:
            continue  # empty array — fine.
        # Strip out object-literal contents (matched braces, naive but
        # sufficient — none of our specs nest braces). What's left
        # should be whitespace + commas only.
        stripped = re.sub(r"\{[^{}]*\}", "", arr)
        leftovers = re.sub(r"[\s,]", "", stripped)
        assert leftovers == "", (
            f"strategy '{strategy}' in editor.ts EDITABLE_FIELDS has "
            f"non-object-literal elements: {leftovers!r}. The parity "
            f"regex can only see inline `{{ name: 'X' }}` specs; an "
            f"extracted constant or identifier reference would silently "
            f"hide field names. Either inline the spec or rewrite the "
            f"parity parser to use a real TS AST walker."
        )


def test_python_and_ts_editable_fields_match():
    ts = _parse_ts_editable_fields()
    py = {k: set(v) for k, v in EDITABLE_FIELDS_BY_STRATEGY.items()}
    assert ts == py, (
        "editable-field map drift between api/routers/editor.py "
        "(EDITABLE_FIELDS_BY_STRATEGY) and web/src/lib/editor.ts "
        f"(EDITABLE_FIELDS).\n  python={py}\n  ts={ts}"
    )


def test_every_strategy_present_in_both():
    """No strategy missing from either side — covers the full engine
    strategy set. §16.14.t6 added `archive_ref` (TS side updated in
    the t6 sweep; Python side updated in t8 c1 after the sweep
    miss was caught by this very test)."""
    expected = {
        "pick", "window", "sequential", "jingle", "mix", "weighted",
        "prev_ref", "archive_ref",
    }
    assert set(EDITABLE_FIELDS_BY_STRATEGY) == expected
    assert set(_parse_ts_editable_fields()) == expected


def test_editable_fields_are_engine_accepted_optional_params():
    """Cross-check vs the engine: every editable field must be one the
    engine's `_require_strategy_field` actually couples to that
    strategy (required_for OR optional_for). Catches a field that's
    editable in the UI but the engine forbids → parse_template 400 on
    every save.

    Source-of-truth expectations (engine/template.py `_parse_block`):
    """
    engine_accepts = {
        # §16.14.t7 slice-review SR1: each content-sampling strategy
        # (weighted, window, pick, sequential — the t7
        # `CONTENT_SAMPLING_STRATEGIES` opt-in set) also accepts
        # `allowlist_sources` per the t7 parser extension. The
        # frontend EDITABLE_FIELDS deliberately doesn't expose it
        # for editing yet (t7 c3 docs punt; the block visualiser
        # at /stations/{slug} doesn't expose per-block fields for
        # editing pending §16.2.4/§16.2.5) — but the engine's
        # capability table needs to reflect REALITY, not the
        # frontend's surface choices. Subset relation (EDITABLE
        # ⊆ engine_accepts) still holds.
        "pick": {"allowlist_sources"},
        "window": {"allowlist_sources"},
        "sequential": {"cursor_id", "allowlist_sources"},
        "jingle": {"genre"},
        "mix": {"genre", "exclusion_window"},
        "weighted": {"window", "allowlist_sources"},
        "prev_ref": {"position", "fallback_genre"},
        # §16.14.t6: archive_ref shares prev_ref's `position`
        # (required) field but NOT fallback_genre (no fallback path).
        "archive_ref": {"position"},
    }
    for strategy, fields in EDITABLE_FIELDS_BY_STRATEGY.items():
        assert fields <= engine_accepts[strategy], (
            f"strategy '{strategy}' lists editable fields the engine "
            f"doesn't accept: {fields - engine_accepts[strategy]}"
        )
