"""Just-this-week changes (engine.week_changes) and the generator's
`stop_after` / `override_block`, which rule changes are worked out with.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from typing import Any

import pytest

from engine.generator import generate
from engine.template import parse_template
from engine.week_changes import (
    MAX_RULE_CHANGES, Change, apply_week_changes, changed_rule, find_slot,
)


@dataclass
class T:
    id: int
    name: str
    artist: str | None = None


def mk_pool(prefix: str, n: int, start: int = 1) -> list[T]:
    return [T(id=start + i, name=f"{prefix}{i:04d}", artist=f"{prefix}-artist{i % 7}")
            for i in range(n)]


def row_station(rng: str = "on") -> dict[str, Any]:
    """Two rows of [pick A ×3, pick B ×2]."""
    return {
        "station": "wc",
        "cadence": "weekly",
        "rng": rng,
        "sources": [
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
            {"name": "C", "colour": "#ccc", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 5, "cycle_weeks": 4},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon", "Tue"],
            "row_start_offsets": [0, 10],
            "row_template": [
                {"source": "A", "strategy": "pick", "size": 3},
                {"source": "B", "strategy": "pick", "size": 2},
            ],
        },
    }


def flat_station() -> dict[str, Any]:
    return {
        "station": "wcf",
        "cadence": "weekly",
        "rng": "on",
        "sources": [
            {"name": "A", "colour": "#aaa", "role": "content"},
            {"name": "B", "colour": "#bbb", "role": "content"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "flat_slots": [
                {"source": "A", "strategy": "weighted", "size": 2},
                {"source": "B", "strategy": "weighted", "size": 1},
                {"source": "A", "strategy": "weighted", "size": 2},
            ],
        },
    }


POOLS = {"A": mk_pool("A", 60), "B": mk_pool("B", 40, 1000), "C": mk_pool("C", 30, 2000)}
SEED = 1234


def week(raw: dict, **kw):
    return generate(parse_template(raw), POOLS, week=2, seed=SEED, **kw)


def runner(raw: dict):
    t = parse_template(raw)
    calls: list[tuple[int, int]] = []

    def run(spec, at):
        calls.append(at)
        return generate(t, POOLS, week=2, seed=SEED,
                        stop_after=at, override_block=(at[0], at[1], spec))
    run.calls = calls  # type: ignore[attr-defined]
    return run


def names(result) -> list[str]:
    return [p.name for p in result.picks]


# ---------- the generator: stop_after, override_block ------------------


def test_stop_after_is_a_prefix_of_the_week():
    full = week(row_station())
    for at, n in [((1, 1), 3), ((1, 2), 5), ((2, 1), 8), ((2, 2), 10)]:
        part = week(row_station(), stop_after=at)
        assert names(part) == names(full)[:n]
        assert [s.to_dict() for s in part.trace.slots] == \
            [s.to_dict() for s in full.trace.slots[:n]]


def test_override_changes_only_that_row():
    raw = row_station()
    spec = changed_rule(raw, 1, {"source": "C", "strategy": "pick", "size": 99})
    assert spec is not None and spec.source == "C" and spec.size == 3
    full = week(raw)
    over = week(raw, override_block=(2, 1, spec))
    # Row 1 (both rules) and row 2's other rule play as the template's.
    assert names(over)[:5] == names(full)[:5]
    assert [s.source for s in over.trace.slots[5:8]] == ["C"] * 3
    assert all(p.name.startswith("C") for p in over.picks[5:8])
    assert [s.source for s in over.trace.slots[8:]] == ["B", "B"]


def test_override_needs_its_pool():
    raw = row_station()
    spec = changed_rule(raw, 1, {"source": "C", "strategy": "pick"})
    with pytest.raises(ValueError, match="not in pools"):
        generate(parse_template(raw), {"A": POOLS["A"], "B": POOLS["B"]},
                 week=2, seed=SEED, override_block=(1, 1, spec))


def test_no_override_or_stop_is_the_week_unchanged():
    a = week(row_station())
    b = week(row_station(), stop_after=None, override_block=None)
    assert names(a) == names(b)
    assert [s.to_dict() for s in a.trace.slots] == [s.to_dict() for s in b.trace.slots]


# ---------- changed_rule ------------------------------------------------


def test_changed_rule_keeps_size_and_validates():
    raw = flat_station()
    before = copy.deepcopy(raw)
    spec = changed_rule(raw, 2, {"source": "A", "strategy": "weighted", "size": 7})
    assert (spec.source, spec.strategy, spec.size) == ("A", "weighted", 1)
    assert raw == before  # the station's template isn't touched
    assert changed_rule(raw, 2, {"source": "Nope", "strategy": "weighted"}) is None
    assert changed_rule(raw, 2, {"source": "A", "strategy": "bogus"}) is None
    assert changed_rule(raw, 0, {"source": "A", "strategy": "weighted"}) is None
    assert changed_rule(raw, 4, {"source": "A", "strategy": "weighted"}) is None
    assert changed_rule(raw, 1, "not a rule") is None  # type: ignore[arg-type]


# ---------- find_slot ---------------------------------------------------


def test_find_slot_counts_within_the_rule():
    r = week(row_station())
    assert find_slot(r.trace.slots, 1, 1, 0) == 0
    assert find_slot(r.trace.slots, 1, 2, 1) == 4
    assert find_slot(r.trace.slots, 2, 1, 2) == 7
    assert find_slot(r.trace.slots, 2, 2, 2) is None
    assert find_slot(r.trace.slots, 3, 1, 0) is None
    as_dicts = [s.to_dict() for s in r.trace.slots]
    assert find_slot(as_dicts, 2, 2, 1) == 9


# ---------- apply_week_changes -----------------------------------------


def swap(row, block, k, track, strategy="pick", source=None, **kw):
    source = source or ("A" if block == 1 else "B")
    return Change(row=row, block=block, k=k, kind="swap",
                  rule_strategy=strategy, rule_source=source, track=track, **kw)


def rule(raw, row, block, k, new, **kw):
    orig = raw["layout"].get("row_template") or raw["layout"]["flat_slots"]
    o = orig[block - 1]
    return Change(row=row, block=block, k=k, kind="rule",
                  rule_strategy=o["strategy"], rule_source=o["source"],
                  spec=changed_rule(raw, block, new), rule=new, fields=kw.get("fields"))


def test_no_changes_is_the_same_week():
    r = week(row_station())
    out = apply_week_changes(r, [], run=runner(row_station()))
    assert out.result is r and out.applied == [] and out.dropped == []
    assert r.rule_picks is None


def test_a_swap_changes_one_song_and_notes_it():
    raw = row_station()
    r = week(raw)
    mine = T(id=777, name="Mine", artist="Me")
    out = apply_week_changes(r, [swap(2, 1, 1, mine)], run=runner(raw))
    assert out.dropped == [] and len(out.applied) == 1
    i = find_slot(r.trace.slots, 2, 1, 1)
    got = out.result
    assert got.picks[i] is mine
    assert names(got)[:i] + names(got)[i + 1:] == names(r)[:i] + names(r)[i + 1:]
    s, was = got.trace.slots[i], r.trace.slots[i]
    assert (s.track_id, s.track_name, s.track_artist) == (777, "Mine", "Me")
    # The slot keeps its rule's place, source and strategy.
    assert (s.slot, s.row, s.block, s.unit, s.source, s.strategy, s.pool_index) == \
        (was.slot, was.row, was.block, was.unit, was.source, was.strategy, was.pool_index)
    assert s.reason == "Just this week: chosen by hand"
    assert s.excluded == []
    assert s.change == {"kind": "swap", "was": {
        "track_id": was.track_id, "track_name": was.track_name,
        "track_artist": was.track_artist, "pool_index": was.pool_index,
        "reason": was.reason, "excluded": [dataclasses.asdict(e) for e in was.excluded],
    }}
    # Undone, the stored slot is the rule's own again, exactly.
    from engine.week_changes import unchanged_slot
    assert unchanged_slot(s.to_dict()) == was.to_dict()
    assert unchanged_slot(was.to_dict()) == was.to_dict()
    assert s.to_dict()["change"]["kind"] == "swap"
    assert "change" not in got.trace.slots[0].to_dict()
    # The station's own picks are kept for its state (mix usage).
    assert got.rule_picks == r.picks
    # The week's own result isn't changed in place.
    assert r.picks[i] is not mine and r.trace.slots[i].change is None


def test_a_rule_change_picks_as_the_changed_rule_would_there():
    raw = row_station()
    r = week(raw)
    new = {"source": "C", "strategy": "pick"}
    run = runner(raw)
    out = apply_week_changes(r, [rule(raw, 2, 1, 2, new, fields={"source": "C"})], run=run)
    assert out.dropped == []
    i = find_slot(r.trace.slots, 2, 1, 2)
    # The same song as the week played with that rule changed in that row.
    spec = changed_rule(raw, 1, new)
    whole = week(raw, override_block=(2, 1, spec))
    assert out.result.picks[i] == whole.picks[i]
    assert run.calls == [(2, 1)]
    s = out.result.trace.slots[i]
    assert (s.source, s.strategy) == ("A", "pick")  # the rule's, still
    assert s.change["kind"] == "rule"
    assert s.change["source"] == "C" and s.change["strategy"] == "pick"
    assert s.change["pool_index"] == whole.trace.slots[i].pool_index
    assert s.change["fields"] == {"source": "C"}
    assert s.change["rule"] == new
    assert s.reason == f"Just this week: {whole.trace.slots[i].reason}"
    others = [j for j in range(len(r.picks)) if j != i]
    assert [out.result.picks[j] for j in others] == [r.picks[j] for j in others]


def test_a_rule_change_on_a_flat_station():
    raw = flat_station()
    r = week(raw)
    new = {"source": "B", "strategy": "weighted"}
    out = apply_week_changes(r, [rule(raw, 1, 3, 1, new)], run=runner(raw))
    assert out.dropped == []
    i = find_slot(r.trace.slots, 1, 3, 1)
    whole = week(raw, override_block=(1, 3, changed_rule(raw, 3, new)))
    assert out.result.picks[i] == whole.picks[i]
    assert out.result.picks[i].name.startswith("B")


@pytest.mark.parametrize("make, reason", [
    (lambda raw: swap(3, 1, 0, T(1, "x")), "that song is no longer in the week"),
    (lambda raw: swap(1, 1, 3, T(1, "x")), "that song is no longer in the week"),
    (lambda raw: swap(1, 1, 0, T(1, "x"), source="B"), "the rule for that song has changed since"),
    (lambda raw: swap(1, 1, 0, T(1, "x"), strategy="window"), "the rule for that song has changed since"),
    (lambda raw: swap(1, 1, 0, None), "the song chosen is no longer in the library"),
    (lambda raw: dataclasses.replace(rule(raw, 1, 1, 0, {"source": "A", "strategy": "pick"}), spec=None),
     "the changed rule isn't a valid rule"),
    (lambda raw: dataclasses.replace(swap(1, 1, 0, T(1, "x")), kind="other"),
     "unknown kind of change 'other'"),
])
def test_changes_that_cant_be_made_are_dropped(make, reason):
    raw = row_station()
    r = week(raw)
    c = make(raw)
    out = apply_week_changes(r, [c], run=runner(raw))
    assert [(d.change, d.reason) for d in out.dropped] == [(c, reason)]
    assert out.applied == [] and out.result is r


def test_a_rule_that_picks_nothing_there_is_dropped():
    raw = row_station()
    r = week(raw)
    c = rule(raw, 1, 1, 0, {"source": "C", "strategy": "pick"})

    def empty(spec, at):
        return generate(parse_template(raw), POOLS, week=2, seed=SEED, stop_after=(1, 0))
    out = apply_week_changes(r, [c], run=empty)
    assert [d.reason for d in out.dropped] == ["the changed rule picks no song there"]


def test_two_changes_to_one_song_keep_the_first():
    raw = row_station()
    r = week(raw)
    first, second = swap(1, 2, 0, T(1, "one")), swap(1, 2, 0, T(2, "two"))
    out = apply_week_changes(r, [first, second], run=runner(raw))
    assert out.applied == [first]
    assert [(d.change, d.reason) for d in out.dropped] == \
        [(second, "another change is already made to that song")]
    assert out.result.picks[3].name == "one"


def test_rule_changes_are_capped_swaps_arent():
    raw = {**row_station(), "layout": {
        "unit": "day", "unit_labels": ["Mon"], "row_start_offsets": [0],
        "row_template": [{"source": "A", "strategy": "pick", "size": MAX_RULE_CHANGES + 3}],
    }}
    r = week(raw)
    run = runner(raw)
    changes = [rule(raw, 1, 1, k, {"source": "C", "strategy": "pick"})
               for k in range(MAX_RULE_CHANGES + 2)]
    changes.append(swap(1, 1, MAX_RULE_CHANGES + 2, T(5, "swapped")))
    out = apply_week_changes(r, changes, run=run)
    assert len(run.calls) == MAX_RULE_CHANGES
    assert len(out.applied) == MAX_RULE_CHANGES + 1
    assert [d.reason for d in out.dropped] == \
        [f"a week can have at most {MAX_RULE_CHANGES} rule changes"] * 2
    assert out.result.picks[-1].name == "swapped"


def test_changes_are_independent_of_each_other():
    """Each rule change is worked out against the week as the rules play
    it, not as other changes have made it."""
    raw = row_station()
    r = week(raw)
    a = rule(raw, 1, 1, 0, {"source": "C", "strategy": "pick"})
    b = rule(raw, 2, 1, 0, {"source": "C", "strategy": "pick"})
    both = apply_week_changes(r, [a, b], run=runner(raw)).result
    only_b = apply_week_changes(r, [b], run=runner(raw)).result
    i = find_slot(r.trace.slots, 2, 1, 0)
    assert both.picks[i] == only_b.picks[i]


def test_a_changed_song_drops_the_rules_exclusions_and_undo_restores_them():
    from engine.trace import ExcludedCandidate, TraceSlot
    from engine.week_changes import changed_slot, unchanged_slot
    slot = TraceSlot(
        slot=4, unit="Mon", row=1, block=2, source="A", strategy="weighted",
        pool_index=9, track_id=9, track_name="Nine", track_artist="N",
        reason="weighted pick", excluded=[ExcludedCandidate(
            pool_index=8, track_id=8, track_name="Eight", track_artist="N",
            reason="artist played recently",
        )],
    )
    c = Change(row=1, block=2, k=0, kind="swap", rule_strategy="weighted",
               rule_source="A", track=T(id=1, name="One"))
    out = changed_slot(slot, c, c.track)
    assert out.excluded == []  # they were the rule's song's, not this one's
    assert out.change["was"]["excluded"] == [slot.excluded[0].to_dict()]
    assert unchanged_slot(out.to_dict()) == slot.to_dict()
