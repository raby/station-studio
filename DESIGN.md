# Station Studio — Design Document

**Status:** draft v1, authored during scaffolding conversation on 2026-04-22
**Sister to:** `rebirth/weekly` (Rebirth Radio generator)
**First station:** Rebirth Classics

This document captures the full planning conversation, the assumptions made, the design decisions taken, the rationale behind each, and the open items deferred to later phases. It is the canonical record of *why* the system looks the way it does.

---

## 1. Origin and motivation

The existing `weekly/` project is a bespoke playlist generator for **Rebirth Radio** — a 2,196-slot weekly playlist with a hardcoded template interleaving commercial music, night/weekend sequential blocks, genre-tagged mixes, jingles, and previous-week references. It is a single-station system: its template, rules, and source-pool assumptions are baked in.

The user now needs a second station, **Rebirth Classics** (internally discussed as "peak gold" earlier in the conversation; naming settled mid-conversation). Rather than fork `weekly/` and embed a second hardcoded template, the user chose to **build a general-purpose station-authoring tool** so that this station, and any future ones, can be defined by end-users via a UI without code changes.

Station Studio is that tool.

### Lexicon (settled)

| Internal name       | Station                  | Role                          |
|---------------------|--------------------------|-------------------------------|
| weekly / "weekly"   | Rebirth Radio            | Existing hardcoded generator  |
| peak gold / classics| Rebirth Classics         | First Station Studio station  |

---

## 2. Scope

### In scope (v1)

- Generalised generator engine decoupled from any single station
- Template JSON as the canonical artifact
- Importer for iTunes XML source playlists (reused from weekly)
- FastAPI backend exposing stations CRUD, source imports, and generation
- Trace emission per generated slot, with a persisted `trace_id`
- SvelteKit web wizard (thin, reads/writes template JSON)
- Rebirth Classics authored and generating end-to-end
- Pytest coverage proving Classics generates correctly across week boundaries

### Out of scope (v1, explicitly deferred)

- Visual block editor (v2) — the wizard produces the template JSON; the editor comes later over the *same* JSON
- Weighted selection, prev-week reference, genre-tagged picks — not needed for Classics. Added to engine when the second station needs them
- Porting Rebirth Radio onto the new engine — explicit *migration target* but not attempted now
- Jingle-interleaver primitive — Classics bakes jingles into source files; engine never sees them (see §5.3)

### Explicit non-goals

- `weekly/` remains untouched. Station Studio is a parallel system.
- No UI-only state. The UI is replaceable; the JSON is the source of truth.

---

## 3. Conversation history (key turns)

The plan was worked out in a short planning conversation. Full back-and-forth summary:

### Turn 1 — User framing
User asked for a sister project: a web UI where users put in a "formula", answer questions to describe a schedule, the system "shows its working", and generates playlists. Motivating example: a new station (peak gold → Rebirth Classics) was about to be requested, and rather than commission yet another bespoke system, the user wanted self-serve station authoring.

