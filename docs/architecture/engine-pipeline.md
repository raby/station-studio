# Engine pipeline

How `engine.generate.generate(...)` turns a station template + a
week number into a list of `TraceSlot`s.

## The pipeline, end to end

```text
Template + week
  │
  ▼
Cursor advance           ──  engine.cursor.advance_cursors()
  │                          (base_advance_per_week × (week-1),
  │                           wrapped at cycle_weeks, with
  │                           §14.30 persistent cursors honoured)
  ▼
Layout walk              ──  engine.layout.iter_slots()
  │                          (row_template × unit_labels, OR
  │                           flat_slots; each slot has a strategy
  │                           + source pointer)
  ▼
Per-slot sampling        ──  engine.sampling.<strategy>(slot, ctx)
  │                          (pick / window / weighted / mix /
  │                           sequential / jingle / archive_ref /
  │                           hook — each module owns its
  │                           candidate-selection + exclusion
  │                           bookkeeping)
  ▼
Dedup + cross-pool       ──  enforced inside strategies via the
  │                          cross_pool_dedup_sources rule + the
  │                          per-slot excluded list
  ▼
TraceSlot[]              ──  one per slot; carries the pick + the
                             rejected candidates with reasons
```

The walker is a pure function: same template + same source pools +
same week + same seed = same trace, byte-for-byte. That property
is the foundation for [trace versioning][trace-vers] and the
byte-equality test harness in `tests/test_byte_equality.py`.

## Where the code lives

- `engine/generate.py` — orchestration
- `engine/cursor.py` — cursor math + persistence
- `engine/layout.py` — row_template + flat_slots iteration
- `engine/sampling/` — one module per strategy
- `engine/persistence.py` — `save_playlist()` writes the trace +
  versioned PlaylistEntry rows

## Read more

Full design notes for each strategy + the cursor model live in
[DESIGN.md §14.22–§14.42][design] (the Phase B port section).
The pipeline behaviour is also exercised end-to-end by the
byte-equality harness against real Rebirth Radio XMLs.

[trace-vers]: trace-versioning.md
[design]: https://github.com/raby/station-studio/blob/main/DESIGN.md
