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
    not at all.
  * `move_section` — the run is all of a section's rules, which keep their
    section; it moves to before another section's rules (or the end), and
    the section's place in `layout_order` moves with it.

Both are pure: they return a new template and sidecar, and raise
`RuleMoveError` for a move the station can't take. Stations built from a
repeating row (`row_template`) can't move rules yet.

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


def _flat_slots(template: dict) -> list:
    layout = template.get("layout", {})
    if layout.get("row_template"):
        raise RuleMoveError(
            "rules can't be moved in a station built from a repeating row yet.",
        )
    slots = layout.get("flat_slots")
    if not isinstance(slots, list):
        raise RuleMoveError("the station has no rules to move.")
    return slots


def _check_run(n: int, from_: int, count: int, to: int) -> None:
    if count < 1 or from_ < 0 or from_ + count > n:
        raise RuleMoveError(
            f"rules {from_}..{from_ + count - 1} aren't all in the station "
            f"({n} rules).",
        )
    if to < 0 or to > n:
        raise RuleMoveError(f"can't move rules to {to}: the station has {n} rules.")
    if from_ <= to <= from_ + count:
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
        anchor = next(
            (j for j, b in enumerate(blocks)
             if b.get("kind") == "atomic" and isinstance(b.get("slot_index"), int)),
            None,
        )
        if anchor is None:
            out.extend((section, b, None) for b in blocks)
            continue
        pos = blocks[anchor]["slot_index"] - sum(width(b) for b in blocks[:anchor])
        for b in blocks:
            if b.get("kind") == "atomic":
                out.append((section, b, [b["slot_index"]]))
                pos = b["slot_index"] + 1
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
        out[macro_id] = sorted(a["slot_index"] for a in addrs if "slot_index" in a)
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
    slots = _flat_slots(template)

    moved = slots[from_:from_ + count]
    rest = slots[:from_] + slots[from_ + count:]
    at = to if to < from_ else to - count
    template["layout"]["flat_slots"] = rest[:at] + moved + rest[at:]

    for section in sidecar.get("sections", []):
        for b in section.get("blocks", []):
            if b.get("kind") == "atomic" and isinstance(b.get("slot_index"), int):
                b["slot_index"] = new_position(b["slot_index"], from_, count, to)

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
    slots = _flat_slots(template)
    _check_run(len(slots), from_, count, to)
    run = range(from_, from_ + count)

    elements = _element_slots(sidecar)
    moving: list[tuple[int, dict]] = []
    covered: set[int] = set()
    for _section, block, slots_of in elements:
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
        moving.append((min(slots_of), block))
        covered.update(slots_of)
    if covered != set(run):
        raise RuleMoveError(
            f"rules {sorted(set(run) - covered)} aren't in a section: the "
            "sidecar has drifted from the station.",
        )

    if not any(s.get("id") == section_id for s in sidecar.get("sections", [])):
        raise RuleMoveError(f"section '{section_id}' not found.")

    # Take the moved blocks out of their sections, by identity, then put
    # them into the target section, in play order.
    staged = copy.deepcopy(sidecar)
    originals = [b for s in sidecar.get("sections", []) for b in s.get("blocks", [])]
    copies = [b for s in staged.get("sections", []) for b in s.get("blocks", [])]
    copy_of = {id(o): c for o, c in zip(originals, copies)}
    moved_copies = [copy_of[id(b)] for _p, b in sorted(moving, key=lambda m: m[0])]
    moved_copy_ids = {id(c) for c in moved_copies}
    for section in staged.get("sections", []):
        section["blocks"] = [b for b in section.get("blocks", []) if id(b) not in moved_copy_ids]
    # After the section's blocks that play before `to` (its first, when
    # none do; at the end, when it has blocks that can't be placed).
    target = next(s for s in staged["sections"] if s.get("id") == section_id)
    placed = {id(copy_of[id(b)]): slots_of for sec, b, slots_of in elements
              if sec.get("id") == section_id}
    at = sum(1 for b in target["blocks"]
             if placed.get(id(b)) is not None and max(placed[id(b)]) < to)
    if any(placed.get(id(b)) is None for b in target["blocks"]):
        at = len(target["blocks"])
    target["blocks"][at:at] = moved_copies

    return _reorder(template, staged, from_, count, to, _macro_slots(sidecar))


def section_slots(sidecar: dict, section_id: str) -> list[int] | None:
    """The slots a section's blocks cover, sorted; None when it has a
    block that can't be placed (a macro in a section with no atomic)."""
    out: list[int] = []
    for section, _block, slots_of in _element_slots(sidecar):
        if section.get("id") != section_id:
            continue
        if slots_of is None:
            return None
        out.extend(slots_of)
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
    slots = _flat_slots(template)
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
