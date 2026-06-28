# Your first station in 10 minutes

A working playlist from a fresh install, no music library required.
The bundled **Starter Station** auto-seeds on first boot with a
synthetic pool of 50 tracks across 10 artists, so you can play with
the engine before pointing it at your own music.

!!! tip "What you'll need"
    - Python 3.11+ (or Docker)
    - A terminal
    - ~10 minutes

By the end you'll have generated a 168-slot weekly playlist, viewed
the per-pick trace explaining every choice, and exported it as
iTunes XML.

## 1. Install (~2 min)

Pick the channel that matches what you've already got. All three
ship the same UI + API + bundled example stations; the difference
is the wrapper.

=== "Docker"

    Zero Python or Node needed; the image is multi-stage (~312MB)
    and includes the SvelteKit static SPA + FastAPI backend.

    ```bash
    git clone https://github.com/raby/station-studio.git
    cd station-studio
    docker compose up --build
    ```

    First run takes ~2 min to build; later runs reuse the image.

=== "Pip wheel"

    Single-process app on `localhost`. Builds the SvelteKit static
    bundle into the wheel; you don't need Node at runtime.

    ```bash
    git clone https://github.com/raby/station-studio.git
    cd station-studio
    make wheel
    pip install dist/station_studio-*.whl
    station-studio serve
    ```

=== "Dev clone"

    For hacking on Station Studio itself. Two processes (FastAPI
    :8001 + SvelteKit :5173) with hot-reload on both.

    ```bash
    git clone https://github.com/raby/station-studio.git
    cd station-studio
    make install
    make dev
    ```

    Open <http://localhost:5173> instead of :8001 — Vite's dev
    server proxies the API.

Once the server is running, open <http://localhost:8001> (or
:5173 for dev clone). You'll see the **landing page** with four
auto-seeded example stations.

## 2. Look at the Starter Station (~1 min)

Click **Starter Station** in the stations list. The detail page
shows:

- **Template summary** — 168 slots / week, 2 strategies (`weighted`
  for content, `jingle` every 8 slots)
