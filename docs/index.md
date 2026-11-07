# Station Studio

Generative iTunes XML playlists with a visual block editor.

Station Studio turns a JSON **template** (sources + cursor + layout) into
deterministic weekly playlists for iTunes / Apple Music. It's the OSS
tool behind [Rebirth Radio](https://github.com/raby/rebirth-radio)'s
2,196-slot weekly programme.

## What's in the box

- **Generative engine** — 8 sampling strategies (window, pick,
  sequential, jingle, weighted, mix, prev_ref, archive_ref), seeded RNG for
  reproducibility, multi-week chains via persistent cursor state.
- **Visual block editor** — `/stations/{slug}/visualise` renders the
  template as a drag-and-drop canvas. **Preview songs** shows any
  week's songs beside it as you edit: each with its rule in plain words
  and why it's there, the ones your changes alter marked. Click a song
  and its block lights up and its rule opens beside the list (or see
  one row per slot on the Slots tab). Drag a song, a run of songs or a
  section by its grip (or from the keyboard) to change when it plays
(on a station built from a repeating day, in every day).
  Changes wait until you save, listed in plain words, each with an
  Undo.
- **Trace versioning** — every regenerate keeps the prior trace as
  a version; pick / rollback / tag via the trace viewer.
- **Disk export** — iTunes XML written to a configurable directory;
  the typical loop is a OneDrive/Dropbox folder synced to a Windows
  iTunes box.

Two more apps live alongside it, each released on its own:

- **[Station Player](player/index.md)** — plays radio and music; for
  station makers, Station Studio's weeks, recording and the play log.
- **[Station Server](server/index.md)** — puts a station on air: plays
  its week, live, to a SHOUTcast or Icecast server, and publishes what's
  on now and next.

## Where to start

<div class="grid cards" markdown>

- :material-rocket-launch: **[Tutorial](tutorial/index.md)** —
  your first station in 10 minutes
- :material-book-open-variant: **[Reference](reference/index.md)** —
  template grammar, strategy reference, API, CLI
- :material-puzzle: **[Architecture](architecture/index.md)** —
  how the three apps are designed, for contributors
- :material-radio: **[Station Player](player/index.md)** —
  install it, and what it does
- :material-broadcast: **[Station Server](server/index.md)** —
  put a station on air

</div>

!!! note "How it's built"
    [Design](architecture/design.md) describes how the three apps are
    put together. The project's full record of decisions, slice by
    slice, is the
    [design log](https://github.com/raby/station-studio/blob/main/docs/history/design-log.md).
