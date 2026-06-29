# Sidecar + DB-as-truth

How the visual block editor's source-of-truth lives.

## The two halves of a station

A station has TWO JSON documents:

| Document | Purpose | Shape |
|---|---|---|
| `Station.template` | The **engine input** — sources / cursor / layout. Read by `engine.generate.generate()`. | `stations/<slug>.json` format. |
| `Station.sidecar` | The **editor's mental model** — sections, blocks, macros, colour assignments, layout order. Read by the visual editor at `/stations/<slug>/visualise`. | A separate grammar; validated against the template at write time. |

The sidecar is **not part of the engine contract**. It's a
visualisation overlay: which slots group into "Morning Drive",
what colour the chips render in, whether a macro is collapsed or
expanded. Stripping it changes nothing about generation.

## DB-as-truth (§16.2.5.t3)

Pre-§16.2.5, the sidecar lived on disk at `stations/<slug>.editor.
json` and was loaded at request time. The editor's mutations
(rename a section, move a block, edit a macro) wrote DIRECTLY to
disk, racing with concurrent reads from the same file.

§16.2.5 moved it into the DB as a JSON column on the `Station`
table. The disk file is now an **export artifact** (committable to
git, shared across machines); at runtime the DB column is
authoritative. Recovery escape hatch: `UPDATE station SET
sidecar = NULL WHERE slug = 'foo'` + restart triggers the
`derive_sidecar()` re-population.

## Grammar drift detection

`engine.editor_metadata.validate_sidecar_grammar()` enforces the
shape contract; `_resolve_sidecar(strict=False)` is the lenient
runtime path that tolerates legacy/grandfathered data + surfaces a
warning banner via the `sidecar_error` API field.

`SidecarJSON` is a SQLAlchemy `TypeDecorator` wrapping
`JSON(none_as_null=True)` — `process_bind_param` calls the
grammar validator at write time, so fixtures / scripts / future
endpoints that bypass `_persist_sidecar` still get checked. Defence
in depth, not user-facing.

## Read more

DESIGN.md §16.2.5 (D1 + D2 + the SHIPPED block) for the migration
shape; §16.2.5.t4 for the `TypeDecorator` rationale.

[DESIGN.md →](https://github.com/raby/station-studio/blob/main/DESIGN.md)
