# Template grammar

A station template is a single JSON document. The engine reads it
with `engine.template.parse_template()`; the wizard reads + writes
it via `POST/PUT /api/stations`; the visual editor renders it at
`/stations/{slug}/visualise`. Same shape across all three surfaces.

## Top-level shape

```json
{
  "station": "starter",
  "name": "Starter Station",
  "cadence": "weekly",
  "rng": "off",
  "sources": [ ... ],
  "cursor": { ... },
  "layout": { ... },
  "rules": { ... }
}
```

| Field      | Required | Type                         | Notes |
|---         |---       |---                           |---|
| `station`  | yes      | string (slug)                | URL slug; lowercase, digits, dashes. Embedded in iTunes Persistent IDs + disk paths — **immutable after creation**. |
| `name`     | no       | string                       | Display name. Wizard-visible only; ignored by the engine. |
| `cadence`  | yes      | string                       | `"weekly"` today. Other cadences exist as forward stubs but only weekly is engine-supported. |
| `rng`      | yes      | string                       | `"off"` (deterministic) or `"on"` (seeded random). Determines whether strategies that *can* be random actually are. |
| `sources`  | yes      | array of [SourceSpec](#sourcespec) | Pools the station can draw from. At least one with role `content` is required for layout blocks to reference. |
| `cursor`   | yes      | [CursorSpec](#cursorspec)    | Per-station base advance + cycle length, plus per-named-cursor persist flag. |
| `layout`   | yes      | [LayoutSpec](#layoutspec)    | How slots are produced: either `row_template` (one row per unit) or `flat_slots` (explicit slot list). |
| `rules`    | no       | object                       | Operator-side overrides for engine rules (cross-pool dedup, mix exclusion window, etc). See [stations/README.md](https://github.com/raby/station-studio/blob/main/stations/README.md). |

## SourceSpec

```json
{ "name": "RG_RADIO", "colour": "#117733", "role": "content" }
```

| Field    | Required | Constraints |
|---       |---       |---|
| `name`   | yes      | Pool name; matches the `SourcePlaylist.name` you imported. Unique within `sources[]`. |
| `colour` | yes      | CSS hex (`#RRGGBB`). Drives the chip palette in the trace viewer + visual editor. |
| `role`   | yes      | `"content"` (placeable by layout blocks) or `"reference"` (only usable by `archive_ref` / `prev_ref` / `cross_pool_dedup_sources`). |

## CursorSpec

```json
{
  "base_advance_per_week": 60,
  "cycle_weeks": 16,
  "persist": true,
  "cursors_init": { "MORNING_1": 0, "EVENING_1": 1500 }
}
```

| Field                    | Required | Constraints |
|---                       |---       |---|
| `base_advance_per_week`  | yes      | Integer ≥ 0. The amount each persistent cursor advances per generated week (unless overridden by `cursor_advance_step` rule). |
| `cycle_weeks`            | yes      | Integer ≥ 0. The wrap period; `0` means "no wrap, position grows until pool underflow" (Weekly-style). |
| `persist`                | no       | Bool, default `false`. Opt-in to **stateful** cursors — when `true`, named cursors (those referenced by `sequential` blocks via `cursors_init`) keep their position across weeks in the `cursor_state` table. Stateless stations regenerate from position 0 every week. |
| `cursors_init`           | no       | Map of cursor-id → initial 0-indexed position. Required for `sequential` blocks; their `cursor_id` field must reference a key here. |

## LayoutSpec

Two flavours; pick one. Both produce the same slot output.

### `row_template` (programmatic)

```json
{
  "unit": "day",
  "unit_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  "row_start_offsets": [0, 24, 48, 72, 96, 120, 144],
  "row_template": [
    { "source": "STARTER_CONTENT", "strategy": "weighted", "size": 23 },
    { "source": "STARTER_JINGLES", "strategy": "jingle", "size": 1, "genre": "jingle" }
  ]
}
```

The engine generates one *row* per entry in `unit_labels`,
expanding `row_template` end-to-end per row. `row_start_offsets`
controls each row's starting slot index — useful when units have
different lengths (e.g. days that start at different hours).

| Field                | Required | Notes |
|---                   |---       |---|
| `unit`               | yes      | Free-text label for what a row represents (`"day"`, `"hour"`, `"slot"`). Display-only. |
| `unit_labels`        | yes      | Array of row-display names. **Length determines row count.** |
| `row_start_offsets`  | yes      | Array of starting slot indices, one per `unit_label`. Must have the same length. |
| `row_template`       | yes      | Array of [BlockSpec](#blockspec) instances. The engine walks this list per row, advancing through each block's `size`. |

### `flat_slots` (explicit)

```json
{
  "unit": "slot",
  "unit_labels": [],
  "row_start_offsets": [],
  "cursors_init": { "DEDICATED_1": 0 },
  "flat_slots": [
    { "source": "STARTER_JINGLES", "strategy": "jingle", "size": 1, "genre": "jingle" },
    { "source": "STARTER_CONTENT", "strategy": "weighted", "size": 1 }
  ]
}
```

Same `BlockSpec` per entry, but the array is the *entire* slot
list — no per-row expansion. Use this when slots don't fit a
neat row-pattern (Rebirth Radio's 2,196 slots use `flat_slots`).

## BlockSpec

```json
{
  "source": "STARTER_CONTENT",
  "strategy": "weighted",
  "size": 23,
  "exclusion_window": 5,
  "allowlist_sources": ["RG_MASTER"]
}
```

Required for **every** block:

| Field      | Type   | Notes |
|---         |---     |---|
| `source`   | string | Pool name. Must exist in `sources[]`. Role-check: `archive_ref` requires `role: reference`; every other strategy requires `role: content`. |
| `strategy` | string | One of the [8 supported strategies](strategies.md). |
| `size`     | int    | Slot count this block produces. Block `{strategy: weighted, size: 23}` produces 23 picks in a row. |

Per-strategy optional / required fields are documented per
strategy in [Strategies](strategies.md).

## Validation contract

`parse_template()` validates fail-loud on parse — the wizard's
`POST /api/stations` 400 response carries the structured
`{detail, errors: [{field, message}]}` envelope so per-field
`aria-describedby` association works on the wizard form.

Wizard-tagged fields (errors map to a specific input):

- `rng`
- `sources` (e.g. duplicate names)
- `cursor.base_advance_per_week`
- `cursor.cycle_weeks`

Deep cross-field errors (e.g. layout mismatches, unknown
strategies, block→source role mismatches) stay in the
catch-all error banner — they don't map 1:1 to a wizard
input. See `engine/template.py` for the full raise-site list.
