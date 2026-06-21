"""TS↔Py `SYSTEM_SECTION_IDS` parity (§16.2.5.t20).

The frontend mirrors `engine.editor_metadata.SYSTEM_SECTION_IDS`
in `web/src/lib/drag-drop.ts` so client-side drag-drop affordances
can gate target sections without a server round-trip. Two
hand-synced sources of truth with no drift guard: a future
contributor adding `_archived` server-side could forget the TS
Set; the frontend's `canDropOn` would then permit drops the
backend rejects (the user sees the chip snap back; nothing logs
why).

This parity test reads the TypeScript module via regex + asserts
the parsed set matches the Python frozenset byte-for-byte. Cheap
defense; runs in <50ms.

Picked the regex-parse approach over codegen because:
  * codegen needs a build step the CI pipeline doesn't have today;
  * the set is small + stable (one entry today, maybe two in
    future);
  * a failing parity test points the contributor at the divergent
    file with a precise error message.

Promote to codegen if the set grows beyond ~5 members OR a parallel
constant emerges (the row's "second system bin" trigger from the
c1 review #3 docstring).
"""
from __future__ import annotations

import re
from pathlib import Path

from engine.editor_metadata import SYSTEM_SECTION_IDS


_TS_MODULE = (
    Path(__file__).resolve().parent.parent
    / "web" / "src" / "lib" / "drag-drop.ts"
)

# `export const SYSTEM_SECTION_IDS: ReadonlySet<string> = new Set(['_unsorted']);`
# Capture the bracketed initializer; the inner-quoted values are
# extracted by a second pass so an extra trailing comma or whitespace
# variation doesn't trip the outer pattern.
_DECL_PATTERN = re.compile(
    r"export\s+const\s+SYSTEM_SECTION_IDS\s*:\s*ReadonlySet<string>\s*=\s*"
    r"new\s+Set\(\s*\[(?P<members>[^\]]*)\]\s*\)\s*;",
    re.MULTILINE,
)

# Single or double quoted string literal — accepts either.
_MEMBER_PATTERN = re.compile(r"""['"]([^'"]+)['"]""")


def _parse_ts_system_section_ids() -> frozenset[str]:
    """Return the TS-side set members or raise AssertionError with the
    offending source so a contributor changing the declaration syntax
    sees a precise error pointing at the diff site."""
    text = _TS_MODULE.read_text(encoding="utf-8")
    match = _DECL_PATTERN.search(text)
    assert match is not None, (
        f"could not parse SYSTEM_SECTION_IDS declaration in {_TS_MODULE}. "
        "Check that the line still matches the expected shape: "
        "`export const SYSTEM_SECTION_IDS: ReadonlySet<string> = "
        "new Set([...]);` — if the TS-side syntax changed, update "
        "_DECL_PATTERN in this test."
    )
    members = tuple(_MEMBER_PATTERN.findall(match.group("members")))
    return frozenset(members)


def test_ts_and_py_system_section_ids_agree():
    """The frontend's `SYSTEM_SECTION_IDS` Set must equal the backend's
    frozenset. If this fails: add the new bin to BOTH source files
    AND the canDropOn / structural-edit guard paths (per the row's
    `_archived` example in §16.2.5.t13)."""
    ts_set = _parse_ts_system_section_ids()
    py_set = SYSTEM_SECTION_IDS
    assert ts_set == py_set, (
        f"SYSTEM_SECTION_IDS drift detected:\n"
        f"  TS  ({_TS_MODULE.name}): {sorted(ts_set)}\n"
        f"  Py  (engine.editor_metadata): {sorted(py_set)}\n"
        f"Synchronise the two declarations before merging."
    )
