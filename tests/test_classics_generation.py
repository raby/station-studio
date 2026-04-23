"""End-to-end test for Rebirth Classics generation.

Validates, using synthetic in-memory source pools, that the Classics template
produces the playlist the user specified:

* 3,010 slots per week
* Each row = one day (Mon..Sun)
* Rows 6 and 7 are identical (deliberate weekend duplication)
* Week 2 row 1 starts at pool index 61 (+60 from week 1)
* Week 17 row 1 starts at pool index 1 (16-week cycle wrap)
* The trace round-trips correctly (trace_id present; slots match picks)

Run:  python -m pytest tests/test_classics_generation.py -v
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from engine.template import load_template
from engine.generator import generate


# ---------- fixtures --------------------------------------------------------


@dataclass
class FakeTrack:
    """Minimum Track-like for engine consumption."""
    id: int
    name: str
    artist: str | None = None


def _pool(prefix: str, size: int = 2000) -> list[FakeTrack]:
    """Build a synthetic pool of `size` tracks named '{prefix} 001', '{prefix} 002', ..."""
    return [
        FakeTrack(
            id=hash((prefix, i)) & 0x7FFFFFFF,
            name=f"{prefix} {i:04d}",
            artist=f"{prefix}-Artist-{i % 50}",
        )
        for i in range(1, size + 1)
    ]


@pytest.fixture
def template():
    return load_template(
        Path(__file__).resolve().parent.parent / "stations" / "rebirth-classics.json"
    )


@pytest.fixture
def pools():
    return {
        "RG_RADIO": _pool("RG_RADIO"),
        "RG_PARTY": _pool("RG_PARTY"),
        # RG_MASTER is 'reference' role — not passed to generator
    }


# ---------- tests -----------------------------------------------------------


def test_week1_length_is_3010(template, pools):
    result = generate(template, pools, week=1)
    assert len(result.picks) == 3010
    assert template.slots_per_week() == 3010


def test_week1_first_slot_is_rg_radio_1(template, pools):
    result = generate(template, pools, week=1)
    assert result.picks[0].name == "RG_RADIO 0001"
    assert result.trace.slots[0].source == "RG_RADIO"
    assert result.trace.slots[0].pool_index == 1
    assert result.trace.slots[0].unit == "Mon"
    assert result.trace.slots[0].row == 1


def test_week1_block_switch_at_slot_216(template, pools):
    """Slot 216 is the first RG_PARTY entry of Monday (slots 1..215 are RG_RADIO)."""
    result = generate(template, pools, week=1)
    assert result.picks[214].name == "RG_RADIO 0215"  # slot 215
    assert result.picks[215].name == "RG_PARTY 0001"  # slot 216
    assert result.trace.slots[215].source == "RG_PARTY"
    assert result.trace.slots[215].pool_index == 1


def test_week1_row_day_labels(template, pools):
    result = generate(template, pools, week=1)
    # Row 1, slot 1 = Mon; row 7, slot 2581 = Sun
    assert result.trace.slots[0].unit == "Mon"
    assert result.trace.slots[2580].unit == "Sun"
    assert result.trace.slots[2580].row == 7


def test_week1_row_6_equals_row_7(template, pools):
    """The deliberate weekend duplication. Row 6 and row 7 must match track-for-track."""
    result = generate(template, pools, week=1)
    # Row 6 = slots 2151..2580 (0-indexed 2150..2579)
    # Row 7 = slots 2581..3010 (0-indexed 2580..3009)
    row6 = [t.name for t in result.picks[2150:2580]]
    row7 = [t.name for t in result.picks[2580:3010]]
    assert row6 == row7, "rows 6 and 7 must be identical (weekend cohesion)"
    # And the selections used must start at pool index 51
    assert result.trace.slots[2150].pool_index == 51
    assert result.trace.slots[2580].pool_index == 51


def test_week2_first_slot_shifts_plus_60(template, pools):
    """Week 2 row 1 slot 1 = RG_RADIO pool index 61."""
    result = generate(template, pools, week=2)
    assert result.picks[0].name == "RG_RADIO 0061"
    assert result.trace.slots[0].pool_index == 61


def test_week16_last_window_end(template, pools):
    """Week 16: base = 900. Row 6/7 start = 950. End of window = 950 + 215 - 1 = 1164."""
    result = generate(template, pools, week=16)
    # Last slot of row 7 is slot 3010 (0-indexed 3009). It's the 215th item in RG_PARTY.
    assert result.trace.slots[3009].source == "RG_PARTY"
    assert result.trace.slots[3009].pool_index == 1164 + 1  # wait — reason below

    # Actually, the last slot is RG_PARTY window starting at 950 (0-indexed),
    # i.e. pool index 951 through 1165 (1-indexed). Last slot index = 1165.
    # The start-at (0-indexed) = base + row_offset = 900 + 50 = 950.
    # Window is [950..1164] (0-indexed). 1-indexed pool indices = 951..1165.
    assert result.trace.slots[3009].pool_index == 1165


def test_week17_wraps_to_week1(template, pools):
    """16-week cycle: week 17 base cursor = 0 again, so row 1 slot 1 = pool index 1."""
    r1 = generate(template, pools, week=1)
    r17 = generate(template, pools, week=17)
    # Byte-for-byte identical playlist
    assert [t.name for t in r1.picks] == [t.name for t in r17.picks]


def test_trace_id_present_and_stable(template, pools):
    result = generate(template, pools, week=5)
    assert result.trace.trace_id
    # UUID-ish: 36 chars with dashes
    assert len(result.trace.trace_id) == 36


def test_trace_slots_match_picks(template, pools):
    """Each trace slot's track details must match the corresponding pick."""
    result = generate(template, pools, week=3)
    assert len(result.trace.slots) == len(result.picks)
    for slot, track in zip(result.trace.slots, result.picks):
        assert slot.track_name == track.name
        assert slot.track_artist == track.artist


def test_rng_off_is_deterministic(template, pools):
    """Two generations with the same inputs must produce identical picks."""
    a = generate(template, pools, week=7)
    b = generate(template, pools, week=7)
    assert [t.name for t in a.picks] == [t.name for t in b.picks]


def test_reference_source_not_required_in_pools(template):
    """RG_MASTER is 'reference' role. Generator must not demand it."""
    content_only_pools = {
        "RG_RADIO": _pool("RG_RADIO"),
        "RG_PARTY": _pool("RG_PARTY"),
    }
    # Must not raise
    generate(template, content_only_pools, week=1)


def test_generator_raises_when_pool_too_small(template):
    """If a window runs off the end of a pool, fail loud."""
    from engine.strategies.window import WindowOutOfRangeError
    short_pools = {
        "RG_RADIO": _pool("RG_RADIO", size=100),   # too small for window [0..214]
        "RG_PARTY": _pool("RG_PARTY", size=2000),
    }
    with pytest.raises(WindowOutOfRangeError):
        generate(template, short_pools, week=1)


def test_template_slots_per_week_matches_spec(template):
    """Template-level slot count derivation matches the Classics spec."""
    assert template.slots_per_week() == 7 * (215 + 215)
