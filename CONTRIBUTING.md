# Contributing to Station Studio

Thanks for your interest. Station Studio is open-source under
[MIT](LICENSE) — fork freely, send PRs, file issues.

This doc covers the conventions the project uses internally. They
exist because the engine is byte-equality-tested against two
production radio stations; loose conventions would erode that
guarantee. Following them is what keeps "any contributor can ship
without breaking real stations" true.

## How a slice happens

Station Studio's history is a long sequence of small, named slices
(§14.x in `DESIGN.md`). Each slice is one coherent unit of work:
designed → discussed → implemented → reviewed → shipped. The
discipline is:

1. **Design discussion** — for anything non-trivial, propose a
   design before writing code. Open an issue or PR with the design
   first. The §14.x entries in `DESIGN.md` are the model; lay out
   what the slice ships, what decisions were taken, what was
   punted.

2. **Mid-slice review** (for two-commit slices) — after the first
   commit lands, review it before the second. Real bugs have been
   caught this way in §14.25 / §14.28 / §14.32 / §14.34 / §14.37
   / §14.38 / §14.40 by a fresh eye on settled structure.

3. **Slice-level review** — after the slice ships, review the
   whole thing. Catches integration-level issues the per-commit
   reviews miss.

4. **DESIGN entry + status snapshot** — every shipped slice gets a
   §14.x entry. The entry documents *why* the slice exists, *what*
   shipped, *what was punted*, and any *carry-forward notes* for
   the next slice. Pattern: read existing §14.x entries to see the
   shape.

The slice pattern is mature; if a change doesn't fit it, propose
the change to the pattern itself first.

## Test discipline

| Layer | Tool | Gate |
|---|---|---|
| Backend (engine + API) | pytest | ≥90% line coverage (`make test-backend`) |
| Migrations | `make migration-parity` | upgrade-head must match `Base.metadata.create_all` |
| Frontend unit | vitest | per-file lines gate in `web/vite.config.js` |
| End-to-end | Playwright | must pass in CI |
| Byte-equality (Rebirth Radio) | `tests/test_port_byte_equality.py` | skip-when-XMLs-missing; **must stay green when XMLs present** |
| Byte-equality (Classics) | `tests/test_classics_byte_equality.py` | always runs (synthetic pools committed) |

Changes that affect the engine MUST not break byte-equality for
either example station. If you're intentionally changing engine
output (new strategy, bug fix), regenerate the fixtures via
`BOOTSTRAP_BYTE_EQ_FIXTURES=1` AND `make refresh-port-fixture`,
inspect the diff, and document the intentional change in the
slice's DESIGN entry.

## Code style

- **Python**: stdlib first; SQLAlchemy 2.0 patterns; type hints on
  module-level signatures (relaxed on test helpers); no `mypy`
  strict yet.
- **Frontend**: SvelteKit; TypeScript; Vitest for unit + Playwright
  for e2e. `svelte-check --threshold error` in CI.
- **Docstrings**: long-form, narrative. Not just "what" — *why*.
  See existing engine modules for the shape. The goal is that
  someone reading the code six months later understands the
  decisions, not just the mechanics.

## Proposing a new station shape

The engine's grammar is documented in `DESIGN.md` §14.32 (flat_slots)
and §14.34 (weekly-format translator). Two ways to add a station:

1. **From scratch** — author a `stations/<slug>.json` matching the
   template grammar. Add a byte-equality "freeze-against-self" test
   under `tests/test_<slug>_byte_equality.py` so future engine
   changes don't silently regress your station. The Classics test
   is the model.

2. **From a weekly-format export** — if your station has a
   `weekly/`-style generator already, use
   `scripts/dump_weekly_template.py` + `make import-station` to
   bring it in.

If your station needs a primitive the engine doesn't have, propose
it as a §14.x slice (engine strategy modules live under
`engine/strategies/`; pattern: pure function, no DB, no
randomness-source as arg).

## Commit message style

```
§14.NN <slice title>: <one-line summary>

<paragraph(s) explaining what shipped + why>

What ships
----------
* <bullet per artifact>

Verification
------------
* <pytest counts, coverage, byte-equality status>

Co-Authored-By: ...
```

See git log for examples. The `§14.NN` prefix anchors the commit
to its DESIGN entry; pre-§14 work used `§13` etc. Sweep-up commits
that fix review findings use `§14.NN sweep-up: ...`.

## When in doubt

Read `DESIGN.md`. The §14.x entries explain why every decision was
taken; the §15 status snapshot tells you where the project is; the
§17 roadmap tells you where it's going. If your question isn't
answered there, open a discussion / issue before writing code.
