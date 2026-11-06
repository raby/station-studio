"""Moving a station's rules to another place in play order.

The engine plays a flat station's rules (`layout.flat_slots`) in list
order; the sidecar only groups them into sections for the editor. So
moving songs to play somewhere else means reordering `flat_slots`, and
then the sidecar follows: every atomic block's `slot_index` is renumbered
to where its rule now is, and the moved rules join a section.

Two moves, both by a run of slots, `[from_, from_ + count)`, moved to
before slot `to` (`len(flat_slots)` for the end), all in the station's
numbering before the move:

  * `move_rules` — the rules join `section_id`, placed among its blocks by
    when they play (after the ones that play before `to`). A run never
    splits a macro, and never lands inside one: a macro moves whole, or
    not at all. With `to` just after the run (`from_ + count`), they play
    where they did and only change section (one dragged over the line
    between two sections).
  * `move_section` — the run is all of a section's rules, which keep their
    section; it moves to before another section's rules (or the end), and
    the section's place in `layout_order` moves with it.

Both are pure: they return a new template and sidecar, and raise
`RuleMoveError` for a move the station can't take.

A station built from a repeating row (`row_template`) is moved the same
way, numbered by the row's blocks (`block_idx`): every row plays the row
template, so a move changes every row. Its sidecar's blocks belong to a
row: an atomic block its `row_idx`, a macro its section's, and a section
that repeats for every row (`repeat_with_offsets`) is every row's. A
moved block joins `section_id` when that section has blocks of its row,
else it stays in its own (so a section per day keeps its day's blocks);
a block of a section repeated for every row can only join another such
section, and the other way round. Every moved block goes in among its
section's blocks by when it plays now, and sections without macros are
kept in play order.

The editor's in-browser preview (`web/src/lib/edit-ops.ts`) mirrors these
exactly; `tests/fixtures/rule_moves.json` holds the cases both are checked
against.
"""
from __future__ import annotations

import copy
from typing import Any

from engine.editor_metadata import MacroResolutionError, resolve_macro_addresses


class RuleMoveError(ValueError):
    """A move the station can't take (the message says why)."""


def new_position(p: int, from_: int, count: int, to: int) -> int:
    """Where the rule at slot `p` is after moving `[from_, from_+count)` to
    before slot `to`."""
    end = from_ + count
    if from_ <= p < end:
        return p - from_ + (to if to < from_ else to - count)
    if to < from_ and to <= p < from_:
        return p + count
    if to > end and end <= p < to:
        return p - count
    return p


def _rules(template: dict) -> tuple[str, list]:
    """Where the station's rules are, and them: `flat_slots`, or the
    repeating row's `row_template`."""
    layout = template.get("layout", {})
    if layout.get("row_template"):
        return "row_template", layout["row_template"]
    slots = layout.get("flat_slots")
    if not isinstance(slots, list):
        raise RuleMoveError("the station has no rules to move.")
    return "flat_slots", slots


def _pos(block: dict) -> int | None:
    """An atomic block's rule: its slot (flat) or its place in the row."""
    if block.get("kind") != "atomic":
        return None
    for key in ("slot_index", "block_idx"):
        if isinstance(block.get(key), int):
            return block[key]
    return None


def _set_pos(block: dict, p: int) -> None:
    block["slot_index" if "slot_index" in block else "block_idx"] = p


EVERY = "every"  # the row of a block in a section repeated for every row


def _repeats(section: dict) -> bool:
    return section.get("repeat_with_offsets") is not None


