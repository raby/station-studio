"""rng='on' tests.

Classics doesn't use RNG, but the engine supports it. These tests prove the
RNG path is wired correctly so that when the first random-selection station
lands, it doesn't surprise us.
"""
from __future__ import annotations

from dataclasses import dataclass
import pytest

from engine.template import parse_template
from engine.generator import generate


@dataclass
class T:
    id: int
    name: str
    artist: str | None = None


def mk_pool(prefix: str, n: int) -> list[T]:
    return [T(id=i, name=f"{prefix}{i:04d}") for i in range(1, n + 1)]


def mk_rng_template(rng: str = "on") -> dict:
    return {
        "station": "rng-test",
        "cadence": "weekly",
        "rng": rng,
        "sources": [{"name": "A", "colour": "#a", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "pick", "size": 20}],
        },
    }


def test_rng_on_requires_seed():
    t = parse_template(mk_rng_template("on"))
    with pytest.raises(ValueError, match="seed"):
        generate(t, {"A": mk_pool("A", 50)}, week=1)


def test_rng_on_same_seed_is_deterministic():
    t = parse_template(mk_rng_template("on"))
    pools = {"A": mk_pool("A", 50)}
    a = generate(t, pools, week=1, seed=42)
    b = generate(t, pools, week=1, seed=42)
    assert [p.name for p in a.picks] == [p.name for p in b.picks]


def test_rng_on_different_seeds_diverge():
    t = parse_template(mk_rng_template("on"))
    pools = {"A": mk_pool("A", 50)}
    a = generate(t, pools, week=1, seed=1)
    b = generate(t, pools, week=1, seed=2)
    assert [p.name for p in a.picks] != [p.name for p in b.picks]


def test_rng_off_ignores_seed():
    """If template says rng='off', a seed argument is ignored rather than erroring."""
    t = parse_template(mk_rng_template("off"))
    pools = {"A": mk_pool("A", 50)}
    a = generate(t, pools, week=1, seed=1)
    b = generate(t, pools, week=1, seed=99)
    assert [p.name for p in a.picks] == [p.name for p in b.picks]
    # And trace.seed is None because rng is off
    assert a.trace.seed is None


def test_rng_on_seed_recorded_in_trace():
    t = parse_template(mk_rng_template("on"))
    r = generate(t, {"A": mk_pool("A", 50)}, week=1, seed=123)
    assert r.trace.seed == 123
    assert r.trace.rng == "on"
