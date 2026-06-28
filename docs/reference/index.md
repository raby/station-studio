# Reference

The canonical surface for Station Studio's template grammar,
sampling strategies, REST API, and CLI commands.

<div class="grid cards" markdown>

- :material-format-list-bulleted-type: **[Template grammar](template-grammar.md)** —
  the JSON schema for a station: `station`, `cadence`, `rng`,
  `sources`, `cursor`, `layout`, optional `rules`. Required +
  optional fields with their constraints.
- :material-shuffle-variant: **[Strategies](strategies.md)** —
  all 8 sampling strategies (`pick`, `weighted`, `mix`,
  `window`, `sequential`, `jingle`, `archive_ref`, `prev_ref`)
  with required fields, semantics, and a "when to use" line
  for each.
- :material-api: **[API](api.md)** — auto-rendered Swagger UI
  from the FastAPI app's `app.openapi()` schema. Always in
  lockstep with the running code; CI regenerates the JSON
  before every docs build.
- :material-console: **CLI** — auto-rendered from
  `station-studio --help`. *(Landing in §16.15 c5.)*

</div>

!!! tip "Looking for the engine internals?"
    See [Architecture](../architecture/index.md) for curated
    `DESIGN.md` extracts covering cursor advance semantics,
    trace recording, and the editor sidecar grammar.