- **Source pools** — three auto-imported on first boot:
  `STARTER_CONTENT` (50 tracks, role: content),
  `STARTER_JINGLES` (5 jingle tracks, role: content), and
  `REFERENCE_ARCHIVE` (role: reference — declared on the
  template but not placed by any layout block in the Starter;
  it's there as the third pool you can reach for if you fork).

!!! note "Starter is intentionally small + stateless"
    Smallest, most approachable shape — one slot per hour for a
    week. Stateless cursors (`persist: false`), so each week
    generates from pool position 0. Perfect for "how does the
    slot+strategy model work?" before you fork anything bigger
    or move on to the Stateful Starter for cross-week cursor
    state.

## 3. Generate week 1 (~10 sec)

Click the big **Generate week 1 →** button. The engine runs in
the background (you'll see a progress indicator); on completion
the page reloads with the new playlist as the first row of a
table:

| Week | Slots | Generated | Trace | (actions) |
|---|---|---|---|---|
| 001 | 168 | just now | `b3c8…` | view → / ↻ regen / ✕ delete |

(The three rightmost columns are unlabelled in the UI — the
buttons carry their own meaning. The headers exist for screen
readers via `visually-hidden` labels.)

Click **view →**.

## 4. Read the trace (~3 min)

The trace viewer (`/playlists/{id}`) is where Station Studio earns
its keep. Every one of the 168 slots is rendered as a row showing:

- **#** — slot number (0 to 167)
- **Source** — coloured chip showing which pool the engine picked
  from (`STARTER_CONTENT` blue, `STARTER_JINGLES` orange)
- **Strategy** — the rule that placed this slot (`weighted` or
  `jingle`)
- **Pool / Track / Artist** — the actual track, plus its position
  in the source pool

Try this:

1. **Search** for an artist name in the filter bar. The slot
   count badge shows "Showing N of 168".
2. **Filter by source** — pick `STARTER_JINGLES` from the dropdown.
   You should see exactly 21 jingle slots (every 8th: 0, 8, 16…).
3. **Click "🗺 Visualise template"** in the station header (or
   navigate to `/stations/starter/visualise`) — the chips on
   THAT page open a DetailPanel with full block / macro
   information. Trace-viewer chips here are display-only
   spans; the visualise canvas is where chip-click editing
   lives.

The "Why not these? (N)" toggle on each row (when there are
rejections) shows the engine's reasoning for the picks it
considered and skipped — useful when a strategy uses
artist-dedup / recent-use constraints.

## 5. Export to disk (~30 sec)

Click **Export to disk** in the trace viewer's actions strip. The
engine writes the iTunes XML to the configured output directory
and reports back the path + byte count in a status banner that
looks roughly like:

> **Wrote:** `/abs/path/out/starter/001.xml` (12 KB)

The actual path is the **literal string** in that banner — read
from there, don't infer. The default output dir is `out/`
(relative to the process working directory: where you ran
`station-studio serve` from). To set an absolute path that
survives restarts, write a `Config` row with key
`playlist_output_dir`:

```sql
-- one-time, against ~/.station-studio/station_studio.db
INSERT INTO config(key, value) VALUES
  ('playlist_output_dir', '/Users/you/Dropbox/itunes-sync');
```

If you sync that directory to a Dropbox / OneDrive folder shared
with your iTunes box, every generation lands in your library
automatically. The same flow drives the Rebirth Radio production
loop (2,196 slots/week, Mon-Sun, mixed strategies).

## 6. Regenerate + version-pick (~30 sec)

Back on the station detail page, click the **↻ regen** button
on the week-001 row. The engine re-runs against the same
template; on completion the page reloads. Visit the trace
viewer again — at the top you'll now see a **version picker**
dropdown showing `v2 (current)` and `v1`.

This is §16.4 trace versioning in action: the prior trace
isn't deleted, it's preserved as a version you can switch
back to. Pick `v1` to see the previous picks; pick `v2` to
return to the current. Each version has its own slot table,
seed, generated-at timestamp, and trace-id.

!!! note "Why isn't this 'Generate week 2'?"
    The Starter Station is stateless (cursors set
    `persist: false`), so week 2 looks identical to week 1
    aside from RNG variance — there's nothing to demonstrate
    by stepping forward. The **Stateful Starter** is built for
    that demo: 336 slots/week with a persistent cursor that
    advances 24 positions per week, cycling every 14 weeks.
    Open it from the sidebar after this tutorial finishes.

## What you just did

| Step | Took | What landed |
|---|---|---|
| Install + boot | ~2 min | UI + API + 4 auto-seeded stations (Starter / Stateful Starter / Rebirth Classics / Rebirth Radio) + 4 auto-imported pools |
| Look at template | ~1 min | Read 168-slot shape, 2 strategies, 3 declared sources |
| Generate w1 | ~10 sec | 168 picks via `weighted` + `jingle` strategies |
| Trace viewer | ~3 min | Per-pick attribution + filter + rejection reasoning |
| Disk export | ~30 sec | iTunes XML on disk, ready for Dropbox sync |
| Regenerate + version pick | ~30 sec | §16.4 trace versioning: prior trace preserved, switchable via dropdown |

Total: well under 10 minutes if you skip the trace deep-dive.

## Where next

<div class="grid cards" markdown>

- :material-school: **Stateful Starter** — your second station,
  with persistent cursors. Click **Stateful Starter** in the
  sidebar to see what the engine does with `sequential` +
  `prev_ref` strategies across multiple weeks. Template lives
  on disk at `stations/stateful-starter.json` if you want to
  read it side-by-side with the trace.
- :material-book-open-variant: **[Reference](../reference/index.md)** —
  template grammar, all 8 strategies, the REST API surface, CLI
  commands.
- :material-puzzle: **[Architecture](../architecture/index.md)** —
  the design notes future contributors need: how cursor advance
  works, how the trace records picks, how the visual editor's
  sidecar grammar maps to the template.
- :material-source-fork: **Fork a starter** — back on the
  Starter Station detail page, click **Fork station** in the
  header to copy its template under a new slug; tweak via the
  wizard at `/stations/<new-slug>/edit` or by editing
  `stations/<new-slug>.json` directly.

</div>

!!! tip "When you're ready to use your own music"
    Open the **Sources** page in the sidebar and drag in an
    iTunes XML export of one of your playlists. Name the pool,
    set the role to `content`, hit Import. Then fork a starter
    and edit its `sources:` block to use your new pool name. See
    [Reference → Template grammar](../reference/index.md) for the
    full schema.
