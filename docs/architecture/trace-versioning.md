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
2. Clear existing `PlaylistEntry` rows + `MixUsage` rows.
3. **Append** a new `PlaylistVersion(version_no = max + 1)`.
4. Write the new `PlaylistEntry` rows (latest playable order)
   + new `MixUsage` rows (bookkeeping for §14.25 mix exclusion);
   both reference the same Playlist PK and run after the
   version append.
5. Flip `Playlist.trace_id` to point at the new version's id.

The trace viewer's `?v=N` URL parameter selects any historical
version; the picker dropdown lists every version oldest-first.

## Why entries are latest-only

`PlaylistEntry` mirrors the **current playable order** — that's
what the iTunes XML export reads. Historical versions are
reconstructable from `PlaylistVersion.slots` JSON; they don't
need a parallel entries table.

## Storage

`PlaylistVersion.slots` is stored compressed
(`engine.compressed_json`: a JSON string `"z1:<base64 zlib JSON>"`):
a week's slots are mostly the candidates each rule passed over, so a
Rebirth Radio version is about 16.7 MB as JSON and 0.5 MB stored. Rows
written before stay plain JSON and read the same; the migration that
introduced it unpacks every row on downgrade. The column is deferred:
listing a week's versions or finding its current one doesn't load the
songs of every version.

Versions that aren't generations (a rollback, a
[just-this-week change](week-changes.md)) carry a `note` saying what
made them.

## Read more

DESIGN.md §16.4 (the SHIPPED block at the top of the section)
documents the v1 decisions + the migration shape. §16.4.t5
(version tags) and §16.4.t6 (versioned disk filenames) are
the post-v1 follow-ons.

[DESIGN.md →](https://github.com/raby/station-studio/blob/main/DESIGN.md)
