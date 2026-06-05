"""Lint test: guard against bare `layout.row_template` access.

The "row_template-only walk" bug class has fired 3 times in
station-studio's history:

  1. §14.32 — `max_mix_exclusion_window`, `_save_mix_usages`, and
     the `_cursors_to_sources` site each iterated `row_template`
     only, silently skipping flat_slots templates.
  2. §14.40 — sequential cursor offset (caught during port byte-
     equality; root-caused to the same pattern).
  3. §14.41 post-review (sweep 2) — parse-time strategy + role
     validation at `engine/template.py:288` still iterated
     `row_template`, letting flat_slots templates with unknown
     strategy / non-content source sail through parse.

The structural fix is `LayoutSpec.all_blocks()` (introduced in
§14.32). The bug keeps recurring because new code can ALSO write
`for b in layout.row_template:` and look perfectly innocent — the
pattern's locality means review doesn't catch it.

This test scans production source files for bare
`.layout.row_template` access and fails if the site isn't on the
allowlist of legitimately-XOR-aware locations.

Allowlist:
  * `engine/template.py:LayoutSpec.all_blocks` — the helper that
    consolidates both forms.
  * `engine/template.py:Template.slots_per_week` — sums flat_slots
    AND multiplies row_template by row count; an XOR-aware use.
  * `engine/generator.py:_iterate_slots` — inside the
    `if layout.flat_slots: … else:` row_template-dispatch branch;
    the engine's intentional walk.

ANY OTHER access in `engine/`, `api/`, or `scripts/` is a bug
waiting to happen. The fix: use `layout.all_blocks()`.

Allowed locations explicitly note the §-history above; if you
genuinely need to read `layout.row_template` outside the
allowlist, add an entry HERE (with reasoning) and re-run the test.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Glob-matched. Restricts the scan to PRODUCTION source files.
PRODUCTION_DIRS = ["engine", "api", "scripts"]

# Pattern: any access spelled `.layout.row_template` (with leading
# dot — avoids matching the dataclass field's bare name in the
# parser's allowlists, or JSON construction in tests that use
# `"row_template"` as a dict key).
PATTERN = re.compile(r"\.layout\.row_template\b")

# Allowlist: (file_relative_to_repo_root, line_number).
# Update when adding a NEW legitimately-XOR-aware site, with a
# comment in this list explaining why.
ALLOWED_SITES: set[tuple[str, int]] = {
    # §14.32: Template.slots_per_week — sums BOTH layout forms (the
    # XOR is exploited correctly here; one branch contributes 0).
    # (Line moved 204 → 221 in §16.14.t9 c1 when the rules: block
    # added 2 dataclass fields + a top-level import block.)
    ("engine/template.py", 301),  # bumped from 265 in §16.14.t7 c1 (BlockSpec.allowlist_sources + Template.allowlist_source_names + role cross-check expansion + BlockSpec docstring)
    # §14.32: _iterate_slots — engine's slot-dispatch loop; the
    # `else` branch after `if layout.flat_slots:` is the row_template
    # dispatch and CORRECTLY reads row_template only.
    # (Line moved 688 → 838 in §16.11 c1 when generate became a wrapper
    # around generate_stream + ProgressEvent emissions; 838 → 856 in
    # §16.11 c1 review fixes when the wrapper grew a "no events after
    # Result" guard + the cadence comment + prev_counter helper; 856 →
    # 870 in §16.11 slice review fixes when the ProgressEvent.phase
    # vocabulary block was expanded with the current valid values;
    # 870 → 878 in §16.14.t5 c1 when both apply_weighted call sites
    # picked up the excluded_track_pids kwarg + comment lines;
    # 878 → 920 in §16.14.t6 c1 when the archive_ref dispatch case
    # + the strategies-package import comment landed; 920 → 928 in
    # §16.14.t6 c1-review F1 when block.position's parser-guarantee
    # assertion replaced the type:ignore; 928 → 962 in §16.14.t8 c1
    # when window/pick/sequential dispatch cases gained the
    # excluded_track_pids kwarg + comments + sequential's inline
    # cross-pool-dedup pre-check.)
    # 962 → 966 in t8 c1 when WindowOutOfRangeError moved to an
    # explicit module-level import for the sequential dispatch's
    # raise site; 966 → 969 in t8 c1-review F2 when that import
    # consolidated into the strategies-package re-exports + the
    # explanatory comment expanded.
    ("engine/generator.py", 969),
    # NOTE: `LayoutSpec.all_blocks` itself (engine/template.py:174)
    # uses `self.row_template` (no `.layout.` prefix; it's a method
    # ON LayoutSpec) and so doesn't match this lint pattern. That's
    # the intended consolidator other code should call.
}


def test_no_bare_row_template_access_outside_allowlist():
    """Scan PRODUCTION sources for bare `.layout.row_template` access.

    If any new site appears that isn't on the allowlist, this test
    fails LOUDLY. Two ways to address a failure:

      1. **Use `layout.all_blocks()`**. Almost always correct — the
         pattern's whole point is to handle both layout forms.

      2. **Add the site to ALLOWED_SITES with a comment** explaining
         why this is a legitimately-XOR-aware location (i.e. the
         code is INSIDE an `if layout.flat_slots: … else:` branch,
         or is the `all_blocks()` helper itself).

    Failure mode being prevented: a future PR introduces
    `for b in template.layout.row_template:` in a NEW helper, and
    the helper silently mishandles flat_slots templates until a
    real station of that shape exposes it.
    """
    found: list[tuple[str, int, str]] = []
    for dirname in PRODUCTION_DIRS:
        root = REPO_ROOT / dirname
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT).as_posix()
            for lineno, line in enumerate(
                path.read_text().splitlines(), start=1,
            ):
                if PATTERN.search(line):
                    found.append((relative, lineno, line.strip()))

    # Build a set of found (file, lineno) pairs for comparison.
    found_sites = {(f, ln) for (f, ln, _) in found}
    unexpected = found_sites - ALLOWED_SITES

    if unexpected:
        # Format the message with the actual offending lines for
        # quick fix-or-allowlist triage.
        lines = ["bare `.layout.row_template` access at non-allowlisted sites:"]
        for (f, ln, code) in found:
            if (f, ln) in unexpected:
                lines.append(f"  {f}:{ln}: {code}")
        lines.append("")
        lines.append(
            "Fix: change to `layout.all_blocks()` so the helper handles "
            "BOTH flat_slots AND row_template templates. If this is a "
            "legitimately-XOR-aware site (inside an `if layout.flat_slots:` "
            "branch, or is the all_blocks helper itself), add it to "
            "ALLOWED_SITES in tests/test_no_bare_row_template_access.py "
            "with a comment explaining why."
        )
        raise AssertionError("\n".join(lines))

    # Sanity check the allowlist hasn't gone stale (allowlisted lines
    # that no longer match the pattern → silent allowlist drift).
    stale = ALLOWED_SITES - found_sites
    if stale:
        raise AssertionError(
            f"allowlist has STALE entries (no longer match the pattern): "
            f"{sorted(stale)}. Remove them from ALLOWED_SITES."
        )
