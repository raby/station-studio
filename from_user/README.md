# `from_user/` — drop iTunes XML exports here

This directory mirrors the convention used in `weekly/from_user/`. It is the
canonical spot for user-supplied iTunes XML files that the engine should
import as source pools.

## Rebirth Classics expects three files

| File name       | Pool name    | Role      |
|-----------------|--------------|-----------|
| `RG_RADIO.xml`  | `RG_RADIO`   | content   |
| `RG_PARTY.xml`  | `RG_PARTY`   | content   |
| `RG_MASTER.xml` | `RG_MASTER`  | reference |

The file names here map 1:1 to the `sources` entries in
`stations/rebirth-classics.json`.

## One-command bootstrap

Once the three files are in place:

```bash
python -m scripts.bootstrap_classics
```

That script:

1. Creates the DB schema if missing
2. Seeds `rebirth-classics` into the `station` table (via `scripts.seed_stations`)
3. Imports each of the three XML files as a source pool
4. Generates week 1 and prints a summary + trace ID

It is idempotent: re-running updates any changed inputs and regenerates week 1.

## Partial imports

If you have just one or two of the three files, run the per-file command
instead:

```bash
python -m scripts.bootstrap_classics --skip-missing
```

This imports whatever is present, seeds the station, and skips generation if
content pools are missing.

## Replacing a pool later

Just overwrite the XML file and re-run `bootstrap_classics`. The importer is
idempotent — same `Persistent ID`s keep their `Track` rows; new ones are
added; entries are replaced.

## Not checked in

This directory is gitignored — source XMLs can be large and are not repo
content. This README is the only file tracked.
