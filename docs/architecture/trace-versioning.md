# Trace versioning

How Station Studio keeps every regeneration's history without
deleting the prior trace.

## The model

```text
station ──< playlist ──< playlist_version
                  │             │
                  │             ├── trace_id (UNIQUE)
                  │             ├── version_no (UNIQUE per playlist)
                  │             └── slots JSON
                  │
                  └── trace_id (denormalised pointer to the
                                CURRENT version's trace_id;
                                flips on every regen)
```

A `Playlist` row is a `(station, week)` pair — externally
addressable via `/api/playlists/{id}` and stable across regens.
The actual trace data lives in `PlaylistVersion`, and the
`Playlist.trace_id` column is a *denormalised pointer* to the
latest version's trace_id so callers that don't care about
history don't pay a join.

## What changes on regen

`save_playlist()` is **non-destructive**:

1. Reuse the same `Playlist` row (same PK — external pointers
   stay valid).
2. Clear + rewrite `PlaylistEntry` rows (latest-only; the trace
   slots JSON has the historical detail).
3. Clear + rewrite per-station `MixUsage` bookkeeping.
4. **Append** a new `PlaylistVersion(version_no = max + 1)`.
5. Flip `Playlist.trace_id` to point at the new version's id.

The trace viewer's `?v=N` URL parameter selects any historical
version; the picker dropdown lists every version oldest-first.

## Why entries are latest-only

`PlaylistEntry` mirrors the **current playable order** — that's
what the iTunes XML export reads. Historical versions are
reconstructable from `PlaylistVersion.slots` JSON; they don't
need a parallel entries table.

## Read more

DESIGN.md §16.4 (the SHIPPED block at the top of the section)
documents the v1 decisions + the migration shape. §16.4.t5
(version tags) and §16.4.t6 (versioned disk filenames) are
the post-v1 follow-ons.

[DESIGN.md →](https://github.com/raby/station-studio/blob/main/DESIGN.md)
