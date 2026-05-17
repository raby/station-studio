"""Slot-filling strategies.

Each strategy is a small module that, given a source pool and some parameters,
emits tracks for a block. Strategies are pure: no DB, no side effects.
"""
from engine.strategies.window import apply_window
from engine.strategies.pick import apply_pick
from engine.strategies.jingle import apply_jingle
from engine.strategies.mix import apply_mix

__all__ = ["apply_window", "apply_pick", "apply_jingle", "apply_mix"]
