# Migration policy

How DB schema changes ship.

## The contract

For every model change in `engine/models.py`, ship an Alembic
migration. The CI `migration-parity` job enforces this — it runs
`alembic upgrade head` against a fresh DB, then runs
`Base.metadata.create_all()` against ANOTHER fresh DB, and
diffs the two schemas. If they disagree, the merge fails.

That means:

- A new `Column`, `Index`, `ForeignKey`, or `Table` MUST come
  with a matching migration.
- A renamed column / table needs an explicit migration step.
- A backfill (e.g. populating a new NOT NULL column on existing
  rows) goes in the migration's `upgrade()`.

## Generating a migration

```bash
make alembic-revision MSG="add foo column to playlist"
```

Edits the auto-generated migration in `migrations/versions/`
to add backfill logic + assertions before running:

```bash
make migrate              # forward to head
make alembic-current      # print current head
```

## Idempotency convention

Every migration's `upgrade()` should be safe to re-run after a
crash. Use `op.add_column` (raises if exists — that's fine, it
signals a partial prior run). DON'T use raw `op.execute("ALTER
TABLE ...")` unless the SQL is itself idempotent (`IF NOT
EXISTS` etc).

## Downgrade policy

Every `upgrade()` MUST have a matching `downgrade()` that
reverses the change. Even one-way migrations (e.g. dropping a
column whose data is regeneratable) get a `downgrade()` that
re-creates the column with default values.

The post-§16.4 migration is the canonical example: dropping
`playlist_trace` is one-way, but `downgrade()` rebuilds it from
`MAX(version_no)` so the table re-exists even if version
history beyond v1 is lost.

## Reading the alembic history

```bash
.venv/bin/alembic history --verbose
.venv/bin/alembic show <revision_id>
```

## Read more

`migrations/env.py` for the live Engine wiring. DESIGN.md §17 +
the `migration-parity` review notes for the historical evolution
of the gate.

[DESIGN.md →](https://github.com/raby/station-studio/blob/main/DESIGN.md)
