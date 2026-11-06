"""Moving a station's rules: `engine.rule_moves` against the shared
cases (`tests/fixtures/rule_moves.json`, which the editor's in-browser
mirror is checked against too), and the two batch edits end to end."""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from engine.editor_metadata import resolve_macro_addresses, validate_sidecar
from engine.models import Station
from engine.rule_moves import RuleMoveError, _element_slots, move_rules, move_section, new_position
from tests._helpers import seed_station
from tests.test_editor_api import editor_api  # noqa: F401 (fixture)

CASES = json.loads((Path(__file__).parent / "fixtures" / "rule_moves.json").read_text())
RANDOM_FILE = Path(__file__).parent / "fixtures" / "rule_moves_random.json"


def _apply(station: dict, edit: dict) -> tuple[dict, dict]:
    if edit["kind"] == "moveRules":
        return move_rules(
            station["template"], station["sidecar"], edit["from"], edit["count"],
            edit["to"], edit["sectionId"],
        )
    return move_section(
        station["template"], station["sidecar"], edit["sectionId"],
        edit["beforeSectionId"], edit["from"], edit["count"], edit["to"],
    )


def _blocks(section: dict) -> list:
    def one(b: dict):
        if b["kind"] != "atomic":
            return f"macro:{b['ref']}"
        return b["slot_index"] if "slot_index" in b else f"{b['row_idx']}:{b['block_idx']}"
    return [one(b) for b in section["blocks"]]


def _rules(template: dict) -> list:
    layout = template["layout"]
    return layout.get("row_template") or layout["flat_slots"]


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_shared_case(case):
    station = CASES["stations"][case["station"]]
    before = json.dumps(station, sort_keys=True)
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(RuleMoveError, match=expect["error"]):
            _apply(station, case["edit"])
    else:
        template, sidecar = _apply(station, case["edit"])
        assert [b["source"] for b in _rules(template)] == expect["order"]
        assert {s["id"]: _blocks(s) for s in sidecar["sections"]} == expect["sections"]
        assert sidecar["layout_order"] == expect["layout_order"]
    # Pure: the station it was given is untouched.
    assert json.dumps(station, sort_keys=True) == before


def test_new_position_is_a_permutation():
    for n in range(1, 7):
        for f in range(n):
            for c in range(1, n - f + 1):
                for t in [*range(0, f), *range(f + c + 1, n + 1)]:
                    image = sorted(new_position(p, f, c, t) for p in range(n))
                    assert image == list(range(n)), (n, f, c, t)


# ---------- end to end, through the save batch ------------------------------

def _five_rules_station(Session) -> None:
    template = seed_station(Session, slug="mv")
    template["layout"]["flat_slots"] = [
        {**template["layout"]["flat_slots"][0], "size": n} for n in range(1, 6)
    ]
    sidecar = {
        "schema_version": 1,
        "sections": [
            {"id": "morning", "name": "Morning", "blocks": [
                {"kind": "atomic", "slot_index": i} for i in range(3)]},
            {"id": "evening", "name": "Evening", "blocks": [
                {"kind": "atomic", "slot_index": i} for i in range(3, 5)]},
        ],
        "macros": [],
        "layout_order": ["morning", "evening"],
    }
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "mv").first()
        s.template = template
        s.sidecar = sidecar
        db.commit()


@pytest.fixture
def five(editor_api):  # noqa: F811
    client, Session, _dir = editor_api
    _five_rules_station(Session)
    return client, Session


def _sizes(client) -> list[int]:
    r = client.get("/api/stations/mv/sidecar")
    assert r.status_code == 200, r.json()
    return [b["size"] for b in r.json()["template"]["layout"]["flat_slots"]]


def test_batch_moves_rules_and_their_section(five):
    client, _Session = five
    r = client.post("/api/stations/mv/sidecar/batch", json={"edits": [
        {"kind": "moveRules", "from": 0, "count": 2, "to": 4, "sectionId": "evening"},
    ]})
    assert r.status_code == 200, r.json()
    body = r.json()
    # Sizes tell the rules apart: 1 and 2 now play before 5.
    assert [b["size"] for b in body["template"]["layout"]["flat_slots"]] == [3, 4, 1, 2, 5]
    assert [[b["slot_index"] for b in s["blocks"]] for s in body["sidecar"]["sections"]] == [[0], [1, 2, 3, 4]]
    assert _sizes(client) == [3, 4, 1, 2, 5]


