# Sub-slice #17 plan — iTunes XML pool import + SourcePlaylist.role

**Status**: queued (next slice). DESIGN.md §14.35 lands when this ships;
this doc is the pre-implementation plan + design decisions from the
discussion preceding commit 1. Delete when §14.35 commits.

## Context: where #16 left things

After §14.34 shipped, the chain is:

```
weekly/from_user/*.xml ──╮  GAP — pools never imported
                         │
weekly/app/services/  ───┤
   template.py            │
       │ dump_weekly_template.py
       ▼
   weekly-template.json ─► import_station.py ─► Station row (rebirth-radio)
                                                  template ✅
                                                  pools     ❌
```

`_check_pools_present` raises on the first generate. The 6 source
pools (JINGLES / COMMERCIAL / MIXES / NIGHT / WEEKEND / MASTER) need
to land before the imported station can produce a playlist.

## Phase B closeout — three remaining sub-slices

| Slice | Goal | Est. commits | Status |
|---|---|---|---|
| **#17** | Pool import CLI + filename→pool-name mapping + MASTER reference-role decision | 2 | **next** |
| **#18** | Commit `stations/rebirth-radio.json` artifact (so `make seed-stations` ships Rebirth Radio out of the box) | 1 | queued |
| **#19** | Byte-equality verification harness — same pools + same seed + same week → same output as weekly's known-good fixture. Phase B's acceptance criterion. | 1-2 | queued (closes Phase B) |

After #19 lands, Phase B is done. Phase C (operational cutover —
retire weekly's generator, point production at station-studio)
becomes ops work, not engineering.

## Design decisions (from the AskUserQuestion discussion)

1. **Framing**: A — pool import only. Smallest viable slice. Don't
   bundle the rebirth-radio.json commit (#18) or byte-equality (#19)
   into #17.

2. **MASTER reference role**: Add `role` column to SourcePlaylist
   + `--reference` / `role=` flag to the importer. Schema change +
   Alembic migration. Models the role at the DB level.

3. **CLI shape**: Manifest JSON — single file pointing at XML paths
   with explicit role per pool. More flexible than directory-and-
   hardcoded-filenames; one extra fixture to author.

4. **Auto-backup**: Yes. `make import-weekly-pools` auto-backs-up
   the DB before writing (pool import is a heavy write — 10K+ rows
   across 6 pools); `ARGS=--no-backup` escape hatch matches
   `make import-station`.

5. **Reference-check semantics**: Cross-check between template's
   `sources[].role` and DB pool's `role`. Raise `TemplateError`
   at generation time when template says `content` but DB pool is
   `reference` (the dangerous direction — engine would sample
   from a pool the template marked as not-for-sampling). Opposite
   direction is benign and silently OK.

6. **Manifest role declaration**: Explicit per pool (no default).
   Reads better in code review; prevents the "forgot to set role
   on MASTER" mistake. ~6 LoC per pool is fine.

## Commit 1 — schema + engine plumbing

* Add `role` column to `SourcePlaylist`: `Text, nullable=False,
  default='content'`.
* Alembic migration: add the column with default `'content'` for
  existing rows; downgrade drops it.
* `engine.importer.import_source(db, path, name, *, role='content')` —
  keyword-only role parameter.
* Engine-side consistency check: at generation time, raise
  `TemplateError` if a layout-`content` source resolves to a DB
  pool with `role='reference'`. Opposite direction (layout-
  `reference` + DB-`content`) is benign — engine skips per template.
* API surface: `POST /api/sources/import` accepts optional `role`
  field in payload; `GET /api/sources` returns role.
* Tests:
  - model column default
  - migration up/down
  - importer with role=reference
  - the cross-check error path
  - API round-trip

## Mid-slice review

Per the established §14.25 / §14.28 / §14.32 / §14.34 pattern.
Three of the last four mid-slice reviews caught real bugs that
slice-level review would have missed. Pattern: after commit 1 the
structure is settled and the second pass notices "this allowlist
is asymmetric with §14.32's parser policy" — easier across two
commits than within one.

## Commit 2 — manifest CLI + DESIGN §14.35

* `scripts/import_weekly_pools.py` reads a manifest:

  ```json
  {
    "_comment": "Manifest for weekly's 6-pool XML import (§14.35).",
    "schema_version": 1,
    "pools": {
      "JINGLES":    {"path": "from_user/JINGLES.xml",    "role": "content"},
      "COMMERCIAL": {"path": "from_user/COMMERCIAL.xml", "role": "content"},
      "MIXES":      {"path": "from_user/MIXES.xml",      "role": "content"},
      "NIGHT":      {"path": "from_user/NIGHT.xml",      "role": "content"},
      "WEEKEND":    {"path": "from_user/WEEKEND.xml",    "role": "content"},
      "MASTER":     {"path": "from_user/MASTER.xml",     "role": "reference"}
    }
  }
  ```

* Strict-mode manifest parsing — mirrors §14.32 / §14.34 policy:
  - Allowed top-level keys: `pools`, `_comment`, `schema_version`
  - Allowed per-pool keys: `path`, `role`
  - Unknown keys → clear error
  - Role values must be `"content"` or `"reference"`
* Relative `path` resolved against the manifest file's parent
  directory (typical workflow: manifest sits next to the XMLs).
* Per-pool action reporting (created / updated / unchanged),
  matching `import_station.py`'s output style.
* `tests/fixtures/sample_pool_manifest.json` + tiny inline iTunes
  XMLs (2-3 tracks per pool) under `tests/fixtures/itunes_xml/`.
* `tests/test_import_weekly_pools.py` — parallel to
  `test_import_station.py`. CLI/DB shim tests + manifest validation
  + end-to-end with the sample fixture.
* `Makefile`: `import-weekly-pools manifest=path/to/manifest.json`
  with auto-backup + `ARGS=--no-backup` escape hatch.
* `DESIGN.md §14.35` — design discussion outcome, manifest shape
  rationale, role-mismatch check, MASTER reference role semantics,
  decisions punted, carry-forward for #18 + #19.
* §15 status snapshot bump; §17 roadmap update mentions #18 + #19
  as named queued slices.

## Out of scope (deferred to #18+)

* Committing the real Rebirth Radio iTunes XML files (the synthetic
  test fixtures cover #17's purposes).
* Wire MASTER's reference role into anything beyond the type column
  (e.g. "use MASTER's persistent IDs to validate other pools" —
  that's an exporter concern, not import).
* UI for pool import (the wizard still uses the existing single-pool
  `POST /api/sources/import` form).
* Re-import workflow ergonomics (today: re-run the manifest, each
  pool's idempotency handles the rest).
