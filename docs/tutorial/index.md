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
- **Source pools** — `STARTER_CONTENT` (50 tracks) and
  `STARTER_JINGLES` (5 tracks), both auto-imported on first boot
- **Cursor state** — empty until you generate

!!! note "Starter is intentionally small"
    Smallest, most approachable shape — one slot per hour for a
    week. Perfect for "how does the slot+strategy model work?"
    before you fork anything bigger.

## 3. Generate week 1 (~10 sec)

Click the big **Generate week 1** button. The engine runs in the
background (you'll see a progress indicator); on completion the
page reloads with the new playlist row in the table:

| Week | Slots | Generated | Trace | | | |
|---|---|---|---|---|---|---|
| 001 | 168 | just now | `b3c8…` | [view →](#) | ↻ regen | ✕ delete |

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
3. **Click any slot's chip** — wait, that's a §16.2 visual-editor
   feature for a future tutorial. Just admire the layout for now.

The "Why not these? (N)" toggle on each row (when there are
rejections) shows the engine's reasoning for the picks it
considered and skipped — useful when a strategy uses
artist-dedup / recent-use constraints.

## 5. Export to disk (~30 sec)

Click **Export to disk** in the trace viewer's actions strip. The
engine writes the iTunes XML to the configured output directory
(`~/.station-studio/out/starter/001.xml` by default) and reports
back the path + byte count:

> **Wrote:** `~/.station-studio/out/starter/001.xml` (12 KB)

If you sync `~/.station-studio/out/` to a Dropbox / OneDrive
folder shared with your iTunes box, every generation lands in
your library automatically. The same flow drives the Rebirth
Radio production loop (2,196 slots/week, Mon-Sun, mixed
strategies).

## 6. Generate week 2 (~10 sec)

Back on the station detail page, click **Generate week 2**.

Cursor state from week 1 carries forward — open `STARTER_CONTENT`
in the trace viewer's filter and you'll see the engine resumes
where it left off rather than restarting at pool position 0. This
is the "shuffle, but smarter" promise: weeks chain.

Try **regenerating** week 1 (the ↻ button). The trace updates;
the old version is preserved (§16.4 trace versioning) and you can
switch between them via the picker dropdown at the top of the
trace viewer.

## What you just did

| Step | Took | What landed |
|---|---|---|
| Install + boot | ~2 min | UI + API + auto-seeded starter station + 2 pools |
| Look at template | ~1 min | Read 168-slot shape, 2 strategies, 2 sources |
| Generate w1 | ~10 sec | 168 picks via `weighted` + `jingle` strategies |
| Trace viewer | ~3 min | Per-pick attribution + filter + rejection reasoning |
| Disk export | ~30 sec | iTunes XML on disk, ready for Dropbox sync |
| Generate w2 | ~10 sec | Cursor state persistence demonstrated |

Total: well under 10 minutes if you skip the trace deep-dive.

## Where next

<div class="grid cards" markdown>

- :material-school: **Stateful Starter** — your second station,
  with persistent cursors. Open
  [stations/stateful-starter.json](https://github.com/raby/station-studio/blob/main/stations/stateful-starter.json)
  in your editor + read the template; then click it in the UI to
  see what the engine does with `sequential` + `prev_ref`
  strategies.
- :material-book-open-variant: **[Reference](../reference/index.md)** —
  template grammar, all 8 strategies, the REST API surface, CLI
  commands.
- :material-puzzle: **[Architecture](../architecture/index.md)** —
  the design notes future contributors need: how cursor advance
  works, how the trace records picks, how the visual editor's
  sidecar grammar maps to the template.
- :material-fork: **Fork a starter** — click **Fork station** on
  the Starter Station detail page to copy its template under a new
  slug; tweak the JSON via the wizard or via your editor (the
  station file lives under `stations/<slug>.json`).

</div>

!!! tip "When you're ready to use your own music"
    Open the **Sources** page in the sidebar and drag in an
    iTunes XML export of one of your playlists. Name the pool,
    set the role to `content`, hit Import. Then fork a starter
    and edit its `sources:` block to use your new pool name. See
    [Reference → Template grammar](../reference/index.md) for the
    full schema.
