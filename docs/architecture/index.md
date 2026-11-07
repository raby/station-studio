# Architecture

Notes for contributors. [Design](design.md) is the whole picture:
the three apps, how they connect, and the rules they keep. Each page
after it covers one load-bearing part of Station Studio in more depth,
with a link to the sections of the [design log][log] where it was
worked out.

If you're trying to **land your first PR**, read these in
order — they unblock most "why is it like this?" questions.

<div class="grid cards" markdown>

- :material-sitemap: **[Design](design.md)** —
  the three apps, how they connect, and the rules they keep.

- :material-cog-transfer: **[Engine pipeline](engine-pipeline.md)** —
  how `generate()` turns a template + week into a trace.

- :material-history: **[Trace versioning](trace-versioning.md)** —
  the `Playlist` + `PlaylistVersion` model + the `trace_id`
  denormalised pointer.

- :material-swap-horizontal: **[Just-this-week changes](week-changes.md)** —
  one song of a week changed for that week only, kept when it's
  generated again.

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
    The [design log][log] is the project's running record up to
    November 2026, about 16,000 lines: every slice's design, its
    decisions and the alternatives turned down, and every review.
    Section numbers in code comments ("DESIGN.md §16.2.6") point
    into it.

[log]: https://github.com/raby/station-studio/blob/main/docs/history/design-log.md

