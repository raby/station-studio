"""Just-this-week changes: one song of a generated week, changed for that
week only.

A week's songs come from the station's rules. A just-this-week change
replaces ONE of them, in one week, and is kept whenever the week is
generated again:

  * a swap: a song chosen by hand;
  * a rule change: the song that rule, changed, picks there (another
    source, say), with the rest of the week as the unchanged rules play
    it.

Every other song stays exactly as the rules play it, and so does the
station's state: cursors and mix exclusions follow the rule's own pick
(`GenerationResult.rule_picks`), so later weeks don't shift.

A change is addressed by the song's place: its row and its rule's place
in it (`row`, `block`, numbered from 1 as traces number them; a flat
station's row is 1) and which of that rule's songs it is (`k`, from 0).
It also keeps the rule's strategy and source as they were when it was
made, so a change whose rule has since become something else isn't put
on it: it's dropped instead, and says why.

A rule change's song is worked out by playing the week again with that
rule changed at that one place (`spec`, built by the caller from the
station's template; its size kept) and nowhere else (on a row station,
not in the other rows), up to that rule, with the same seed and inputs
as the week itself (`run`). Everything before it plays the same, so the
song is picked in the same context the rule's own was.

Pure: the caller loads changes from the database and saves the result
(engine.persistence).
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from engine.generator import GenerationResult
from engine.template import BlockSpec, TemplateError, parse_template
from engine.trace import TraceSlot

#: At most this many rule changes in one week: each plays (part of) the
#: week again. Swaps cost nothing and aren't counted.
MAX_RULE_CHANGES = 20


@dataclass(frozen=True)
class Change:
    row: int
    block: int
    k: int
    kind: str  # "swap" | "rule"
    rule_strategy: str
    rule_source: str
    # A swap: the song (a Track-like object; None if it's gone from the
    # library).
    track: Any = None
    # A rule change: the rule as changed (its size the original's), and
    # what changed (for the record). None if it isn't a valid rule.
    spec: BlockSpec | None = None
    rule: dict | None = None
    fields: dict | None = None
    # Why this change can't be made, when the caller already knows (its
    # rule's pool isn't imported, say). It's dropped with this reason.
    invalid: str | None = None
    # The database row's id, when it has one.
    id: int | None = None


@dataclass(frozen=True)
class Dropped:
    """A change that couldn't be made, and why (in words)."""
    change: Change
    reason: str


@dataclass
class Applied:
    """The week with its changes made."""
    result: GenerationResult
    applied: list[Change] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)


def changed_rule(template: dict, block: int, rule: dict) -> BlockSpec | None:
    """The station's rule number `block` (from 1, of its flat slots or its
    row) as `rule` changes it, or None if that isn't a valid rule there.

    `rule` is the block as changed (a whole block dict); its size is the
    original rule's whatever it says, so the week keeps its shape. It's
    checked as the station's template would be with it in place, so a
    rule naming a source the station doesn't have isn't valid.
    """
    raw = copy.deepcopy(template)
    layout = raw.get("layout") or {}
    key = "flat_slots" if layout.get("flat_slots") else "row_template"
    blocks = layout.get(key) or []
    if not 1 <= block <= len(blocks) or not isinstance(rule, dict):
        return None
    changed = dict(rule)
    changed["size"] = blocks[block - 1].get("size")
    blocks[block - 1] = changed
    try:
        parsed = parse_template(raw)
    except TemplateError:
        return None
    except Exception:  # noqa: BLE001 — a rule malformed past the grammar's checks
        return None
    return parsed.layout.all_blocks()[block - 1]


