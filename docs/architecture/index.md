# Architecture

# Architecture

Curated notes for contributors. Each page covers one
load-bearing design decision in 100-300 words + a cross-link
back to the canonical [DESIGN.md][design] archive (which has
the full history + the punted alternatives + every review-fix
rationale).

If you're trying to **land your first PR**, read these in
order — they unblock most "why is it like this?" questions.

<div class="grid cards" markdown>

- :material-cog-transfer: **[Engine pipeline](engine-pipeline.md)** —
  how `generate()` turns a template + week into a trace.

- :material-history: **[Trace versioning](trace-versioning.md)** —
  the `Playlist` + `PlaylistVersion` model + the `trace_id`
  denormalised pointer.

- :material-database-edit: **[Sidecar + DB-as-truth](sidecar-db-as-truth.md)** —
  the visual editor's JSON column + its grammar drift detector.

- :material-eye-refresh: **[Live preview](live-preview.md)** —
  the job-id transport that runs preview generation
  off-thread without writing to the DB.

- :material-test-tube: **[Test pyramid](test-pyramid.md)** —
  pytest + vitest + Playwright + axe-core, and when to add
  what.

- :material-database-arrow-up: **[Migration policy](migration-policy.md)** —
  Alembic migrations, the CI parity gate, idempotency
  convention, downgrade requirements.

</div>

!!! tip "Not finding what you need?"
    These pages are a curated entry point, not a wholesale
    republish. `DESIGN.md` at the repo root has the full
    archive across ~15,000 lines — every architectural
    decision since the §13 design pass, every review-fix
    rationale, every `.tX` punt with its promotion criteria.
    The pages above link to the relevant section.

[design]: https://github.com/raby/station-studio/blob/main/DESIGN.md

