"""Per-slot trace-diff helpers (§16.4.t1 + §16.23).

Two route layers consume this:

  * §16.4.t1 — `GET /api/playlists/{id}/diff?from=N&to=M` compares
    two PlaylistVersions of the same playlist (version-vs-version).
  * §16.23   — `GET /api/stations/{slug}/diff?from={week}&to={week}`
    compares the current version of two playlists of the same
    station (week-vs-week).

Both walk the slot lists side-by-side and build a per-row diff +
summary. The walker is in this leaf module so the two route layers
share one implementation; future diff surfaces (cross-station,
historical-version × week, …) can reuse without dragging route
plumbing through engine code.

Public surface:

  * `DIFF_SLOT_FIELDS_COMPARED` — the tuple of TraceSlot fields the
    `changed` predicate compares.
  * `slot_projection(slot)` — pluck the visible columns from a
    TraceSlot dict.
  * `compute_trace_diff(src_slots, dst_slots)` — walk both lists by
    slot number; return `(rows, summary)`.
"""
from __future__ import annotations


# Fields compared per slot when classifying a diff row as "changed".
# Positional fields (slot/unit/row/block) NEVER vary across versions
# of the same playlist (same template, same week) — they're the row
# key. `track_id` is the load-bearing change signal (which track
# ended up here). `source` + `strategy` shift only when the operator
# edited the template between regens — captured here so a template-
# shift surfaces in the diff even when the picked track happens to
# match. `pool_index` moving without track_id moving is unusual
# (would require the source pool to have been re-imported with
# reordered entries) but operator-interesting when it happens —
# captured too.
DIFF_SLOT_FIELDS_COMPARED = (
    "source", "strategy", "pool_index", "track_id",
)


def slot_projection(slot: dict) -> dict:
    """Pluck the visible columns for the diff response from a
    TraceSlot dict. Mirrors the trace viewer's table columns + the
    positional row key so the frontend doesn't need to re-look-
    anything-up."""
    return {
        "slot": slot.get("slot"),
        "unit": slot.get("unit"),
        "row": slot.get("row"),
        "block": slot.get("block"),
        "source": slot.get("source"),
        "strategy": slot.get("strategy"),
        "pool_index": slot.get("pool_index"),
        "track_id": slot.get("track_id"),
        "track_name": slot.get("track_name"),
        "track_artist": slot.get("track_artist"),
        "reason": slot.get("reason"),
    }


def compute_trace_diff(
    src_slots: list[dict],
    dst_slots: list[dict],
) -> tuple[list[dict], dict]:
    """Walk two slot lists side-by-side; return `(rows, summary)`.

    Each row carries the positional key (`slot` / `unit` / `row` /
    `block`), a server-computed `changed: bool` flag, and the full
    projection of both sides. Rows where either side is missing
    (mismatched slot counts — different templates at the time the
    two traces were generated) count as `changed: true` with the
    missing side rendered as `None`; the frontend renders that as
    a "no slot in vN" / "no slot in week N" placeholder cell.

    Walks by slot NUMBER (the `slot` key on TraceSlot), not by list
    index. zip-by-index breaks if the two traces somehow have
    different slot counts; the by-number walk is robust against
    template shifts between generations.

    Summary: `total_slots = changed + unchanged`. Frontend uses the
    count to render "N of M changed" + the unchanged toggle.
    """
    src_by_slot: dict[int, dict] = {s.get("slot"): s for s in src_slots}
    dst_by_slot: dict[int, dict] = {s.get("slot"): s for s in dst_slots}
    all_slot_nums = sorted(set(src_by_slot.keys()) | set(dst_by_slot.keys()))

    rows: list[dict] = []
    changed_count = 0
    for slot_num in all_slot_nums:
        s_from = src_by_slot.get(slot_num)
        s_to = dst_by_slot.get(slot_num)
        if s_from is None or s_to is None:
            changed = True
        else:
            changed = any(
                s_from.get(k) != s_to.get(k)
                for k in DIFF_SLOT_FIELDS_COMPARED
            )
        if changed:
            changed_count += 1
        # Row key from whichever side has the slot. Both sides usually
        # do; pick `s_to` first so the "to" side's positional metadata
        # wins on mismatch (the operator's "current" mental frame).
        keyrow = s_to or s_from
        rows.append({
            "slot": keyrow.get("slot"),
            "unit": keyrow.get("unit"),
            "row": keyrow.get("row"),
            "block": keyrow.get("block"),
            "changed": changed,
            # Explicit `is not None` over truthiness — an empty
            # TraceSlot dict is unreachable in production but the
            # precise check survives a future refactor that might
            # introduce a defensible empty-dict case.
            "from": slot_projection(s_from) if s_from is not None else None,
            "to": slot_projection(s_to) if s_to is not None else None,
        })

    total = len(rows)
    summary = {
        "total_slots": total,
        "changed": changed_count,
        "unchanged": total - changed_count,
    }
    return rows, summary
