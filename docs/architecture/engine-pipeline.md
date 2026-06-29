# Engine pipeline

How `engine.generator.generate(...)` turns a station template + a
week number into a list of `TraceSlot`s.

## The pipeline, end to end

```text
Template + week
  │
  ▼
Cursor advance           ──  engine.persistence.advance_cursors()
  │                          (base_advance_per_week × (week-1),
  │                           wrapped at cycle_weeks, with
  │                           §14.30 persistent cursors honoured)
  ▼
Layout walk              ──  engine.generator._iterate_slots()
  │                          (row_template × unit_labels, OR
  │                           flat_slots; each slot has a strategy
  │                           + source pointer)
  ▼
Per-slot sampling        ──  engine.strategies.<strategy>(slot, ctx)
  │                          for window / pick / jingle / mix /
  │                          weighted / prev_ref / archive_ref;
  │                          `sequential` is dispatched inline in
  │                          generator.py (no dedicated module —
  │                          it's the cursor-driven sibling of
  │                          window/pick)
  ▼
Dedup + cross-pool       ──  enforced inside strategies via the
  │                          cross_pool_dedup_sources rule + the
  │                          per-slot `excluded` list (each slot
  │                          carries WHY each rejected candidate
  │                          was rejected — §14.23)
  ▼
TraceSlot[]              ──  one per slot; carries the pick + the
                             rejected candidates with reasons
```

The walker is a pure function: same template + same source pools +
same week + same seed = same trace, byte-for-byte. That property
is the foundation for [trace versioning][trace-vers] and the
byte-equality test harness (see `tests/byte_equality.py` + the
test modules: `test_byte_equality_harness.py`,
`test_byte_equality_synthetic.py`, `test_classics_byte_equality.py`,
`test_port_byte_equality.py`).

## Where the code lives

- `engine/generator.py` — orchestration (`generate()` + the
  inline layout walk + the strategy dispatcher)
- `engine/persistence.py` — `advance_cursors()` + `save_playlist()`
  (cursor math + non-destructive regen + MixUsage rewrites)
- `engine/strategies/` — one module per non-trivial strategy
  (`pick`, `window`, `weighted`, `mix`, `jingle`, `archive_ref`,
  `prev_ref` — 7 modules; `sequential` dispatches inline)
- `engine/template.py` — `SUPPORTED_STRATEGIES` + `parse_template`
  + the strategy-aware role cross-check

## Read more

Full design notes for each strategy + the cursor model live in
[DESIGN.md §14.22–§14.42][design] (the Phase B port section).
The pipeline behaviour is also exercised end-to-end by the
byte-equality test modules listed above, which pin generated
output against committed PID-sequence JSON snapshots under
`tests/fixtures/expected/`.

[trace-vers]: trace-versioning.md
[design]: https://github.com/raby/station-studio/blob/main/DESIGN.md