def test_batch_moves_a_section(five):
    client, _Session = five
    r = client.post("/api/stations/mv/sidecar/batch", json={"edits": [
        {"kind": "moveSection", "sectionId": "evening", "beforeSectionId": "morning", "from": 3, "count": 2, "to": 0},
    ]})
    assert r.status_code == 200, r.json()
    assert r.json()["sidecar"]["layout_order"] == ["evening", "morning"]
    assert _sizes(client) == [4, 5, 1, 2, 3]


def test_batch_edits_after_a_move_address_rules_where_they_are(five):
    """Edits in one batch apply in turn: a patch after a move is to the
    rule now at that slot."""
    client, _Session = five
    r = client.post("/api/stations/mv/sidecar/batch", json={"edits": [
        {"kind": "moveRules", "from": 4, "count": 1, "to": 0, "sectionId": "morning"},
        {"kind": "structuralBlockEdit", "edit": {"op": "size", "slot_index": 0, "new_size": 9}},
    ]})
    assert r.status_code == 200, r.json()
    assert _sizes(client) == [9, 1, 2, 3, 4]


def test_batch_refuses_a_bad_move_and_keeps_everything(five):
    client, _Session = five
    r = client.post("/api/stations/mv/sidecar/batch", json={"edits": [
        {"kind": "renameSection", "sectionId": "morning", "name": "AM"},
        {"kind": "moveRules", "from": 1, "count": 1, "to": 2, "sectionId": "morning"},
    ]})
    assert r.status_code == 400
    body = r.json()
    assert (body["failed_at_idx"], body["kind"]) == (2, "moveRules")
    assert "already there" in body["error"]
    r = client.get("/api/stations/mv/sidecar")
    assert r.json()["sidecar"]["sections"][0]["name"] == "Morning"
    assert _sizes(client) == [1, 2, 3, 4, 5]


# ---------- a station built from a repeating row ----------------------------

@pytest.fixture
def rows(editor_api):  # noqa: F811
    """Two days of three blocks (told apart by size), in two sections that
    span both days: Morning has blocks 0-1, Evening block 2."""
    client, Session, _dir = editor_api
    template = seed_station(Session, slug="rw")
    template["layout"] = {
        "unit": "day", "unit_labels": ["Mon", "Tue"], "row_start_offsets": [0, 3],
        "row_template": [{"source": "A", "strategy": "pick", "size": n} for n in (1, 2, 3)],
    }
    at = lambda r, b: {"kind": "atomic", "row_idx": r, "block_idx": b}  # noqa: E731
    sidecar = {
        "schema_version": 1,
        "sections": [
            {"id": "morning", "name": "Morning", "blocks": [at(0, 0), at(0, 1), at(1, 0), at(1, 1)]},
            {"id": "evening", "name": "Evening", "blocks": [at(0, 2), at(1, 2)]},
        ],
        "macros": [],
        "layout_order": ["morning", "evening"],
    }
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "rw").first()
        s.template = template
        s.sidecar = sidecar
        db.commit()
    return client


def test_batch_moves_a_rows_rules_in_every_row(rows):
    r = rows.post("/api/stations/rw/sidecar/batch", json={"edits": [
        {"kind": "moveRules", "from": 2, "count": 1, "to": 0, "sectionId": "morning"},
    ]})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert [b["size"] for b in body["template"]["layout"]["row_template"]] == [3, 1, 2]
    assert "flat_slots" not in body["template"]["layout"]
    morning = body["sidecar"]["sections"][0]["blocks"]
    assert [(b["row_idx"], b["block_idx"]) for b in morning] == [(r, b) for r in (0, 1) for b in range(3)]
    saved = rows.get("/api/stations/rw/sidecar").json()
    assert [b["size"] for b in saved["template"]["layout"]["row_template"]] == [3, 1, 2]