def week_matches_rules(slots: list[dict], template: dict) -> bool:
    """Whether a week (its stored trace slots) is the station's rules
    as they are now: every song in play order, each under a rule of
    the strategy and source it played as. The twin of the week page's
    `weekMatchesRules` (web/src/lib/songList.ts). A week made before
    the rules were moved or changed isn't, and changing one of its
    songs would put the change on whichever rule is at its place now.
    """
    layout = template.get("layout") or {}
    rows = bool(layout.get("row_template"))
    rules = layout.get("row_template") if rows else layout.get("flat_slots")
    if not rules:
        return False
    last = (0, 0)
    for s in slots:
        block = s.get("block")
        if not isinstance(block, int) or not 1 <= block <= len(rules):
            return False
        rule = rules[block - 1]
        row = s.get("row") if rows else 0
        if not isinstance(row, int) or (row, block) < last:
            return False
        if rule.get("strategy") != s.get("strategy") or rule.get("source") != s.get("source"):
            return False
        last = (row, block)
    return True


def change_matches_slot(slot: dict, *, kind: str, track_id: int | None,
                        rule: dict | None, rule_strategy: str, rule_source: str) -> bool:
    """Whether stored trace `slot` carries this change: its note is of
    that kind and song (or rule), under the rule the change was made
    for. After the station's rules move, a change follows its rule but
    the week's songs don't until it's generated again, so the song at a
    change's place can carry another change, or none."""
    note = slot.get("change")
    if not note or note.get("kind") != kind:
        return False
    if (slot.get("strategy"), slot.get("source")) != (rule_strategy, rule_source):
        return False
    if kind == "swap":
        return slot.get("track_id") == track_id
    return note.get("rule") == rule


def find_slot(slots: list[TraceSlot] | list[dict], row: int, block: int, k: int) -> int | None:
    """Where in the week (an index into `slots`) the `k`-th song of the
    rule at (`row`, `block`) is; None if it has no such song."""
    seen = 0
    for i, s in enumerate(slots):
        r, b = (s["row"], s["block"]) if isinstance(s, dict) else (s.row, s.block)
        if r == row and b == block:
            if seen == k:
                return i
            seen += 1
    return None


def _describe(slot: TraceSlot) -> dict:
    """The rule's own song in `slot`, all of it a change replaces, so
    undoing the change puts it back exactly (`unchanged_slot`)."""
    excluded = [
        e if isinstance(e, dict) else dataclasses.asdict(e)
        for e in (slot.excluded or [])
    ]
    return {
        "track_id": slot.track_id,
        "track_name": slot.track_name,
        "track_artist": slot.track_artist,
        "pool_index": slot.pool_index,
        "reason": slot.reason,
        "excluded": excluded,
    }


def unchanged_slot(slot: dict) -> dict:
    """A trace slot (as stored) with its change undone: the rule's own
    song back. A slot with no change is returned as it is."""
    note = slot.get("change")
    if not note:
        return dict(slot)
    was = note["was"]
    out = {k: v for k, v in slot.items() if k != "change"}
    for k in ("track_id", "track_name", "track_artist", "pool_index", "reason", "excluded"):
        if k in was:
            out[k] = was[k]
    return out


def changed_slot(slot: TraceSlot, change: Change, track: Any, *,
                 picked: TraceSlot | None = None) -> TraceSlot:
    """`slot` (the rule's song) with `track` playing instead, and a note
    of the change. The slot keeps its rule's source, strategy and place.
    `picked`: for a rule change, the slot the changed rule picked it in."""
    note: dict = {"kind": change.kind, "was": _describe(slot)}
    if change.kind == "rule":
        assert picked is not None
        note.update({
            "source": picked.source,
            "strategy": picked.strategy,
            "pool_index": picked.pool_index,
            "rule": dict(change.rule or {}),
            "fields": dict(change.fields or {}),
        })
        reason = f"Just this week: {picked.reason}"
    else:
        reason = "Just this week: chosen by hand"
    return dataclasses.replace(
        slot,
        track_id=getattr(track, "id", None),
        track_name=getattr(track, "name", "?"),
        track_artist=getattr(track, "artist", None),
        reason=reason,
        excluded=[],
        change=note,
    )