### Turn 2 — Forks surfaced
Four decisions put to the user:
1. **What is a station?** Agreed: a station = a named template (slot layout) + source-pool bindings + rules.
2. **How is the template defined?** Agreed: wizard first, visual block editor later, over the same JSON.
3. **"Show your working"** — agreed as a per-slot trace showing why each track was chosen; excluded candidates nice-to-have.
4. **Relationship to weekly/** — agreed: parallel engine, with migration of weekly onto the new engine as the explicit long-term target. Weekly is not disturbed now.

### Turn 3 — Station-specific spec
User specified Rebirth Classics:
- Three source imports, all `RG*` files, user-replaceable like Rebirth Radio
- No RNG anywhere
- Cursor advances +60 per week, cycles 16 weeks
- 7 rows of 430 slots = 3,010 slots/week
- Each row = 215 `RG_RADIO` selections followed by 215 `RG_PARTY` selections
- Row start offsets: 1, 11, 21, 31, 41, 51, **51** (row 7 listed as same as row 6)
- Jingles originally described as separate pools, then revised — see §5.3
- Colour-code source pools in the UI

### Turn 4 — Clarifications
- **Third source file** confirmed as `RG_MASTER` (all tracks, canonical reference), not `RG_JINGLES`. User offered to create a jingles file "for consistency" but confirmed Classics has no jingles.
- **Jingles principle** stated by user: *"A jingle is a placeholder between random selections. So if no random elements involved, no jingles needed."* Locked in as an engine design principle (§5.3).
- **Row 7 start index** was *not* a typo. User confirmed deliberate duplication: rows 6 and 7 both use selections `51-265`. Rationale: without this trick, next week's row 1 (starting +60 at `61-275`) would be identical to this week's row 7 (`61-275`) — a cross-week-boundary duplicate. Deliberately duplicating Sat + Sun instead places the pair at the weekend, reading as "weekend keeps the same vibe" rather than "Sunday bled into Monday."
- **Pool size** — user-maintained; engine will raise clear errors rather than silently wrap when a window exceeds pool length.
- **UI stack** — SvelteKit chosen over htmx (rationale in §7).
- **Repo** — new repo `station-studio` alongside `weekly/`.
- **iTunes XML importer** — confirmed reusable from weekly (standard `plistlib` on `Persistent ID`).
- **Trace** — `trace_id` baked into saved playlists, not ephemeral.

### Turn 5 — Each row = a day
User revealed: **each row represents one day of the week, Mon–Sun.** This is not just a structural detail; it shapes the UI's language ("Saturday 18:42" beats "row 6 slot 184") and justifies the row-7-equals-row-6 trick (the duplicate days are the weekend, not an arbitrary pair).

User also noted: *"This may not be applied to other stations."* — i.e. the duplicate-last-row behaviour is Classics-specific. Engine must express it cleanly but not inherit it by default.

### Turn 6 — Scaffold authorised
User said **YES!** — proceed with scaffolding the repo as previously summarised.

---

## 4. Architectural decisions

### 4.1 Engine as pure library

The engine (`engine/`) is a pure Python library with no FastAPI dependency and no UI dependency. It accepts a template + source pools (as plain Python dicts of track lists) + a week number, and returns a playlist plus a trace.

**Why:** testable without a database or HTTP layer; mockable in pytest; portable if ever wrapped in a CLI, desktop app, or scheduled job.

### 4.2 Template JSON is canonical

The template is a JSON document. The wizard writes it. The block editor (v2) edits the same JSON. The API accepts and returns the same JSON. The engine consumes it. The checked-in `stations/*.json` files are human-authored and live in version control.

**Why:** Single source of truth. UI is always replaceable because it has no hidden state. Templates are diffable, reviewable, and committable.

### 4.3 Small primitive vocabulary

The engine understands a bounded set of slot-filling strategies:

| Primitive      | Classics? | Notes                                                           |
|----------------|-----------|-----------------------------------------------------------------|
| `pick`         | no        | Take next item from a source (sequential or random)             |
| `window`       | **yes**   | Take N consecutive items starting at a pool index               |
| `sequential`   | later     | Cursor-driven sequential block of N from a source               |
| `weighted`     | later     | Squared-remaining weighted pick (Rebirth Radio commercial slot) |
| `prev_ref`     | later     | Reference slot N of previous week                               |
| `genre_pick`   | later     | Pick from a genre-tagged sub-pool                               |

Classics requires only `window`.

**Why:** Keep the engine core minimal. New stations that can be expressed with existing primitives require zero engine changes. New primitives only land when a real station demands one.

### 4.4 Prefer source-file shaping over engine primitives

Stated principle (quoted from user): *"Jingles are placeholders between random selections."* Generalised: **if a station's structure can be expressed by shaping its source files, prefer that over adding an engine primitive.**

Concrete consequence for Classics: jingles are baked into `RG_RADIO` and `RG_PARTY` by the user at source-file preparation time; the engine neither knows nor cares. No `jingle` primitive.

### 4.5 Determinism by default

`rng: "off"` in the template means the playlist is a pure function of `(template, source pools, week number)`. Same inputs → same outputs, byte-for-byte. Classics runs in this mode.

`rng: "on"` (future) will require a seed — like weekly's existing `random.Random(seed)` pattern — and the trace will record the seed.

**Why:** Reproducibility for debugging. Trust in the "show your working" view. Ability to regenerate a specific week's playlist years later and get the same result.

### 4.6 Trace is first-class

Every generation emits a trace:
- **Per-slot entries** recording: slot index, day label, row index, source pool, strategy used, pool index consulted, track chosen (persistent ID + display fields), and rationale.
- **Optional excluded-candidates sub-list** per slot (nice-to-have, not required for Classics since there are no exclusions).
- A **`trace_id`** (UUID) persisted on the generated playlist row.

Given a `trace_id` the full trace can be retrieved later for auditing ("why did slot 142 pick that track in week 7?").

**Why:** The user explicitly asked for this as a core feature ("show your working"). It also underpins the UI wizard's live-preview feature.

### 4.7 Parallel to weekly, eventual convergence

Weekly stays untouched. Station Studio is built independently. Once the generalised engine supports the primitives weekly needs (`weighted`, `prev_ref`, `genre_pick`), weekly's hardcoded template is re-expressed as `stations/rebirth-radio.json` and weekly's generator is retired in favour of Station Studio's engine.

**Why:** De-risks delivery of Classics. Avoids destabilising a live production system mid-refactor. Makes the migration a *discrete future project* rather than a blocker.

---

## 5. Rebirth Classics — full spec

### 5.1 Source pools

| Pool        | Role       | Colour    | Purpose                                           |
|-------------|------------|-----------|---------------------------------------------------|
| `RG_RADIO`  | content    | `#c0392b` | First half of each day's 430-slot row (215 slots) |
| `RG_PARTY`  | content    | `#8e44ad` | Second half of each day's row (215 slots)         |
| `RG_MASTER` | reference  | `#7f8c8d` | Canonical track list; imported but not referenced by layout |

All three are standard iTunes XML files in the user's `from_user/` directory (or equivalent), user-replaceable at any time. The importer is idempotent — re-importing a source replaces its entries cleanly.

### 5.2 Weekly layout

- 7 rows × 430 slots = **3,010 slots per week**.
- Each row = one day of the week, labelled Mon through Sun.
- Each row is composed of two 215-slot windows: `RG_RADIO[start..start+214]` followed by `RG_PARTY[start..start+214]`.
- Row start offsets (from the week's base cursor): `[0, 10, 20, 30, 40, 50, 50]`.
- Row 7 deliberately duplicates row 6's start offset (both = 50). This places the pair of duplicate days at the weekend, preventing a cross-week-boundary duplicate when the base cursor advances +60.

### 5.3 Jingles

Not handled by the engine. The user bakes jingles into `RG_RADIO` and `RG_PARTY` at source preparation time (pre-determined, not random). The engine sees ordinary track slots.

Rationale (quoted): *"A jingle is a placeholder between random selections. So if no random elements involved, no jingles needed."*

### 5.4 Cursor

- `base_advance_per_week: 60`
- `cycle_weeks: 16`
- Base cursor for week `w`: `((w - 1) % 16) * 60`.
- Week 1 base = 0, week 2 = 60, ..., week 16 = 900, week 17 = 0 again.

Row `r`'s pool-index start: `base + row_start_offsets[r]`.
Block within a row: consumes `size` consecutive entries from the named pool starting at that index.

### 5.5 Pool-size requirements

- Week 16 consumes up to pool index `900 + 50 + 215 - 1 = 1164` in both `RG_RADIO` and `RG_PARTY`.
- User has accepted responsibility for keeping pools ≥ 1,165 tracks by week 16.
- Engine raises a clear error (not a silent wrap) if a window runs off the end of a pool. Fail loud, early.

### 5.6 RNG

`rng: "off"`. Output fully determined by `(template, pools, week_number)`.

### 5.7 Final template JSON

See `stations/rebirth-classics.json`. Summary:

```json
{
  "station": "rebirth-classics",
  "cadence": "weekly",
  "rng": "off",
  "sources": [
    { "name": "RG_RADIO",  "colour": "#c0392b", "role": "content" },
    { "name": "RG_PARTY",  "colour": "#8e44ad", "role": "content" },
    { "name": "RG_MASTER", "colour": "#7f8c8d", "role": "reference" }
  ],
  "cursor": { "base_advance_per_week": 60, "cycle_weeks": 16 },
  "layout": {
    "unit": "day",
    "unit_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "row_start_offsets": [0, 10, 20, 30, 40, 50, 50],
    "row_template": [
      { "source": "RG_RADIO", "strategy": "window", "size": 215 },
      { "source": "RG_PARTY", "strategy": "window", "size": 215 }
    ]
  }
}
```

---

## 6. Repository structure

```
station-studio/
  README.md
  DESIGN.md                this document
  requirements.txt
  requirements-dev.txt
  .gitignore
  run.py                   launches FastAPI

  engine/                  pure Python; no FastAPI, no UI
    __init__.py
    models.py              SQLAlchemy ORM
    database.py
    importer.py            iTunes XML, lifted from weekly
    template.py            load + validate template JSON
    cursor.py              per-station cursor state
    strategies/
      __init__.py
      window.py            the one primitive Classics needs
      pick.py              added but unused by Classics
    generator.py           orchestrates template + pools + week -> playlist + trace
    trace.py               dataclasses + trace_id plumbing

  api/
    __init__.py
    main.py                FastAPI app
    schemas.py             Pydantic
    routers/
      stations.py          CRUD over stations/*.json
      sources.py           import iTunes XML; list pools
      generate.py          POST /generate -> playlist + trace

  stations/
    rebirth-classics.json

  tests/
    conftest.py
    test_classics_generation.py

  web/
    package.json           SvelteKit scaffold (init via `npm create svelte@latest` in this dir)
    README.md              how to finish the scaffold
```

---

## 7. UI stack: SvelteKit

### Decision

SvelteKit, not htmx.

### Rationale

The UI has three surfaces that benefit from real client-side interactivity:

1. **Wizard** — live slot-grid preview that updates on every answer. Pure server round-trips would feel laggy.
2. **Block editor (v2)** — drag-drop lanes, colour-coded blocks, inline editing. Htmx's round-trip model fights this.
3. **Trace viewer** — collapsible per-slot detail, filter-by-day, filter-by-source. Client-side filtering is the natural fit.

SvelteKit also gives:
- File-based routing out of the box
- SSR when we want it, CSR when we don't
- Smaller shipped bundle than React
- Clean `fetch`-based story to the FastAPI backend
- A path to offline packaging later (like `weekly-desktop`) if ever needed

---

## 8. API surface (v1)

| Method | Endpoint                            | Purpose                                   |
|--------|-------------------------------------|-------------------------------------------|
| GET    | `/api/stations`                     | List station templates                    |
| GET    | `/api/stations/{id}`                | Get a template                            |
| POST   | `/api/stations`                     | Create a template                         |
| PUT    | `/api/stations/{id}`                | Update a template                         |
| POST   | `/api/sources/import`               | Import an iTunes XML as a source pool     |
| GET    | `/api/sources`                      | List imported pools                       |
| POST   | `/api/stations/{id}/generate`       | Generate a playlist (body: `{week: N}`)   |
| GET    | `/api/playlists/{id}`               | Fetch a generated playlist                |
| GET    | `/api/playlists/{id}/trace`         | Fetch the trace by `trace_id`             |
| GET    | `/api/playlists/{id}/export`        | Export as iTunes XML (browser download)   |
| POST   | `/api/playlists/{id}/export-to-disk`| Write iTunes XML to configured on-disk dir|

---

## 9. Trace model

```python
@dataclass
class TraceSlot:
    slot: int                # 1-indexed slot within the week
    unit: str                # e.g. "Mon", "Tue"; from template.layout.unit_labels
    row: int                 # 1-indexed row within the week
    block: int               # 1-indexed block within the row
    source: str              # source pool name
    strategy: str            # "window", "pick", ...
    pool_index: int          # 1-indexed position in the source pool
    track_id: int            # resolved track
    track_name: str
    track_artist: str | None
    reason: str              # human-readable rationale

@dataclass
class Trace:
    trace_id: str            # UUID
    station: str
    week: int
    rng: str                 # "on" | "off"
    seed: int | None
    generated_at: str        # ISO8601
    slots: list[TraceSlot]
```

The trace is stored as JSON alongside the playlist (in a `playlist_trace` table keyed by `trace_id`).

---

## 10. Testing strategy

### v1 test coverage (checked-in at scaffold time)

- `tests/test_classics_generation.py`
  - Synthesises source pools of 2,000 tracks each for `RG_RADIO`, `RG_PARTY`, `RG_MASTER`.
  - Generates week 1 from `rebirth-classics.json`. Asserts:
    - Playlist length = 3,010
    - Row 6 tracks == row 7 tracks (duplicate-by-design)
    - Row 1, block 1, slot 1 = `RG_RADIO` pool index 1
    - Row 1, block 2, slot 216 = `RG_PARTY` pool index 1
  - Generates week 2. Asserts:
    - Row 1, slot 1 = `RG_RADIO` pool index 61 (base cursor +60)
  - Generates week 17. Asserts:
    - Row 1, slot 1 = `RG_RADIO` pool index 1 (cycle wraps after 16 weeks)
  - Round-trips the `trace_id`: fetch playlist, pull `trace_id`, retrieve trace, confirm it matches the generation output.

### Future test coverage

- Property-based tests over random templates (Hypothesis)
- Real iTunes XML integration (mirroring weekly's `@pytest.mark.slow` pattern)
- Error paths: window runs off pool; unknown source; invalid template JSON

---

## 11. Open items and deferred decisions

| Item                                        | Status       | Notes                                                   |
|---------------------------------------------|--------------|---------------------------------------------------------|
| Visual block editor                         | later (§16.2)| v2; operates on same JSON as wizard; needs §17 Phase A  |
| Pre-§16.2 / pre-port roadmap                | next (§17)   | Phase A: Alembic + backups + /api/meta + template.ts + LayoutFieldset arrays |
| Station creation wizard (UI)                | done (14.7)  | Produces the template JSON; reuses `web/src/lib/api.ts` |
| Edit mode for stations                      | done (14.13) | `<StationForm>` extracted; JSON preview + diff + reset  |
| Wizard polish (presets, per-field errors)   | later (14.7) | See 14.7 table; wait for real-user pain                 |
| In-UI generate button + per-row regenerate  | done (14.9)  | Closes wizard → trace-viewer loop; per-row regen too    |
| Delete / station-reset UI                   | done (14.10) | Per-row delete + station-nuke header; both with confirm |
| Trace versioning (history of regenerations) | later (§16.4)| Schema change; only after §16.1 lands and bites         |
| Migrate Rebirth Radio onto Station Studio   | long-term    | §17 Phase C; needs `weighted`, `prev_ref`, `genre_pick` |
| Schema migrations (Alembic)                 | done (14.17) | Baseline migration captures current schema; future deltas via `make alembic-revision` |
| Backup/restore tooling for SQLite           | done (14.18) | `make backup` (WAL-aware) + `make restore date=...`; pre-restore safety copy |
| `GET /api/meta` (shared template constants) | done (14.19) | Removes 3-way constant duplication; loadMeta() module-cached |
| iTunes XML export of Classics playlists     | done (14.1)  | Lifted from weekly, station-scoped; browser + disk paths|
| Disk-export UI button on trace viewer       | done (14.11) | "Export to disk" button + status line; closes prod loop |
| Disk-export prune endpoint                  | on demand    | `POST /api/playlists/export/prune` — wait until clutter |
| Scheduler (auto-generate weekly)            | v1.1         | Weekly has APScheduler; port when stable                |
| Artist aliasing / dedup                     | not needed   | Classics has no dedup; engine grows this when needed    |
| Excluded-candidates in trace                | next (§17 #5)| Prereq for `weighted`-debugability ahead of port        |
| Jingles-as-primitive                        | only if      | Only if a future random-selection station needs them    |
| Trace viewer (UI)                           | done (14.5)  | `/playlists/[id]` — per-slot reasoning, grouped by row  |
| Trace-viewer search / filter                | done (14.15) | Free-text + source/strategy + ANDed filters + counter   |
| Trace-viewer multi-week prev/next nav       | done (14.16) | `← week NNN | week NNN →` in header; closes §14.5 punt  |
| Trace-viewer polish (virtualisation, SSR…)  | later (14.5) | Virtualisation / week-compare remain                    |
| Test plan + CI                              | done (14.8)  | 4-tier; backend 98% lines; 26 Vitest + 9 Playwright     |
| Coverage gate in CI                         | done (14.12) | Backend 90% aggregate; frontend per-file lines ≥ 85%    |
| Trace-viewer Vitest backfill                | done (14.14) | 0% → 96.49% lines; closes §14.8's last 0%-file row      |
| Test follow-ups (viz tests, mutation)       | later (14.8) | See 14.8 table; perf / a11y / mutation remain           |
| Accessibility pass                          | later (§16.5)| Consolidates a11y rows from 14.5/14.7/14.9; one slice   |

---

## 12. Assumptions this design makes

Explicit list so future readers can check which have held:

1. **Users will maintain pool sizes.** Engine raises on underflow rather than silently wrapping. User accepts this.
2. **Templates are small and readable.** Check-in friendly. Classics template fits on one screen.
3. **One playlist per (station, week) tuple.** No sub-variants of the same week.
4. **Tracks are identified by iTunes `Persistent ID`.** Carried over from weekly.
5. **SQLite is sufficient.** Same choice as weekly. WAL mode. Revisit only if a station needs multi-writer concurrency.
6. **Classics never needs randomness.** If it ever does, the template flips `rng: "off"` → `"on"` and adds a seed; no code change needed.
7. **Row 7 duplication is Classics-specific.** Expressed as explicit `row_start_offsets` list, not as a default engine behaviour. Other stations supply their own list.
8. **Cursor cycle is per-station.** Classics = 16 weeks × 60. Other stations will have their own. The engine doesn't hardcode.
9. **The SvelteKit app lives in `web/`** and talks to FastAPI over `localhost:8001` in dev. Production packaging deferred.
10. **iTunes XML is the only import format** for v1. CSV / M3U could be added later.

---

## 13. Glossary

| Term           | Meaning                                                                             |
|----------------|-------------------------------------------------------------------------------------|
| Station        | A named configuration that describes how to generate a playlist for a radio output |
| Template       | The JSON document defining one station's layout, sources, cursor, and rules        |
| Source pool    | An ordered list of tracks imported from an iTunes XML file                         |
| Cursor         | A per-station integer that advances weekly and drives `window` / `sequential` slots|
| Primitive      | An engine-level slot-filling strategy (`pick`, `window`, `sequential`, ...)        |
| Slot           | A single position in the generated weekly playlist                                 |
| Row            | A group of slots; in Classics, one row = one day                                   |
| Block          | A contiguous group of slots within a row filled from one source with one strategy  |
| Trace          | Per-slot record of how and why that slot was filled                                |
| `trace_id`     | UUID linking a generated playlist back to its trace                                |

---

## 14. Session 2 — post-scaffold evolution

This section is appended as we build out functionality after the initial
scaffold. Each sub-section captures the decisions that shaped a chunk of
work, with the reasoning preserved so future readers see the rejected
alternatives too.

### 14.1 Exporter (iTunes XML out)

**Problem.** A generated playlist lives in the DB as `Playlist` +
`PlaylistEntry` rows. iTunes needs it as an XML plist file. Without an
exporter, Classics is write-only — the whole point of the tool is
undermined.

**Approach.** Lifted the weekly/ exporter verbatim and generalised it:

- **Station-scoped playlist identity.** Playlist `Name` is
  `"{station.name} {week:03d}"` (e.g. `"Rebirth Classics 001"`).
  `Playlist Persistent ID` is `{slug.upper().replace("-","_")}_{week:03d}`
  (e.g. `"REBIRTH_CLASSICS_001"`). Previously the weekly/ exporter
  hardcoded `"WEEKLY PLAYLIST NNN"`.
- **Track field order preserved via `itunes_metadata`.** On import we
  store the original iTunes dict insertion-ordered. On export we iterate
  that dict, letting column values (name/artist/…) override for
  post-import edits. Unknown keys pass through unchanged.
- **Library metadata captured at import.** `Config` key/value table
  stores `library_persistent_id` and `music_folder` on first import
  (first-import wins). Exporter reads them; falls back to
  `"STATION_STUDIO"` / `"file://localhost/"` if absent (dev & tests).
- **iTunes formatting quirks kept.** `Apple Computer` DOCTYPE, leaf
  `<key>` + value merged onto one line via regex, Tracks dict sorted by
  numeric Track ID ascending, fixed top-level and playlist key ordering,
  synthetic Track IDs (≥ 100 000) for fixtures without one.

**Template JSON gained an optional top-level `"name"` field** so the
seeder can pick up a display name (e.g. `"Rebirth Classics"`) without
the parser rejecting it as an unknown key. The engine ignores it; it's
purely for the exporter and UI.

### 14.2 Disk vs browser delivery (the C+A decision)

**Question raised by user.** Where does the exported XML go?

The endpoint `GET /api/playlists/{id}/export` serves XML with
`Content-Disposition: attachment`, which a browser saves to Downloads.
Nothing is ever written server-side. Three options surfaced:

- **(a) Browser download only.** Simplest. UI click → Downloads folder.
- **(b) Configured output directory.** Server writes to a predictable
  path on disk. Good for sync'd folders (Dropbox / OneDrive feeding the
  Windows iTunes box).
- **(c) Both.** Endpoint for ad-hoc download; a separate "export to
  disk" action for the configured directory.

**Chosen: (c).** Two endpoints, one underlying function
(`export_playlist(db, id) -> bytes`). `export_playlist_to_disk(db, id)
-> Path` wraps it and writes to `{configured_dir}/{slug}/{week:03d}.xml`.

**Configured-dir source.** `Config` key `playlist_output_dir`. Absolute,
relative-to-CWD, or `~`-prefixed all accepted and normalised to an
absolute Path. Default when unset: `"out"` relative to CWD (the repo
root when launched via `run.py` or `python -m scripts.*`). Stored in
`Config` rather than an env var so the UI can set it later without a
schema change.

**Layout choice.** `{out}/{slug}/{week:03d}.xml` (one subdirectory per
station) over the flatter `{out}/{slug}-{week:03d}.xml`. Scales better
as weeks accumulate; easier to drag whole seasons into iTunes.

**Trigger.** Explicit only — user clicks "export to disk". Not
automatic on generate. Keeps generation fast and non-destructive;
makes the action visible in the UI.

**Overwrite semantics.** Re-exporting the same `(station, week)`
overwrites silently. Mirrors how the underlying playlist is regenerated
(DB unique constraint on `(station_id, week)` means one playlist per
week; re-generation replaces it).

### 14.3 Retention / "too many files" (deliberately punted)

**User concern.** Won't disk exports accumulate without limit?

**Scale check.** ~870 KB per Classics export. One station × 52 weeks =
~45 MB/year. Five stations = ~225 MB/year. Not a disk problem; at most
a tidiness problem.

**Real sources of clutter** (not raw growth):

1. Re-exports of the same week — **not an issue**; overwrite in place.
2. Orphaned files after a station is renamed (slug changes → old
   subdir orphaned).
3. Orphaned weeks (DB playlist deleted but file lingers).
4. Dev/test artefacts.

**Options considered:**

- **Retention limit (keep last N weeks).** Rejected — the whole point
  of keeping old files is dragging week 42 into iTunes months later.
- **Auto-delete on rename.** Rejected — destructive, surprising.
- **Prune endpoint** (`POST /api/playlists/export/prune`): compare
  `out/` to the `playlist` table, report/delete files with no DB row.
  Dry-run by default. **Deferred** until the user actually reports
  clutter.
- **No policy + `.gitignore`.** **Chosen for now.** `out/` added to
  `.gitignore` so real-library exports never accidentally land in the
  repo. A one-line `logger.info` on each disk-export (path + size +
  created/overwrote) makes anomalies visible without any UI work. The
  log line goes to the application logger — **nothing is added to the
  XML file itself**; the bytes iTunes sees are unchanged.

### 14.4 UI ordering: trace viewer before wizard

Two remaining UI chunks: the station wizard (step 3 of the original
plan) and the trace viewer (step 5). Decided to build the **trace
viewer first** on these grounds:

- Reuses existing data. `playlist_id=1` is in the DB from bootstrap;
  `GET /api/playlists/{id}/trace` already returns the slots. Zero
  backend work — just a page that renders what's there.
- Proves the SvelteKit ↔ FastAPI plumbing on a read-only page before
  we wade into form state.
- Delivers on the **central promise of the project** — "see your
  working" — which was the whole motivation for Station Studio over
  commissioning another bespoke generator.
- Builds scaffolding (`web/src/lib/api.ts`, shared layout) that the
  wizard will reuse.
- The wizard is premature when you've only got one station and no felt
  need for a second.

**Scope for the first trace-viewer slice:**

1. `npm install` in `web/` so the stub actually runs.
2. `web/src/lib/api.ts` — typed fetch helpers: `getStations()`,
   `getPlaylist(id)`, `getTrace(id)`.
3. `web/src/routes/playlists/[id]/+page.svelte` — fetch playlist +
   trace, render: header (station · week · trace_id · rng · seed ·
   generated_at) + list of slots grouped by row (day), each slot showing
   position · source chip · strategy · pool index · track · artist.
4. Link the existing landing page through to `/playlists/[id]` (minimal
   station page or direct URL; decide in-flight).

**Deliberately deferred** from this slice:

- Filtering / search over slots (wait until scanning 3 010 rows actually
  feels painful).
- Export buttons in the UI calling `/export` or `/export-to-disk`
  (trivial to add once the layout is proven; don't conflate).
- Excluded-candidates display (already on the deferred list in §11).
- Wizard (step 3) — next, after the viewer lands.

### 14.5 Trace viewer — what shipped, what was punted

**What shipped (commit `f8e6155`).**

- Backend: `GET /api/stations/{slug}/playlists` lists a station's
  playlists with `{id, week, trace_id, generated_at, entry_count}`. Uses
  a single grouped query with `COUNT(PlaylistEntry.id)` joined on
  `Playlist`.
- `PlaylistSummary` Pydantic schema added.
- `web/src/lib/api.ts`: typed fetch client (interfaces mirroring the
  Pydantic schemas; this file is the shared frontend contract the wizard
  will reuse).
- Three SvelteKit routes:
  - `/` — existing landing, upgraded to the typed client.
  - `/stations/[slug]` — header + a table of the station's playlists
    linking to the trace viewer.
  - `/playlists/[id]` — the centrepiece. Header (station · week · RNG ·
    seed · generated · trace_id), "Download iTunes XML" button hitting
    the existing `/api/playlists/{id}/export` endpoint, and the slot
    table **grouped by row** with collapsible headers. Each slot row
    shows: slot number, source chip (coloured from `template.sources`),
    strategy, pool index (with the full `reason` string in a `title`
    tooltip), track name, artist.
- `svelte-check` clean; 88 backend tests still passing; pages
  end-to-end smoke-tested against the real Classics DB.

**User confirmed in-browser.** Works; UI and features can improve later.

**Deliberately punted from this slice — revisit when felt, not pre-emptively.**

| Area                           | Missing                                                                             | Trigger to pick up                           |
|--------------------------------|-------------------------------------------------------------------------------------|----------------------------------------------|
| Rendering 3 010 rows           | No virtualisation; the full DOM is built on first paint                             | Noticeable scroll lag or first-paint delay   |
| Initial-load UX                | All fetches in `onMount` (client-only). SSR shows empty shell for a beat            | Flash becomes annoying; or we want SEO/share |
| Search / filter                | No search box over slots; no filter by source / strategy                            | 3 010 rows makes scanning painful            |
| Multi-week navigation          | No prev/next-week links inside the viewer; back to station, click in again          | Running ≥3 weeks regularly                   |
| Week comparison / diff         | No "show what changed from week N-1" — central once there are real-world edits      | First time a week-to-week question comes up  |
| Excluded-candidate display     | Trace doesn't currently record rejects; viewer has nowhere to show them             | Already deferred in §11; UI slot ready       |
| Disk-export button in UI       | "Download iTunes XML" hits the browser endpoint; no UI trigger for `export-to-disk` | User wants the OneDrive/Dropbox flow visible |
| Regenerate from the viewer     | No "regenerate this week" action; must use the CLI or API                           | User wants round-trip editing                |
| Station page richness          | Shows playlists table only; no template summary, pool stats, cursor position        | Next station starts; user wants comparison   |
| Accessibility polish           | Keyboard-togglable row headers exist (Enter); no ARIA labels on chips, no skip-link | Before real external users                   |
| Error UX                       | Errors render as raw message strings; no retry affordance                           | When we see a real-world failure             |
| Mobile / narrow-viewport CSS   | Table overflows horizontally on small screens; not styled responsively              | When a user tries it on a phone              |
| End-to-end browser tests       | Only curl-level shell smoke tests were run; no Playwright / similar                 | Once the viewer has more moving parts        |
| Shared layout / header         | Each page rolls its own title/container styles                                      | When a third similar page appears            |

**Implementation notes worth remembering when picking any of these up:**

- `onMount` runs client-only in SvelteKit. To SSR the trace data, move
  the three fetches into a `+page.ts` `load` function (`load({ fetch,
  params })`) and pass `data` in as a prop. The `api.ts` helpers already
  accept an optional `fetch_` argument exactly for this.
- Source colours live on `station.template.sources[].colour` and are
  looked up in the page's `sourceColours` map. Adding a new source to a
  station automatically gets a chip colour; no code change needed.
- Row collapse state is in-memory only; reloads reset to "all expanded".
  If we persist it, `sessionStorage` keyed on `trace_id` is enough.
- The export button is a plain `<a href>` so the browser handles the
  `Content-Disposition` download. Keep it that way — no JS needed for a
  one-shot byte download.

### 14.6 Station wizard — planning notes (superseded by 14.7)

Original sketch is kept for historical context. Decisions that actually
shipped live in §14.7; read that one first.

- **Produces the same JSON template** that's checked into `stations/`.
  The template is the canonical artefact (see §4.2). The wizard is a
  writer, not a new format. Saving = `POST /api/stations` with the
  validated JSON.
- **First-screen minimum:** slug, display name, cadence, RNG on/off,
  and the list of source pools the station will use (with roles).
- **Later screens:** cursor (`base_advance_per_week`, `cycle_weeks`),
  layout (unit, `unit_labels`, `row_start_offsets`, `row_template`
  blocks with source + strategy + size). The visual block editor (§11,
  v2) replaces the layout screen eventually.
- **Reuse:** the typed client in `web/src/lib/api.ts`.
- **Validation:** server-side via `engine.template.parse_template`.
  Sketch suggested per-field error surfacing; see §14.7 for what
  actually landed.

### 14.7 Wizard — what shipped, what was punted

**What shipped.**

- **Single-page form at `/stations/new`** — scrollable, not a multi-step
  flow. Four `<fieldset>` sections: Identity, Sources, Cursor, Layout.
  Form state lives in the page component; no external state lib.
- **Every required template field is covered**: slug, display name,
  cadence, RNG (+ optional seed when RNG = 'on'), sources (dynamic
  add/remove list of `{name, colour, role}`), cursor
  (`base_advance_per_week`, `cycle_weeks`), layout (unit, comma-
  separated `unit_labels`, comma-separated `row_start_offsets`, dynamic
  list of `row_template` blocks where the `source` dropdown is
  constrained to content-role sources declared above).
- **Submit flow.** Client runs a minimal pre-submit sanity check
  (non-empty slug matching `^[a-z0-9][a-z0-9-]*$`, non-empty name, ≥ 1
  source, ≥ 1 row-template block, every block references a source).
  Anything beyond that defers to `engine.template.parse_template` —
  `POST /api/stations` returns the `TemplateError` message on 400 and
  the UI shows it at the top of the form.
- **On success:** `goto('/stations/{slug}')` — immediately into the new
  station's page, which shows its (empty) playlists table.
- **createStation() helper** added to `web/src/lib/api.ts` so the
  frontend still has one typed client covering every API call it makes.
- **End-to-end verified.** Wizard page HTML loads, POST via Vite proxy
  creates station, station page renders it, `POST /api/stations/{slug}
  /generate` produces a 3-slot playlist against the existing RG_RADIO
  pool, trace viewer shows it. Test station deleted to leave the real
  DB clean.

**Constants duplicated from engine** (`RNG_OPTIONS`, `STRATEGY_OPTIONS`,
`ROLE_OPTIONS`, `CADENCE_OPTIONS`) with a "keep in sync with
engine/template.py" comment. Formalise as `GET /api/meta` when there's
a second reason to.

**Deliberately punted from this slice — same discipline as §14.5.**

| Area                                 | Missing                                                                          | Trigger                                       |
|--------------------------------------|----------------------------------------------------------------------------------|-----------------------------------------------|
| Multi-step wizard flow               | Single-page form; no step-by-step progression / progress bar                     | User reports cognitive overload on the form   |
| "Start from existing station" preset | No "copy of Rebirth Classics" button                                             | Making a second station like an existing one  |
| Per-field error highlighting         | Server errors render as a blob at the top                                        | Real-user hits one and can't find which field |
| Chip / tag inputs                    | `unit_labels` and `row_start_offsets` are comma-separated strings; splitting is  | Users start writing in arrays consistently    |
|                                      | manual in `buildTemplate()`                                                      |                                               |
| Live template JSON preview           | No "this is what you're saving" view pre-submit                                  | Users want to inspect before committing       |
| Draft autosave                       | Reload loses the form                                                            | User reports losing work                      |
| Edit mode                            | Wizard is create-only; editing a station needs a separate `/stations/[slug]/edit`| First rename / tweak request                  |
| Slug-collision preview               | Collision is caught server-side with a 409; no "this slug is already taken" hint | Users hit it enough to annoy them             |
| Accessibility polish                 | Labels present; no ARIA live regions for the error / submit state                | Before real external users                    |
| Client-side sanity for layout arrays | We don't check `unit_labels.length == row_start_offsets.length`; server does     | Users hit the server error surprisingly often |
| GET /api/meta endpoint               | RNG/strategy/role/cadence lists hardcoded in the form                            | Third constant list needs to be shared        |
| Visual block editor                  | Layout still edited as form inputs; §11 deferred item                            | v2 explicit                                   |

**Implementation notes for future pick-up:**

- Form state is four flat variables + two lists. When the form grows
  (edit mode, presets), a `writable<FormState>` store in
  `web/src/lib/wizardStore.ts` probably earns its keep; not before.
- `buildTemplate()` is where the form-state-to-JSON translation lives.
  If you want a live preview (one of the deferrals), make that function
  reactive and render its output in a `<pre>` block.
- The `<a href="/">` cancel link is deliberate — no unsaved-changes
  confirmation yet. Add a `beforeNavigate` hook once autosave-draft
  lands.

### 14.8 Test plan and CI — what shipped, what was punted

**Problem.** Up to this point the project had a handful of pytest
modules around the engine and a one-off shell smoke test against the
real Classics DB. The frontend had nothing. With the wizard, exporter,
trace viewer, and three round-trippable surfaces all alive, "manually
verify in the browser" no longer scaled — and a regression in the
template parser, the disk-export path, or the wizard wiring would
silently rot until a real user hit it. The 9-commit test plan addresses
that.

**Four-tier strategy.** Each layer covers what's cheapest to test
*there* and nothing else:

1. **Engine unit tests** (`tests/test_*.py`) — pure-function checks of
   `parse_template`, cursor arithmetic, `window` / `pick` strategies,
   importer XML→ORM mapping, exporter ORM→plist round-trip. No HTTP,
   no DB beyond an in-memory SQLite.
2. **API integration tests** (`tests/test_api.py`) — FastAPI
   `TestClient` against a per-test SQLite, exercising every endpoint
   end-to-end including failure cases (404 unknown station, 400 bad
   template, 409 slug collision, 500 surface for malformed XML).
3. **Vitest component tests** (`web/src/**/*.test.ts`) — SvelteKit
   pages mounted under jsdom with `$lib/api` mocked. Each page's
   render branches (loading / error / empty / populated) hit
   independently.
4. **Playwright e2e** (`web/e2e/*.spec.ts`) — real browser against a
   real backend, real SQLite, real Vite proxy. One spec per UI flow
   (landing → wizard → station detail → trace viewer → download), so
   a passing run *is* the manual smoke test we used to do by hand.

**Why all four.** Each tier catches a different failure class. The
pytest tier catches engine regressions in milliseconds. The API tier
catches schema drift between Pydantic and the engine. The Vitest tier
catches Svelte-side render bugs without a backend (fast, works
offline). Playwright catches integration bugs none of the others can —
the wizard form submitting through the Vite proxy to a real FastAPI
that writes a real SQLite row that a follow-up GET reads back. Drop
any tier and a real bug class becomes invisible.

**What shipped (9 commits, `695f919`..`0fa1c4a`, plus the backfill
`b44a3f4`).**

- **Backend coverage at 98% lines.** `tests/test_api.py` covers every
  router branch including the 5xx paths (malformed-XML import, missing
  source pool on generate, etc.). `tests/test_*.py` modules cover the
  engine's edge cases: cursor wrap, week 16→17 cycle reset, pool-
  underflow error, optional-field passthroughs.
- **Vitest harness.** `web/vite.config.js` gates
  `resolve.conditions: ['browser']` on `VITEST`, `web/vitest.setup.ts`
  imports `@testing-library/svelte/vitest` so onMount async work is
  drained per test, coverage via `@vitest/coverage-v8`. 26 tests
  across 4 files: `lib/api.test.ts` (8), `routes/stations/new/wizard
  .test.ts` (5), `routes/landing.test.ts` (5),
  `routes/stations/[slug]/detail.test.ts` (8).
- **Playwright harness.** `web/playwright.config.ts` boots both the
  FastAPI backend (its own per-PID SQLite, wiped before launch) and
  the Vite dev server as `webServer` entries. 9 specs covering
  landing, wizard validation + happy path, station detail empty +
  populated states, trace-viewer rendering + iTunes download.
- **Make + CI.** `Makefile` targets `test`, `test-backend`,
  `test-frontend`, `test-e2e`, `coverage`, `clean-test`. GitHub
  Actions workflow `.github/workflows/ci.yml` runs three jobs in
  parallel (backend, frontend unit, frontend e2e) on Python 3.14 +
  Node 20.

**Gotchas captured for future readers** — every one of these cost
real time to find and the comments in-tree are deliberately verbose:

- **Playwright `webServer` boots before `globalSetup`.** Initial DB-
  wipe lived in `globalSetup` and got run *after* the backend had
  already opened a connection pool against the stale DB. Fix: move
  the `rm -f` into the backend's launch command itself
  (`rm -f db && exec uvicorn ...`). The DB filename also carries
  `process.pid` so two devs on the same checkout don't collide.
- **Hydration sentinel beats `networkidle`.** Playwright maintainers
  discourage `waitForLoadState('networkidle')` and it lies anyway —
  the SSR HTML has the form enabled before Svelte hydrates, so fills
  ran against a dead DOM. The wizard now sets `data-wizard-ready` on
  `<html>` from `onMount`; specs wait on that attribute. *(Update
  per §14.19: the sentinel's meaning has broadened — `<StationForm>`
  now only mounts after `loadMeta()` resolves, so the attribute's
  appearance signals "meta loaded AND form hydrated AND ready". Tests
  still work; the broader meaning is what they actually want.)*
- **Svelte 4's onMount is a no-op without `browser` conditions.**
  Vitest defaults to Svelte's SSR build, which strips onMount. Putting
  `resolve.conditions` under `test:` doesn't help because SvelteKit's
  Vite plugin overrides resolution before Vitest's own resolve fires.
  Fix: top-level `resolve` block, gated on `process.env.VITEST`.
- **`@testing-library/svelte/vitest`'s afterEach already runs
  `await act(); cleanup()`.** Adding a per-file `afterEach(cleanup)`
  on top double-tears the component and races the act flush, which
  deadlocks any test that holds a deferred promise across cleanup.
  Solution: don't double up; let the setup hook do it.
- **Loading-branch testing without a deferred promise.** A pre-
  resolved `mockResolvedValueOnce(...)` works because the `.then`
  microtask hasn't fired by the time `render()` returns synchronously
  — we observe the loading branch first, then `await act()` to drain.
  Cleaner than the deferred-promise dance.
- **`importlib.reload(deps)` polluted global state across tests.**
  Fix: monkeypatch `_SessionLocal` directly. Same pattern any future
  module-global needs.
- **Deterministic export ≠ overwrite test.** A re-export test that
  writes to a sibling path silently passes if the file is identical.
  Pre-seed the target with garbage bytes, then assert post-export
  contains none of the garbage.

### 14.9 In-UI generate button — what shipped, what was punted

**What shipped (3 commits, follow-on to §16.1's plan).**

Backed by the planning round in §16.1 (decisions D1–D7 captured
there); this section records *what actually landed* and the small
in-flight surprises.

- **`generatePlaylist(slug, week, seed?)`** in `web/src/lib/api.ts`.
  Wraps `POST /api/stations/{slug}/generate`. Seed is optional; the
  helper omits it from the request body when undefined (engine
  ignores it for rng:off anyway, but cleaner-on-the-wire).
- **Generate panel on `/stations/[slug]`.** Two visual variants:
  - Empty state: a big primary "Generate week 1 →" CTA with
    `data-testid="generate-empty-cta"`.
  - Populated state: inline "Generate next week (N+1)" primary
    button with `data-testid="generate-next"`, plus a `<details>`
    advanced disclosure for `pick a week` (and `seed` when rng:on).
  - "Replace week N" red label fires when the chosen week already
    exists, with a `confirm()` dialog before the POST. The dialog
    copy points users at §16.4 so the destructive-by-design nature
    of regen-without-versioning is visible.
- **Per-row "↻ regen" link** in the playlists table
  (`data-testid="regen-row-{week}"`). Confirm → POST → `goto`. Uses
  a fresh random seed for rng:on (different output, not byte-
  identical replay); no seed for rng:off.
- **Inline error rendering** (`data-testid="generate-error"`) when
  the backend returns 4xx. Page does not navigate on failure. The
  pool-underflow path (week 13+ on Classics) flows through this.
- **35 Vitest tests** (was 26): 9 new on `detail.test.ts` covering
  the empty-state CTA, populated next-week label, advanced
  disclosure, seed gating on rng, "Replace week N" label on
  conflict, confirm-yes/no paths, per-row regen with seed-presence
  check, and the inline-error path.
- **15 Playwright tests** (was 13, with 1 also rewritten): a new
  `e2e/generate.spec.ts` covers empty → CTA → trace viewer;
  populated → next-week → trace viewer; replace via advanced
  form (with confirm accept); per-row regen; cancel path; engine
  underflow → inline error → no nav. Existing
  `station-detail.spec.ts` updated for the new empty-state copy.

**Decisions in-flight that the §16.1 brief didn't pre-decide.**

- **CTA needed its own testid.** The empty-state CTA *and* the
  advanced-form submit both render "Generate week 1" by default
  when nothing exists, so `findByRole('button', { name: /generate
  week 1/ })` matched both and threw "Found multiple". Added
  `data-testid="generate-empty-cta"` and switched to testid-based
  selection. Lesson: when role-name selectors collide because two
  controls converge on the same default text, reach for testid
  before fighting the regex.
- **Landing spec lost its empty-state assertion.** Playwright's
  `webServer` shares one DB across the whole suite. Adding
  `e2e/generate.spec.ts` (alphabetically before `landing.spec.ts`)
  meant by the time the landing test ran, several stations
  existed, so the empty-state hint copy was no longer visible. The
  empty-state assertion was already covered at the Vitest layer
  (`landing.test.ts` mocks the API response), so I dropped it from
  the Playwright spec rather than imposing test ordering or DB
  isolation. Captured in a comment on the test.
- **Trace-viewer week assertion uses `'002'` (zero-padded).** The
  trace viewer renders week as `String(trace.week).padStart(3, '0')`
  — `'002'`, not `'2'`. Initial regex `/week\s*2/` failed. Lesson:
  read the rendered output, not the model, when writing e2e
  assertions.
- **`generate()` function gained an `opts` arg.** Per-row regen
  needs to bypass the generic overwrite-confirm (because we
  already asked, in row-specific copy) and pass a different seed
  than `seedInput` (which is the form's state, not the row's).
  `generate(week, { seed?, skipConfirm? })` — feels like a small
  thing but worth flagging because the next caller will be tempted
  to pass an entire bag instead of two named flags.
- **Per-row regen uses fresh-random seed for rng:on.** Concrete
  consequence of D3: the user wanting a *specific* seed uses the
  advanced form; the per-row path is for "redo this week with
  whatever current state of the world is". This is unobservable
  for Classics (rng:off) but locks in the contract for the first
  rng:on station.

**What's now reachable from the UI.** The full v1 lifecycle:
landing → wizard → station detail → generate (week 1) → trace
viewer → download iTunes XML. Three clicks from landing to a
playable file. The §16.1 brief's "loudest paper cut" is closed.

**Coverage shipping** — measured post-review-fix commit (39 Vitest tests):

| Layer        | Coverage                                                            |
|--------------|---------------------------------------------------------------------|
| Backend      | 98% lines (unchanged — no backend changes this slice)               |
| Frontend Vitest | landing 100%, station-detail 94.49%, wizard 95.06%, `lib/api` 100%; trace viewer 0% (Playwright-only by design) |
| Playwright   | 15 specs across 6 files                                             |

**Note on the lib/api 100% figure.** The wider §16.1 detail-test suite
mocks the `$lib/api` module so the real `generatePlaylist` body never
executes there. To close that gap a dedicated unit-test block for
`generatePlaylist` was added in `web/src/lib/api.test.ts` (URL build,
seed-omit-when-undefined, slug encoding, ApiError on 4xx). Without
that block, `api.ts` sat at ~84% lines because the new function was
invisible to the test layer it shipped with — easy trap to miss when
mocking out a module you also added a function to.

**Deliberately punted from this slice** — these match §16.1's
pre-flight punt list, restated here for the §11 cross-reference:

| Area                             | Status               | Trigger                              |
|----------------------------------|----------------------|--------------------------------------|
| Delete a playlist                | §16.3                | Common enough to bother              |
| Server-side 409 on overwrite     | not now              | Multi-user / multi-tab use           |
| Disk-export staleness warning    | not now              | User reports forgetting to re-export |
| Trace versioning                 | §16.4                | First "I want the old one" report    |
| Batch "generate weeks N..M"      | not now              | Real ask appears                     |
| Custom seed for per-row regen    | not now              | Advanced form already covers it      |
| Auto-disk-export on generate     | rejected             | Couples concerns, surprising         |
| Concurrent-tab race              | not now              | Single-user tool                     |
| Auto-generate on station create  | rejected             | Generation is a visible action       |
| Scheduler / cron                 | §11 v1.1             | Weekly migration matures             |

**Implementation notes for future pick-up.**

- `nextWeek` is a derived `$:` reactive — `Math.max(...weeks) + 1`
  on the array, with `0` as the empty-state default. The advanced
  form's `chosenWeek` mirrors `nextWeek` until the user touches
  the input (via `userTouchedAdvanced` flag); after that, the
  reactive backs off to avoid yanking values mid-edit. If you
  extend to multi-week batch generation, the same flag pattern
  works for the "from"/"to" inputs.
- The confirm copy points users at `DESIGN.md §16.4` *by name*.
  When §16.4 ships and regen becomes non-destructive, update the
  copy in `+page.svelte`'s `generate()` and `regenRow()` — it'll
  be a search for the literal string "§16.4".
- The `generate-empty-cta` testid is what the empty-state
  Playwright spec hangs on; if you ever change the empty-state to
  not be a button (e.g. a multi-step wizard for the first
  generation), keep the testid attached to the entry-point
  control or update the spec.
- Per-row regen uses `regen-row-{week}` not `regen-row-{playlist_id}`
  because `week` is more stable across regenerations (the playlist
  id changes each time). Tests can therefore re-target the same
  control by week even after a regen has shifted ids underneath.

**Coverage shipping** — last measurement on commit `b44a3f4`:

| Layer        | Coverage                                        |
|--------------|-------------------------------------------------|
| Backend      | **98% lines** across `engine/`, `api/`, scripts |
| Frontend Vitest | landing 100%, station-detail 97.77%, wizard 95.06%, `$lib/api` 100%; trace viewer 0% (Playwright-only by design) |
| Playwright   | 9 specs, every UI flow                          |

**Deliberately punted from this slice.**

| Area                                | Missing                                                                      | Trigger to pick up                              |
|-------------------------------------|------------------------------------------------------------------------------|-------------------------------------------------|
| Vitest tests for trace viewer       | done in §14.14 — was 0%, now 96.49% lines via `trace-viewer.test.ts`         | n/a (closed)                                    |
| Coverage gate in CI                 | done in §14.12 — backend ≥90% aggregate, frontend per-file lines ≥85%        | n/a (closed)                                    |
| Visual-regression / screenshot tests| None — Playwright only asserts text & DOM                                    | A CSS regression slips past assertions          |
| Property-based engine tests         | Mentioned in §10; never landed                                               | Engine grows a primitive that's hard to enumerate|
| Real iTunes XML integration test    | Importer tests use synthesised plists                                        | An iTunes update breaks a real-world XML        |
| Mutation testing                    | `mutmut` would surface assertions that pass with broken code                 | Confidence in coverage % drops                  |
| Multi-Python-version CI matrix      | CI pins 3.14; we don't test 3.12 or 3.13                                     | A user reports running on something else        |

**Implementation notes for future pick-up:**

- The `vi.hoisted()` pattern for mock state (used in
  `wizard.test.ts`, `landing.test.ts`, `detail.test.ts`) is the
  workaround for `vi.mock()` factory hoisting above imports. Keep
  using it; the alternative (top-level `let` + assignment in
  `beforeEach`) trips a "Cannot access X before initialization" race.
- The `$page` store mock in `detail.test.ts` is a hand-rolled
  readable store — `subscribe(fn)` calls `fn(value)` synchronously
  and returns an unsubscribe. If a future page reads more from
  `$page` (e.g. `$page.url`), extend the value object there.
- Playwright DB filename includes `process.pid`, so the `Makefile`'s
  `clean-test` target uses a glob: `rm -f playwright-test.*.db*`.
- The CI workflow runs Python 3.14 with `allow-prereleases: true`
  on `actions/setup-python`. Drop that flag once 3.14 GAs.

### 14.10 Delete / station reset — what shipped, what was punted

**What shipped (5 commits, follow-on to §16.3's plan).**

The plan in §16.3 captured D1–D7 ahead of execution; this section
records what actually landed and the in-flight surprises.

- **Backend (already in place via earlier commits).** Two DELETE
  endpoints, both 204 No Content with cascade through SQLAlchemy
  relationships:
  - `DELETE /api/playlists/{id}` (commit `805ac48`)
  - `DELETE /api/stations/{slug}` (commit `805ac48`)
  - ORM cascade on `Station → Playlist` declared in `engine/models.py`
    (commit `1302218`) — previously relying on the SQLite FK PRAGMA,
    which isn't always emitted per-connection by SQLAlchemy.
  - 9 backend tests covering happy paths, 404, cascade behaviour,
    and the leave-siblings-intact direction.
- **`web/src/lib/api.ts`** — `deletePlaylist(id)` and
  `deleteStation(slug)` typed clients. Plus a small `noContent`
  helper alongside `json` to skip the JSON parse on 204 responses
  (calling `.json()` on an empty body throws).
- **Per-row `✕ delete` link** in the playlists table on
  `/stations/[slug]`, peer to the existing `↻ regen`. Confirms with
  the trace_id fragment per D2; on 204 refetches playlists in place
  so the row vanishes; on error renders inline via
  `data-testid="delete-error"`. `data-testid="delete-row-{week}"`.
- **"Delete station →" button** in the page header, right-aligned,
  outlined-red turning red-fill on hover. The
  `nukeStation()` handler composes the D2 dialog text from page
  state — `playlists.length` for the count, `playlists.reduce`
  for the entry total — no extra fetch. On 204 navigates to `/`.
  `data-testid="delete-station"`.
- **`deleting` flag** disables both regen and delete buttons across
  all rows while a delete is in flight. One delete at a time.
- **11 new Vitest tests** (was 39 → 50): 4 on `lib/api.test.ts` for
  `deletePlaylist` and `deleteStation`, 7 on `detail.test.ts`
  covering both affordances' confirm-yes/no paths, computed-count
  copy, pluralisation, and inline error rendering.
- **4 new Playwright tests** (was 15 → 19) in `e2e/delete.spec.ts`:
  per-row delete and cancel, station nuke and cancel.

**Decisions in-flight that the §16.3 brief didn't pre-decide.**

- **Single shared `deleteError` slot, not two.** The plan had a
  per-row error and a station-level error as two separate concerns.
  In practice only one delete runs at a time — `deleting` ensures
  both buttons are disabled while one is in flight — so the second
  error slot was redundant. One `<p data-testid="delete-error">`
  renders the message regardless of which delete failed.
- **Per-row delete refetches via `getStationPlaylists()` rather
  than splicing local state.** Considered the local splice for
  speed, but a refetch is one extra round-trip and keeps the page
  in sync with anything else that changed server-side (regen from
  another tab, schema migrations, etc.). One less subtle bug to
  watch for. Same pattern any future "delete and stay" flow should
  follow.
- **Pluralisation handled inline in the dialog string.** Tempting
  to reach for a tiny `pluralize()` utility; for two cases ("1
  playlist" / "N playlists") it's overkill. The Vitest test
  explicitly asserts the singular form so a future regression
  (e.g. accidentally adding an `s`) shows up.
- **`.locale.toLocaleString()` for the entry-count number**
  (e.g. `36,030`) — small touch, but at 30k+ entries the comma
  matters for readability. The Vitest assertion uses a
  comma-tolerant regex so a future locale tweak doesn't break it.
- **Disk-export removal stayed out of scope per D3** but I noticed
  the Classics `out/rebirth-classics/001.xml` and `002.xml` files
  still exist after a hypothetical station nuke. Captured this in
  the punt table as the trigger for the disk-prune slice (§14.3
  follow-up).
- **Header layout uses flex with `align-items: flex-start`** so the
  nuke button sits flush with the top of `<h1>` rather than
  vertically-centred against the slug subtitle. Reads better; the
  button is *associated* with the station name, not the slug.

**Application logging vs structured audit log.** The §16.3 review
flagged the absence of any logging on the delete endpoints; the
follow-up commit added `logger.info(...)` calls on both
`delete_playlist` and `delete_station` (matching the existing
pattern in `engine/importer.py` and `engine/exporter.py`). This is
*application logging* — visible in the FastAPI server's stderr,
useful for debugging "did the delete actually fire?" — and is now
present. It is **not** a structured audit log: there's no
`delete_audit` table, no per-user attribution, no retention
policy. That heavier feature is the row in the punt table below
("Audit log of deletes — rejected — single-user"); it stays
rejected because Station Studio is a single-user tool and the
journey from "I deleted by mistake" goes via the regen / template
git-history paths, not via undeleting from an audit table.

**What's now reachable from the UI.** The full station-lifecycle
loop now closes on both ends: create → generate → view trace →
delete. Empty wizard test stations and accidentally-generated
playlists no longer require dropping to `curl -X DELETE`.

**Coverage shipping** — last measurement post-§16.3:

| Layer        | Coverage                                                     |
|--------------|--------------------------------------------------------------|
| Backend      | unchanged from §14.9 (no new backend code in this slice)     |
| Frontend Vitest | landing 100%, station-detail ~95%, wizard 95%, `lib/api` 100% |
| Playwright   | 19 specs across 7 files                                      |

**Deliberately punted from this slice** — match §16.3's pre-flight
punt list:

| Area                                | Status               | Trigger                              |
|-------------------------------------|----------------------|--------------------------------------|
| Trash / soft-delete with restore    | rejected             | Single-user; git history of templates is enough |
| Bulk delete (multiple weeks)        | not now              | Real ask appears                     |
| Typed-confirm ("type the slug")     | not now              | "I nuked the wrong station" report   |
| Disk-export removal flag            | §14.3 disk-prune     | Disk-prune slice lands               |
| Audit log of deletes                | rejected             | Single-user                          |
| Delete from trace viewer            | not now              | Felt friction with station-detail-only path |
| Concurrent-tab race / 409           | not now              | Multi-user / multi-tab use           |

**Implementation notes for future pick-up.**

- The `noContent` helper is `null`-returning by signature
  (`Promise<void>`) — for endpoints that *sometimes* return JSON
  and sometimes 204, you'd want a third helper or a discriminated
  return. None exist today; cross that bridge when one does.
- The `deleteRow` and `nukeStation` handlers both use the same
  `deleting` and `deleteError` state — adding a third delete
  flow (e.g. delete-from-trace-viewer if §16.3's punt-list
  trigger fires) should reuse them rather than introducing a
  third tier of state.
- The header `.station-header` flex assumes the title fits on one
  line. If a station gets a very long display name, the nuke
  button will wrap below — `white-space: nowrap` on the button
  prevents *its* text wrapping, but the layout still reflows.
  Acceptable; revisit if real names get long.
- The disk-export side: `out/{slug}/{week:03d}.xml` files are
  orphaned after a delete. The user's existing workflow notices
  this naturally (the file still appears in their iTunes folder)
  but a future disk-prune slice should clean up. §14.3 already
  has the policy ("No policy + .gitignore"); the prune endpoint
  was deferred there.

### 14.11 Disk-export button on trace viewer — what shipped

**What shipped (single commit, follow-on to §16.6's plan).**

The disk-export endpoint has lived since §14.2; this slice adds
the UI hookup that closes Classics's production loop (write to
the OneDrive/Dropbox folder feeding the Windows iTunes box).

- **`exportPlaylistToDisk(id)`** in `web/src/lib/api.ts`. Returns
  the typed `DiskExportResponse = {path, bytes}` matching the
  endpoint's JSON.
- **"Export to disk" button** in `/playlists/[id]`'s `.actions`
  strip, peer to the existing "Download iTunes XML" link. The
  download is a plain `<a href>` (browser handles
  Content-Disposition); disk-export needs JS so it's a proper
  `<button>` styled `.button` for visual parity.
  `data-testid="export-to-disk"`.
- **Inline status rendering** below the actions strip — green
  success (`"Wrote to /abs/.../cls/001.xml (847 KB)"`) or red
  error (`ApiError.message`). Two reactive vars
  (`diskExportStatus`, `diskExportError`) so the template can
  apply distinct styles cleanly. `data-testid="disk-export-status"`
  and `data-testid="disk-export-error"`.
- **Spinner during fetch** via `disabled={diskExporting}` on the
  button + a `'Writing…'` label swap. Disable prevents a
  double-click queueing two writes on a slow filesystem.
- **2 new Vitest cases** (was 51 → 53) on `lib/api.test.ts`:
  URL build + response shape on success, ApiError on 5xx (e.g.
  permission denied). Trace viewer is still 0% under Vitest by
  design (§14.5); not adding component tests here.
- **1 new Playwright case** (was 19 → 20) extending
  `e2e/trace-viewer.spec.ts`: click the button, assert the
  status line contains the station-slug + week-suffix path
  fragment and the "KB" units. The exact path varies (CWD-
  relative without configured dir) so the assertion is
  loose-but-sufficient.

**Decisions in-flight that the §16.6 brief didn't pre-decide.**

- **`.actions` became flex with a 0.5rem gap.** Previously a single
  `<a>` so display:inline-block was fine. Two siblings need spacing
  + alignment; flex is the cheapest way. The bottom margin moved
  from `.actions` to `.disk-status` so the success/error banner
  doesn't push the heading down when there's no banner showing.
- **`.button` rule got `:disabled` styling and `cursor: pointer`.**
  Before this slice the rule only targeted `<a>` (no disabled
  state, browser-default cursor). Adding the `<button>` sibling
  needed both. Same `.button` class still works for the anchor;
  CSS selectors don't care about element type.
- **Path string broken across long URLs via `word-break: break-all`.**
  Real paths on the user's machine include `/Users/raby/Library/
  CloudStorage/OneDrive-...` which without breakage stretch the
  layout. Word-break-all is ugly but reliable; better than a
  truncation that hides the actual path the user wants to verify.
- **Bytes formatted as KB via `Math.round(bytes / 1024)`.** "847 KB"
  reads better than "868990 bytes" for the verify-the-file-landed
  signal. No need for MB / GB cases — Classics XMLs are ~850 KB,
  even an extreme station won't hit MB territory.

**What's now reachable from the UI.** Classics's production loop —
generate → trace viewer → write to OneDrive/Dropbox-synced folder
→ Windows iTunes picks it up — is finally one-click from the
trace viewer. Before this, the last step was `curl -X POST`.

**Stay-out-of-scope candidates** (from §16.6's punt table):

| Area                                       | Status               | Trigger                                    |
|--------------------------------------------|----------------------|--------------------------------------------|
| Configure `playlist_output_dir` from the UI| not now              | User wants change without DB SQL           |
| Last-exported-when indicator               | §14.5 staleness item | Multi-week confusion about which is current |
| Disk-prune endpoint                        | §14.3                | `out/` clutter becomes painful             |
| Auto-export on regen                       | rejected             | §14.9 D5                                   |
| Multi-playlist batch ("export all weeks")  | not now              | Real ask                                   |
| Checksum / file-vs-DB verification         | not now              | Real bytes-don't-match-trace report        |
| Toast / notification framework             | rejected             | Inline status node is enough               |

**Implementation notes for future pick-up.**

- The status string `"Wrote to {path} ({KB} KB)"` is built
  inside `exportToDisk()` rather than a derived reactive — keeps
  the localisation surface minimal (one template literal, one
  number format). If/when the staleness indicator (§14.5)
  ships, it'll want a structured object (last-export-time +
  path) rather than a pre-formatted string; refactor at that
  time.
- The `<button class="button">` and `<a class="button">` share
  the same CSS rule. Browsers' default button styling
  (`font-family: -webkit-buttons`, sometimes a system tint) is
  reset via `font-family: inherit; border: none;`. If a future
  CSS reset / framework lands, double-check both elements
  still render identically.
- Errors from the endpoint can include long stack-trace-like
  bodies in dev (FastAPI without a reverse proxy returns the
  full traceback). The inline error renders the whole string;
  in production deployments behind nginx etc. the body would
  be the JSON `{"detail": "..."}` and the ApiError message
  parser would render it. Acceptable for v1.

### 14.12 Coverage gate in CI — what shipped

**Why this matters.** The §14.9 review caught a real
documentation-vs-actual mismatch: the `lib/api` line was claimed
at 100% but actual was 84.61% because `vi.mock('$lib/api', ...)`
in detail.test.ts was substituting the real module and the new
`generatePlaylist` body never executed under tests. The doc was
guesswork; nothing was watching for the silent drop. This slice
adds gates so that class of regression now breaks CI instead of
slipping past code review.

**What shipped.**

- **Backend gate (already existed, threshold tuned).** `make
  test-backend` runs `coverage run -m pytest` then
  `coverage report --fail-under=90`. The threshold was 95% but
  actual sits at 94.86% (the `bootstrap_classics` and
  `seed_stations` CLI scripts pull the aggregate down, ~66–69%
  each). 95% would have failed CI on a non-regression. Lowered
  to 90% — fires on a real drop, not on the resting state.
- **`requirements-dev.txt` pins `coverage>=7.0`.** Was
  installed transitively (luck) — CI was working but only
  because Python's dependency resolution happened to pull it.
- **`.coveragerc` excludes `tests/`** from the source set.
  Tests covering themselves is meaningless self-confirmation
  noise; previously inflated the aggregate from 95% to 98%.
- **Frontend gate (new).** `vite.config.js` gains
  `coverage.thresholds`. `make test-frontend-unit` now passes
  `--coverage` so the gate actually fires (without it, vitest
  skips the report and the threshold check is dead code).
- **Per-file `lines` threshold of 85%.** Every file in
  `coverage.include` has to clear it. Current files sit at
  93.75% / 95.06% / 100% / 100% — a §14.9 M1-style 15-point
  drop on any one file lands well below 85% and breaks CI.
- **Trace viewer excluded from coverage** —
  `playlists/[id]/+page.svelte` is Playwright-only by §14.5
  / §16.6 design. Its 0% Vitest coverage was dragging the
  aggregate from 95% lines to 73%; excluding it keeps the
  per-file gate meaningful for the files we *can* unit-test.
- **Aggregate gates for the noisier metrics**
  (`statements: 80, functions: 80, branches: 70`). Lines is
  the cleanest v8-over-Svelte signal; statements and branches
  count compile artifacts (reactive update points etc.) that
  the source can't directly exercise. Aggregate-only there;
  per-file would generate noise without catching real bugs.

**Decisions in flight.**

- **Why not gate per-file on every metric?** Tried first;
  failed immediately on landing (`+page.svelte`) which has
  100% lines but 36% branches because of Svelte-compiled
  reactive update branches that aren't source-level
  branches. Per-file lines is meaningful, per-file branches
  is whack-a-mole. The compromise — per-file lines, aggregate
  branches — catches the M1-style bug without forcing tail-
  chasing on the others.
- **Why 85% per-file lines and not 95%?** The wizard sits at
  95.06%; one untested branch addition lands below 95% and
  fires the gate as a false positive. 85% is the
  documentation-mismatch detection bar — anything below is
  "the doc lied" territory.
- **Why 90% backend aggregate and not 95%?** With `tests/`
  excluded the actual was 94.86%, displayed as "95%" by
  rounding. `--fail-under=95` happened to pass only because
  coverage rounds in the report. 90% gives a real cushion;
  the gate fires on regression, not on noise.
- **Backend per-file gates would be ideal but Python's
  `coverage` doesn't natively support them.** Tools like
  `pytest-coverage-decorator` can do per-file but adding
  another tool for one feature wasn't worth it. The aggregate
  gate suffices; the asymmetry between layers (frontend
  per-file, backend aggregate) is a tooling fact rather
  than a deliberate design.

**Verification — the gate actually fires.** Smoke-tested by
temporarily setting per-file lines to 99% (above wizard's
95.06%); CI exit code 1. Restored to 85%; CI exit code 0.

**What's now caught automatically.** Any future change that
silently drops a file's line coverage below 85%. The §14.9 M1
finding (lib/api 100%→84%) would have broken CI on the offending
commit, not been discovered three review rounds later.

**Stay-out-of-scope.**

| Area                                        | Status               | Trigger                              |
|---------------------------------------------|----------------------|--------------------------------------|
| Per-file backend gates                      | not now              | `coverage` adds the feature, or a third-party plugin earns its install |
| Mutation testing (mutmut)                   | §14.8                | "85% lines passes but the assertions don't actually check anything" report |
| Coverage badges / README integration        | not now              | External-user demand                 |
| Codecov / Coveralls integration             | not now              | Cross-PR diff visualisation becomes a felt need |
| Trace-viewer Vitest coverage                | §14.8 / §16.6 punt   | Trace viewer grows enough branching that Playwright is too slow for the cycle |

**Implementation notes for future pick-up.**

- Vitest's coverage thresholds support both flat (e.g.
  `lines: 85`) — applies to aggregate — and glob-keyed (e.g.
  `'src/**/*': { lines: 85 }`) — applies per-file. They can
  coexist in the same `thresholds` object; that's the
  current setup.