def test_batch_moves_a_rows_section(rows):
    r = rows.post("/api/stations/rw/sidecar/batch", json={"edits": [
        {"kind": "moveSection", "sectionId": "evening", "beforeSectionId": "morning", "from": 2, "count": 1, "to": 0},
    ]})
    assert r.status_code == 200, r.json()
    assert r.json()["sidecar"]["layout_order"] == ["evening", "morning"]
    assert [b["size"] for b in r.json()["template"]["layout"]["row_template"]] == [3, 1, 2]


# ---------- random stations and moves -----------------------------------------

def _random_station(rng, rows: bool) -> dict:
    """A station of 2-6 rules in contiguous sections. Flat: some
    sections hold a macro over a run of their rules. Rows (1-3 of them):
    sections per row (which may hold macros, the same in every row's),
    or over every row (every block of every row is in one: the validator
    wants no orphans), or repeated for every row."""
    n = rng.randint(2, 6)
    rules = [{"source": f"r{i}", "strategy": "pick", "size": 1} for i in range(n)]
    cuts = sorted(rng.sample(range(1, n), rng.randint(0, min(2, n - 1))))
    spans = [range(a, b) for a, b in zip([0, *cuts], [*cuts, n])]
    sections, macros = [], []
    if not rows:
        template = {"layout": {"flat_slots": rules}}
        for k, span in enumerate(spans):
            blocks: list = [{"kind": "atomic", "slot_index": p} for p in span]
            # A macro over the section's rules after its first (its anchor).
            if len(span) >= 3 and rng.random() < 0.5:
                width = rng.randint(2, len(span) - 1)
                macros.append({"id": f"m{k}", "name": f"M{k}", "repeat": width,
                               "spec": {"source": "r0", "strategy": "pick", "size": 1}})
                blocks = blocks[:1] + [{"kind": "macro", "ref": f"m{k}"}] + blocks[1 + width:]
            sections.append({"id": f"s{k}", "name": f"S{k}", "blocks": blocks})
    else:
        widths: dict[int, int] = {}
        for k, span in enumerate(spans):
            # A macro over a section's blocks after its first (its
            # anchor); in sections per row, the same one in every row's (a
            # row station's macros are of the row: two can't cover the
            # same blocks).
            if len(span) >= 3 and rng.random() < 0.5:
                widths[k] = rng.randint(2, len(span) - 1)
                macros.append({"id": f"m{k}", "name": f"M{k}", "repeat": widths[k],
                               "spec": {"source": "r0", "strategy": "pick", "size": 1}})

        def with_macro(k: int, blocks: list) -> list:
            if k not in widths:
                return blocks
            return blocks[:1] + [{"kind": "macro", "ref": f"m{k}"}] + blocks[1 + widths[k]:]
        n_rows = rng.randint(1, 3)
        template = {"layout": {"unit": "day", "unit_labels": [f"D{r}" for r in range(n_rows)],
                               "row_start_offsets": [0] * n_rows, "row_template": rules}}
        shape = rng.choice(["per-row", "every-row", "every-row", "repeat"])
        at = lambda r, b: {"kind": "atomic", "row_idx": r, "block_idx": b}  # noqa: E731
        if shape == "repeat":
            # Sections repeated for every row (as Rebirth Classics' is;
            # the validator allows no macros in them).
            macros.clear()
            for k, span in enumerate(spans):
                sections.append({"id": f"s{k}", "name": f"S{k}", "repeat_with_offsets": [0] * n_rows,
                                 "blocks": [at(0, b) for b in span]})
        elif shape == "per-row":
            for r in range(n_rows):
                for k, span in enumerate(spans):
                    sections.append({"id": f"d{r}s{k}", "name": f"D{r}S{k}",
                                     "blocks": with_macro(k, [at(r, b) for b in span])})
        else:
            macros.clear()  # (a macro is of one row's blocks)
            for k, span in enumerate(spans):
                sections.append({"id": f"s{k}", "name": f"S{k}",
                                 "blocks": [at(r, b) for r in range(n_rows) for b in span]})
    sidecar = {"schema_version": 1, "sections": sections, "macros": macros,
               "layout_order": [s["id"] for s in sections]}
    return {"template": template, "sidecar": sidecar}


