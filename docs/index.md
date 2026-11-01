# Station Studio

Generative iTunes XML playlists with a visual block editor.

Station Studio turns a JSON **template** (sources + cursor + layout) into
deterministic weekly playlists for iTunes / Apple Music. It's the OSS
tool behind [Rebirth Radio](https://github.com/raby/rebirth-radio)'s
2,196-slot weekly programme.

## What's in the box

- **Generative engine** — 8 sampling strategies (pick, weighted, mix,
  window, sequential, jingle, archive_ref, hook), seeded RNG for
  reproducibility, multi-week chains via persistent cursor state.
- **Visual block editor** — `/stations/{slug}/visualise` renders the
  template as a drag-and-drop canvas. **Preview songs** shows any
  week's songs beside it as you edit: each with its rule in plain words
  and why it's there, the ones your changes alter marked. Click a song
  and its block lights up and its rule opens beside the list (or see
  one row per slot on the Slots tab).
- **Trace versioning** — every regenerate keeps the prior trace as
  a version; pick / rollback / tag via the trace viewer.
- **Disk export** — iTunes XML written to a configurable directory;
  the typical loop is a OneDrive/Dropbox folder synced to a Windows
  iTunes box.

## Where to start

<div class="grid cards" markdown>

- :material-rocket-launch: **[Tutorial](tutorial/index.md)** —
  your first station in 10 minutes
- :material-book-open-variant: **[Reference](reference/index.md)** —
  template grammar, strategy reference, API, CLI
- :material-puzzle: **[Architecture](architecture/index.md)** —
  curated design notes for contributors

</div>

!!! note "Docs site is new"
    These pages land alongside Station Studio v1.4. If something
    you need isn't here, the canonical project notes still live
    in `DESIGN.md` at the repo root —
    [browse it on GitHub](https://github.com/raby/station-studio/blob/main/DESIGN.md).