def _rows_of(sidecar: dict, rows: bool) -> dict[int, Any]:
    """Each section block's row, by id(): a flat station's are all one
    (0); a row station's atomic block its `row_idx`, a macro its
    section's (the atomic before it, or the first), a block of a section
    repeated for every row EVERY."""
    out: dict[int, Any] = {}
    for section in sidecar.get("sections", []):
        blocks = section.get("blocks", [])
        if not rows:
            out.update((id(b), 0) for b in blocks)
            continue
        if _repeats(section):
            out.update((id(b), EVERY) for b in blocks)
            continue
        first = next((b.get("row_idx", 0) for b in blocks if b.get("kind") == "atomic"), 0)
        last = first
        for b in blocks:
            if b.get("kind") == "atomic":
                last = b.get("row_idx", 0)
            out[id(b)] = last
    return out


def _plays_before(row: Any, slots: list[int], at_row: Any, to: int) -> bool:
    """Whether a block of `row` covering `slots` plays before rule `to` in
    `at_row` (all numbered as before the move). (Never one row EVERY and
    the other not: such a move is refused.)"""
    if row == at_row:
        return max(slots) < to
    return row < at_row


def _play_order(sidecar: dict) -> None:
    """A row station's sections without macros, in play order: by row,
    then place in it. (Renumbering can reorder a day's blocks in a
    section a move didn't touch; macros are placed by their order, so
    sections with them are left as they are.)"""
    for section in sidecar.get("sections", []):
        blocks = section.get("blocks", [])
        if any(b.get("kind") != "atomic" for b in blocks):
            continue
        section["blocks"] = sorted(
            blocks, key=lambda b: (b.get("row_idx", 0), _pos(b) if _pos(b) is not None else -1),
        )


def _check_run(n: int, from_: int, count: int, to: int, *, stay: bool = False) -> None:
    """The run and `to` are in the station, and `to` isn't where the run
    is; with `stay`, `to` may be just after it (`from_ + count`): the
    rules play where they did."""
    if count < 1 or from_ < 0 or from_ + count > n:
        raise RuleMoveError(
            f"rules {from_}..{from_ + count - 1} aren't all in the station "
            f"({n} rules).",
        )
    if to < 0 or to > n:
        raise RuleMoveError(f"can't move rules to {to}: the station has {n} rules.")
    if from_ <= to <= from_ + count and not (stay and to == from_ + count):
        raise RuleMoveError("the rules are already there.")


def _element_slots(sidecar: dict) -> list[tuple[dict, dict, list[int] | None]]:
    """Every section block as (section, block, the slots it covers).

    An atomic block covers its own slot; a macro covers `repeat` slots
    from where the section's run has got to (as `resolve_macro_addresses`
    places them). A section with no atomic block can't be placed: its
    macros cover None.
    """
    macros = {m.get("id"): m for m in sidecar.get("macros", [])}

    def width(b: dict) -> int:
        if b.get("kind") == "atomic":
            return 1
        return int(macros.get(b.get("ref"), {}).get("repeat", 1))

    out: list[tuple[dict, dict, list[int] | None]] = []
    for section in sidecar.get("sections", []):
        blocks = section.get("blocks", [])
        anchor = next((j for j, b in enumerate(blocks) if _pos(b) is not None), None)
        if anchor is None:
            out.extend((section, b, None) for b in blocks)
            continue
        pos = _pos(blocks[anchor]) - sum(width(b) for b in blocks[:anchor])
        for b in blocks:
            if b.get("kind") == "atomic":
                p = _pos(b)
                if p is None:
                    out.append((section, b, None))
                    continue
                out.append((section, b, [p]))
                pos = p + 1
            else:
                w = width(b)
                out.append((section, b, list(range(pos, pos + w))))
                pos += w
    return out


def _macro_slots(sidecar: dict) -> dict[str, list[int]]:
    """Each macro in use, and the slots it expands to (sorted)."""
    used = {
        b.get("ref")
        for s in sidecar.get("sections", [])
        for b in s.get("blocks", [])
        if b.get("kind") == "macro"
    }
    out: dict[str, list[int]] = {}
    for macro_id in used:
        try:
            addrs = resolve_macro_addresses(sidecar, macro_id)
        except MacroResolutionError:
            addrs = []
        out[macro_id] = sorted({a.get("slot_index", a.get("block_idx")) for a in addrs} - {None})
    return out


