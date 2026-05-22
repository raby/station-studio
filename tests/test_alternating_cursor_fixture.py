"""§14.39 productisation counterexample: a committed station fixture
whose layout INTERLEAVES same-cursor sequential blocks.

Weekly never does this (each named cursor appears in one contiguous
run). The fixture exists to:

  1. Verify station-studio's engine correctly handles the layout
     shape — proving the model isn't weekly-shaped.
  2. Serve as a visible recipe for future station designers
     exploring interleaved-cursor patterns. The fixture file's
     `_comment` documents the contract.

The fixture is NOT a port artifact; it's a regression lock + design
example. See `stations/rebirth-radio.json` for the actual weekly
port and `stations/rebirth-classics.json` for the v1 reference
station.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from engine.generator import generate
from engine.template import parse_template


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "alternating_cursor_station.json"


@dataclass
class _FakeTrack:
    id: int
    name: str
    artist: str | None = None
    genre: str | None = None


def _seq_pool(prefix: str, size: int) -> list[_FakeTrack]:
    return [
        _FakeTrack(id=i, name=f"{prefix}{i:04d}", artist=f"Artist {i}")
        for i in range(1, size + 1)
    ]


def test_alternating_cursor_template_parses_cleanly():
    """The committed fixture is parse_template-valid. Catches
    drift if the template grammar changes in a way that the
    counterexample no longer parses."""
    raw = json.loads(FIXTURE.read_text())
    raw.pop("_comment", None)  # parse_template is strict; comment is for humans
    tmpl = parse_template(raw)
    assert tmpl.station == "alternating-cursor-counterexample"
    assert tmpl.cursor.persist is True
    assert len(tmpl.layout.flat_slots) == 7
    # NIGHT_1 appears in 6 blocks total (3 + 3 interrupted by NIGHT_2).
    night_1_blocks = [
        b for b in tmpl.layout.flat_slots
        if b.cursor_id == "NIGHT_1"
    ]
    assert len(night_1_blocks) == 6


def test_alternating_cursor_generates_disjoint_night_1_slices():
    """The slice's central productisation claim: same-cursor
    sequential blocks separated by non-same-cursor blocks read
    DISJOINT pool slices via the per-cursor within-generation
    offset.

    Pre-§14.39 (with the §14.37 c2 coalescing patch in the
    translator), both NIGHT_1 groups would have read pool[0..2]
    — identical tracks. The engine's `seq_offsets` dict tracks
    NIGHT_1's offset across the NIGHT_2 interruption."""
    raw = json.loads(FIXTURE.read_text())
    raw.pop("_comment", None)  # parse_template is strict; comment is for humans
    tmpl = parse_template(raw)

    pools = {
        "NIGHT": _seq_pool("N", 100),
        "WEEKEND": _seq_pool("W", 100),
    }
    # cursor.persist=True; pass named_cursor_positions explicitly so
    # the test doesn't depend on a DB.
    r = generate(
        tmpl, pools,
        week=1,
        named_cursor_positions={"NIGHT_1": 0, "NIGHT_2": 50},
    )
    names = [p.name for p in r.picks]
    # First NIGHT_1 group: pool[0..2].
    assert names[0:3] == ["N0001", "N0002", "N0003"]
    # NIGHT_2 interrupts: reads pool[50] from WEEKEND.
    assert names[3] == "W0051"
    # Second NIGHT_1 group: pool[3..5] — continues across the
    # interruption. Pre-§14.39 this would have been pool[0..2] again.
    assert names[4:7] == ["N0004", "N0005", "N0006"]