- The `coverage.exclude` glob pattern is matched against the
  file path relative to the project root — relative paths
  with leading `src/` work. SvelteKit-generated files in
  `.svelte-kit/` are auto-excluded by the v8 provider.
- `coverage report --fail-under` rounds the aggregate before
  comparing, which is why 94.86% passed `--fail-under=95`.
  Don't trust the rounded display; if you need a tight gate,
  inspect the precise figure.

### 14.13 Edit mode for stations — what shipped

**What shipped (3 commits, follow-on to §16.7's plan).**

The §16.7 brief committed all 10 decisions ahead of execution;
this section records what actually landed and the in-flight
surprises.

- **Backend (already shipped — no work this slice).** `PUT
  /api/stations/{slug}` from §14.7 + slug-immutable enforcement
  from `1302218`.
- **`updateStation(slug, payload)`** typed client in
  `web/src/lib/api.ts`. Mirrors `createStation`; doc-string
  flags slug-immutability + the iTunes Persistent ID
  consequence.
- **Extracted `<StationForm>`** at
  `web/src/lib/components/StationForm.svelte` (~445 LoC). All
  form state, client-side validation, and template-build logic
  live here. Props:
  `{mode: 'create'|'edit', initial?: Station, onSubmit: callback, cancelHref?}`.
  Both `/stations/new` and `/stations/[slug]/edit` use it; the
  parents own the API call (POST vs PUT) and the navigation
  destination.
- **Extracted `<LayoutFieldset>`** at
  `web/src/lib/components/LayoutFieldset.svelte`. The §16.2
  swap-point — when the visual block editor lands, both
  consumer routes (`/stations/new` and `/stations/[slug]/edit`)
  get drag-drop in a single change, with no per-route migration
  duplicated.

  **Caveat — this is not a literal one-component swap.**
  `<LayoutFieldset>` currently binds the form-input strings
  (`unitLabelsStr`, `rowStartOffsetsStr` — comma-separated) and
  `<StationForm>`'s `buildTemplate()` translates them to arrays
  on save. A drag-drop visual editor would naturally bind arrays
  directly. So §16.2's actual swap will involve:
    1. Replacing `<LayoutFieldset>` with `<VisualBlockEditor>`
       in `<StationForm>`'s markup
    2. Refactoring `<StationForm>`'s state to arrays
    3. Removing `parseStringList` / `parseIntList` from
       `buildTemplate()` and joining-back from `unpack()`

  None of that work is duplicated across consumer routes — that's
  the value of the extraction. But it's not a single-line edit.
  The string-vs-array shape choice is deliberate for now (the
  comma-separated input UX matches the wizard's other text inputs)
  and the cleanup naturally lands with §16.2.

  Style rules previously duplicated from `<StationForm>`'s
  `<style>` were extracted to `_form-shared.css` (review S2)
  scoped under `.station-form` so both components reuse them
  via CSS ancestor matching, not via duplication.
- **`/stations/[slug]/edit` route** — loads via `getStation(slug)`,
  mounts `<StationForm mode="edit" initial={station}>`, owns
  the PUT handler. Loading + 404 paths covered.
- **"Edit station →" button** on the station-detail header,
  left of the existing "Delete station →". Visual order per
  D1: edit first (safe, dark-slate filled), delete last
  (destructive, outlined-red). It's a plain `<a href>` (not
  `<button>`) because edit is a navigation, not an action —
  let the browser handle right-click / middle-click /
  open-in-new-tab.
- **Slug input is `readonly` in edit mode** with explicit hint
  copy explaining *why* it's immutable (embedded in iTunes
  Persistent IDs and disk paths). Greyed-out background
  (`#f4f4f4`, `cursor: not-allowed`) reinforces the signal.
  D3 failsafe: backend rejects slug changes with 400; the
  form just doesn't allow trying.