def _reorder(
    template: dict, sidecar: dict, from_: int, count: int, to: int,
    before: dict[str, list[int]],
) -> tuple[dict, dict]:
    """The template with the run moved, and `sidecar` (already regrouped)
    renumbered to match. Checks every macro still covers the rules it did:
    `before`, its slots in the station as it was."""
    template = copy.deepcopy(template)
    sidecar = copy.deepcopy(sidecar)
    key, slots = _rules(template)

    moved = slots[from_:from_ + count]
    rest = slots[:from_] + slots[from_ + count:]
    at = to if to < from_ else to - count
    template["layout"][key] = rest[:at] + moved + rest[at:]

    for section in sidecar.get("sections", []):
        for b in section.get("blocks", []):
            p = _pos(b)
            if p is not None:
                _set_pos(b, new_position(p, from_, count, to))
    if key == "row_template":
        _play_order(sidecar)

    after = _macro_slots(sidecar)
    for macro_id, was in before.items():
        expected = sorted(new_position(p, from_, count, to) for p in was)
        if after.get(macro_id) != expected:
            raise RuleMoveError(
                f"the move would put macro '{macro_id}' out of place: its "
                "section's rules wouldn't play together. Move the whole "
                "macro, or move it with its section.",
            )
    return template, sidecar


def move_rules(
    template: dict,
    sidecar: dict,
    from_: int,
    count: int,
    to: int,
    section_id: str,
) -> tuple[dict, dict]:
    """Move the rules in slots `[from_, from_+count)` to before slot `to`;
    they join section `section_id`, in the order they play, after its
    blocks that play before `to`."""
    key, slots = _rules(template)
    rows = key == "row_template"
    _check_run(len(slots), from_, count, to, stay=True)
    run = range(from_, from_ + count)

    elements = _element_slots(sidecar)
    moving: list[tuple[int, dict, dict]] = []
    covered: set[int] = set()
    for section, block, slots_of in elements:
        if slots_of is None:
            continue
        if min(slots_of) < to <= max(slots_of):
            raise RuleMoveError(f"can't put rules inside a macro (at rule {to}).")
        inside = [p for p in slots_of if p in run]
        if not inside:
            continue
        if len(inside) != len(slots_of):
            raise RuleMoveError(
                f"rules {from_}..{from_ + count - 1} split a macro: move all "
                "of it, or none.",
            )
        moving.append((min(slots_of), block, section))
        covered.update(slots_of)
    if covered != set(run):
        raise RuleMoveError(
            f"rules {sorted(set(run) - covered)} aren't in a section: the "
            "sidecar has drifted from the station.",
        )

    target = next((s for s in sidecar.get("sections", []) if s.get("id") == section_id), None)
    if target is None:
        raise RuleMoveError(f"section '{section_id}' not found.")
    # Where each moved block goes: the target section, when it has blocks
    # of its row (a flat station's: always); else it stays in its own.
    row_of = _rows_of(sidecar, rows)
    target_rows = {row_of[id(b)] for b in target.get("blocks", [])}
    dest: dict[int, dict] = {}
    for _p, b, sec in moving:
        joins = not rows or row_of[id(b)] in target_rows
        if rows and sec is not target and _repeats(sec) != _repeats(target):
            raise RuleMoveError(
                "rules in a section repeated for every row can only move into "
                "another such section, and the other way round.",
            )
        dest[id(b)] = target if joins else sec
    if to == from_ + count and all(dest[id(b)] is sec for _p, b, sec in moving):
        raise RuleMoveError("the rules are already there.")

    # Each moved block, out of its section and into where it goes: after
    # that section's blocks that play before it does now (its first, when
    # none do; at the end, when the section has blocks that can't be
    # placed).
    staged = copy.deepcopy(sidecar)
    copy_of = {
        id(o): c
        for so, sc in zip(sidecar.get("sections", []), staged.get("sections", []))
        for o, c in zip(so.get("blocks", []), sc.get("blocks", []))
    }
    section_copy = {id(so): sc for so, sc in zip(sidecar.get("sections", []), staged.get("sections", []))}
    slots_of = {id(b): sl for _sec, b, sl in elements}
    moved_ids = {id(b) for _p, b, _sec in moving}
    for so in sidecar.get("sections", []):
        comers = sorted(
            ((row_of[id(b)], p, b) for p, b, _sec in moving if dest[id(b)] is so),
            key=lambda m: (0 if m[0] == EVERY else m[0], m[1]),
        )
        if not comers and not any(id(b) in moved_ids for b in so.get("blocks", [])):
            continue
        stay = [b for b in so.get("blocks", []) if id(b) not in moved_ids]
        unplaceable = any(slots_of.get(id(b)) is None for b in stay)
        out: list[dict] = []
        k = 0
        for b in stay:
            while k < len(comers) and not unplaceable and not _plays_before(
                row_of[id(b)], slots_of[id(b)], comers[k][0], to,
            ):
                out.append(copy_of[id(comers[k][2])])
                k += 1
            out.append(copy_of[id(b)])
        out.extend(copy_of[id(c[2])] for c in comers[k:])
        section_copy[id(so)]["blocks"] = out

    return _reorder(template, staged, from_, count, to, _macro_slots(sidecar))


