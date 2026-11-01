# Live preview pipeline

How the visual editor's "what would this template generate?"
preview works without committing to disk.

## The transport

```text
Editor mutation       ──  user edits a chip / block / macro / section
  │
  ▼
applyEdit() in opLog  ──  applies the edit locally to the staged
  │                       template + sidecar (no API call)
  ▼
_schedulePreview()    ──  debounced; posts the staged state to
  │                       POST /api/stations/{slug}/jobs/preview
  ▼
Job-id transport      ──  in-memory JobStore (§16.11) returns a
  │                       job_id; client polls
  │                       GET /api/jobs/{job_id} until terminal
  ▼
TracePane render      ──  the preview trace renders next to the
                          canvas; chips that changed cross-light
                          via §16.2.7's diff classifier
```

## Songs and Slots

The preview column shows the trace two ways, on two tabs (the choice
is remembered per station):

- **Songs** (the default): the week page's song list (`SongList`) for
  the staged trace, read against the staged template, so each song's
  rule reads as it now is. With edits queued it marks the songs they
  change, which needs the **saved** state's trace too: whenever it's
  wanted and not cached, the debounced preview fetches it *before* the
  staged one, because `POLICY_CANCEL_EXISTING` (below) means two
  previews for a station can't run side by side.
- **Slots**: one technical row per slot, with the compare-against-saved
  diff and per-slot revert (a song's rule is its `block` − 1; `slot`
  counts songs).

A click on either jumps to the song's block on the canvas; a song also
opens its rule's panel. With the preview on, that panel (any rule's,
from a song or a chip) shows at the top of the column, not over the
window's edge, so the songs stay in view: the canvas still owns the
panel, and a small `portal` action shows its element in the column.
The open rule's songs are highlighted in the list.

The canvas (and so its rule panel) renders the **staged** state, as the
preview does: queued changes show on the blocks, and an edit made there is
recorded against what's on screen. That matters because the queue replays
edits in order, each on top of the ones before.

## Undoing a queued change

The queued changes are listed above the tabs, in plain words, each with
an **Undo** (`QueuedChanges`, shared with week pages; `queuedChanges`
makes the lines, one per change, so a change made to identical rules
too is one line). A slot's revert on the Slots tab undoes its rule's
edits the same way.

Because each edit replays on top of the ones before, undoing one can
leave a later one meaning something else: an edit addresses a rule by
its slot, so after a rule added or removed ahead of it, it would change
the next rule along; settings for a retyped rule belong to its new
type; and section edits name sections that may not be there. `undoSet`
works out which later edits build on the one undone (it follows rules
through later adds and removes, and checks section edits against the
queue as it would be), and the editor names them and asks before
undoing them too (`confirmUndo`). A randomised test undoes every edit
of many generated queues and checks that what stays still changes the
same rules.

Staged edits share one per-station queue in `localStorage` with the
week pages' song-list editor, so a station with edits queued opens its
editor with the preview on: with it off they'd be discarded.

## Why a job-id, not a sync response

`engine.generator.generate()` for a Rebirth-Classics-sized template
takes seconds, not milliseconds. A sync POST would block
the editor's UI thread + leave no way to cancel a stale preview
when the user keeps editing. Job-id transport:

- Returns immediately with `job_id`.
- Client polls + can cancel by deleting the job.
- `POLICY_CANCEL_EXISTING` server-side: if a new preview job
  starts for the same `(slug, session)` while an old one is
  still running, the old job's traces get evicted from the
  store (grace window for fast cancellations).
- Generation-gated client-side: late-arriving results from a
  superseded job get discarded before render.

## What the preview never touches

The preview path is **read-only**:

- No `Playlist` row created.
- No `PlaylistVersion` appended.
- No `MixUsage` written.
- The DB sees ZERO writes — `JobStore` is in-memory.

Save commits the staged state via `POST /api/stations/{slug}/
sidecar/batch` (§16.2.6.t2 transactional batch endpoint) which
applies every staged op as one atomic transaction. Until Save
fires, the preview is just a projection.

## Read more

DESIGN.md §16.2.6 + its `.tX` follow-ups (§16.2.6.t1 staged-edit
recovery, .t2 transactional batch, .t3 click-trace-row jump-to-
chip, .t4 silent-evict grace window, etc.) — 13 follow-up
promotions land alongside the main slice.

[DESIGN.md →](https://github.com/raby/station-studio/blob/main/DESIGN.md)
