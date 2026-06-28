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

Tier-fallback weighted pick from the full pool. Probability is
**not** a per-track weight in the database; the sampler
computes weights at sampling time from `remaining²` (remaining =
artist's pool count − their cycle-used count) so under-represented
artists are favoured. Three tiers: artist-dedup → relaxed-dedup
→ unconstrained — each tier widens the candidate set if the
previous tier produced no eligible picks.

`window: N` controls the **artist-dedup sliding window** — the
last `N` picks whose artist must not match this pick's artist.
Default is `10`. Smaller = looser dedup; `0` disables the
artist constraint entirely.

```json
{
  "source": "STARTER_CONTENT", "strategy": "weighted", "size": 23,
  "window": 8
}
```

**When to use.** The default content strategy. Most stations'
biggest blocks are `weighted`. Tune `window` per-block when a
mix of long + short blocks share a pool: long blocks tolerate a
bigger artist-dedup window, short interstitials may need a
smaller one.

## `window`

Deterministic consecutive read: returns `size` tracks in pool
order starting at the block's cursor position. No RNG, no
candidate selection — `size: 8` returns exactly the 8
consecutive pool entries from the cursor onwards.

```json
{ "source": "STARTER_CONTENT", "strategy": "window", "size": 8 }
```

**When to use.** Block-sized chunks of a curated pool that
should preserve original ordering (e.g. an album side, a
sequenced segment). Differs from `sequential` in that `window`
uses the per-row positional offset, not a named cursor.

## `sequential`

Walk a **named** cursor through the source pool. Three advance
steps to keep straight:

* **Within a block** — consecutive picks read consecutive
  positions (within-block step = 1).
* **Across blocks within one generation** — the cursor advances
  by `block.size` so the next block reading the same cursor
  picks up immediately after this block.
* **Across weeks** — when `cursor.persist: true`, named cursors
  resume from their end-of-week position in the `cursor_state`
  table and advance by `cursor_advance_step` (a per-station
  rule, default `120`); when `persist: false` the cursor is
  recomputed each week as
  `cursors_init[cursor_id] + week × base_advance_per_week`.

The cursor's starting position for week 1 is
`layout.cursors_init[cursor_id]`.

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

Pick from a single-genre subset of the source pool. The genre
filter runs **per pick** against the full pool (not at
pool-load time — the pool itself is shared whole with any
other block reading the same source). Most stations declare a
separate `_JINGLES` pool to keep the genre filter cheap; you
can also filter a content pool by genre.

```json
{
  "source": "STARTER_JINGLES", "strategy": "jingle",
  "size": 1, "genre": "jingle"
}
```

**Required.** `genre` — non-empty string. Pool tracks whose
genre doesn't match are excluded from candidates for this
block, but remain visible to other blocks reading the same
pool.

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
cross-week reference — does NOT advance any cursor.
**Source role: `content`** (the source is the same pool the
prior week's slot was placed into; the *playlist* being
referenced is cross-week, not the pool).

If the mirror attempt fails (no prior week, or the position
wasn't in the prior playlist), the generator falls back via
the **`mix` strategy** when `fallback_genre` is provided —
which is why `fallback_genre` exists as an option — and via
**`weighted`** on the same source when no fallback is
specified. There is no "empty slot" outcome; every position
gets a track one way or another.

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
- `engine/strategies/` — per-strategy generator implementations
  (one module per strategy: `weighted.py`, `window.py`, etc.)
- `engine/generator.py` — block dispatch, cursor advance,
  prev_ref fallback routing into mix / weighted
- `stations/README.md` — pattern recipes + worked examples
  (deeper jingle composition, archive_ref position tables,
  cross-pool dedup, etc.)