- **Live JSON preview** (D6, both modes) — `<details>`
  disclosure showing `JSON.stringify(buildTemplate(), null, 2)`
  in a styled `<pre>` block. Closes the §14.7 punt.
- **Diff disclosure** (D7, edit-mode only) — header shows
  "what's changing" + a pill (`unsaved` red, `no changes`
  grey). Body groups changes under Identity / Sources /
  Cursor / Layout headings. Backed by the
  `templateDiff(before, after)` pure function in
  `web/src/lib/templateDiff.ts`. 10 Vitest cases pin the
  comparator's contract: section ordering, identity changes,
  RNG flip, source add/remove/modify with name-based matching
  (reorder is no-diff), cursor scalar changes, layout array
  changes, row_template compact summary, malformed-template
  resilience.
- **"Reset to saved" button** (D8, edit-mode only) — disabled
  when clean, prompts a `confirm()` when dirty, restores the
  form via `unpack(structuredClone(initial))`. Shallow copy
  via spread/map alone left nested arrays shared with
  `initial`; subsequent edits mutated the original. The
  `structuredClone` is the deep-copy fix.

**Decisions in flight that the §16.7 brief didn't pre-decide.**

- **Edit route excluded from Vitest coverage.** Mirrors the
  trace viewer pattern (§14.5 / §16.6). The route is a thin
  parent (load → mount component → callback) whose behaviour
  is pinned by Vitest tests on `<StationForm>` (8 cases) and
  Playwright tests on the round-trip (5 cases). Adding
  Vitest mounts of the route itself would only re-test the
  parent wiring at the cost of mocking `$app/stores` etc.
  Excluded in `vite.config.js` `coverage.exclude` to keep
  the per-file gate meaningful.
- **The dirty-pill targets `.dirty-pill` / `.clean-pill`
  classes for tests, not the text content.** First write of
  the test used `screen.getByText(/no changes/i)` — but the
  body has "No changes yet — everything matches" prose too,
  so it matched twice and threw "Found multiple". Switched
  to class-targeted `container.querySelector('.clean-pill')`
  which is the correct way to disambiguate visual states
  that share copy.
- **`structuredClone` over `Object.assign({}, initial)` for
  reset.** Caught during dev: shallow-cloning `initial` and
  reassigning the form variables left `sources` / `rowTemplate`
  pointing at the same arrays as `initial`, so subsequent
  edits mutated the loaded reference. After a reset, the
  diff thought everything was clean even though the form
  showed edits. `structuredClone` deep-copies and resolves
  it cleanly. Comment in `resetToSaved()` flags this so the
  next person doesn't repeat.
- **JSON-preview pre-tag uses `white-space: pre`** instead of
  `pre-wrap`. The output is small (~20 lines for Classics)
  and reads better with horizontal scroll than wrap-on-comma
  garbage. If templates ever grow huge enough that this
  matters, switch to `pre-wrap` and accept the wrapping.
- **Diff "before"/"after" colour palette uses Material-style
  red (`#b71c1c`) + green (`#1b5e20`)** matching the
  §14.11 disk-status palette. This means we now have two
  established palettes in the codebase (the old crimson /
  `#666` and Material), which §16.5's a11y pass should
  consolidate. Noted there.

**What's now reachable from the UI.** The full station
lifecycle — create → edit → generate → view → delete — is
finally reachable end-to-end without dropping to JSON-by-hand
or curl. Combined with §16.6 (disk-export button), the
Classics production loop now flows:

```
new wizard → save → station detail → generate → trace viewer
       ↓                    ↑                 ↓
       └─── edit ───────────┘            export to disk
```

…all from the browser, no shell.

**Stay-out-of-scope candidates** (re-stated for traceability):

| Area                                              | Status              | Trigger                                      |
|---------------------------------------------------|---------------------|----------------------------------------------|
| Visual block editor                               | §16.2               | Real second-station with awkward layout      |
| `beforeNavigate` unsaved-changes warning          | §14.7 punt          | "I lost my edits" report                     |
| Draft autosave                                    | §14.7 punt          | Same                                         |
| Confirm on save with existing playlists           | not now             | "Old playlists didn't update" report         |
| Per-field error association (a11y)                | §16.5               | A11y pass runs                               |
| Auto-regenerate playlists on template change      | rejected            | Generation is a visible action (§16.1 D7)    |

**Implementation notes for future pick-up.**

- The `unpack()` helper in `<StationForm>` is the
  template→form-state translation. If the canonical template
  shape grows a field, update `unpack` *and* `buildTemplate`
  in lockstep — they're the inverse-pair.
- The diff comparator's `Sources` matching is by `name`. If
  a future edit allows renaming a source, that rename will
  appear as `(removed)` + `(added)` rather than a single
  rename row. Acceptable for v1; switch to a smarter match
  (e.g. by colour-stable identity) if rename UX matters.
- `<LayoutFieldset>` style rules are duplicated from
  `<StationForm>` because of Svelte scoping. Comment in both
  files notes "tweak in StationForm, mirror here". When
  §16.2 replaces LayoutFieldset wholesale that duplication
  goes away.
- The hydration sentinel (`data-wizard-ready` on `<html>`)
  moved from the wizard route into `<StationForm>`'s
  `onMount`. Both consumers benefit and the existing
  Playwright spec keeps working unchanged.
- `<StationForm>` styles aren't scoped tighter than they
  need to be — generic selectors like `fieldset { ... }` or
  `label { ... }` will hit elements rendered by the parent
  route too. *(Update: addressed in the §16.7 review fix
  pass — generic selectors moved into `_form-shared.css`
  scoped under `.station-form`. See review S1+S2 in commit
  `79f1ea0`.)*

### 14.14 Trace viewer Vitest backfill — what shipped

**Why.** The trace viewer (`/playlists/[id]/+page.svelte`) was
the last 0%-Vitest file in the project, deliberately punted by
§14.5 / §16.6 with the rationale "Playwright covers it; jsdom
doesn't render layout faithfully". The §14.8 "Test follow-ups"
punt table named it explicitly. Closing the gap now means a
component-level regression in the trace viewer fails fast
(vitest milliseconds) instead of slow (Playwright seconds, only
on full e2e runs).

**What shipped (single commit).**

- **`web/src/routes/playlists/[id]/trace-viewer.test.ts`** —
  10 Vitest cases covering:
  - **Render branches**: invalid playlist id (`/playlists/abc`),
    loading state, ApiError 404 from `getTrace`, non-ApiError
    failure stringification, populated render with header /
    meta / row groups / slot rows, rng:on with seed display,
    row-collapse toggle.
  - **Disk-export wiring (§16.6)**: success path renders the
    path + KB size, sub-KB sizes render as raw bytes (S1 from
    the §16.6 review), failure renders inline with no nav
    side-effect.
- **`vite.config.js`** removes the trace viewer from
  `coverage.exclude`. Per-file lines threshold (≥85%) now fires
  for it; current coverage is 96.49% lines, comfortable
  headroom.

**Decisions in flight.**

- **Mock the `$app/stores` page store the same way as
  `detail.test.ts`.** Hoisted holder + `setId(s)` helper so
  individual tests can swap the route param. Same shape as
  the existing pattern; nothing new.
- **No dedicated test for the chip colour rendering.** The
  `chipStyle()` function builds an inline `style="..."` string
  from the `sourceColours` lookup — testing the actual colour
  in jsdom would assert on the inline-style attribute, not on
  the visible colour, which is what we'd actually care about.
  Deferred to Playwright (where the `.chip` class exists in
  the existing `trace-viewer.spec.ts`). The fallback grey
  `#888` for unknown sources is similarly visual-only.
- **The row-collapse test walks up via `.closest('header')`.**
  `getByRole('button', { name: 'Mon' })` would match because
  the header has `role="button"`, but the matcher's name
  strategy is brittle when the header has multiple text
  children (caret + unit-label + row-meta). Walking up from
  the unit-label text is more robust.
- **The §16.6 disk-export tests assert via `data-testid`,
  not text content.** Status / error elements both render text
  that includes the path; testid-targeting avoids the multiple-
  matches problem when the path itself contains words like
  "error" or "status".

**What stays Playwright-only by design.**

- **CSS / layout assertions** — chip colours, table widths,
  scroll behaviour, mobile-narrow CSS (§14.5 punt). jsdom
  doesn't compute styles faithfully; assertions in unit tests
  would test the wrong thing. The existing
  `e2e/trace-viewer.spec.ts` covers the visual contract.
- **The browser-download `<a href>`** — handled entirely by
  the browser's `Content-Disposition` machinery; no JS to
  unit-test.

**Coverage shipping** — last measurement post-§14.14:

| File                                 | Lines  | Trend                          |
|--------------------------------------|--------|--------------------------------|
| `playlists/[id]/+page.svelte`        | 96.49% | was 0% (excluded); now gated   |
| Aggregate                            | 96.16% | unchanged-ish (small uptick)   |

**Implementation notes for future pick-up.**

- The fixture stations (`stubStation`, `stubPlaylist`,
  `stubTrace`) are deliberately small — three slots across two
  rows, two source pools. Enough to exercise grouping +
  collapse + rng-flag rendering. Don't grow them unless a new
  branch genuinely needs more shape.
