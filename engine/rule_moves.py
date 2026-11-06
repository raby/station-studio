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
template, so a move changes every row. Its sidecar's atomic blocks carry
a row too (`row_idx`); a moved block joins `section_id` when that section
has blocks of its row (so a section per day keeps its day's blocks),
and each section's blocks are kept in play order. Such stations can't
move rules while their sidecar uses macros or sections that repeat for
every row (`repeat_with_offsets`).

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


def _check_rows(sidecar: dict) -> None:
    """A row station's sidecar the moves can follow."""
    for section in sidecar.get("sections", []):
        if section.get("repeat_with_offsets") is not None:
            raise RuleMoveError(
                f"section '{section.get('id')}' repeats for every row: rules "
                "can't be moved in a station built from a repeating row with "
                "such sections yet.",
            )
        if any(b.get("kind") == "macro" for b in section.get("blocks", [])):
            raise RuleMoveError(
                "rules can't be moved in a station built from a repeating row "
                "while its sections use macros yet.",
            )


def _play_order(sidecar: dict) -> None:
    """Each section's blocks in play order: by row, then place in it.
    (Row stations: their sections are all atomic blocks.)"""
    for section in sidecar.get("sections", []):
        section["blocks"] = sorted(
            section.get("blocks", []),
            key=lambda b: (b.get("row_idx", 0), _pos(b) if _pos(b) is not None else -1),
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
    if rows:
        _check_rows(sidecar)
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

    target_was = next((s for s in sidecar.get("sections", []) if s.get("id") == section_id), None)
    if target_was is None:
        raise RuleMoveError(f"section '{section_id}' not found.")
    # Which moved blocks join the section: all of them; in a row
    # station, those of the rows it has blocks of (the rest stay put).
    target_rows = {b.get("row_idx") for b in target_was.get("blocks", [])}
    joining = [m for m in moving if not rows or m[1].get("row_idx") in target_rows]
    if to == from_ + count and all(sec is target_was for _p, _b, sec in joining):
        raise RuleMoveError("the rules are already there.")

    # Take the moved blocks out of their sections, by identity, then put
    # them into the target section, in play order.
    staged = copy.deepcopy(sidecar)
    originals = [b for s in sidecar.get("sections", []) for b in s.get("blocks", [])]
    copies = [b for s in staged.get("sections", []) for b in s.get("blocks", [])]
    copy_of = {id(o): c for o, c in zip(originals, copies)}
    moved_copies = [copy_of[id(b)] for _p, b, _sec in sorted(joining, key=lambda m: m[0])]
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
    if key == "row_template":
        _check_rows(sidecar)
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