def _random_move(rng, station: dict) -> dict:
    layout = station["template"]["layout"]
    n = len(layout.get("row_template") or layout["flat_slots"])
    ids = [s["id"] for s in station["sidecar"]["sections"]]
    if rng.random() < 0.35:
        # A whole block (a rule, or a macro's), as a drag of one song is.
        blocks = [sl for _s, _b, sl in _element_slots(station["sidecar"]) if sl]
        sl = rng.choice(blocks)
        return {"kind": "moveRules", "from": min(sl), "count": len(sl), "to": rng.randint(0, n),
                "sectionId": rng.choice(ids)}
    if rng.random() < 0.55:
        f = rng.randrange(n)
        c = rng.randint(1, n - f)
        return {"kind": "moveRules", "from": f, "count": c, "to": rng.randint(0, n), "sectionId": rng.choice(ids)}
    sid = rng.choice(ids)
    before = rng.choice([*ids, None])
    return {"kind": "moveSection", "sectionId": sid, "beforeSectionId": before,
            "from": rng.randrange(n), "count": rng.randint(1, n), "to": rng.randint(0, n)}


def _full(template: dict) -> dict:
    """The template as the validator wants it."""
    t = copy.deepcopy(template)
    t.update({"station": "x", "name": "x", "cadence": "weekly", "rng": "off",
              "sources": [{"name": f"r{i}", "colour": "#abc", "role": "content"} for i in range(8)],
              "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1}})
    t["layout"].setdefault("unit", "slot")
    t["layout"].setdefault("unit_labels", [])
    t["layout"].setdefault("row_start_offsets", [])
    return t


def random_cases(seed: int, count: int) -> list[dict]:
    """Random stations and moves, each with what the server does: the
    station after, or the error. (Also dumped for the browser's mirror.)"""
    rng = random.Random(seed)
    out = []
    for i in range(count):
        station = _random_station(rng, rows=i % 2 == 1)
        edit = _random_move(rng, station)
        try:
            template, sidecar = _apply(station, edit)
            out.append({"station": station, "edit": edit, "template": template, "sidecar": sidecar})
        except RuleMoveError as e:
            out.append({"station": station, "edit": edit, "error": str(e)})
    return out


def test_random_moves_keep_every_block_and_a_valid_sidecar():
    """Any move the server takes: every rule and every block is still
    there, renumbered as the move says; the sidecar still validates; a
    row station's sections are in play order; blocks not moved keep
    their section, moved ones join the target or (a row station's, the
    target having none of their row) keep theirs; every macro covers the
    rules it did."""
    taken = 0
    for case in random_cases(seed=2026, count=3000):
        if "error" in case:
            continue
        taken += 1
        station, edit, template, sidecar = case["station"], case["edit"], case["template"], case["sidecar"]
        layout = template["layout"]
        rows = "row_template" in layout
        key = "row_template" if rows else "flat_slots"
        f, c, t = edit["from"], edit["count"], edit["to"]
        was_rules = station["template"]["layout"][key]
        assert sorted(r["source"] for r in layout[key]) == sorted(r["source"] for r in was_rules)
        for p, rule in enumerate(was_rules):
            assert layout[key][new_position(p, f, c, t)] is not None
            assert layout[key][new_position(p, f, c, t)]["source"] == rule["source"]
        validate_sidecar(station["sidecar"], _full(station["template"]))  # a real station
        validate_sidecar(sidecar, _full(template))

        def placed(sc):
            return {(b.get("row_idx"), b.get("slot_index", b.get("block_idx"))): s["id"]
                    for s in sc["sections"] for b in s["blocks"] if b["kind"] == "atomic"}
        before, after = placed(station["sidecar"]), placed(sidecar)
        assert len(before) == len(after)
        target_rows = {b.get("row_idx") for s in station["sidecar"]["sections"]
                       if s["id"] == edit.get("sectionId") for b in s["blocks"] if b["kind"] == "atomic"}
        for (r, p), sec in before.items():
            now = after[(r, new_position(p, f, c, t))]
            if edit["kind"] == "moveRules" and f <= p < f + c and (not rows or r in target_rows):
                assert now == edit["sectionId"]
            else:
                assert now == sec
        if rows:
            for s in sidecar["sections"]:
                order = [(b["row_idx"], b["block_idx"]) for b in s["blocks"] if b["kind"] == "atomic"]
                assert order == sorted(order)
        # Every macro covers the rules it did, where they are now.
        for m in station["sidecar"]["macros"]:
            def cover(sc, mid=m["id"]):
                return sorted({a.get("slot_index", a.get("block_idx")) for a in resolve_macro_addresses(sc, mid)})
            assert cover(sidecar) == sorted(new_position(p, f, c, t) for p in cover(station["sidecar"]))
    assert taken > 600  # the random moves aren't nearly all refused


def _kind(case: dict) -> str:
    """Which kind of station a case is of, and whether it was taken."""
    sc, layout = case["station"]["sidecar"], case["station"]["template"]["layout"]
    kind = "rows" if "row_template" in layout else "flat"
    if any(s.get("repeat_with_offsets") is not None for s in sc["sections"]):
        kind += "-repeated"
    if sc["macros"]:
        kind += "-macros"
    return kind + (" refused" if "error" in case else " taken")


def random_snapshot() -> dict:
    """Random cases for the browser's mirror to be checked against: of
    each kind of station (flat or rows; macros; sections repeated for
    every row), up to 25 moves the server takes and 10 it refuses."""
    cases = random_cases(seed=11, count=3000)
    picked: dict[str, list] = {}
    for c in cases:
        k = _kind(c)
        if len(picked.setdefault(k, [])) < (10 if k.endswith("refused") else 25):
            picked[k].append(c)
    return {
        "_about": (
            "Random stations and moves, and what the server does with each: the "
            "station after, or the error. Made by tests/test_rule_moves.py "
            "(random_snapshot), which checks this file is what it makes; "
            "web/src/lib/ruleMoves.test.ts checks the editor's preview does the "
            "same. After changing the moves: python -m tests.test_rule_moves"
        ),
        "cases": [c for k in sorted(picked) for c in picked[k]],
    }


def test_random_snapshot_is_current():
    """The browser's mirror is checked against this file: it must be what
    the server does now (regenerate it: `python -m tests.test_rule_moves`)."""
    assert json.loads(RANDOM_FILE.read_text()) == json.loads(json.dumps(random_snapshot()))


if __name__ == "__main__":
    RANDOM_FILE.write_text(json.dumps(random_snapshot(), separators=(",", ":")) + "\n")


def test_batch_moves_rebirth_classics_in_every_day(editor_api):  # noqa: F811
    """The bundled row station, as shipped: one section repeated for all
    seven days. Its two blocks swap, in every day; the section stays."""
    client, Session, _dir = editor_api
    stations = Path(__file__).parent.parent / "stations"
    template = json.loads((stations / "rebirth-classics.json").read_text())
    sidecar = json.loads((stations / "rebirth-classics.editor.json").read_text())
    seed_station(Session, slug="rc")
    with Session() as db:
        s = db.query(Station).filter(Station.slug == "rc").first()
        s.template = {**template, "station": "rc"}
        s.sidecar = sidecar
        db.commit()
    r = client.post("/api/stations/rc/sidecar/batch", json={"edits": [
        {"kind": "moveRules", "from": 1, "count": 1, "to": 0, "sectionId": "weekly-cycle"},
    ]})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert [b["source"] for b in body["template"]["layout"]["row_template"]] == ["RG_PARTY", "RG_RADIO"]
    (cycle,) = body["sidecar"]["sections"]
    assert cycle["repeat_with_offsets"] == sidecar["sections"][0]["repeat_with_offsets"]
    assert [b["block_idx"] for b in cycle["blocks"]] == [0, 1]
