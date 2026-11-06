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
  diff and a Revert per song (below).

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
too is one line). A song's Revert on the Slots tab undoes its rule's
edits the same way (`editsForRule`: a song's rule is its `block` − 1;
a song the edits took out of the week is in the station as saved, so
it's followed through the queue to its rule now, or to the edit that
removed it).

Because each edit replays on top of the ones before, undoing one can
leave a later one meaning something else: an edit addresses a rule by
its slot, so after a rule added or removed ahead of it, it would change
the next rule along; settings for a retyped rule belong to its new
type; and section edits name sections that may not be there. `undoSet`
works out which later edits build on the one undone (it follows rules
through later adds, removes and moves, and checks each later edit still
does what it did, against the queue as it would be), and the editor
names them and asks before
undoing them too (`confirmUndo`). Dropping the edit a save failed on
works the same way, before the rest is saved. A randomised test undoes
every edit of many generated queues and checks that what stays still
changes the same rules and does something.

## Moving rules

The engine plays a flat station's rules (`layout.flat_slots`) in list
order and ignores the sidecar, so a move that should change when songs
play reorders `flat_slots`: `moveRules` (a run of rules, to before
another; they join a section, among its blocks by when they play) and
`moveSection` (all of a section's rules, before another section's, and
its place in `layout_order` with them). The sidecar follows: every
atomic block is renumbered to where its rule now plays. A macro moves
whole or not at all, and a move that would leave one out of place is
refused. A `moveRules` with `to` just after the run (`from + count`)
only changes the rules' section: they play where they did. `moveBlock`,
older, only regroups: the songs play where they did.

A station built from a repeating row (`layout.row_template`, a day,
say) is moved the same way, numbered by the row's blocks: every row
plays the row, so a move reorders `row_template` and is made in every
row. Its sidecar's atomic blocks carry a row (`row_idx`) as well as
their place (`block_idx`); every row's are renumbered, a moved block
joins the target section when that section has blocks of its row (so a
section per day keeps its day's), and each section's blocks are kept
in play order. Such a station can't move rules while its sections use
macros, or repeat for every row (`repeat_with_offsets`).

Both are in `engine/rule_moves.py` on the server and mirrored in
`edit-ops.ts` for the preview; `tests/fixtures/rule_moves.json` holds
the cases both are checked against, so the preview shows what saving
does. Undo, a song's Revert and an open rule panel follow a row's
blocks through moves just as a flat station's rules.

### Dragging in the song list

The song list (the editor's Songs tab and a week's page) queues these
edits. A song's grip moves its whole rule (a macro's song, the whole
macro); Shift-click selects the songs between two, rounded out to whole
rules; a section's grip moves the section, when its rules play
together. Each can be dragged, or picked up from the keyboard (Space,
the arrows, Space; Escape puts it back), and a live region says where
it would go. Focus follows what moved once the list shows the new
order. Dropped over the line between its section and the next (or on
a section's heading), a song joins that section and plays where it
did.

On a station built from a repeating row the list says so: a move is
made in every row. Each row's first song says which row it is (its
unit label, "Mon"), what's carried is marked in every row, and from
the keyboard the places offered are those of the row the song was
picked up in (every row has the same). A selection is of one row's
songs.

Moves are numbered by places in the list, so they're only taken from a
list of the rules as they are now. The list waits while it's behind:
in the editor, until the preview of the latest queue is in (the
preview's template and sidecar must be the staged ones); on a week's
page, until the session's preview was made with the current queue
(`stagedFor`), and with nothing queued, only for a week that lines up
with the rules (`weekMatchesRules`: a week made before they last
changed may not). Moving is off while the list is searched or
filtered.

The changed-song marks follow each song's rule back through the queue
(`slotAsSaved`) rather than comparing place by place, so rules a move
only shifts along stay unmarked, and the rules moved say **moved** (if
they now play in a different order from the saved station: one moved
back, or only into another section, doesn't).
Shuffled rules can still pick other songs where they play now: those
say **changed**.

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
