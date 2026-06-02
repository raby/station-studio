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

## Plan audits

The slice pattern is great for shipping but easy to drop "we
should also..." items between commits. Punts in §14.x entries,
"later" rows in §11, and `Trigger to pick up` columns in punt
tables accumulate; without periodic surfacing they decay into
"things only the original author remembers."

**Plan audits prevent that.** A plan audit is a 30-60 minute
sweep: read DESIGN.md looking for queued / deferred / punted items
not in the §16 plan order, then decide for each one:

- **Elevate** — give it a §16.x number + a brief in the plan. The
  brief is short (effort estimate + trigger + scope), not a spec.
- **Confirm tail** — explicitly note it stays deferred + WHY (no
  demand, waits for a felt pain, etc.). The note prevents the
  question coming up again next audit.
- **Drop** — if the item has become irrelevant (e.g. shipped under
  a different name, or the original problem evaporated), strike
  it from §11 / the punt table with a one-line note.

After an audit, the master queued-order table at the top of §16
should reflect everything tracked. No item should live ONLY in a
§14.x punt table — they should also surface in §16's index.

### When to do one

Triggers (any one is enough):

- **Phase boundary** — Phase A → Phase B, Phase B → Phase C-or-
  whatever-comes-next. We just finished one at the §14.41 →
  §16.0 transition; it caught 9 items that hadn't propagated to
  the §16 plan.
- **Major focus narrowing** — before starting work that will
  occupy attention for ≥1 week. Tail items risk falling off when
  focus narrows; audit first so they're indexed before they
  disappear from working memory.
- **Every ~10 slices** — rough heuristic for "the queue has
  accumulated enough that small punts have started to drift."
- **When the queue feels chaotic** — trust the feeling. If the
  next-slice question takes longer than a minute to answer, that's
  a signal.

### How to do one

1. **Search DESIGN.md** for queued markers — `Deferred`, `Queued`,
   `Punted`, `LATER`, `TODO`, "wait for", "trigger to pick up".
   Plus the punt tables at the bottom of each §14.x entry.

2. **Build a working list** of items found but not in the §16
   master table. Group by category (operational / UI polish /
   testing / cross-cutting / tail).

3. **Triage each one** — elevate, confirm tail, or drop. For
   elevations, assign a §16.x number from above the existing
   range (don't reuse old numbers even if they're "shipped";
   §14.x entries reference them).

4. **Commit one slice**: DESIGN.md updates (new briefs + updated
   master table + status snapshot rows). Same shape as any other
   slice; commit message names "audit follow-up: N new slices."

5. **Verify** — re-run the search after the commit; nothing
   queued should be missing from the master table.

The §14.40 review's "DESIGN §16 audit follow-up" commit
(`3a18548`) is the worked example.

### What an audit is NOT

- Not a code review. Audits look at the PLAN, not the
  implementation. Code review happens slice-by-slice.
- Not a design overhaul. If the audit surfaces an item that
  needs a design discussion, the audit FILES that discussion as
  a §16.x entry to pick up later; it doesn't dive in.
- Not a sprint plan. Plan audits index the queue; they don't
  schedule it. Which slice comes next is a separate decision.

## Test discipline

| Layer | Tool | Gate |
|---|---|---|
| Backend (engine + API) | pytest | ≥90% line coverage (`make test-backend`) |
| Migrations | `make migration-parity` | upgrade-head must match `Base.metadata.create_all` |
| Frontend unit | vitest | per-file lines gate in `web/vite.config.js` |
| End-to-end | Playwright | must pass in CI |
| Byte-equality (Rebirth Radio) | `tests/test_port_byte_equality.py` | skip-when-XMLs-missing; **must stay green when XMLs present** |
| Byte-equality (Classics) | `tests/test_classics_byte_equality.py` | always runs (synthetic pools committed) |
| Install (wheel channel) | `make wheel-smoke` | **release-blocker** — installs the wheel into a temp venv, launches `station-studio serve`, curls `/`, `/api/stations`, `/sources` and asserts HTTP 200 + content-type. Catches bundled-asset force-include regressions + lifespan breakage that pytest can't see. |
| Install (docker channel) | `make image-smoke` | **release-blocker** — builds the docker image, runs a container, curls the same three URLs against it. Healthcheck reports `(healthy)` after lifespan completes. Run before tagging a release. |

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

**The fast path** (hobbyist building their first station): fork
`stations/rebirth-classics.json`, edit the slot vocabulary to match
what you want, drop it back in `stations/`, restart the app. The
auto-seed lifespan handler picks it up on next boot. Your station
shows up in the sidebar.

That's it for the minimum case. The fast path is the path. You DO
NOT need to write a byte-equality test, port through the weekly
translator, or know what `mix_exclusion_weeks` means. Most stations
won't.

### When you DO want more

Three signals you're past the fast path:

1. **You want to port from an existing weekly-format generator.**
   Use `scripts/dump_weekly_template.py` + `make import-station`
   to bring its template in. See `DESIGN.md` §14.34 for the
   translation table.

2. **You want regression-locked output across engine changes.**
   Add a "freeze-against-self" byte-equality test under
   `tests/test_<slug>_byte_equality.py`. The Classics test is
   the model. Only worth doing if you're maintaining the station
   long-term + care about engine changes not silently changing
   your output.

3. **You hit a primitive the engine doesn't have.** Propose it
   as a §14.x slice — engine strategy modules live under
   `engine/strategies/`; pattern: pure function, no DB, no
   randomness-source baked in. `DESIGN.md` §14.24-§14.28 walk
   through five real examples.

The grammar reference is `DESIGN.md` §14.32 (flat_slots form) and
§14.34 (weekly-format translator). You don't need to read both
before starting — read the one that matches what you're doing.

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
