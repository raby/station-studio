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
| Visual block editor                         | next (§16.2) | v2; operates on same JSON as wizard                     |
| Station creation wizard (UI)                | done (14.7)  | Produces the template JSON; reuses `web/src/lib/api.ts` |
| Wizard polish (presets, per-field errors)   | later (14.7) | See 14.7 table; wait for real-user pain                 |
| In-UI generate button + per-row regenerate  | done (14.9)  | Closes wizard → trace-viewer loop; per-row regen too    |
| Delete / station-reset UI                   | next (§16.3) | Delete playlist; full station nuke; cascades            |
| Trace versioning (history of regenerations) | later (§16.4)| Schema change; only after §16.1 lands and bites         |
| Migrate Rebirth Radio onto Station Studio   | long-term    | Needs `weighted`, `prev_ref`, `genre_pick` primitives   |
| iTunes XML export of Classics playlists     | done (14.1)  | Lifted from weekly, station-scoped; browser + disk paths|
| Disk-export prune endpoint                  | on demand    | `POST /api/playlists/export/prune` — wait until clutter |
| Scheduler (auto-generate weekly)            | v1.1         | Weekly has APScheduler; port when stable                |
| Artist aliasing / dedup                     | not needed   | Classics has no dedup; engine grows this when needed    |
| Excluded-candidates in trace                | v1.1         | Nice-to-have per user; shown in viewer when added       |
| Jingles-as-primitive                        | only if      | Only if a future random-selection station needs them    |
| Trace viewer (UI)                           | done (14.5)  | `/playlists/[id]` — per-slot reasoning, grouped by row  |
| Trace-viewer polish                         | later (14.5) | Search, virtualisation, SSR, multi-week nav, compare    |
| Test plan + CI                              | done (14.8)  | 4-tier; backend 98% lines; 26 Vitest + 9 Playwright     |
| Test follow-ups (coverage gate, viz tests)  | later (14.8) | See 14.8 table                                          |

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
  `<html>` from `onMount`; specs wait on that attribute.
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

**Coverage shipping** — last measurement on commit `<this>`:

| Layer        | Coverage                                                   |
|--------------|------------------------------------------------------------|
| Backend      | 98% lines (unchanged — no backend changes this slice)      |
| Frontend Vitest | landing 100%, station-detail ~99%, wizard 95%, lib/api 100% |
| Playwright   | 15 specs across 6 files                                    |

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
| Vitest tests for trace viewer       | `playlists/[id]/+page.svelte` is 0% lines under Vitest (Playwright covers it)| Trace viewer grows branching that's hard to e2e |
| Coverage gate in CI                 | Coverage runs but no failing threshold                                       | First time coverage silently drops              |
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
| In-UI generate button    | Shipped (14.9). Empty-CTA + next-week + advanced + per-row regen.|
| Visual block editor      | Deferred (v2). See §16.2.                                        |
| Delete / station-reset   | Deferred. See §16.3.                                             |
| Trace versioning         | Deferred. See §16.4 (queued behind §16.1 by design).             |
| Scheduler                | Deferred (v1.1).                                                 |
| Weekly migration         | Long-term.                                                       |

Active TODOs live in §11 (deferred), §14.5 (trace-viewer follow-ups),
§14.7 (wizard follow-ups), §14.8 (test-plan follow-ups), and §16
(next up — generate button, block editor, reset). Don't start chipping
at the follow-ups until Station Studio earns them — the project's
whole premise is "build the smallest thing that proves the next
decision", not "polish the thing we've got". §16 is the queue we
*are* going to chip at.

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

### 16.3 Delete / station reset

**Scope narrowed.** Originally framed as "reset / regenerate". The
regenerate half migrated into §16.1 (per-row regen + advanced-form
overwrite); this slice is now just *delete*. Two affordances:

1. **Delete a single playlist.** Per-row "Delete" link in the
   station-detail playlists table. Pinned behind a typed-confirm or
   two-click pattern. Optional checkbox "also remove disk export".
2. **Delete a station entirely.** From the landing page or station-
   detail header. Cascades through every playlist + trace + entry.
   Useful for cleaning up wizard test stations (today the only way
   is direct SQL).

**Backend additions.**

- `DELETE /api/playlists/{id}` — removes playlist + entries + trace.
  Optional `also_disk=true` query param triggers `out/{slug}/{week:03d}.xml`
  removal (lives behind a flag because most users don't disk-export).
- `DELETE /api/stations/{slug}` — full station nuke. Cascades. Returns
  `204 No Content` on success, `404` if slug unknown.

**Why queued behind 16.1 and 16.2.** Generate-first earns the basic
loop. Block editor unlocks station #2. Delete only matters once
either of those is in real use — and even then, the underlying API
overwrites correctly via §16.1's regen, so this is a
clean-up-after-yourself affordance, not a critical-path one.

**Stay-out-of-scope candidates.**

- Trash / soft-delete with restore — overkill for a single-user tool.
  Hard delete + git history of the template JSON is enough.
- Bulk delete — wait for it to feel painful.
- Disk-export prune endpoint — already deferred in §14.3; this slice
  may or may not pull it forward.

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