def section_slots(sidecar: dict, section_id: str) -> list[int] | None:
    """The slots a section's blocks cover, sorted, once each (a row
    station's: the places in the row, of every row it has); None when it
    has a block that can't be placed (a macro in a section with no
    atomic)."""
    out: set[int] = set()
    for section, _block, slots_of in _element_slots(sidecar):
        if section.get("id") != section_id:
            continue
        if slots_of is None:
            return None
        out.update(slots_of)  # a row station's: each row's, once
    return sorted(out)


def move_section(
    template: dict,
    sidecar: dict,
    section_id: str,
    before_section_id: str | None,
    from_: int,
    count: int,
    to: int,
) -> tuple[dict, dict]:
    """Move section `section_id`, whose rules are slots `[from_,
    from_+count)`, to before section `before_section_id`'s rules (slot
    `to`, its first), or to the end (`before_section_id` None, `to` the
    number of rules). Its place in `layout_order` moves too."""
    key, slots = _rules(template)
    sections = {s.get("id"): s for s in sidecar.get("sections", [])}
    if section_id not in sections:
        raise RuleMoveError(f"section '{section_id}' not found.")
    if before_section_id is not None and before_section_id not in sections:
        raise RuleMoveError(f"section '{before_section_id}' not found.")
    if before_section_id == section_id:
        raise RuleMoveError("a section can't move before itself.")
    _check_run(len(slots), from_, count, to)

    mine = section_slots(sidecar, section_id)
    if mine != list(range(from_, from_ + count)):
        raise RuleMoveError(
            f"section '{section_id}' isn't rules {from_}..{from_ + count - 1} "
            "played together: move its rules one run at a time.",
        )
    if before_section_id is None:
        if to != len(slots):
            raise RuleMoveError("moving a section to the end: `to` must be the number of rules.")
    else:
        theirs = section_slots(sidecar, before_section_id)
        if not theirs or theirs[0] != to:
            raise RuleMoveError(
                f"section '{before_section_id}' doesn't start at rule {to}.",
            )

    template, sidecar = _reorder(template, sidecar, from_, count, to, _macro_slots(sidecar))
    order: list[Any] = [i for i in sidecar.get("layout_order", []) if i != section_id]
    if before_section_id is None or before_section_id not in order:
        order.append(section_id)
    else:
        order.insert(order.index(before_section_id), section_id)
    sidecar["layout_order"] = order
    return template, sidecar
