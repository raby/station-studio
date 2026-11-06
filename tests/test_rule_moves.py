"""Moving a flat station's rules: `engine.rule_moves` against the shared
cases (`tests/fixtures/rule_moves.json`, which the editor's in-browser
mirror is checked against too), and the two batch edits end to end."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.models import Station
from engine.rule_moves import RuleMoveError, move_rules, move_section, new_position
from tests._helpers import seed_station
from tests.test_editor_api import editor_api  # noqa: F401 (fixture)

CASES = json.loads((Path(__file__).parent / "fixtures" / "rule_moves.json").read_text())


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
    return [
        b["slot_index"] if b["kind"] == "atomic" else f"macro:{b['ref']}"
        for b in section["blocks"]
    ]


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
        assert [b["source"] for b in template["layout"]["flat_slots"]] == expect["order"]
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