- The disk-export test for sub-KB sizes (47 bytes → "47
  bytes") is the unit-level pin for §16.6's S1 review fix.
  If a future change reverts the bytes-vs-KB branching in
  `formatBytes()`, this test fails; the formatter stays
  honest.
- If §16.4 (trace versioning) ever lands and the viewer
  grows a version picker, expect to mock another endpoint
  and add per-version render branches here. The hoisted
  mock holder pattern accommodates that without restructure.

### 14.15 Trace viewer search / filter — what shipped

**What shipped (2 commits, follow-on to §16.8's plan).**

The §16.8 brief committed all 10 decisions ahead of execution;
this section records what actually landed and the in-flight
surprises.

- **Filter bar** between the "Your working" header and the row
  sections (D4). Three inputs that AND together (D3): free-text
  search across track-name + artist (case-insensitive), source
  dropdown, strategy dropdown.
- **Source / strategy options derived from trace content** —
  `[...new Set(trace.slots.map((s) => s.source))].sort()` — not
  from the station template. A trace can reference pools the
  template no longer lists (renamed / removed); deriving from
  the trace shows what's actually present.
- **Match counter** ("Showing N of M slots") with
  `toLocaleString()` for thousands separators (D10) — at 3,010
  slots the comma matters for readability.
- **Clear button** hidden when no filters active; the dedicated
  bar button and the empty-state's recovery button share one
  handler — single source of truth for "reset filter state".
- **Row sections with 0 matching slots hide entirely** (D8).
- **Empty-state copy** ("No slots match the current filters")
  with an inline clear button when filters yield zero matches.
- **No URL state, no debounce, no sticky bar** (D5 / D6 / D4
  end-note). Each is on the punt list with explicit triggers.
- **10 new Vitest cases** (was 16 → 26 on `trace.test.ts`):
  filter bar options derivation, counter formatting, search by
  track name, search case-insensitive against artist, source
  filter, AND combinations, row-hide D8, empty state, atomic
  clear, clear-button hidden when no filters.
- **1 new Playwright case** in `e2e/trace-viewer.spec.ts`:
  type → counter goes 0/N → empty state visible → clear →
  counter restored. Round-trip pin.

**Decisions in flight that the §16.8 brief didn't pre-decide.**

- **The filter predicate must be inlined inside the
  reactive `$:` block, NOT extracted to a named function.**
  This was the only real surprise — caught when 6 of the new
  Vitest cases failed on first run with "expected element not
  to be in the document".

  Svelte's `$:` dependency tracker introspects the AST of the
  reactive block, not the bodies of functions it calls. With
  a named `matchesFilters(slot)` reading `searchQuery` /
  `sourceFilter` / `strategyFilter` via closure, the compiler
  saw only the function name and `rowGroups` — searchQuery
  changes wouldn't re-fire the reactive, so the filtered
  groups stayed stale.

  Inlining the predicate inside an IIFE inside the `$:`
  block makes every variable read visible to the dependency
  tracker:
  ```ts
  $: filteredRowGroups = (() => {
    const q = searchQuery.trim().toLowerCase();
    return rowGroups.map(...).filter(...);
  })();
  ```

  The source has a long comment flagging this trap so the
  next contributor doesn't repeat. Worth knowing for ANY
  future Svelte 4 reactive block that depends on multiple
  inputs via a helper function.

- **`fireEvent.input` returns a promise that awaits Svelte's
  tick.** The Vitest cases use `await fireEvent.input(...)`
  and the assertion that follows reads the post-update DOM.
  Was concerned this wouldn't work; it does cleanly. Same
  pattern works for `fireEvent.change` on the dropdowns.

- **The `<select>` "All sources" option uses empty string
  as its value, not a sentinel like `"*"`.** Empty matches
  the `if (sourceFilter && ...)` predicate truthiness check
  — falsy = no constraint. Cleaner than a sentinel; would
  bite if a real source were ever named `""` (it can't be,
  source-name validation rejects empty).

**Coverage shipping** — last measurement post-§14.15:

| File / metric                        | Before | After  |
|--------------------------------------|--------|--------|
| `playlists/[id]/+page.svelte` lines  | 97.36% | 98.08% |
| `playlists/[id]/+page.svelte` branches | 54.37% | 83.63% |
| Aggregate branches                   | 71.60% | 79.16% |
| Aggregate lines                      | 96.32% | 96.56% |

Branch coverage is dramatically better — the filter code adds
many small branches all of which are exercised by the new
Vitest cases. The aggregate-branches figure is now well above
the 70% gate (had been 1.6pp above; now 9.16pp).

**Stay-out-of-scope candidates** (re-stated for traceability;
all already in §16.8's punt table):

| Area                                        | Status              |
|---------------------------------------------|---------------------|
| URL state / shareable filter links          | not now (§16.4 prereq) |
| Slot-number / pool-index range filters      | not now             |
| Highlight-in-place mode (vs hide)           | not now             |
| Saved filter presets                        | not now             |
| Regex search                                | rejected            |
| Export-filtered-view                        | rejected            |
| Cross-week search                           | not now (§16.4)     |
| Debounced search                            | not now             |
| Sticky filter bar                           | not now             |
| Find-rejected-candidates                    | §11 v1.1            |

**Implementation notes for future pick-up.**

- The dependency-tracking trap (named function vs inlined
  predicate) is documented in the source. If you add more
  filters and want to factor them out for readability, do
  so by computing the *result* first (e.g. compute a
  `Set<number>` of matching slot ids ahead of the reactive)
  rather than calling a predicate function from inside the
  reactive.
- The empty-state copy is intentionally short. If the punt
  list's "saved filter presets" lands, the empty-state could
  grow a "Did you mean…?" suggestion based on common preset
  matches.
- The new Playwright test creates `trv-filter` station and
  generates one playlist. Like the disk-export test, it leaves
  `<repo>/out/trv-filter/001.xml` behind on every run (the
  same gitignored side-effect documented in the §14.11
  disk-export spec comment).
- If Vitest aggregate-branches ever drops below 70% again,
  the first place to look is whether a new file was added
  with low branch coverage — adding one more inlined-predicate
  reactive (like the filter) is one of the fastest ways to
  lift the figure.

### 14.16 Trace viewer multi-week prev/next nav — what shipped

**What shipped (single commit, follow-on to §16.9's plan).**

The §16.9 brief committed all 7 decisions ahead of execution;
this section records what actually landed and the in-flight
surprises.

- **`getStationPlaylists(slug)` fetched in the trace viewer**
  alongside the existing trace / playlist / station calls.
  D2's "client-side derivation" — no backend reshape; the
  list query was already an existing endpoint.
- **`siblings` reactive** (IIFE inside `$:` per the §14.15
  trap-doc) returns `{ prev, next }` as `PlaylistSummary | null`.
  Sort by week, find current's index, derive neighbours.
  Defensive against missing-current-from-list (returns both
  null → nav suppressed entirely per the single-week branch).
- **Nav strip** between the `<h1>` and the meta `<dl>`. Renders
  `← week NNN` / `week NNN →` with zero-padded weeks (D4),
  inside `<nav aria-label="adjacent weeks">` for assistive tech.
  Disabled boundaries render as styled `<span>`s, not anchors-
  with-no-href — keeps focus order sane and avoids "navigate
  to nowhere" links.
- **`<a href>` not `<button>`** (D5) — same rationale as §16.7's
  "Edit station →": browser middle-click / open-in-new-tab.
- **Single-week station: nav suppressed entirely** via
  `{#if siblings.prev || siblings.next}`. Reads cleaner than
  rendering both placeholders for a one-week station.
- **5 new Vitest cases** in `trace.test.ts`: middle week (both
  hrefs correct), first week (prev disabled), last week (next
  disabled), single-week station (no nav at all), out-of-order
  list (sort happens client-side). 33 trace tests total
  (was 28).
- **1 new Playwright case** in `trace-viewer.spec.ts`: real
  backend, generate weeks 1+2, navigate from week 1 to week 2
  via the next link, assert prev now points back to week 1
  and next is the disabled placeholder.

**Decisions in flight that the §16.9 brief didn't pre-decide.**

- **The `onMount` → reactive-watch refactor was forced.**
  This was the load-bearing surprise. SvelteKit reuses the
  same `+page.svelte` instance when navigating between sibling
  routes (`/playlists/3` → `/playlists/4` is the same `[id]`
  route), so `onMount` only fires once on initial load. The
  fetched data stays from the first render even though
  `$page.params.id` updates. Caught by the Playwright spec
  immediately — clicking next changed the URL but
  `week-nav-prev` didn't update.

  Fix: extracted the fetch logic into `loadAll(playlistId)`
  and replaced `onMount(...)` with a reactive watcher
  `$: if (!isNaN(id)) void loadAll(id);`. Fires once on
  initial mount AND on every subsequent id change. The
  `void` annotation acknowledges the dropped promise; errors
  flow into `error` via `loadAll`'s own catch.

  This pattern is the right answer for any future
  same-route-different-id navigation (e.g. when §16.4's
  trace versioning adds `?v=N` and back/forward between
  versions becomes a thing). Worth knowing for the next
  contributor who hits the trap.

- **Disabled state rendered as `<span>` not `<a>`.** Two
  alternatives considered: `<a>` with no `href` attribute
  (focusable but unnavigable — confusing for keyboard users)
  or `<a aria-disabled="true">` (still focusable, still
  technically a link). `<span class="disabled">` keeps focus
  order honest — boundaries skip past the disabled placeholder
  on Tab.

- **The placeholder copy is "← (first week)" / "(last week) →".**
  Considered "← week 000" but that reads as a real week.
  Considered just hiding the disabled side, but losing one
  side of the nav strip looks broken. Showing parens-wrapped
  context is the cheapest "this doesn't go anywhere" signal.

- **`getStationPlaylistsMock.mockResolvedValue([])` default in
  beforeEach** so existing tests that don't seed the mock
  don't crash. The page's `stationPlaylists.length` reactive
  would throw on `undefined.length` otherwise. The §16.9 nav
  tests override with `mockResolvedValueOnce([...])`. Pattern
  is generic — apply to any new mock that the page calls
  unconditionally.

**Coverage shipping** — last measurement post-§14.16:

| File / metric                          | Before | After  |
|----------------------------------------|--------|--------|
| `playlists/[id]/+page.svelte` lines    | 98.08% | ~98%   |
| `playlists/[id]/+page.svelte` branches | 83.63% | ~85%   |
| Aggregate lines                        | 96.56% | ~96.5% |
| Aggregate branches                     | 79.16% | ~80%   |

Coverage stays comfortably above gates; new derivation has
many small branches all exercised by the new Vitest cases.

**Stay-out-of-scope candidates** (re-stated for traceability;
all already in §16.9's punt table):

| Area                                              | Status              |
|---------------------------------------------------|---------------------|
| Side-by-side multi-week comparison                | §14.5 / §16.4       |
| Slot-level diff across weeks                      | §16.4               |
| Keyboard shortcuts (j/k, arrows)                  | not now             |
| Position indicator ("Week 3 of 12")               | not now             |
| Cross-station navigation                          | rejected            |

**Implementation notes for future pick-up.**

- The `loadAll` + reactive-watch pattern is the project's
  blueprint for any same-route-different-id navigation. If
  §16.4 (trace versioning) lands and `/playlists/3?v=2` →
  `/playlists/3?v=3` becomes a thing, fold the version into
  `loadAll`'s args and add it to the reactive deps. Don't
  go back to onMount-only — bug class re-emerges.
- **Filter state (§16.8) is intentionally cross-week.**
  `searchQuery` / `sourceFilter` / `strategyFilter` aren't
  reset by `loadAll`. Useful — "find Bohemian across all
  my weeks" is a real workflow. May surprise users expecting
  filter-bar reset on context change. If "I keep getting
  confused by leftover filters" reports come in, add a
  reset call inside `loadAll` (or pin to a station-change,
  not a week-change). The `collapsed[]` per-row state is
  similarly cross-week — same reasoning.
- **Atomic-swap pattern in `loadAll` (review N3).** Initial
  loads still show the `<p>Loading…</p>` branch (when
  `trace === null`). Subsequent navigations skip the loading
  flag entirely — stale content stays visible while the new
  fetches run, then all component state swaps in one tick at
  the end of the try block. Avoids the half-state flash
  where the header reads "week 4" but the rows are still
  from week 3. Generation tracking (S1) cancels the swap if
  a newer invocation has bumped the counter.
- The `siblings` reactive's defensive "current not in list"
  case (returns both null) handles a malformed playlist list
  without crashing the page. Untested directly but exercised
  by the single-week test (current is in the list of length 1
  but no siblings exist either side). If a real "current
  missing" report comes in, add a test seeded with siblings
  that don't include the current playlist id.
- The nav copy uses `String(week).padStart(3, '0')` matching
  the existing h1 week-badge rendering. If the project ever
  drops zero-padding on week numbers, search for that
  literal everywhere — it appears in the h1, the nav, and
  the station-detail playlists table.
- The `trv-nav` station leaves `<repo>/out/trv-nav/00{1,2}.xml`
  on disk via the implicit disk-export side-effect of the
  fixture's tiny library. Same gitignored footprint as the
  §14.11 disk-export spec; no cleanup required.

### 14.17 Alembic baseline — what shipped (§17 Phase A #8)

**Why.** `Base.metadata.create_all()` handles initial DB creation
but doesn't track schema changes. Any non-additive change (column
type swap, drop, rename) is impossible to deploy cleanly without
either hand-written SQL or a migration tool. §17 Phase A names
this as the cheapest unblock — the first migration is a no-op
baseline; future schema work (§16.4 trace versioning, §17 #5
excluded-candidates, §17 #2 weighted's `Track.genre_tags`) hangs
off it.

**What shipped (single commit).**

- **`alembic>=1.13` pinned in `requirements.txt`** (runtime dep
  rather than dev-only — `make migrate` is a deploy op, not a
  test-only op).
- **`alembic.ini` + `migrations/env.py` configured** to use the
  project's `Base` from `engine/models.py` and the same DB-URL
  resolution as `engine/database.py` (env var
  `STATION_STUDIO_DB_URL` falling back to
  `sqlite:///station_studio.db`).
- **SQLite batch mode enabled** in env.py
  (`render_as_batch=True` for sqlite URLs). Critical for any
  future migration that isn't purely additive — SQLite rejects
  most ALTER TABLE flavours, batch mode wraps them in
  rename+rebuild transparently.
- **Baseline migration `0f76cfb45e8d_baseline.py`** generated
  via `--autogenerate` against an empty DB. Captures every
  table, index, FK cascade and unique constraint from
  `engine/models.py`. Verified: `alembic upgrade head` on a
  fresh DB produces a byte-identical schema to
  `Base.metadata.create_all()` (only diff is Alembic's own
  `alembic_version` tracking table, which is the point).
- **Make targets**: `make migrate` (apply pending migrations),
  `make alembic-stamp` (one-time op for existing dev DBs to
  mark them at-baseline without re-running the create-all),
  `make alembic-revision msg="..."` (autogenerate a new
  revision against the live DB), `make alembic-current`
  (which revision is the DB at?).

**Decisions in flight.**

- **`create_all()` is NOT removed from the codebase.** Tests
  still call it (it's faster than running migrations on every
  test run), `bootstrap_classics.py` still calls it for the
  init step. Alembic is *additive* infrastructure — it tracks
  deltas going forward, doesn't replace the create-all path.
  The intent: `create_all` for fresh-DB creation, `migrate`
  for deltas. Tests use `create_all`; production uses
  `create_all` then `alembic stamp head`; future schema
  changes use real revisions.
- **`render_as_batch=True` is gated on `DB_URL.startswith('sqlite')`**
  — no-op for non-SQLite if the project ever migrates off.
  Cheap defensive check; alembic ignores the flag for
  backends that support real ALTER.
- **Baseline migration uses the auto-generated hash filename
  (`0f76cfb45e8d_baseline.py`) not a numeric `0001_baseline.py`.**
  Standard alembic practice; numeric prefixes create
  ordering-by-number expectations that break if migrations
  ever branch (rare but possible). The `revision` /
  `down_revision` fields inside the file are alembic's source
  of truth, not the filename.
- **No `__init__.py` in `migrations/` or `migrations/versions/`.**
  Alembic loads them dynamically; making them packages breaks
  alembic's loader. The directory IS in the project but is
  NOT a Python package.
- **Alembic-generated `__pycache__` in `migrations/versions/`**
  is gitignored via the existing `__pycache__/` rule.

**Stay-out-of-scope.**

| Area                                     | Status      | Trigger                                  |
|------------------------------------------|-------------|------------------------------------------|
| Replace `create_all` with `upgrade head` | not now     | First time `create_all` and `migrate` drift |
| Auto-run migrate on `run.py` startup     | rejected    | Couples app boot to schema state; surprising on rollback |
| Migration smoke-test in CI               | later       | First non-trivial migration ships        |
| Online migrations (no downtime)          | n/a         | Single-user; downtime is fine            |
| Data migrations alongside schema         | as needed   | First migration that needs to backfill   |

**Implementation notes for future schema changes.**

- **Adding a column**: run `make alembic-revision msg="..."`
  to autogenerate against the live DB; review the output;
  commit. Apply via `make migrate`. SQLite batch mode handles
  it.
- **Dropping a column**: same flow, but the autogen output
  uses `op.drop_column` inside a `batch_alter_table`. Manual
  review essential — SQLite's batch rebuild can be slow on
  large tables.
- **Renaming a column**: alembic's autogenerate sees this as
  drop + add, losing data. Hand-edit the migration to use
  `op.alter_column(..., new_column_name=...)` inside
  `batch_alter_table`. Standard alembic gotcha; documented in
  alembic's own docs.
- **Backfilling data**: separate the schema-shape migration
  from the data-fill migration if possible. Schema first,
  then a data migration that uses `op.execute` or the
  ORM session via `op.get_bind()`.
- **The §16.4 trace-versioning slice will produce the first
  real revision.** Expect a `playlist_trace` table change
  (move `slots` JSON to a new `playlist_trace_version` table
  with a version_no column). Will exercise SQLite batch mode
  in earnest.
- **Tests don't run migrations.** `tests/conftest.py` calls
  `Base.metadata.create_all` directly. If a future migration
  depends on data ordering or specific column defaults that
  diverge from the ORM declaration, tests would silently
  pass against a different schema than production.
  *(Update per §17 review S2: a CI smoke job now runs
  `make migration-parity` after `make test-backend` —
  `scripts/check_migration_parity.py` does a structural
  comparison via SQLAlchemy's Inspector. Catches the
  "added a model class but forgot to alembic-revision"
  regression class. Structural rather than text-diff
  because alembic and create_all order CREATE TABLE
  constraints differently — same content, different
  declaration order. The Inspector normalises by extracting
  columns / pk / FKs / uniques / indexes as sorted tuples.)*

### 14.18 Backup / restore tooling — what shipped (§17 Phase A #9)

**Why.** Station Studio is single-user, but the data it holds
is precious. One accidental `DELETE /api/stations/cls` (now
that §16.3 made deletion a one-click affordance) and 12+
weeks of generated playlists vanish. The trace JSON alone is
~150 MB after a season; regenerating from sources is
deterministic but slow. A 5-line Makefile target removes that
class of "I just lost everything" event.

**What shipped (single commit).**

- **`make backup`** — uses `sqlite3`'s `.backup` command,
  which is **WAL-aware** (safe to run while uvicorn is using
  the DB). Output: `backups/YYYY-MM-DD-HHMMSS.db`. Plain
  `cp` of the live DB risks catching half-flushed WAL state;
  `.backup` checkpoints the WAL into the snapshot file
  transparently.
- **`make backups`** — lists existing backups newest-first
  (`ls -lht`). Empty-state copy: "No backups yet — run
  'make backup'."
- **`make restore date=YYYY-MM-DD-HHMMSS`** — three steps:
  - **Pre-restore safety copy** of the current DB to
    `backups/pre-restore-YYYY-MM-DD-HHMMSS.db` (so the
    restore is itself recoverable if it was the wrong one).
  - **Clear stale `-wal` / `-shm` files** — restoring just
    the `.db` file with companion WAL state from a different
    schema version corrupts. Removed before the cp.
  - **`cp backup → DB_FILE`** — straight overwrite. User
    must stop `run.py` first; concurrent writes during
    restore corrupt. The output reminds them to restart.
- **`backups/` directory gitignored.** Backup files contain
  user data; never repo content.
- **`DB_FILE` configurable** via `make backup DB_FILE=...`
  for ad-hoc inspection of e.g. Playwright per-PID DBs.
  Default is `station_studio.db`.

**Decisions in flight.**

- **`sqlite3 .backup` over `cp`.** SQLite WAL mode is enabled
  in `engine/database.py:25`; while uvicorn is running, the
  authoritative state is across `.db` + `.db-wal` + `.db-shm`.
  Naive `cp station_studio.db backups/...` skips the WAL
  data and produces an inconsistent snapshot. `.backup` runs
  a hot consistency-checkpoint into a single output file.
- **Pre-restore safety copy.** Asymmetric: backup is hot,
  restore is cold (must stop `run.py`). The pre-restore copy
  is hot — captures whatever state the DB is in at the
  moment of the restore call, even if uvicorn is running.
  If the user runs `make restore` against an actively-
  written DB, the pre-restore copy is still consistent;
  only the post-cp result is risky (and only if uvicorn
  keeps writing during the cp). The output message says to
  stop run.py.
- **`restore` clears `-wal` / `-shm` before the cp.** Stale
  WAL files from before the restore can carry pages that
  belong to the now-replaced schema. Even with the same
  schema version, the WAL pages would re-apply on the next
  open and corrupt the freshly-restored snapshot. Cheapest
  fix is `rm -f` of the companions; SQLite recreates them
  empty on first open.
- **No automatic backup before destructive ops.** Considered:
  hooking `make backup` into a pre-commit, or auto-backup
  before every `DELETE` API call. Both rejected — the
  former is annoying for non-data commits, the latter
  couples the API path to filesystem ops. The convention
  is "run `make backup` before risky ops"; the §16.3
  delete-station confirm dialog could grow a "tip: run
  `make backup` first" mention if users start losing data
  to it.
- **Hash-named backups (`YYYY-MM-DD-HHMMSS.db`) over
  rolling `latest.db` or numbered `001.db` style.** The
  timestamp lets users see which backup is which without
  inspecting; rolling files would lose context.
- **No retention policy / pruning.** Backup files
  accumulate indefinitely. Each is ~9 MB on the current
  Classics DB; even daily backups for a year = ~3.3 GB.
  Real but not pressing. If clutter becomes felt, a
  `make backup-prune` target could keep last-N + monthly
  archives. Currently: user manages the directory.

**Stay-out-of-scope.**

| Area                                    | Status      | Trigger                                  |
|-----------------------------------------|-------------|------------------------------------------|
| Automated backup-before-destructive-op  | rejected    | Couples API to filesystem; user-driven by convention |
| Backup-pruning / retention policy       | not now     | Disk usage becomes felt                  |
| Off-site / cloud backup                 | rejected    | Single-user; user can `cp` to wherever   |
| Restore from a backup taken on a different machine | not now | Slugs / paths in trace JSON could be machine-specific |
| Backup encryption                       | rejected    | Single-user; user encrypts the backups/ folder if needed |
| `make backup-and-tag-with-message`      | not now     | If someone wants annotated backups       |

**Implementation notes for future pick-up.**

- **Recovery story**: if `station_studio.db` is corrupted
  beyond restore, the *worst* case is "regenerate every
  playlist from sources". Templates live in `stations/*.json`
  (committed), source pools live in `from_user/*.xml`
  (re-importable), and generation is deterministic
  (`rng:off`). The trace JSON is the only truly-lost data;
  it can be regenerated but the original `trace_id` UUIDs
  are gone.
- **Backups should not be stamped at any Alembic revision.**
  When the user restores a backup taken at revision X and
  the code is now at revision Y, `make migrate` should run
  the deltas X→Y on the restored DB. The `alembic_version`
  table inside the backup file naturally carries X with it,
  so this works without any special handling — just
  remember to `make migrate` after restoring to a different
  code state.
- **The pre-restore safety copy** is named
  `pre-restore-YYYY-MM-DD-HHMMSS.db` not just
  `pre-restore.db` — concurrent restores within the same
  second are unlikely but possible (re-running the command
  after a misclick). The timestamp prevents
  silent-overwrite of the safety copy itself.
- **Cross-platform note**: the `date +%Y-%m-%d-%H%M%S`
  format is POSIX. Works on macOS (BSD date) and Linux
  (GNU date). Avoid `date -Iseconds` (GNU-only) or
  `date +%Y%m%dT%H%M%S` (less human-readable). The current
  format is the standard sortable timestamp.
- **`DB_FILE` is independent of `STATION_STUDIO_DB_URL`**
  (post-commit-review S1). The Alembic targets
  (`make migrate`, `make alembic-revision`) honour the env
  var via `migrations/env.py`; the backup/restore targets
  use the literal `DB_FILE` make-variable. A user with a
  non-default DB URL who runs `make backup` without also
  passing `DB_FILE=` would silently back up the wrong file.
  The Makefile comment now flags this; the chosen design
  is "explicit override only" rather than env-var
  carry-through (would need a SQLite-URL parser shim for
  marginal value at single-user scale).

### 14.19 GET /api/meta — what shipped (§17 Phase A #3)

**Why.** Four template-shaped option lists (`STRATEGY_OPTIONS`,
`RNG_OPTIONS`, `ROLE_OPTIONS`, `CADENCE_OPTIONS`) lived in the
frontend `<StationForm>` and `<LayoutFieldset>` components, each
with a "keep in sync with `engine/template.py`" comment. The
§14.7 punt named the trigger to extract: "third caller appears".
The visual block editor (§16.2) is that third caller. Landing
the endpoint now means §16.2 doesn't grow a fourth duplicate.

**What shipped (single commit).**

- **Backend** — `GET /api/meta` returns a sorted `MetaResponse`
  with the four lists. Lives in `api/routers/meta.py`; pulls
  the values from `engine/template.py`'s constants. The endpoint
  has no DB dependency — pure metadata over a route.
- **Three engine-side constant cleanups** (`engine/template.py`):
  - `SUPPORTED_ROLES = {"content", "reference"}` extracted from
    the inline check at line 161 (was a literal set in
    `_parse_source`).
  - `SUGGESTED_CADENCES = {"weekly"}` introduced as a forward-
    looking constant. Engine doesn't yet close-set-validate
    cadence (§11 v1.1 scheduler will likely tighten this); the
    frontend dropdown sources from this so it's the single
    source of truth even though the validator is permissive.
  - The role-validation error message now uses
    `sorted(SUPPORTED_ROLES)` instead of a hardcoded list,
    matching the strategy-validation error.
- **Frontend** — `getMeta()` typed client in
  `web/src/lib/api.ts`; module-cached promise loader
  in `web/src/lib/meta.ts` (`loadMeta()` + the test-only
  `_resetMetaCache()`). Both consumer routes
  (`/stations/new`, `/stations/[slug]/edit`) fetch meta in
  `onMount` and pass it as a `meta` prop into `<StationForm>`.
  The edit route parallels meta + station via `Promise.all`
  for the same reason §14.16 did so for the trace viewer
  (one round-trip latency for two independent fetches).
- **`<StationForm>` and `<LayoutFieldset>` accept `meta` /
  `strategies` as required props.** All three hardcoded
  constant blocks removed; the markup loops over the
  prop-supplied lists.
- **Tests**:
  - Backend: 2 new cases on `tests/test_api.py` —
    sorted-ness + parity with engine constants. The parity
    test catches the "contributor adds 'sequential' to
    `SUPPORTED_STRATEGIES` but forgets the meta endpoint"
    drift class.
  - Frontend: 2 new cases on `lib/api.test.ts` for `getMeta`
    URL build + ApiError on 5xx; new `lib/meta.test.ts`
    with 3 cases pinning the cache contract (single fetch
    across N callers, `_resetMetaCache` forces refetch,
    rejected fetch caches the rejection too — important
    contract documented in the test).
  - Existing `wizard.test.ts` updated to mock `$lib/meta`'s
    `loadMeta` returning a fixed META blob; first sync
    `getBy*` call per test became `await screen.findBy*`
    because the form renders only after meta resolves.
  - Existing `StationForm.test.ts` adds the `meta` fixture
    to every render call.

**Decisions in flight.**

- **Cadence stays open server-side, listed only in the meta
  response.** Considered closing the loop (`if cadence not in
  SUPPORTED_CADENCES: raise`). Rejected because the engine
  doesn't actually use the value — it's metadata, no
  scheduling semantics behind it yet. Naming the constant
  `SUGGESTED_CADENCES` rather than `SUPPORTED_CADENCES`
  signals the asymmetry: the others are validator-enforced,
  this is a UI-presentation set. When §11's scheduler v1.1
  lands, the constant gets renamed and the validator gets
  tightened in lockstep.
- **`Strategy` / `Role` / `Rng` types stay inline as string-
  literal unions** (`'pick' | 'window'`, etc.) inside the
  Svelte components. Svelte 4's `bind:value` narrowing works
  better with literal types than `string`. The runtime
  *values* come from the meta prop; the types stay tight at
  compile time. Slight asymmetry: the literals have to be
  updated in lockstep with the engine constants if a new
  primitive ever lands. The existing parity test (backend)
  catches the values; svelte-check would catch a missing
  literal-union update.
- **`<LayoutFieldset>`'s `strategies` prop has a default**
  (`['pick', 'window']`) so the component still renders if
  a parent forgets to wire it through. Defensive — the
  default mirrors current engine support, so degrades to
  status-quo rather than blank dropdown.
- **`loadMeta()` caches the rejection.** Important contract:
  if the user is offline at first paint, every subsequent
  `loadMeta()` call would otherwise hammer the backend.
  Caching the rejection is symmetric with caching the
  resolution. To retry, callers (or a future "Retry"
  button) call `_resetMetaCache()` before re-`loadMeta()`.
  Pinned in the meta.test.ts contract test.
- **The test-only `_resetMetaCache` is exported with a
  leading underscore** to signal "not part of the public
  API". Production code shouldn't reach for it; the only
  legitimate caller is the meta.test.ts `beforeEach`.

**Stay-out-of-scope.**

| Area                                          | Status      | Trigger                                  |
|-----------------------------------------------|-------------|------------------------------------------|
| Server-Sent Events for live meta updates      | rejected    | Constants don't change without a deploy  |
| Inline meta in HTML at SSR time               | not now     | After §14.5's `+page.ts` refactor (still punted) |
| Versioning the meta endpoint                  | not now     | First time the response shape needs a breaking change |
| Tightening cadence validation                 | §11 v1.1    | Scheduler lands and gives cadences semantics |
| Localised option labels                       | not now     | Real i18n requirement                    |
| Auto-retry of rejected loadMeta               | not now     | "Frequent flaky network" report          |

**Implementation notes for future pick-up.**

- **The §16.2 visual block editor will be the third route**
  using `<StationForm>` (or `<VisualBlockEditor>` over the
  same data model). It should `loadMeta()` the same way
  `/stations/new` and `/stations/[slug]/edit` do — the cache
  ensures no extra fetch even though it's a third call site.
- **A new engine primitive** (e.g. `weighted` per §17 #2)
  needs to be added in three places:
  1. `engine/template.py` `SUPPORTED_STRATEGIES`
  2. `engine/strategies/<name>.py` (the actual implementation)
  3. The Svelte literal union in `<StationForm>` /
     `<LayoutFieldset>` (`type Strategy = 'pick' | 'window'
     | 'weighted'`)
  Item 3 is the manual step. The backend parity test catches
  if (1) and (2) aren't aligned with the meta response;
  svelte-check catches if (3) doesn't include a value the
  meta sends. So: add the engine constant, fix the resulting
  svelte-check error, ship.
- **The `meta` prop is required** on `<StationForm>` (no
  default). Test-time fixtures construct one inline; runtime
  callers fetch via `loadMeta()`. This is the right rigidity
  — a missing meta means the component can't render dropdowns
  meaningfully, and TS catches it at build time.
- **Why module-level cache rather than a Svelte store**:
  considered; rejected. A store invites consumers to
  subscribe and re-fetch on subscription. The cache contract
  is "fetch exactly once per page session"; a plain promise
  enforces it without the subscribe/unsubscribe ceremony. If
  the meta ever needs to be reactive (e.g. a future admin
  panel adds new strategies at runtime), promote to a store
  then.
- **Hydration sentinel meaning broadened post-§14.19**
  (post-commit-review S3). Pre-§14.19, `<StationForm>`'s
  `onMount` set `data-wizard-ready` on `<html>` when the
  form was hydrated and ready to interact. Post-§14.19,
  `<StationForm>` only mounts after `loadMeta()` resolves —
  so the sentinel now means "meta loaded AND form hydrated
  AND ready to interact". Tests still work (they wait for
  the sentinel before interacting; the broader meaning is
  what they actually want), but if a future contributor reads
  §14.8's "Hydration sentinel beats `networkidle`" rationale
  expecting just-hydration semantics, the answer is no longer
  pure. If the meta endpoint ever gets slow, the sentinel-
  wait would extend Playwright runtime accordingly.

---

## 15. Status snapshot

A quick "where are we" for a future reader. Last updated at commit
`b44a3f4` — test-plan delivery + landing/detail backfill.

| Area                     | State                                                            |
|--------------------------|------------------------------------------------------------------|
| Engine                   | Shipped. `pick`, `window`. Cursor, trace, RNG on/off.            |
| Template validation      | Shipped. Strict; optional top-level `name` for UI.               |
| Importer                 | Shipped. Idempotent, library metadata captured.                  |
| Exporter                 | Shipped. Browser endpoint + disk endpoint, round-trip.           |
| Bootstrap script         | Shipped. One-command Classics setup.                             |
| API                      | Stations CRUD, sources, generate, playlist, trace, export.       |
| Trace viewer (UI)        | Shipped (first cut). Works end-to-end against real data.         |
| Station creation wizard  | Shipped (first cut). End-to-end verified.                        |
| Test plan + CI           | Shipped. 4-tier (engine, API, Vitest, Playwright). 98% backend.  |
| Coverage gate in CI      | Shipped (14.12). Backend 90% aggregate; frontend per-file lines ≥85%. |
| Trace-viewer Vitest      | Shipped (14.14). 0% → 96.49% lines; closes §14.8's last gap.     |
| Trace-viewer search/filter | Shipped (14.15). Free-text + source/strategy filters; ANDed; counter; clear. |
| Trace-viewer multi-week nav| Shipped (14.16). Prev/next links + disabled boundaries; closes §14.5 punt. |
| In-UI generate button    | Shipped (14.9). Empty-CTA + next-week + advanced + per-row regen.|
| Delete / station-reset   | Shipped (14.10). Per-row delete + station nuke; cascades.        |
| Disk-export UI button    | Shipped (14.11). Trace viewer "Export to disk" + status line.    |
| Edit mode for stations   | Shipped (14.13). Shared `<StationForm>` + JSON preview + diff + reset. |
| **Pre-§16.2 / port roadmap** | **In flight. §17 Phase A — #8 + #9 + #3 shipped (14.17-14.19). Next: #4 extract template.ts, then #1 LayoutFieldset arrays.** |
| Visual block editor      | Deferred (v2). Lands as §17 Phase B after Phase A unblocks.      |
| Trace versioning         | Deferred. See §16.4 (queued behind §16.1 by design).             |
| Accessibility pass       | Deferred. §17 Phase D — single sweep after §16.2 + port.         |
| Scheduler                | Deferred (v1.1).                                                 |
| Weekly migration         | Long-term — §17 Phase C; needs three new engine primitives.      |

Active TODOs live in §11 (deferred), §14.5 (trace-viewer follow-ups),
§14.7 (wizard follow-ups), §14.8 (test-plan follow-ups), §16 (per-slice
briefs), and **§17 (the actual execution roadmap before §16.2 + the
port)**. The original top-10 next-up list is fully cleared; the
remaining queued slices are the three big pieces (§16.2 block
editor, §16.4 trace versioning, §16.5 a11y pass) plus the long-
term port. §17 captures what should land before / between those.

Don't start chipping at the follow-ups until Station Studio earns
them — the project's whole premise is "build the smallest thing
that proves the next decision", not "polish the thing we've got".
§17 is the queue we *are* going to chip at; everything else stays
trigger-gated.

---

## 16. Next up — planned slices

Three queued slices, in the order we expect to take them. Each section
is intentionally a short brief, not a spec — the spec gets written
when the slice lands, in the same §14.x format as the others.

### 16.1 In-UI generate button (and per-row regenerate)

**Why next.** Today, `POST /api/stations/{slug}/generate` is the only
way to materialise a playlist, and the wizard's happy path lands the
user on an *empty* station-detail page with no obvious next action.
Bootstrapping a station from the UI but having to drop to `curl` to
generate the first week is the loudest paper cut left in the v1 loop.
Plus: each subsequent week (week 3, 4, 5…) costs another shell drop.
This slice closes both.

**Use cases enumerated.** Working through this in advance because the
generate button is deceptively simple — it's actually a confluence of
several user intents that need different UX.

| # | Use case                                     | Today                | Risk if ignored             |
|---|----------------------------------------------|----------------------|-----------------------------|
| 1 | Generate the next week (the 95% case)        | API works            | None — main path            |
| 2 | Generate week 1 from an empty station        | API works            | UX hole post-wizard         |
| 3 | Regenerate an existing week                  | Silently overwrites  | **Trace lost without warning** |
| 4 | Generate an arbitrary week (e.g. skip ahead) | API works            | None                        |
| 5 | Accidentally generated                       | Playlist now exists  | Mild — non-destructive      |
| 6 | "Go back" to a previous week                 | Click row in table   | Already covered for viewing |
| 7 | Regenerate with a specific seed (rng:on)     | API supports seed    | Classics is rng:off; design now |
| 8 | Re-roll seed until happy (rng:on)            | Manual reissue       | Same                        |
| 9 | Reproduce a previous generation byte-for-byte| Trace stores seed    | Already covered             |
| 10| Pool-underflow failure (week 13+ on Classics)| API returns 400      | UI must surface inline      |
| 11| Concurrent click — two tabs both POST week 3 | Last-write-wins      | Single-user; not a problem  |
| 12| Disk export now stale after regenerate       | Disk file untouched  | Footgun — flagged           |
| 13| "I want the old trace from before regen"     | Gone forever         | **§16.4 — separate slice**  |
| 14| Generate weeks N..M in batch                 | No                   | Wait until asked            |

**Decisions taken** (D1–D7 — D8 is its own slice §16.4):

- **D1. Two affordances, different places.** A "Generate" panel at
  the top of `/stations/[slug]` for next-week-or-arbitrary-week flow.
  A per-row "↻ Regen" link in the playlists table for redoing a
  specific existing week. Top button stays simple; the row action
  carries the regen-specific semantics (warning copy, confirm).
- **D2. Layered overwrite guard.** When the chosen week already
  exists: button label flips to "Replace week N" with red styling
  *and* a `confirm("Replace week N? Current trace `…` will be deleted.")`
  fires before the POST. No backend 409 handshake — single-user, the
  client-side check is sufficient. Add the 409 path if/when we
  multi-user.
- **D3. Seed UI gated on `template.rng === 'on'`.** Classics is
  rng:off; rendering a seed input would be misleading (no effect).
  When rng is on, render: `<input type="number">` prefilled with a
  fresh random 32-bit int + 🎲 reroll button (re-randomises the field
  without submitting). The trace already persists `seed`; reproducing
  a generation byte-for-byte is an existing capability via the
  advanced form. Per-row regen always uses a fresh random seed —
  users who care about specific seeds use the advanced form.
- **D4. Empty-state CTA is big and primary.** When
  `playlists.length === 0`, the small "Generate" button is replaced
  by a big primary "Generate week 1 →". The wizard → station-detail
  → click → trace-viewer flow becomes the canonical first run, three
  clicks total.
- **D5. Disk-export staleness: do nothing this slice, document.** If
  a user regenerates and forgets to re-click "Export to disk", the
  next click overwrites correctly. Adding a "⚠ stale" badge couples
  the web layer to filesystem mtime checks; not worth it until a
  real user reports forgetting. Will note in the punted-items table.
- **D6. Accidental forward generation needs nothing.** Non-destructive;
  next "Generate next week" click does the right thing. Cleanup of
  unwanted weeks is the *delete* affordance — §16.3.
- **D7. "Go back" is three readings, two solved.** Navigate to
  prior-week trace = already works (click row). Regenerate prior
  week = D1's per-row action. Delete prior week to "rewind" = §16.3.

**Decision deliberately deferred.**

- **D8. Trace history / versioning** — see §16.4. Today, regenerating
  drops the prior `PlaylistTrace` row. Recovering "what did week 5
  look like before I re-imported the source XMLs?" requires a schema
  change and a viewer that surfaces multiple traces per week. That's
  its own slice; calling it out here so the regen confirm dialog can
  be honest about what it's destroying.

**Scope — three commits.**

1. **`web/src/lib/api.ts` + station-detail generate panel.**
   - `generatePlaylist(slug, week, seed?)` typed client.
   - On `/stations/[slug]`, above the playlists table:
     - Empty state: big primary "Generate week 1 →".
     - Populated: "Generate next week (N+1)" primary, plus
       `<details>` advanced disclosure with week input (default
       `nextWeek`) and conditional seed input + 🎲 reroll for
       `rng === 'on'`.
     - When chosen week exists: red "Replace week N" label +
       `confirm()` before POST.
   - Spinner + disabled state during fetch.
   - Success → `goto('/playlists/{playlist_id}')`.
   - Failure → inline `ApiError.message` rendering, no navigation.

2. **Per-row "↻ Regen" link in the playlists table.**
   - Click → `confirm("Replace week N? Trace `…` will be deleted.")`
   - POST with fresh random seed for rng:on, no seed for rng:off.
   - Success → `goto('/playlists/{new_id}')`.

3. **Tests + §14.9 docs.**
   - Vitest: 4–6 new cases on `detail.test.ts` covering empty-state
     CTA, populated-state next-week label, advanced-disclosure toggle,
     seed-input visible only on rng:on, "Replace week N" label on
     conflict, per-row regen present.
   - Playwright: new spec — empty → generate week 1 → trace viewer;
     populated → next-week → trace viewer; per-row regen → confirm →
     new trace_id; underflow → inline error, no nav.
   - DESIGN.md §14.9 captures what shipped (same convention as
     14.5/14.7/14.8); §15 ticks 16.1 done.

**What stays out of scope (re-stated for the §14.9 punt table).**

| Area                             | Status               | Trigger                            |
|----------------------------------|----------------------|------------------------------------|
| Delete a playlist                | §16.3                | Common enough to bother            |
| Server-side 409 on overwrite     | not now              | Multi-user / multi-tab use         |
| Disk-export staleness warning    | not now              | User reports forgetting to re-export|
| Trace versioning                 | §16.4                | First "I want the old one" report  |
| Batch "generate weeks N..M"      | not now              | Real ask appears                   |
| Custom seed for per-row regen    | not now              | User wants it; advanced form works |
| Auto-disk-export on generate     | rejected             | Couples concerns, surprising       |
| Concurrent-tab race              | not now              | Single-user tool                   |
| Auto-generate on station create  | rejected             | Generation is a visible action     |
| Scheduler / cron                 | §11 v1.1             | Weekly migration matures           |

**What this earns us.** A station's full lifecycle — create →
generate → view trace → download — is reachable from the landing page
without leaving the browser. Closes the v1 loop the wizard opened,
and the per-row regen handles the iterate-on-source-pools flow that
will dominate Classics's authoring use.

**Pool-capacity ceiling worth mentioning to the user before week 13.**
With current pools (`RG_RADIO=958`, `RG_PARTY=959`), the highest
1-indexed position needed for week W is `(W-1)*60 + 265`. Weeks 1–12
generate cleanly; week 13 needs index 985 → engine raises pool-
underflow per §5.5. The inline error rendering tested in commit 3
makes this visible; user adds tracks and re-clicks.

### 16.2 Visual block editor (v2)

**Why this one matters but not yet.** §11 has named the visual block
editor as a v2 explicit since the original scaffold. The wizard's
text-form layout (`row_start_offsets` as a comma-separated string,
`row_template` as a stacked add/remove list) carries us as long as
there's only Classics. A second station — especially one with a
non-uniform layout — turns the form from "boring but tolerable" into
"the thing that's stopping me from defining the station".

**Trigger to start.** A real second-station layout that's awkward to
express in the current form, *or* the user explicitly asking for it.
Don't pre-build.

**Sketch (not a spec).**

- Same JSON in, same JSON out. The editor reads
  `template.layout.row_template` and writes back into the same shape.
  Wizard ↔ editor round-trip cleanly via the JSON.
- Drag-drop lanes per row (one column per row, vertical = within a
  row). Each block is a coloured chip carrying source + strategy +
  size; colour comes from `template.sources[].colour`.
- Inline edit on the chip — click to expand strategy/size; the source
  swap is a dropdown constrained to content-role sources (same rule
  as the wizard).
- Row-start-offset slider per row (or a per-row numeric input) with
  the +60 cursor advance hinted in the gutter so "row 7 = row 6" is
  visible at a glance.

**Open questions to settle when we start.**

- Can we drop the wizard's layout fieldset entirely once the editor
  exists, or do we keep both for accessibility / quick edits?
- Does the editor live at `/stations/[slug]/edit` (single page) or
  `/stations/[slug]/edit/layout` (sub-route)? Probably the latter
  once identity / sources / cursor get their own edit views.
- Live JSON preview alongside the visual canvas — yes, very probably,
  reusing the deferred wizard "live template JSON preview" item.

**Reuse from §14.7 wizard.** Form-state-to-JSON lives in
`buildTemplate()`; the editor probably wants the same translation
function (extracted to `web/src/lib/template.ts`). The constants
duplicated from the engine (`STRATEGY_OPTIONS`, `ROLE_OPTIONS`) become
worth extracting to `GET /api/meta` once a third caller appears — the
editor is that third caller.

**Update — §16.7 set up the swap-point.** As of §14.13 the
wizard's layout fieldset is extracted as
`web/src/lib/components/LayoutFieldset.svelte`. Both
`/stations/new` and `/stations/[slug]/edit` use it. **However**,
the swap from `<LayoutFieldset>` to `<VisualBlockEditor>` is
not a one-line edit — see the §16.7 D10 caveat: `<LayoutFieldset>`
binds comma-separated *strings* whereas a drag-drop visual editor
naturally binds *arrays*, so the swap also entails:
  1. Replace the component in `<StationForm>`'s markup
  2. Refactor `<StationForm>`'s state from strings to arrays
  3. Drop `parseStringList` / `parseIntList` from `buildTemplate`
     and join-back from `unpack`

None of that work is duplicated across consumer routes — that's
the value of the §16.7 extraction. But it's a real piece of work
that lands alongside §16.2 itself, not before. The §16.7 D10
caveat in §14.13 has the full text.

**This brief is intentionally light** — the use-cases / decisions
format the later slices use isn't applied here because the trigger
is "real second station with awkward layout", and pre-deciding
D1–D10 without that station's needs in hand risks designing for
hypothetical pain. When the trigger fires, expand to the full
brief shape using §16.7 / §16.8 as templates.

### 16.3 Delete / station reset

**Why this matters.** Today the only way to clean up a wizard test
station, or to remove a playlist that was generated by accident, is
direct SQL on the database file. Once the wizard becomes part of
real authoring (D4 in §16.1 made the canonical first-run wizard →
station-detail → generate three clicks), people *will* generate
junk while exploring. They need a way out.

**Scope narrowed since the original framing.** Originally drafted as
"reset / regenerate". The regenerate half migrated into §16.1 (D1's
top-bar form + per-row ↻ regen). This slice is now just *delete* —
two affordances, two consequences-of-blast-radius.

**Use cases enumerated.**

| # | Use case                                              | Today                  | Risk if ignored                      |
|---|-------------------------------------------------------|------------------------|--------------------------------------|
| 1 | Delete an accidentally-generated playlist             | API exists; no UI      | Mild — clutters the table            |
| 2 | Delete a wizard test station for cleanup              | API exists; no UI      | Mild — must use curl                 |
| 3 | Delete a station that has 12 weeks of real playlists  | API exists; no UI      | **Severe** — silent cascade of data loss |
| 4 | Confirmation that delete is irreversible              | None today             | User accidentally nukes a real week  |
| 5 | Disk-exported XML still on disk after DB delete       | File orphaned          | Footgun — flagged                    |
| 6 | Bulk-delete N weeks at once                           | No                     | Wait until asked                     |
| 7 | Soft-delete with restore                              | No                     | Out of scope for single-user tool    |
| 8 | "I deleted by mistake"                                | No undo                | Out of scope; confirms must work     |
| 9 | Concurrent-tab delete vs another session's edit       | Last-write-wins        | Single-user; not a problem           |
| 10| Audit log of deletes                                  | None                   | Not needed for single-user           |
| 11| Delete via the trace viewer                           | No path; only station detail | Could add; not necessary       |

**Decisions taken** (D1–D7):

- **D1. Two affordances, two places, asymmetric weight.**
  - Per-row "Delete" link in the playlists table on `/stations/[slug]`,
    next to the existing "↻ regen" — peer to it.
  - Station-nuke control in the station-detail header (right-aligned,
    distinct visual treatment). Not on the landing page; users
    navigate into a station before nuking it, which adds one click
    of friction by design.
- **D2. Confirm dialog text scales with blast radius.**
  - Playlist delete: `confirm("Delete week 5 (trace abc12345…)? This is permanent.")`
  - Station delete: scarier copy with counts —
    `confirm("Delete station 'cls' and N playlists, M total entries?
    This is permanent.")`. The counts come from `playlists.length` and
    `playlists.reduce((a, p) => a + p.entry_count, 0)` already in
    page state; no new fetch needed.
  - No typed-confirm pattern (where the user has to type the slug).
    Considered. Rejected for now — single-user, the counts in the
    dialog are the safety. Add typed-confirm if a real "I nuked the
    wrong station" report comes in.
- **D3. Disk-file removal is not part of this slice.**
  Considered: `?also_disk=true` query param + a checkbox in each
  dialog. Rejected because §14.3 ("No policy + .gitignore") says
  the disk-export side is deliberately decoupled from DB state. Re-
  coupling here partly walks that back. Defer to whenever the disk-
  prune endpoint lands (§14.3 deferred item) — that slice can
  handle it holistically across all stations at once. The §14.10
  shipping doc should restate this so the next reader doesn't think
  it was forgotten.
- **D4. After-delete navigation.**
  - Per-row delete: refetch playlists in place; the row vanishes,
    user stays on the station detail. No goto.
  - Station delete: `goto('/')` — the station's URL no longer
    exists, can't stay on it.
- **D5. Errors render inline, no navigation on failure.**
  Same pattern as §16.1's `generateError` — a `<p class="error"
  data-testid="delete-error">` near the affected affordance.
  Concrete failure modes: 404 (raced with another session's
  delete), 500 (DB lock contention).
- **D6. No undo, no soft-delete.**
  Hard delete keeps the implementation small. Trace versioning
  (§16.4) is a separate feature for keeping history *within* a
  playlist. The git history of `stations/*.json` is the safety net
  for stations the user wants back; for playlists, regenerate
  reproduces them deterministically (rng:off) or with the original
  seed (rng:on, if they remembered to record it).
- **D7. Visual hierarchy matches blast radius.**
  Per-row delete: subdued red link button (matching the existing
  `.link-btn` style for ↻ regen, but red `#c0392b`).
  Station nuke: a properly red destructive button in the page
  header — easy to find on purpose, hard to misclick because it's
  not adjacent to anything else routinely interacted with.

**Backend (already shipped — see §14.10).**

- `DELETE /api/playlists/{id}` → 204 No Content; cascade through
  entries + trace.
- `DELETE /api/stations/{slug}` → 204 No Content; cascade through
  playlists + entries + traces + cursor state.
- 404 on unknown id / slug.
- ORM cascade declared in `engine/models.py` — no longer relying
  solely on the SQLite FK pragma (per the wider review fix in
  commit `1302218`).

**Frontend scope — three commits.**

1. **`web/src/lib/api.ts` + per-row delete in playlists table.**
   - `deletePlaylist(id)` typed client returning `void`. Uses the
     same `json<T>()` helper for error mapping but with no JSON
     parse on 204 — needs a small `noContent` variant or a tweak
     to `json<T>()` to tolerate empty bodies.
   - Per-row "Delete" link/button on `/stations/[slug]` next to
     "↻ regen". `data-testid="delete-row-{week}"`.
   - `confirm()` per D2. On 204: refetch playlists (existing helper
     still works). On error: `deleteError` rendered inline.

2. **`deleteStation(slug)` + station nuke affordance.**
   - `deleteStation(slug)` typed client.
   - Right-aligned destructive button in the station header on
     `/stations/[slug]`: e.g. "Delete station →" with red styling.
   - Confirm copy with computed counts per D2.
   - On 204: `goto('/')`.

3. **Tests + DESIGN.md §14.10.**
   - Vitest cases on `detail.test.ts`: delete-row confirms
     accept/cancel, error renders, refetch removes the row;
     station-nuke confirms with counts, navigates to '/'.
   - Playwright `delete.spec.ts`: delete a generated playlist
     (table shrinks); delete a station (lands on landing without
     it).
   - DESIGN.md §14.10: shipped vs punted, gotchas, pool of
     decisions made in flight.
   - §15 ticks 16.3 done.

**Stay-out-of-scope candidates.**

| Area                                | Status               | Trigger                              |
|-------------------------------------|----------------------|--------------------------------------|
| Trash / soft-delete with restore    | rejected             | Single-user; git history of templates is enough |
| Bulk delete (multiple weeks)        | not now              | Real ask appears                     |
| Typed-confirm ("type the slug")     | not now              | "I nuked the wrong station" report   |
| Disk-export removal flag            | §14.3 disk-prune     | Disk-prune slice lands               |
| Audit log of deletes                | rejected             | Single-user                          |
| Delete from trace viewer            | not now              | Felt friction with station-detail-only path |
| Concurrent-tab race / 409           | not now              | Multi-user / multi-tab use           |

**Implementation notes for future pick-up.**

- `json<T>()` in `lib/api.ts` parses the response body unconditionally;
  for 204 responses it'll throw on the empty body. Either add a
  `noContent` helper or change `json<T>()` to handle 204 by returning
  `null as T`. The latter is messier; prefer the former.
- The trace viewer already has a "this playlist was deleted" failure
  mode if someone deletes via API while the viewer is open. Today
  it'll already render the 404 from `getPlaylist`. Leave alone.
- The header nuke button needs at least 16px of spacing from the
  page heading — easy to misclick if it's flush against the title.

### 16.4 Trace versioning (D8 from §16.1)

**The hole.** Today, when §16.1's regenerate flow fires, the prior
`PlaylistTrace` row is hard-deleted via the cascade on the
`Playlist` row's replacement. The old `trace_id` is unrecoverable —
gone forever. The `confirm()` dialog in §16.1 is honest about this
("Trace `abcd…` will be deleted"), but for any authoring workflow
where the user iterates on source pools and wants to compare "what
did week 5 look like before I re-imported RG_RADIO?", today's
behaviour is destructive in a way that can't be undone.

**Why this is its own slice and not part of 16.1.** Versioning a
playlist is a real piece of work:

1. **Schema change.** `playlist_trace.playlist_id` is a unique FK
   today; needs to become a multi-row relation. Probably introduce a
   `playlist_version` table keyed `(playlist_id, version_no, trace_id)`
   with the slot JSON, and reduce `PlaylistTrace` to a view (or
   denormalise the latest into `Playlist` for query speed).
2. **Regeneration semantics shift.** Regenerate stops being
   destructive — the prior version becomes `version_no=1`, the new
   one `version_no=2`. The "current" pointer needs an explicit
   column or is implicitly max(version_no).
3. **Viewer UX.** The trace viewer needs a version picker (dropdown,
   prev/next arrows, or a side-by-side diff for "show me what
   changed between v1 and v2"). The diff view is genuinely
   interesting — once it exists, "did my XML re-import actually
   change anything for week 5?" becomes answerable in one glance.
4. **Disk export ambiguity.** `out/{slug}/{week:03d}.xml` is a single
   file today. Versioned playlists either pin disk to "always the
   latest" (simplest) or grow `{week:03d}-v{version_no}.xml`
   (preserves history but multiplies files). Probably the former,
   with a versioned export as an explicit power-user action.
5. **Storage cost.** Each Classics trace is ~3,010 slot dicts
   (~600 KB JSON). Five regenerations of fifty-two weeks = ~150 MB
   per station. Not a problem for SQLite, but worth knowing before
   anyone runs hundreds of regenerations.

**Sketch for the slice (when we take it).**

- **Schema migration:** add `PlaylistVersion(id, playlist_id,
  version_no, trace_id, generated_at, rng, seed, slots_json)`,
  one-to-many from `Playlist`. `PlaylistTrace` keeps existing shape
  for backwards-compat but reads from the latest version.
- **Backfill:** existing rows become `version_no=1`. Idempotent
  migration script that's safe to re-run.
- **Generate router:** instead of `db.delete(existing)`, append a
  new `PlaylistVersion` and bump `current_version_no`.
- **Viewer:** version picker in the header of `/playlists/{id}`.
  URL grows `?v=N` for permalinks. Default = latest.
- **Diff (stretch):** `/playlists/{id}/compare?from=N&to=M` page.
  Shows per-slot track-changed / source-changed / pool-index-changed
  rows. Probably the most interesting bit of this whole slice.
- **§16.1 regen confirm dialog updates:** copy flips from "Trace
  `…` will be deleted" to "Saved as version N+1; the current
  trace will still be available." Honest.

**Why this is queued at §16.4 and not earlier.** It only matters
once §16.1 has shipped *and* the user has experienced "I regenerated
and now I wish I hadn't" at least once. Building it pre-emptively
risks designing the wrong shape. The §16.1 confirm dialog gives the
user the "are you sure?" guard rail that, for a single-user tool,
is *probably* enough — and we'll know after a few weeks of real use
whether that's true.

**Triggers to pick this up.**

- User reports wanting an old trace back (the most direct signal).
- The diff-between-versions question gets asked ("did my re-import
  actually change anything?").
- A second user joins and "I overwrote your trace" becomes a
  collaboration problem.

**Stay-out-of-scope candidates** (capture upfront so we don't gold-
plate when we get here):

- Branching versions / tags (vN-experimental). Linear history is
  enough; users can clone the station if they need a what-if.
- Cross-station diff. Way out of scope.
- A "rollback" button that mutates current to a prior version. Add
  if asked, not before — it's surprisingly subtle (rolling back v3
  to v1 makes v3 either disappear or become v4 with v1's content;
  pick wrong and users get confused).

**Implementation notes for future pick-up.**

- The DB cascade today is `Playlist → PlaylistEntry` and `Playlist →
  PlaylistTrace`, both via SQLAlchemy `relationship(cascade=...)`.
  Look at `engine/models.py` and the relevant cascade configs before
  changing schema; the cascades implicitly drive the "regen replaces
  silently" behaviour today.
- The trace JSON is large; consider gzipping `slots_json` if
  multi-version growth becomes a real cost. SQLite has no built-in
  compressed-blob type but `gzip.compress()` on the way in / out is
  trivial.
- The viewer's per-row collapse state (already in-memory only per
  §14.5) needs to invalidate when switching versions. Reset on every
  navigation; don't try to preserve.

### 16.5 Accessibility pass

**Why this one matters but not yet.** Every shipped slice has noted
a11y gaps in its own §14.x punt list — §14.5 (trace viewer: ARIA on
chips, skip-link, expanded-state announcements), §14.7 (wizard:
`aria-live` errors, required markers, per-field error association),
§14.9's review N3 (station-detail action columns lack a11y labels).
Each was reasonable to defer — the alternative (fix piecemeal as we
ship) leads to inconsistent solutions: one page uses `aria-live`,
another uses visible-text errors, a third has neither. A focused
pass over the whole UI gives one set of patterns the next slice can
hold to, and stops the §14.x punt tables accumulating "a11y" rows.

**Trigger to start.** Any of:

- A real external user requests / needs assistive-tech support (most
  direct — and the most common reason a11y work actually moves).
- Before broadening user base beyond the single-user assumption in
  §12. The current "you" model excuses a lot.
- A library or hosting context demands WCAG conformance (e.g. the
  Studio gets embedded in a regulated environment).

**Known gaps consolidated** — pull these in when the slice runs so
the trail of previous "later" notes converges in one place:

| Surface                               | Gap                                                                 | Source         |
|---------------------------------------|---------------------------------------------------------------------|----------------|
| Trace viewer (`/playlists/[id]`)      | No ARIA on source chips; no skip-link; collapsible row headers don't announce expanded state | §14.5         |
| Wizard (`/stations/new`)              | No `aria-live` for the error block; no `aria-required` on required inputs; server errors not associated with the offending field | §14.7         |
| Station detail (`/stations/[slug]`)   | Empty `<th></th>` for view + regen action columns lack visually-hidden label text | §16.1 N3      |
| Generate panel (`/stations/[slug]`)   | Inline error after backend failure has no `aria-live="polite"`      | §14.9 review   |
| All pages                             | No "skip to main content" link; no shared layout with `<main id="content">` landmark; focus-visible styles not customised | (general)     |
| Colour contrast                       | Not audited; the `.crumbs` `#666` on white and the red replace button on white may both be borderline AA | (general)     |

**Sketch for the slice (when we take it).**

- **Tooling first.** Add `@axe-core/playwright` so each existing e2e
  spec runs an automated WCAG audit on its destination page.
  Failures break CI. Caveat: axe catches roughly 30–40% of real
  issues; manual screen-reader testing (VoiceOver / NVDA) is still
  needed for the rest. Don't treat axe-clean as a11y-clean.
- **Fix the known-gaps table** in one pass — the goal is one merged
  diff that every reviewer can trace to one set of decisions. Don't
  scatter across multiple slices.
- **Introduce a shared `+layout.svelte`** with a "skip to main
  content" link and `<main id="content">` landmark. Each route's
  current top-level `<main>` becomes a child or gets the wrapper
  removed depending on what reads cleaner.
- **Extract an `<ErrorBanner>` component** — every page renders
  errors as raw text today (three different inline styles). One
  component with `aria-live="polite"` (or `assertive` for hard
  failures) cleans up the punt-list rows simultaneously.
- **Custom focus-visible styles.** Default browser focus rings are
  inconsistent across UAs and easy to mistake for "selected"
  styling. A two-line CSS rule with high contrast goes a long way.
- **Manual sweep with VoiceOver** — automated tooling won't catch
  things like "button label reads 'icon' aloud" or "the regen
  confirm flow disorients screen-reader users". 30 minutes per page
  with VoiceOver enabled is the test.

**Open question worth deciding upfront when the slice runs.**

- **Wizard per-field error association needs a backend change.**
  Today `POST /api/stations` returns a single string blob on 400
  (`engine.template.parse_template`'s `TemplateError.message`). To
  programmatically associate errors with the offending input —
  `<input aria-describedby="slug-error">` and friends — the API
  needs to return a structured response, e.g.
  `[{field: "slug", message: "..."}]`. Two options:
  1. **Backend dependency:** make the API change first as a
     §16.5 prerequisite. Bigger blast radius but cleaner outcome.
  2. **Punt within the slice:** wrap the existing blob in
     `aria-live` and call it done; per-field association comes when
     a real screen-reader user complains.
  Decide when starting based on whether axe / manual testing
  flags it as a real friction point. Probably (2) is right for v1.

**What this earns us.**

- Consistent error/loading/success patterns across pages (instead
  of three different rolls).
- A baseline future slices can hold to — the §14.x "Accessibility
  polish" rows can stop accumulating because new code is held to
  the pattern from day one.
- CI signal — axe violations show up before merge, not in
  production.

**Stay-out-of-scope candidates** (decide upfront so the pass stays
bounded):

- **Full WCAG 2.1 AA conformance certification.** Overkill for a
  single-user tool. Aim for "no obvious issues with VoiceOver" and
  "axe clean", not a formal audit.
- **Translations / RTL support.** Separate concern, much bigger.
  Don't conflate.
- **Reduced-motion preferences.** Relevant if we add animations;
  we currently have none. If §16.2 (block editor) ships drag-drop
  animations, this becomes a §16.2 concern not a §16.5 one.
- **Voice control / dictation testing.** Not enough surface area
  to justify; the keyboard-only sweep is a reasonable proxy.

**Implementation notes for future pick-up.**

- The generate panel's `<details>` element gives keyboard a11y for
  free; don't replace it with a custom disclosure unless there's a
  strong reason.
- The trace viewer's row headers are already keyboard-togglable
  (Enter handler in §14.5); they need `aria-expanded={open}` on the
  same element. One-line addition.
- Don't try to make every piece of text screen-reader friendly
  without testing. Well-meaning ARIA attributes can make things
  worse than no attributes ("button button button" disclosed for
  every nested element). When in doubt: leave the semantic HTML
  and don't add ARIA.
- `confirm()` dialogs are browser-native and inherit OS-level a11y;
  don't rebuild them as custom modals just for "consistency". The
  inline error rendering (`<p class="error">`) is the part that
  needs `aria-live`, not the dialog.
- Svelte's `svelte-check` already surfaces a11y warnings during
  build (missing alts, label-for, etc) — gauge the starting point
  by running it on the current tree before the slice begins.

### 16.6 Disk-export button on trace viewer

**Why this matters.** The disk-export endpoint
(`POST /api/playlists/{id}/export-to-disk`) has existed since
§14.2 and writes the iTunes XML to a configured directory on the
server's filesystem. Classics's actual production loop depends on
this — the user keeps a OneDrive/Dropbox folder synced to a
Windows iTunes box, points `Config.playlist_output_dir` at that
folder, and each generated week shows up there. Today the only
trigger is `curl -X POST`. The trace viewer has the browser-
download button (§14.5) but nothing for the disk path. Closing
this gap is the loudest paper cut left in Classics's day-to-day.

**Use cases enumerated.**

| # | Use case                                                  | Today                       | Risk if ignored                            |
|---|-----------------------------------------------------------|-----------------------------|--------------------------------------------|
| 1 | Click → write to disk → see where the file landed         | Endpoint exists; no UI path | Mild — main path, works via curl           |
| 2 | Re-export the same week (overwrite)                       | Endpoint silently overwrites| None — mirrors regen semantics             |
| 3 | Disk write fails (no permission, IO error, dir missing)   | Backend 4xx/5xx             | UI must surface; user blamed for stale file |
| 4 | Output dir not configured                                 | Falls back to `out/` per §14.2 | None — covered by backend                |
| 5 | "When was this last exported, and to where?"              | No record                   | Stale file confusion (deferred — §14.5 staleness) |
| 6 | Configure the output dir from the UI                      | DB Config key only          | User wants OneDrive flow without SQL       |
| 7 | Auto-export on regen                                      | No                          | **Rejected** per §14.9 D5                  |
| 8 | Verify the disk file matches the DB after export          | No checksum                 | Out of scope; trust the bytes              |
| 9 | Multi-playlist batch ("export all weeks of cls")          | No                          | Wait for ask                               |

**Decisions taken** (D1–D6):

- **D1. Button sits next to "Download iTunes XML"** on the trace
  viewer. Same `.actions` strip; same visual weight. The two
  represent the two delivery paths (browser download vs disk).
- **D2. Plain `<button>`, not `<a>`.** The browser-download is an
  `<a href>` because §14.5 explicitly noted "no JS needed for a
  one-shot byte download". Disk-export is POST + JSON response —
  needs JS. Use `<button on:click>` and reach for `.button`
  styling for parity.
- **D3. Inline status, not toast.** On success, render the
  returned path + size as a small status line below the button:
  `Wrote to /abs/.../cls/001.xml (847 KB)`. On failure, render
  the ApiError message in the same slot but red. No modal, no
  toast library — single status node mirrors the §16.1
  `generate-error` pattern.
- **D4. No confirmation dialog.** Disk-write is non-destructive
  in the §16.3 sense (overwrites only its own file at the
  predictable path). No unrecoverable consequence. User's mental
  model is "save", not "delete".
- **D5. Spinner during fetch.** The endpoint should be fast
  (<1s) but on slow filesystems (Dropbox sync, network share)
  could pause briefly. Disable the button during the call so a
  double-click can't queue two writes.
- **D6. `bytes` formatted as KB.** The endpoint returns
  `{path, bytes}`. `Math.round(bytes / 1024)` reads better than
  raw bytes ("868990" vs "849 KB") for humans verifying the file
  landed sanely.

**Scope — single commit (small slice).**

- **`web/src/lib/api.ts`** — `exportPlaylistToDisk(id)` typed
  client. Returns the response shape `{path: string; bytes:
  number}`. POST, no body, JSON response.
- **`web/src/routes/playlists/[id]/+page.svelte`** — second
  button "Export to disk" in the existing `.actions` strip,
  plus inline `diskExportStatus` state for success / error
  rendering. Spinner on the button while the request is in
  flight.
- **Vitest** — one new case on `lib/api.test.ts` for
  `exportPlaylistToDisk` (URL build, response parse, ApiError
  on 4xx). Trace viewer is still 0% under Vitest by design
  (§14.5 — "Playwright covers it"); not adding component tests
  here as that's a separate slice in §14.8's punt list.
- **Playwright** — extend `e2e/trace-viewer.spec.ts` with a
  case that clicks the new button and asserts the success
  status renders.
- **Docs** — DESIGN.md §14.11 shipped doc + §15 ticks 16.6
  done. web/README's surface table picks up the disk-export row.

**Stay-out-of-scope candidates.**

| Area                                       | Status               | Trigger                                    |
|--------------------------------------------|----------------------|--------------------------------------------|
| Configure output dir from the UI           | not now              | User wants OneDrive flow without DB SQL    |
| Last-exported-when indicator               | §14.5 staleness item | Multi-week confusion about which is current |
| Disk-prune endpoint                        | §14.3                | `out/` clutter becomes painful             |
| Auto-export on regen                       | rejected             | §14.9 D5                                   |
| Multi-playlist batch ("export all weeks")  | not now              | Real ask                                   |
| Checksum / file-vs-DB verification         | not now              | Real bytes-don't-match-trace report        |
| Toast / notification framework             | rejected             | Inline status node is enough               |

**Implementation notes for future pick-up.**

- The endpoint reads the destination from `Config.playlist_output_dir`
  (per §14.2). When that's unset, falls back to `out/` relative
  to the process CWD. Tests set the dir to a `tmp_path` via direct
  DB write; production users configure it once via SQL or whatever
  the future UI from "Configure output dir" punt-row lands.
- The status rendering uses two reactive vars
  (`diskExportStatus: string | null`, `diskExportError: string |
  null`) rather than a single string-with-flag, so the template
  can apply different styles cleanly (`.success` vs `.error`).
- If/when the staleness indicator (§14.5 / §16.5 punt items) gets
  built, the natural place to surface it is alongside the disk-
  export button in this same `.actions` strip — keeping all
  disk-related UX in one cluster.

### 16.7 Edit mode for stations

**Why this matters.** Stations are create-only today. The wizard
at `/stations/new` writes a template once via POST; after that,
tweaking the RNG, swapping a source pool name, or adding a row
means hand-editing `stations/{slug}.json` and re-seeding, OR a
direct DB write to `Station.template`, OR `curl -X PUT`. The
`update_station` endpoint already exists (and was hardened in
commit `1302218` to reject slug changes); the gap is purely UI.
For Classics today the template is locked in — but the second
the user wants to fix something ("row 7 should be `[60]` not
`[50]`", "let me try `rng: 'on'` for a week"), they're back to
JSON-by-hand. This slice closes that.

**It's also the slice that unblocks the visual block editor
(§16.2).** Without an edit-mode page, a future drag-drop layout
editor's output has nowhere meaningful to go (it can only
create, not modify). Build the edit shell now, drop the visual
editor into the layout fieldset later — see D10.

**Use cases enumerated.**

| # | Use case                                                 | Today              | Risk if ignored                              |
|---|----------------------------------------------------------|--------------------|----------------------------------------------|
| 1 | Rename the display name (typo / better name)            | DB SQL             | Mild — workaround exists                     |
| 2 | Flip RNG on/off, set/change seed                        | DB SQL or curl PUT | Locked-in — no path for "try a week with rng:on" |
| 3 | Tweak `row_start_offsets`                               | JSON-by-hand       | Painful — error-prone retype                 |
| 4 | Add or remove a row (different week shape)              | JSON-by-hand       | Same                                         |
| 5 | Add or remove a source pool from the template           | JSON-by-hand       | Same                                         |
| 6 | Change cursor (`base_advance_per_week`, `cycle_weeks`)  | JSON-by-hand       | Same                                         |
| 7 | Rename the slug                                         | **Rejected** (400) | Slug is immutable — UI must communicate it   |
| 8 | Edit a station that already has 12 generated playlists  | Allowed silently   | User doesn't realise existing playlists used a different template |
| 9 | Cancel after exploring the form                         | No state to lose   | Need cancel-without-save path                |
| 10| Lose unsaved changes by navigating                      | No safety net      | Punted — see §14.7                           |
| 11| Roll back to the previously saved template              | No undo            | Worth a "Reset to saved" button              |
| 12| See what's actually changing before saving              | Mental comparison  | Worth a diff view                            |
| 13| Share form with `/stations/new` to avoid duplication    | Duplicate code     | Architectural — extract `<StationForm>`      |

**Decisions taken** (D1–D10):

- **D1. New route `/stations/[slug]/edit`.** Bookmarkable; URL
  communicates state; no `?slug=...` query params; the wizard
  stays single-purpose POST. Inline editing on the detail page
  rejected: form is too big (4 fieldsets — Identity / Sources
  / Cursor / Layout) and would dwarf the playlists table.
- **D2. Extract `<StationForm>` Svelte component shared with
  `/stations/new`.** Component takes
  `{mode: 'create' | 'edit', initial?: StationCreatePayload, onSubmit: (payload) => Promise<void>}`.
  Both routes import it and own their submit handler. Extraction
  is the right move regardless because the visual block editor
  (§16.2) and a future "Start from existing" preset (§14.7
  punt) reuse the same form.
- **D3. Slug rendered `readonly` with hint text.** Backend 400
  is the failsafe (and stays so — single-user UI shouldn't be
  the only enforcement). Hint copy:
  `"Slug is immutable. Create a new station to use a different one."`
  The form doesn't include `slug` in the PUT body — it's
  already in the URL. Why the slug *can't* change: it's
  embedded in the iTunes `Playlist Persistent ID` for every
  exported playlist, in `out/{slug}/` disk paths, in
  `PlaylistTrace.station_slug`, and in `stations/{slug}.json`.
  Changing it would either break the user's iTunes library
  (renamed playlists would appear as "new" alongside the old)
  or require a coordinated multi-system migration not worth
  building for a single-user tool.
- **D4. Display name + everything else editable.** Name is
  presentational; cadence / rng / seed / sources / cursor /
  layout flow through `parse_template` validation server-side.
  The wizard's existing client-side sanity checks (slug regex,
  ≥1 source, ≥1 row block) apply to edit too.
- **D5. "Save changes" / "Cancel" buttons.** Cancel =
  `goto('/stations/[slug]')` without save; no `beforeNavigate`
  hook (D9 below). Save: PUT → on 200 `goto('/stations/[slug]')`;
  on 400 inline error rendering same as the wizard.
- **D6. Live JSON preview** in a `<details>` disclosure,
  available in BOTH create and edit modes (closes the §14.7
  punt). Renders `JSON.stringify(buildTemplate(state), null, 2)`
  in a `<pre>`. ~10 LoC. Useful for verification, debugging
  save failures, and educating users on the canonical JSON
  shape.
- **D7. Diff view** in a second `<details>` disclosure,
  edit-mode-only. Custom field-by-field comparator over the
  known schema (`name`, `cadence`, `rng`, `seed`, `sources`,
  `cursor`, `layout`). Shows only changed fields:
  ```
  rng: "off" → "on"
  seed: (unset) → 42
  row_start_offsets:
    [0, 10, 20, 30, 40, 50, 50]
    [0, 12, 24, 36, 48, 60, 60]
  Sources, Cursor: unchanged
  ```
  ~80 LoC of comparison + render, no library dep. Tailored
  output beats `diff`-package whitespace lines.
- **D8. "Reset to saved" button** edit-mode-only. Snaps the
  form back to the originally loaded template. Uses
  `confirm()` if the form is dirty. Counterpart to D7's diff:
  see what's changing → decide it's wrong → reset.
- **D9. No `beforeNavigate` "unsaved changes" hook this slice.**
  Cancel + Reset cover the intentional flows. The browser
  back-button has no warning, but accidentally losing edits
  is recoverable (re-load the form, fields repopulate from
  server state). §14.7's "Draft autosave" punt is the real
  fix — defer until a real "I lost my edits" report appears.
- **D10. Extract `<LayoutFieldset>` Svelte component** (sub-
  extraction of D2). The wizard's layout fieldset
  (`unit_labels` + `row_start_offsets` + `row_template` list)
  becomes its own component. When §16.2 ships, the migration
  is *not duplicated across the two consumer routes* — that's
  the value. The actual change is more than swapping one
  component file though: see §14.13 for the post-shipping
  caveat (the comma-separated string state in
  `<LayoutFieldset>` becomes array state when the visual
  editor takes its place; that's a `<StationForm>`-internal
  refactor that lands alongside §16.2).

**Decisions deliberately deferred.**

- **Confirm-on-save when the station has existing playlists.**
  Considered: dialog warning "this template change won't
  retro-apply to the 12 existing playlists; regenerate them
  via §16.1 to use the new template." Rejected for v1 — the
  user already has §16.1's per-row ↻ regen for the
  retro-apply story, and the existing playlists' content is
  unchanged by the edit (the trace JSON is denormalised).
  Add the confirm if a real "I edited and the old playlists
  didn't update" report comes in.

**Backend (already shipped — see §14.7 + §1302218).**

- `PUT /api/stations/{slug}` exists in `api/routers/stations.py:85`
  since the original wizard slice (§14.7).
- Rejects slug changes with 400 (commit `1302218`).
- Validates the template via `parse_template`; returns 400
  with TemplateError message on invalid.
- Existing tests cover happy path + 400 paths.

No backend work in this slice. Pure frontend.

**Frontend scope — three commits.**

1. **Extract `<StationForm>` and `<LayoutFieldset>` from the
   wizard.** Pure refactor, no new behaviour. Both components
   live in `web/src/lib/components/`. The wizard at
   `/stations/new` is rewritten as a thin parent that imports
   `<StationForm mode="create">` and owns the POST handler.
   *All wizard tests must continue to pass unchanged* — they
   are the safety net for this extraction.

2. **Add `/stations/[slug]/edit` route + Edit button on station
   detail header.** New route loads the existing template via
   `getStation(slug)`, mounts `<StationForm mode="edit" initial={...}>`,
   owns the PUT handler. Header on `/stations/[slug]` gets
   `[Edit station →]` button next to `[Delete station →]`
   (visual order per D1: edit first, delete last). Edit
   styling: dark-slate filled `.button` (matches trace
   viewer's "Download iTunes XML"); delete keeps its outlined-
   red `.nuke` styling. Hierarchy: safe = filled, destructive
   = outlined.

3. **JSON preview + diff + reset + tests + DESIGN.md §14.13.**
   The two disclosures and the reset button. Vitest cases for
   the diff comparator (it's a pure function — many edge
   cases worth pinning: unchanged, single-field, array
   diff, removed-source, added-source). Playwright e2e for
   the round-trip (edit form → save → station detail shows
   the new template).

**Stay-out-of-scope candidates.**

| Area                                              | Status              | Trigger                                      |
|---------------------------------------------------|---------------------|----------------------------------------------|
| Visual block editor                               | §16.2               | Real second-station with awkward layout      |
| `beforeNavigate` unsaved-changes warning          | §14.7 punt          | "I lost my edits" report                     |
| Draft autosave                                    | §14.7 punt          | Same                                         |
| Slug rename (recreate-and-delete flow)            | rejected            | iTunes Persistent ID would double in library |
| Confirm on save with existing playlists           | not now             | "Old playlists didn't update" report         |
| Per-field error association (a11y)                | §16.5               | A11y pass runs                               |
| Auto-regenerate playlists on template change      | rejected            | Generation is a visible action (§16.1 D7)    |
| Migrate disk-exported XMLs after layout change    | rejected            | User re-exports via §16.6                    |

**Implementation notes for future pick-up.**

- The `<StationForm>` extraction is the architectural pivot —
  the place §16.2 plugs in, the place "Start from existing"
  (§14.7) plugs in. Get the props shape right
  (`mode`, `initial`, `onSubmit`) and the rest is rote.
- The diff comparator is a pure function over
  `StationCreatePayload`. Test it standalone with a
  representative set of edits before wiring it into the
  page; render is just a `<dl>` over its output.
- "Reset to saved" needs to be careful about reference-equal
  vs deep-equal — the loaded template is an object;
  resetting via `Object.assign({}, initial)` won't reset
  nested arrays/objects unless deep-cloned. Use
  `structuredClone(initial)`.
- The wizard's existing 5 Vitest cases (`wizard.test.ts`)
  and 2 Playwright cases (`wizard.spec.ts`) are the
  safety net for commit 1's extraction. If any of them
  fails after extraction, the extraction missed
  something — read the failure, fix the component, don't
  weaken the test.
- The `Edit station →` button uses dark-slate filled
  `.button` (matches trace viewer's "Download iTunes XML"),
  NOT the outlined-red `.nuke`. Visual hierarchy: safe is
  filled, destructive is outlined-red.
- The diff view's "Sources unchanged" row is the
  signal-vs-noise test — if the diff just dumps every
  field whether changed or not, it stops being useful.
  Lean towards over-collapsing ("Layout: 1 change to
  row_start_offsets") rather than under-collapsing.

**What this earns us.** The full station lifecycle —
create → edit → generate → view → delete — finally closes
in the UI. Combined with §16.6 (disk-export button), the
Classics production loop gains "I want to tweak the
template and re-export" as a one-route flow instead of
"edit JSON / re-import / re-generate / curl-export".

### 16.8 Trace viewer search / filter

**Why this matters.** Classics's trace viewer renders **3,010
slot rows** grouped across 7 days. The §14.5 punt list flagged
"Search / filter — 3 010 rows makes scanning painful" with the
trigger "noticeable scroll lag or first-paint delay" — and the
day-to-day pain is "where did track X go in this week?" or
"show me only the RG_RADIO slots". Today, the answer is
Cmd-F + manual scroll. With filtering in place, those questions
become one keystroke.

**Use cases enumerated.**

| # | Use case                                                              | Today                  | Importance |
|---|-----------------------------------------------------------------------|------------------------|------------|
| 1 | Find all slots with a track-name substring                            | Cmd-F                  | Common     |
| 2 | Find all slots with an artist substring                               | Cmd-F                  | Common     |
| 3 | Show only slots from one source pool (e.g. RG_PARTY)                  | Visual scan            | Common     |
| 4 | Show only slots filled by one strategy (window vs pick)               | Visual scan            | Less common — usually 1:1 with source for Classics |
| 5 | Find a specific slot number (e.g. "what's at slot 1842?")             | Cmd-F or scroll        | Occasional |
| 6 | Find a specific pool index ("what came from RG_RADIO[100]?")          | Visual scan            | Occasional |
| 7 | Show only one row/day (e.g. "just Saturday")                          | Collapse the others    | Already partial via §14.5 collapse |
| 8 | Combine filters (source RG_PARTY + track name "love")                 | Not possible           | Real       |
| 9 | Preserve context — "what's around this match?"                        | Filter loses neighbours| Punt — see D1 |
| 10| Share a filtered view with someone                                    | Screenshot             | Punt — URL state, see D5 |
| 11| Highlight matches in place rather than hide non-matches               | n/a                    | Punt — see D1 |
| 12| Find rejected candidates                                              | n/a — trace doesn't record them | Out of scope (§11 v1.1) |

**Decisions taken** (D1–D10):

- **D1. Filter (hide non-matching), not highlight in place.** The
  95% case is "where did X go" — a compact list of matches reads
  faster than the full 3,010 rows with yellow underlines. The
  context-preservation use case (#9) is real but rare; serving
  it would mean either toggling between filter / highlight modes
  (UI complexity) or showing N±1 rows around each match
  (heuristic with edge cases). v1 is filter-only; revisit when
  someone reports the "what's around this match" pain.
- **D2. Three filter inputs**: free-text search (track name OR
  artist substring, case-insensitive), source dropdown, strategy
  dropdown. Each input is independent; combinations AND together
  (D3). The free-text covers use cases 1+2 with one input —
  splitting them adds an OR/AND toggle that's overkill for v1.
- **D3. Filters AND together.** A slot must match *every* active
  filter to render. Predictable, matches user expectation.
  Empty-string / unselected = no constraint, not a wildcard.
- **D4. Filter bar sits between the "Your working" header and
  the row sections.** Above the rows is the right cognitive
  position; sticky-on-scroll considered but rejected for v1
  (Classics has 7 rows, scrolling-back is mild; sticky has
  z-index gotchas; revisit when a station has 50+ rows).
- **D5. No URL state for v1.** Filters are local component state;
  reload clears them. URL persistence (`?q=love&src=RG_PARTY`)
  is bookmarkable + shareable but adds complexity (parse, sync,
  encode-special-chars) without a real driver yet. Revisit when
  §16.4 trace versioning lands and we already need `?v=N`.
- **D6. No debounce on the search input.** Filtering 3,010
  Svelte-rendered nodes per keystroke benchmarks under 50ms in
  modern browsers; user-perceptible lag is not a v1 concern.
  Revisit if a real "typing in search feels laggy" report comes
  in or once a station has 30k slots.
- **D7. Empty-result state**: "No slots match the current
  filters." with an inline "Clear filters" button. Counter copy
  (D10) tells the user the impact ("Showing 0 of 3,010"); the
  clear button is the recovery path.
- **D8. Row sections with 0 matching slots are hidden entirely.**
  Showing every row header with "0 slots" is noise; hiding
  collapses the visible list to just the rows that have
  something. Header still appears for rows with ≥1 match, even
  if the user had collapsed it manually (D9).
- **D9. Collapse state survives filtering.** A row collapsed
  before the user typed a filter stays collapsed; when they
  expand it, only matching slots render inside. The collapse map
  (already keyed by row index) doesn't change shape.
- **D10. Match counter visible**: "Showing N of M slots" near
  the filter inputs, where N is matches-after-filter and M is
  total. Updates reactively. Tells the user "your filter
  worked" and "your filter is too aggressive" without them
  having to count.

**Decisions deliberately deferred.**

- **Slot-number / pool-index range filters** (use cases 5+6).
  Could add a 4th filter input for "show slots N..M". Real but
  niche; the free-text search handles "I'm looking for track
  X" which is the dominant phrasing. Add when someone wants
  contiguous-range slicing.
- **Highlight mode toggle** (use case 11). Implemented as a
  filter v1; toggle to "highlight in place" later if the
  context-preservation pain comes in.
- **Saved filter presets** ("RG_PARTY only" as a one-click
  button). Defer until presets exist for two more screens.

**Backend.** No backend work. Filtering is purely client-side
over the trace JSON the page already has (`trace.slots`).

**Frontend scope — likely 2 commits.**

1. **Filter implementation + Vitest.** New filter bar above
   the row sections; reactive `filteredRowGroups` derivation
   joining the existing `rowGroups`; match-counter + clear-
   filters affordance; empty-state copy. Vitest cases for
   each filter type, AND combinations, empty result state,
   counter accuracy.

2. **Playwright e2e + DESIGN.md §14.15.** One round-trip
   spec asserting that typing "Mon-1" in the search box
   collapses the visible slots; clearing the filter restores
   them. §14.15 captures the shipping decisions + any in-
   flight surprises.

Could merge both into one commit if the diff stays small;
two reads cleaner if the implementation evolves during execution.

**Stay-out-of-scope candidates.**

| Area                                              | Status              | Trigger                                    |
|---------------------------------------------------|---------------------|--------------------------------------------|
| URL state / shareable filtered links              | not now             | §16.4 lands and we already need `?v=N`     |
| Slot-number / pool-index range inputs             | not now             | Real "I want slots 100-200" report         |
| Highlight-in-place mode (vs filter)               | not now             | "I lose context when filtering" report     |
| Saved filter presets                              | not now             | Multiple screens have presets              |
| Regex search                                      | rejected            | 99% of users want substring; regex is power-tool feature |
| Export-filtered-view (download iTunes XML)        | rejected            | Filter is a viewing concern, not a content one |
| Search across multiple weeks at once              | not now             | §16.4 trace versioning would precede       |
| Debounced search                                  | not now             | "typing feels laggy" report                |
| Sticky filter bar                                 | not now             | Station with 50+ rows                      |
| Find-rejected-candidates                          | §11 v1.1            | excluded-candidates trace feature lands    |

**Implementation notes for future pick-up.**

- The filter logic is a pure derivation off `rowGroups` — keep
  it that way. Don't mutate `trace.slots`. The reactive
  pattern is `$: filteredRowGroups = rowGroups.map(...).filter(...)`
  with a `matchesFilters(slot)` predicate.
- Source / strategy dropdown options should be derived from the
  trace's actual content (`[...new Set(trace.slots.map(s =>
  s.source))]`), not from the station template's source list.
  A trace might reference sources the template no longer lists
  (renamed pool, removed pool); deriving from the trace shows
  what's actually present.
- The clear-filters button resets all three filter states in one
  click. Tests should pin this — a regression that only resets
  the search box would be a real UX bug.
- The match-counter copy uses `toLocaleString()` for thousands
  separators ("Showing 47 of 3,010"). At 3,010 slots the comma
  matters for readability — same reasoning as §14.11 disk-status.
- Filter inputs disabled state isn't needed — there's no async
  operation that races with filtering. Keep them always
  interactive.
- A future cross-row filter (e.g. "show only slots that match X
  AND are in row Y") would be a new dimension on the
  predicate. The current shape — predicate over slot, not
  predicate over row — supports it without restructuring.
- The empty-state "Clear filters" button uses the same handler
  as a dedicated clear button next to the inputs. One source
  of truth for "reset all filter state".

### 16.9 Multi-week prev / next navigation in trace viewer

**Why this matters.** Classics generates multiple weeks. With
2+ weeks in the database, jumping between traces means: back
to station detail → scroll to find the row → click → back →
scroll → click. The §14.5 punt list named "Multi-week
navigation" with the trigger "running ≥3 weeks regularly" —
that bar isn't quite met yet (Classics has 2 weeks at time of
writing) but the pain is starting to feel real and acute by
week 3+ is inevitable. A `← week NNN | week NNN →` pair in
the viewer header is one of the cheapest UX wins left.

**Use cases enumerated.**

| # | Use case                                                 | Today                  | Importance |
|---|----------------------------------------------------------|------------------------|------------|
| 1 | Jump to next week's trace from the current viewer        | Back × 2, click row    | Common (the dominant pain) |
| 2 | Jump to previous week's trace                            | Back × 2, click row    | Common     |
| 3 | Skip to the first / last available week                  | Back, scroll, click    | Occasional |
| 4 | Compare slot N across two weeks side-by-side             | Two windows            | Punt — §16.4 / §14.5 punt |
| 5 | Keyboard shortcut for prev/next (j/k or arrows)          | n/a                    | Punt — power-user feature |
| 6 | Disable prev when at first week / next when at last      | n/a                    | Real        |
| 7 | Show the prev/next week numbers (not just arrows)        | n/a                    | Real — context > brevity at this scale |
| 8 | Current week's position ("week 3 of 12")                 | n/a                    | Nice-to-have |

**Decisions to take when this slice runs** — drafted lightly
because the slice is small and the design surface is real but
narrow. Confirm in-flight:

- **D1. Position**: in the trace viewer's header area, near
  the meta dl. Probably as a small `< prev | next >` pair
  beside the existing week badge (`<span class="week">003</span>`).
- **D2. Data source**: fetch `getStationPlaylists(trace.station)`
  in `onMount`, find the current playlist's index in the
  sorted-by-week list, derive prev/next playlist IDs.
  Alternative: pre-compute server-side as part of the trace
  response (would need a backend change). Client-side is
  simpler for v1.
- **D3. Disabled state**: prev disabled at first week, next
  disabled at last. Visual styling matches the existing
  `.button:disabled` pattern.
- **D4. Show week numbers, not just arrows**. `← week 002 |
  week 004 →` reads as one glance "where am I, where can I
  go". Pure-arrow nav is brisk but loses context.
- **D5. Use `<a href>` not `<button>`** — same reasoning as
  §16.7 D1's "Edit station →" anchor: navigation, not action;
  let the browser handle middle-click / open-in-new-tab.
- **D6. No keyboard shortcuts v1** — punt to a real "I want
  j/k" report. Browser arrows / scroll behaviour stays
  uninterrupted.
- **D7. Position indicator is optional**. "Week 3 of 12"
  context could go alongside the meta dl. Defer if the
  prev/next links alone read clearly enough.

**Scope — likely 1 commit.**

- Trace viewer page (`playlists/[id]/+page.svelte`):
  - Fetch the station's playlist list in `onMount`
  - Derive `prevPlaylistId` / `nextPlaylistId` from current
    week's index
  - Render the prev/next pair in the header (or under it)
  - Visual + disabled states for boundaries
- Vitest cases: middle week (both links present), first week
  (prev disabled), last week (next disabled), single-week
  station (both disabled).
- Playwright case: load week 2 of 3, click next → week 3
  loads; click prev → back to 2.
- DESIGN.md §14.x captures shipping decisions.

No backend work. No schema change.

**Stay-out-of-scope candidates.**

| Area                                              | Status      | Trigger                                  |
|---------------------------------------------------|-------------|------------------------------------------|
| Side-by-side multi-week comparison                | §14.5 / §16.4 | Real "what changed week to week" report |
| Slot-level diff across weeks                      | §16.4       | Trace versioning lands first             |
| Keyboard shortcuts (j/k, arrows)                  | not now     | Real "I want j/k" report                 |
| URL state for the current week (already in URL via id) | n/a — the playlist id is the URL | already done |
| Position indicator ("Week 3 of 12")               | not now     | If user reports loss of context          |
| Cross-station navigation                          | rejected    | Different concern; users shouldn't navigate between stations via week prev/next |

**Implementation notes for future pick-up.**

- The data fetch can ride on the same `onMount` as
  `getTrace` / `getPlaylist` / `getStation`; one extra call
  to `getStationPlaylists`. Cheaper than reshaping the
  trace endpoint to include siblings.
- The "find current week's index" logic is small but deserves
  pinning in a Vitest case — sort by week number, find
  matching playlist by id, derive prev/next neighbours. A
  malformed list (no current playlist found) should fall
  through with both nav links disabled.
- If §16.4 (trace versioning) ever lands, the prev/next
  navigation should respect the current `?v=N` if present
  — clicking next on `/playlists/3?v=2` could go to
  `/playlists/4?v=2` if v=2 exists for week 4, or fall
  back to v=latest. Decide when §16.4 lands; this slice
  doesn't pre-decide.
- The links use the same `.button` styling family as
  §16.7's "Edit station →" — pre-existing pattern, no new
  CSS duplication. Reuse.
- The rendered text "← week 002" should match the existing
  `String(week).padStart(3, '0')` zero-padding for visual
  consistency with the heading badge.

---

## 17. Pre-§16.2 / pre-port roadmap

**Why this section exists.** The original top-10 of next-up
slices is fully cleared (§16.3 → §16.9, §14.12, §14.14). The
remaining queued work is three big pieces: §16.2 visual block
editor, §16.4 trace versioning, §16.5 accessibility pass. Plus
the long-term "port Rebirth Radio onto Station Studio" goal
named in §11. Before charging at §16.2 + the port, a handful of
smaller pieces should land to unblock them cleanly. This section
is the prioritised list, captured so the next session can pick
up cold.

### What the upcoming big slices actually need

**Visual block editor (§16.2)** — the §16.7 D10 caveat is
honest: today `<LayoutFieldset>` binds comma-separated *strings*
for `unitLabelsStr` and `rowStartOffsetsStr`; `<StationForm>`'s
`buildTemplate()` parses them to arrays at save time. A drag-
drop visual editor naturally binds *arrays*. So §16.2's "swap"
actually entails refactoring `<StationForm>`'s state too. Plus
the editor would be the third caller of `STRATEGY_OPTIONS` /
`ROLE_OPTIONS` / `RNG_OPTIONS` — `GET /api/meta` (§14.7 punt's
"third caller appears" trigger) is met. And `buildTemplate` /
`unpack` need to live in a sibling module so the editor can
consume them as pure functions.

**Rebirth Radio port (§11 long-term)** — weekly's template uses
weighted commercial-music selection, previous-week references,
and genre-tagged picks. None are expressible with Classics's
`window` + `pick` primitive set. Three new engine primitives
are *required*: `weighted`, `prev_ref`, `genre_pick`. Plus
weighted picks are uninterpretable without trace excluded-
candidates (§11 v1.1 punt) — "why didn't it pick X?" is the
dominant authoring question. Plus some import path so the
existing `weekly` template JSON can land in `stations/` without
hand-retyping.

### Tier 1 — Hard prerequisites

These slices literally cannot ship without these.

| # | Item | For | Cost | Reference |
|---|------|-----|------|-----------|
| 1 | `<LayoutFieldset>` state-shape refactor (strings → arrays) | §16.2 | ~50 LoC; tests already pin the round-trip via §16.7 S3 | §16.7 D10, §14.13 caveat |
| 2 | Engine primitives: `weighted`, `prev_ref`, `genre_pick` | port | 3–4 commits per primitive | §11, §4.3 |

### Tier 2 — Soft prerequisites

The slices work without these, but landing them first removes
real friction.

| # | Item | Why | Cost | Reference |
|---|------|-----|------|-----------|
| 3 | `GET /api/meta` (shared template constants) | The §14.7 "third caller" trigger is met by §16.2; landing now keeps wizard + edit + editor on one source of truth | ~30 LoC backend + ~20 LoC frontend | §14.7 punt |
| 4 | Extract `buildTemplate` / `unpack` to `web/src/lib/template.ts` | Editor needs them as pure functions, not `<StationForm>`-closure state | ~40 LoC refactor; existing tests catch regressions | §16.2 reuse note |
| 5 | Excluded-candidates in trace | Weighted picks are uninterpretable without "why not X?"; primary debug-loop for port | ~50 LoC across `engine/trace.py`, weighted strategy, viewer | §11 v1.1 |
| 6 | "Start from existing" preset OR raw-JSON import in wizard | Makes the port concrete — copy weekly's template JSON, edit, save; otherwise port is hand-retyping a 200-line template | ~30 LoC for preset; ~50 for JSON-paste import | §14.7 punt |

### Tier 3 — Cross-cutting cleanups

Worthwhile independent of the upcoming slices, but the timing
matters because each one's blast radius grows after §16.2 lands.

| # | Item | Why now | Cost | Reference |
|---|------|---------|------|-----------|
| 7 | §16.5 accessibility pass | Doing it before §16.2 means §16.5 is retroactive only; doing it after means §16.5 also has to audit the new editor. Either order works — see "ordering tradeoff" below | 4–5 commits | §16.5 |
| 8 | Schema migrations via Alembic | `Base.metadata.create_all()` doesn't handle column changes; the moment §16.4 / weighted / genre_pick / excluded-candidates lands, schema *will* change. First migration is trivial if Alembic is set up; painful catch-up later | ~1 commit (config + baseline migration) | (gap — not in any prior punt list) |
| 9 | Backup / restore tooling for `station_studio.db` | Single-user but precious. One `DELETE /api/stations/cls` and 12+ weeks of generated playlists vanish. Trivial `make backup` / `make restore <date>` targets that copy the SQLite file | ~5 LoC of Makefile | (gap) |

### Tier 4 — Defer-able

Real value, but no current pain and not unblocking anything
specific.

| # | Item | Defer until | Reference |
|---|------|-------------|-----------|
| 10 | `+page.ts` `load` functions for SSR / prefetch | "first paint feels slow" or "want share-link previews" report | §14.5 punt |
| 11 | Trace versioning (§16.4) | "I want my old trace back" report; or after §16.2 if iterative authoring loses too much trace history | §16.4 brief |
| 12 | Property-based engine tests (Hypothesis) | A primitive ships that's hard to enumerate (likely with `weighted` in #2) | §14.8 punt |
| 13 | Real iTunes XML integration test | An iTunes update breaks a real-world XML | §14.8 punt |
| 14 | Mutation testing (mutmut) | Confidence in coverage % drops | §14.8 punt |
| 15 | Visual-regression / screenshot tests | A CSS regression slips past Playwright DOM assertions | §14.8 punt |
| 16 | Multi-Python-version CI matrix | A user reports running on something other than 3.14 | §14.8 punt |
| 17 | API authentication / rate limiting | Station Studio runs anywhere outside localhost | (gap) |

### Recommended ordering

Stable execution path; each step listed unblocks the next or
reduces cleanup later. Numbers map to the tier tables above.

**Phase A — pre-block-editor unblocks** (~6 commits):

1. ~~**Alembic baseline** (#8)~~ — **shipped (§14.17)**
2. ~~**Backup tooling** (#9)~~ — **shipped (§14.18)**
3. ~~**`GET /api/meta`** (#3)~~ — **shipped (§14.19)**
4. **Extract `template.ts`** (#4) — moves `buildTemplate` / `unpack` out of the component
5. **`<LayoutFieldset>` arrays refactor** (#1) — makes §16.2's swap a real one-component swap

**Phase B — visual block editor (§16.2)**

The slice itself, lands cleanly on Phase A's groundwork. Real
second-station layout will surface the design shape; until then
§16.2's brief stays intentionally light per its own framing.

**Phase C — port prep + the port** (probably 6–8 commits):

6. **Excluded-candidates in trace** (#5) — prereq for #2's weighted being authoriable
7. **Engine primitives** (#2) — `prev_ref` first (smallest), `genre_pick` second (needs Track.genre_tags column — Alembic earns its keep), `weighted` third (biggest, depends on excluded-candidates)
8. **"Start from existing" / template-JSON import** (#6) — makes the port concrete instead of hand-retyping
9. **The port itself** — express weekly's template as `stations/rebirth-radio.json`, run weekly's existing byte-equality check against the new engine, retire weekly's hardcoded generator

**Phase D — accessibility (§16.5)**

Run §16.5's full pass last so it sweeps both retroactive a11y
gaps AND any new surface area added by §16.2 + the port. Single
audit, single set of fixes, single set of axe-clean assertions
in CI.

### Ordering tradeoff: §16.5 timing

Two coherent placements considered:

- **Phase A position** (pre-§16.2): §16.5 only audits what's
  already shipped; cleaner scope. But block editor lands after
  and adds new a11y debt — eventually a "second pass" is needed.
- **Phase D position** (post-port, current recommendation): one
  comprehensive sweep. But the existing a11y debt sits longer
  before being fixed. And the existing §16.5 brief explicitly
  says "wait for trigger" (real assistive-tech user request).

The current ordering puts §16.5 last because: (a) its trigger
hasn't fired (no real a11y user report), and (b) doing it last
absorbs the new editor + port surface into one sweep rather
than fragmenting across two.

If a real assistive-tech user lands before §16.2 ships, jump
§16.5 to Phase A. The single-pass-vs-two-pass tradeoff isn't
worth deferring a real user need.

### Skipped / explicitly deferred

| Item | Why skipped |
|------|-------------|
| API auth / rate limiting | Single-user tool; out of scope until cross-network deployment |
| Cross-station diff in trace viewer | Way out of scope; the within-station search/filter (§14.15) covers the realistic use cases |
| Soft-delete with restore for stations | §16.3 already considered and rejected; git history of templates + deterministic regen are enough |
| Auto-generate weekly via scheduler | §11 v1.1; weekly migration matures first |

### Implementation notes for cold pickup

- **Phase A is small and parallelisable.** #8 + #9 + #3 are
  independent; could land in any order or even one combined
  commit if a single review is preferred. #4 and #1 are the
  load-bearing refactors that actually shape what §16.2
  becomes.
- **#1 / #4 should be paired with the round-trip test from
  §16.7 S3** — that test is the safety net for the refactor.
  Read `web/src/lib/components/StationForm.test.ts` for the
  pattern; the round-trip case will catch most regressions.
- **#2 weighted needs care around RNG seed handling.** Weekly's
  existing `random.Random(seed)` pattern works; reproduce it
  exactly. Don't introduce a different RNG provider mid-port
  — byte-equality check against the old generator is the port's
  acceptance criterion.
- **#5 excluded-candidates** changes the `playlist_trace.slots`
  JSON shape. Older traces won't have `excluded` fields; the
  viewer must handle the optional. New schema migration via
  #8's Alembic.
- **The port's byte-equality check** is the goal: `weekly`'s
  existing test suite generates a known week and compares the
  output against an iTunes XML file. The same week generated
  via station-studio's engine must produce the same bytes.
  When that passes, the port is done; weekly's hardcoded
  generator can be retired.
- **§16.4 (trace versioning) is a wildcard.** Could land
  before §16.2 if iterative authoring loses too much trace
  history. Defer-decide based on §16.2's authoring pain.
- **The §16.5 ordering decision is reversible.** If the next
  session prefers Phase A position for §16.5, the existing
  brief covers retroactive-only audit cleanly; the §16.2
  block-editor a11y becomes its own follow-up under §16.5's
  same toolchain.


