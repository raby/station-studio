# Strategies

Eight sampling strategies. Every layout block declares exactly
one. The engine routes by strategy name → per-strategy
generator → per-pick result with a recorded reason.

| Strategy        | One-line shape                                                  | Required fields   | Optional fields                        |
|---              |---                                                              |---                |---                                     |
| [`pick`](#pick) | Random pick from the source pool, RNG-controlled                | —                 | `allowlist_sources`                    |
| [`weighted`](#weighted) | Weighted random pick (track weights from pool metadata) | —                 | `window`, `allowlist_sources`           |
| [`window`](#window) | Pick within a sliding window of the source pool              | —                 | `allowlist_sources`                    |
| [`sequential`](#sequential) | Walk a named cursor through the source pool          | `cursor_id`       | `allowlist_sources`                    |
| [`jingle`](#jingle) | Pick from a single-genre subset of the source pool           | `genre`           | —                                      |
| [`mix`](#mix)   | Pick from a genre subset with exclusion-window dedup            | `genre`           | `exclusion_window`                     |
| [`prev_ref`](#prev_ref) | Read the previous week's slot at a fixed position       | `position`        | `fallback_genre`                       |
| [`archive_ref`](#archive_ref) | Read a fixed position from a reference pool       | `position`        | —                                      |

A block always also needs `source`, `strategy`, and `size`. See
[Template grammar → BlockSpec](template-grammar.md#blockspec).

---

## `pick`

Random pick from the source pool. If `rng: "off"`, uses the
station seed; if `rng: "on"`, uses per-run randomness.

```json
{ "source": "STARTER_CONTENT", "strategy": "pick", "size": 1 }
```

**When to use.** Simplest possible block. Useful as a smoke
test or for very small pools where weighting / windowing adds
no signal.

## `weighted`

Weighted random pick. Each track's pick probability is
proportional to its weight (read from the iTunes XML
`Play Count` or a per-pool override). With `window: N`, the
sampler picks from the next `N`-track window starting at the
cursor — turning weighted into "weighted within a sliding
window" (useful for limiting how far from the cursor the
engine can reach).

```json
{
  "source": "STARTER_CONTENT", "strategy": "weighted", "size": 23,
  "window": 50
}
```

**When to use.** The default content strategy. Most stations'
biggest blocks are `weighted`.

## `window`

Pick within a sliding window starting at the cursor. The window
size is implicit from `size` — `size: 8` reads 8 candidates from
the cursor onwards. Pure positional, no RNG.

```json
{ "source": "STARTER_CONTENT", "strategy": "window", "size": 8 }
```

**When to use.** Programmatic blocks where you want
deterministic forward walks. Rarely used compared to
`sequential`.

## `sequential`

Walk a **named** cursor through the source pool. Each pick
advances the cursor by 1; the cursor's starting position comes
from `layout.cursors_init[cursor_id]`. If
`cursor.persist: true`, the cursor's end-of-week position is
saved in the `cursor_state` table and resumed next week — the
basis for "weeks chain" semantics.

```json
{
  "source": "STATEFUL_CONTENT", "strategy": "sequential",
  "size": 1, "cursor_id": "DEDICATED_1"
}
```

**Required.** `cursor_id` must reference a key in
`layout.cursors_init`.

**When to use.** Stations with cross-week continuity (the
Stateful Starter is built around this). Pairs with
`cursor.persist: true` to chain weeks.

## `jingle`

Pick from a single-genre subset of the source pool. Most
stations declare a separate `_JINGLES` pool to keep the genre
filter cheap; you can also filter a content pool by genre.

```json
{
  "source": "STARTER_JINGLES", "strategy": "jingle",
  "size": 1, "genre": "jingle"
}
```

**Required.** `genre` — non-empty string. Tracks without the
matching genre are skipped at pool-load time.

**When to use.** Jingles, station idents, sweepers,
weather/news drops — any short content with a stable cadence.
Often combined with `row_template` patterns like "23 weighted
+ 1 jingle = 24-slot day".

## `mix`

Pick from a genre subset with exclusion-window dedup. Same
genre filter as `jingle`, but also dedupes against the last N
*generated* slots (`exclusion_window: N` — falls back to the
per-station `mix_exclusion_weeks` rule default if omitted).

```json
{
  "source": "RG_RADIO", "strategy": "mix",
  "size": 1, "genre": "ambient", "exclusion_window": 5
}
```

**Required.** `genre`. **Optional.** `exclusion_window` — see
the rule default in `stations/README.md`.

**When to use.** Mix blocks where the same track from the
last N weeks should not repeat. Common in long-running
genre-themed stations.

## `prev_ref`

Read the previous week's slot at a fixed position. Pure
cross-week reference — does NOT advance any cursor. If the
prior week is missing (e.g. first generated week), falls back
to picking from `fallback_genre` if provided; otherwise the
slot is empty.

```json
{
  "source": "STATEFUL_CONTENT", "strategy": "prev_ref",
  "size": 1, "position": 24, "fallback_genre": "instrumental"
}
```

**Required.** `position` — 1-indexed slot index in the prior
week's playlist.

**When to use.** "Same track every week at this time" without
hand-pinning the pool position. The Stateful Starter uses it
every 12 slots to demonstrate cross-week continuity beyond
cursor-walk.

## `archive_ref`

Read a fixed position from a **reference** pool (role:
reference). The reference pool is the curated source-of-truth
order; archive_ref's `position` is 1-indexed into that order.
Pure positional, no RNG, no cursor.

```json
{
  "source": "RG_MASTER", "strategy": "archive_ref",
  "size": 1, "position": 47
}
```

**Required.** `position` (1-indexed); `source.role: reference`.

**When to use.** Stations with a fixed-order archive (e.g.
Rebirth Radio's `RG_MASTER` pool) that gets sampled at
predictable positions per slot. Differs from `prev_ref` in
that the source is *static* (the reference pool), not the
previous week's generated playlist.

---

## Strategy + `allowlist_sources`

Content-sampling strategies (`pick`, `weighted`, `window`,
`sequential`) accept an optional `allowlist_sources: [name,
...]` array of **reference**-role pool names. When set, the
sampler restricts candidates to those whose `Persistent ID` also
appears in at least one allowlist pool — a positive intersection
filter useful for "only pick tracks that survived this curated
review pass" workflows.

Forbidden for non-sampling strategies (`jingle`, `mix`,
`prev_ref`, `archive_ref`) — those either pick from a fixed
pool with no candidate selection, or sample from a reference
pool already.

## Where each strategy is documented in code

- `engine/template.py` — `_require_strategy_field` calls + the
  `SUPPORTED_STRATEGIES` set
- `engine/sampling/` — per-strategy generator implementations
- `stations/README.md` — pattern recipes + worked examples
  (deeper jingle composition, archive_ref position tables,
  cross-pool dedup, etc.)
