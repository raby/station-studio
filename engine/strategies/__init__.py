"""Slot-filling strategies.

Each strategy is a small module that, given a source pool and some parameters,
emits tracks for a block. Strategies are pure: no DB, no side effects.
"""
from engine.strategies.window import apply_window
from engine.strategies.pick import apply_pick
from engine.strategies.jingle import apply_jingle
from engine.strategies.mix import apply_mix
from engine.strategies.weighted import (
    apply_weighted, build_lookups, WeightedLookups, WeightedPickInfo,
)
from engine.strategies.prev_ref import (
    try_prev_ref_mirror, PrevRefMirrorFailure, PrevRefPositionOutOfRangeError,
)

__all__ = [
    "apply_window", "apply_pick", "apply_jingle", "apply_mix",
    "apply_weighted", "build_lookups", "WeightedLookups", "WeightedPickInfo",
    "try_prev_ref_mirror", "PrevRefMirrorFailure", "PrevRefPositionOutOfRangeError",
]