def apply_week_changes(
    result: GenerationResult,
    changes: list[Change],
    *,
    run: Callable[[BlockSpec, tuple[int, int]], GenerationResult],
) -> Applied:
    """`apply_week_changes_stream`, drained: the week with `changes` made."""
    out: Applied | None = None
    for step in apply_week_changes_stream(result, changes, run=run):
        if isinstance(step, Applied):
            out = step
    assert out is not None
    return out


def rule_change_count(changes: list[Change]) -> int:
    """How many of `changes` play part of the week again (at most
    `MAX_RULE_CHANGES` are made)."""
    return min(MAX_RULE_CHANGES, sum(1 for c in changes if c.kind == "rule"))


def apply_week_changes_stream(
    result: GenerationResult,
    changes: list[Change],
    *,
    run: Callable[[BlockSpec, tuple[int, int]], GenerationResult],
) -> Iterator[tuple[int, int] | Applied]:
    """The week (`result`, as the rules play it) with `changes` made.

    `run(spec, (row, block))` plays the week, with `spec` instead of the
    rule at (`row`, `block`) and only there, up to that rule, with the
    same seed and inputs as `result` was made with. Changes that can't
    be made are dropped, with the reason; rule changes past
    `MAX_RULE_CHANGES` are dropped too, and so is one whose replay
    fails (a pool too small for it now, say), so one change can't stop
    the week being generated.

    Yields `(done, total)` before each rule change is worked out (each
    plays part of the week again, so a job can show progress and be
    cancelled between them), then the `Applied` week, last.
    """
    if not changes:
        yield Applied(result=result)
        return
    total = rule_change_count(changes)
    picks = list(result.picks)
    slots = list(result.trace.slots)
    out = Applied(result=result)
    rule_changes = 0
    for change in changes:
        i = find_slot(slots, change.row, change.block, change.k)
        if i is None:
            out.dropped.append(Dropped(change, "that song is no longer in the week"))
            continue
        slot = slots[i]
        if slot.change is not None:
            out.dropped.append(Dropped(change, "another change is already made to that song"))
            continue
        if (slot.strategy, slot.source) != (change.rule_strategy, change.rule_source):
            out.dropped.append(Dropped(change, "the rule for that song has changed since"))
            continue
        if change.invalid is not None:
            out.dropped.append(Dropped(change, change.invalid))
            continue
        if change.kind == "swap":
            if change.track is None:
                out.dropped.append(Dropped(change, "the song chosen is no longer in the library"))
                continue
            picks[i] = change.track
            slots[i] = changed_slot(slot, change, change.track)
        elif change.kind == "rule":
            if change.spec is None:
                out.dropped.append(Dropped(change, "the changed rule isn't a valid rule"))
                continue
            rule_changes += 1
            if rule_changes > MAX_RULE_CHANGES:
                out.dropped.append(Dropped(
                    change, f"a week can have at most {MAX_RULE_CHANGES} rule changes",
                ))
                continue
            yield (rule_changes - 1, total)
            try:
                shadow = run(change.spec, (change.row, change.block))
            except Exception as e:  # noqa: BLE001 — as a generation's errors
                out.dropped.append(Dropped(change, f"the changed rule can't be played: {e}"))
                continue
            j = find_slot(shadow.trace.slots, change.row, change.block, change.k)
            if j is None:
                out.dropped.append(Dropped(change, "the changed rule picks no song there"))
                continue
            picks[i] = shadow.picks[j]
            slots[i] = changed_slot(slot, change, shadow.picks[j], picked=shadow.trace.slots[j])
        else:
            out.dropped.append(Dropped(change, f"unknown kind of change {change.kind!r}"))
            continue
        out.applied.append(change)
    if out.applied:
        trace = dataclasses.replace(result.trace, slots=slots)
        out.result = dataclasses.replace(
            result, picks=picks, trace=trace, rule_picks=list(result.picks),
        )
    yield out
