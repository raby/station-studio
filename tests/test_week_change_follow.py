"""Just-this-week changes follow their rule when the station's rules
are moved, added or removed (engine.week_change_store.follow_rule_ops).

`slot_after` is the twin of web/src/lib/edit-ops.ts `slotAfter`; both
are checked against tests/fixtures/slot_after.json (regenerate with
`python -m tests.test_week_change_follow`).
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.week_change_store import RuleOp, slot_after

FIXTURE = Path(__file__).parent / "fixtures" / "slot_after.json"
N = 6


def _cases() -> list[dict]:
    cases: list[dict] = []
    for at in range(N + 1):
        for p in range(N):
            cases.append({
                "op": {"kind": "structuralBlockEdit", "edit": {"op": "add", "slot_index": at}},
                "p": p, "after": slot_after(RuleOp("add", (at,)), p),
            })
    for at in range(N):
        for p in range(N):
            cases.append({
                "op": {"kind": "structuralBlockEdit", "edit": {"op": "remove", "slot_index": at}},
                "p": p, "after": slot_after(RuleOp("remove", (at,)), p),
            })
            cases.append({
                "op": {"kind": "structuralBlockEdit", "edit": {"op": "size", "slot_index": at, "new_size": 2}},
                "p": p, "after": slot_after(RuleOp("size", (at, 2)), p),
            })
    for kind in ("moveRules", "moveSection"):
        for from_ in range(N):
            for count in range(1, N - from_ + 1):
                for to in range(N + 1):
                    if from_ <= to <= from_ + count and kind == "moveSection":
                        continue
                    # Inside the run isn't a move (to == from_ never is;
                    # to == from_ + count is moveRules' "into the next
                    # section without moving").
                    if from_ <= to < from_ + count:
                        continue
                    for p in range(N):
                        cases.append({
                            "op": {"kind": kind, "from": from_, "count": count, "to": to},
                            "p": p, "after": slot_after(RuleOp("move", (from_, count, to)), p),
                        })
    return cases


def test_snapshot_is_current():
    assert json.loads(FIXTURE.read_text()) == _cases()


def test_moves_are_permutations():
    for c in _cases():
        if c["op"]["kind"] in ("moveRules", "moveSection"):
            o = c["op"]
            got = sorted(
                slot_after(RuleOp("move", (o["from"], o["count"], o["to"])), p)
                for p in range(N)
            )
            assert got == list(range(N)), o


def test_add_and_remove():
    assert [slot_after(RuleOp("add", (2,)), p) for p in range(4)] == [0, 1, 3, 4]
    assert [slot_after(RuleOp("remove", (2,)), p) for p in range(4)] == [0, 1, None, 2]
    assert [slot_after(RuleOp("size", (2, 1)), p) for p in range(4)] == [0, 1, 2, 3]
    assert [slot_after(RuleOp("move", (0, 2, 4)), p) for p in range(5)] == [2, 3, 0, 1, 4]


if __name__ == "__main__":
    FIXTURE.write_text(json.dumps(_cases(), indent=0) + "\n")
    print(f"wrote {FIXTURE} ({len(_cases())} cases)")
