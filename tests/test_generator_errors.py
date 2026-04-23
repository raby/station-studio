"""Error-path tests for the generator.

The engine's job on bad inputs is to fail *loudly* and *early*. These tests
lock in exactly which errors are raised for which broken inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import pytest

from engine.template import parse_template, TemplateError
from engine.generator import generate


@dataclass
class T:
    id: int
    name: str
    artist: str | None = None


def mk_pool(n: int) -> list[T]:
    return [T(id=i, name=f"t{i}") for i in range(1, n + 1)]


BASE_TEMPLATE = {
    "station": "x",
    "cadence": "weekly",
    "rng": "off",
    "sources": [{"name": "A", "colour": "#a", "role": "content"}],
    "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
    "layout": {
        "unit": "day",
        "unit_labels": ["Mon"],
        "row_start_offsets": [0],
        "row_template": [{"source": "A", "strategy": "window", "size": 3}],
    },
}


def test_missing_pool_for_content_source_raises():
    t = parse_template(BASE_TEMPLATE)
    with pytest.raises(KeyError, match="A"):
        generate(t, pools={}, week=1)


def test_empty_pool_raises():
    t = parse_template(BASE_TEMPLATE)
    with pytest.raises(ValueError, match="empty"):
        generate(t, pools={"A": []}, week=1)


def test_week_zero_rejected():
    t = parse_template(BASE_TEMPLATE)
    with pytest.raises(TemplateError, match="week"):
        generate(t, {"A": mk_pool(10)}, week=0)


def test_negative_week_rejected():
    t = parse_template(BASE_TEMPLATE)
    with pytest.raises(TemplateError, match="week"):
        generate(t, {"A": mk_pool(10)}, week=-5)


def test_extra_pools_allowed():
    """Providing more pools than the template references is fine (silent)."""
    t = parse_template(BASE_TEMPLATE)
    r = generate(t, {"A": mk_pool(10), "UNUSED": mk_pool(10)}, week=1)
    assert len(r.picks) == 3
