"""Template validation tests."""
from __future__ import annotations

import pytest

from engine.template import parse_template, TemplateError


BASE = {
    "station": "x",
    "cadence": "weekly",
    "rng": "off",
    "sources": [
        {"name": "A", "colour": "#111", "role": "content"},
        {"name": "B", "colour": "#222", "role": "reference"},
    ],
    "cursor": {"base_advance_per_week": 10, "cycle_weeks": 4},
    "layout": {
        "unit": "day",
        "unit_labels": ["Mon", "Tue"],
        "row_start_offsets": [0, 5],
        "row_template": [
            {"source": "A", "strategy": "window", "size": 3},
        ],
    },
}


def test_base_template_parses():
    tmpl = parse_template(BASE)
    assert tmpl.station == "x"
    assert tmpl.slots_per_week() == 2 * 3


def test_unknown_strategy_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "weighted", "size": 3},
    ]}}
    with pytest.raises(TemplateError, match="strategy"):
        parse_template(bad)


def test_reference_source_in_layout_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "window", "size": 3},
    ]}}
    with pytest.raises(TemplateError, match="reference"):
        parse_template(bad)


def test_mismatched_unit_labels_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "unit_labels": ["Only one"]}}
    with pytest.raises(TemplateError, match="unit_labels"):
        parse_template(bad)


def test_unknown_top_level_key_rejected():
    bad = {**BASE, "surprise": 1}
    with pytest.raises(TemplateError, match="unknown keys"):
        parse_template(bad)


def test_optional_name_top_level_key_accepted():
    """UI-only display name; engine ignores but must not reject it."""
    tmpl = parse_template({**BASE, "name": "Nice Display Name"})
    assert tmpl.station == "x"  # still parses correctly


def test_base_cursor_progression():
    tmpl = parse_template(BASE)  # cycle_weeks=4, advance=10
    assert tmpl.base_cursor_for_week(1) == 0
    assert tmpl.base_cursor_for_week(2) == 10
    assert tmpl.base_cursor_for_week(4) == 30
    assert tmpl.base_cursor_for_week(5) == 0  # wrap


def test_duplicate_source_names_rejected():
    bad = {**BASE, "sources": [
        {"name": "A", "colour": "#111", "role": "content"},
        {"name": "A", "colour": "#222", "role": "content"},
    ]}
    with pytest.raises(TemplateError, match="duplicate"):
        parse_template(bad)


# ---------- additional validation branches -----------------------------


def test_invalid_rng_value_rejected():
    bad = {**BASE, "rng": "maybe"}
    with pytest.raises(TemplateError, match="rng must be one of"):
        parse_template(bad)


def test_invalid_source_role_rejected():
    bad = {**BASE, "sources": [
        {"name": "A", "colour": "#111", "role": "decorative"},
    ]}
    with pytest.raises(TemplateError, match="source role must be"):
        parse_template(bad)


def test_negative_base_advance_rejected():
    bad = {**BASE, "cursor": {"base_advance_per_week": -1, "cycle_weeks": 4}}
    with pytest.raises(TemplateError, match="base_advance_per_week must be >= 0"):
        parse_template(bad)


def test_zero_cycle_weeks_now_accepted():
    # Post-§14.22: cycle_weeks=0 means "no cycle, position grows linearly"
    # (Weekly-style — base_cursor_for_week handles the 0 case). Was rejected
    # in v1 when Classics was the only station and always wrapped at N weeks.
    tmpl = parse_template({**BASE, "cursor": {"base_advance_per_week": 10, "cycle_weeks": 0}})
    assert tmpl.cursor.cycle_weeks == 0
    # Verify the no-wrap behaviour: week W just gives (W-1) * advance.
    assert tmpl.base_cursor_for_week(1) == 0
    assert tmpl.base_cursor_for_week(5) == 40  # (5-1) * 10
    assert tmpl.base_cursor_for_week(100) == 990  # no wrap


def test_negative_cycle_weeks_rejected():
    bad = {**BASE, "cursor": {"base_advance_per_week": 10, "cycle_weeks": -1}}
    with pytest.raises(TemplateError, match="cycle_weeks must be >= 0"):
        parse_template(bad)


def test_block_references_unknown_source_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "NOPE", "strategy": "window", "size": 3},
    ]}}
    with pytest.raises(TemplateError, match="unknown source"):
        parse_template(bad)


def test_block_size_zero_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "window", "size": 0},
    ]}}
    with pytest.raises(TemplateError, match="size must be >= 1"):
        parse_template(bad)


# ---------- §14.22: sequential strategy + named cursors -----------------


def _with_cursors(cursors_init: dict, row_template: list) -> dict:
    return {
        **BASE,
        "layout": {
            **BASE["layout"],
            "cursors_init": cursors_init,
            "row_template": row_template,
        },
    }


def test_sequential_block_with_declared_cursor_accepted():
    tmpl = parse_template(_with_cursors(
        {"c1": 5},
        [{"source": "A", "strategy": "sequential", "size": 3, "cursor_id": "c1"}],
    ))
    assert tmpl.layout.cursors_init == {"c1": 5}
    assert tmpl.layout.row_template[0].cursor_id == "c1"


def test_sequential_block_missing_cursor_id_rejected():
    bad = _with_cursors(
        {"c1": 0},
        [{"source": "A", "strategy": "sequential", "size": 3}],
    )
    with pytest.raises(TemplateError, match="must specify cursor_id"):
        parse_template(bad)


def test_sequential_block_unknown_cursor_id_rejected():
    bad = _with_cursors(
        {"c1": 0},
        [{"source": "A", "strategy": "sequential", "size": 3, "cursor_id": "nope"}],
    )
    with pytest.raises(TemplateError, match="not in layout.cursors_init"):
        parse_template(bad)


def test_window_block_with_cursor_id_rejected():
    bad = _with_cursors(
        {"c1": 0},
        [{"source": "A", "strategy": "window", "size": 3, "cursor_id": "c1"}],
    )
    with pytest.raises(TemplateError, match=r"only valid when strategy in \['sequential'\]"):
        parse_template(bad)


def test_cursors_init_negative_position_rejected():
    bad = _with_cursors(
        {"c1": -1},
        [{"source": "A", "strategy": "sequential", "size": 3, "cursor_id": "c1"}],
    )
    with pytest.raises(TemplateError, match="non-negative integer"):
        parse_template(bad)


def test_cursors_init_must_be_dict():
    bad = {**BASE, "layout": {**BASE["layout"], "cursors_init": [1, 2, 3]}}
    with pytest.raises(TemplateError, match="must be an object"):
        parse_template(bad)


def test_cursor_position_uses_init_plus_base_advance():
    tmpl = parse_template(_with_cursors(
        {"c1": 7},
        [{"source": "A", "strategy": "sequential", "size": 3, "cursor_id": "c1"}],
    ))
    # cycle_weeks=4, advance=10 (from BASE)
    assert tmpl.cursor_position("c1", 1) == 7        # init + 0
    assert tmpl.cursor_position("c1", 2) == 17       # init + 10
    assert tmpl.cursor_position("c1", 5) == 7        # init + wrap(0)


def test_cursor_position_unknown_id_raises():
    tmpl = parse_template(BASE)
    with pytest.raises(TemplateError, match="unknown cursor_id"):
        tmpl.cursor_position("nope", 1)


# ---------- §14.24: jingle strategy + genre coupling --------------------


def test_jingle_block_with_genre_accepted():
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
    ]}})
    assert tmpl.layout.row_template[0].strategy == "jingle"
    assert tmpl.layout.row_template[0].genre == "Daytime"


def test_jingle_block_missing_genre_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "jingle", "size": 1},
    ]}}
    with pytest.raises(TemplateError, match="must specify genre"):
        parse_template(bad)


def test_jingle_block_empty_string_genre_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "jingle", "size": 1, "genre": ""},
    ]}}
    with pytest.raises(TemplateError, match="must specify a non-empty genre"):
        parse_template(bad)


def test_genre_on_non_jingle_strategy_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "window", "size": 3, "genre": "Daytime"},
    ]}}
    with pytest.raises(TemplateError, match=r"only valid when strategy in \['jingle', 'mix'\]"):
        parse_template(bad)


# ---------- §14.25: mix strategy + genre + exclusion_window -------------


def test_mix_block_with_genre_and_window_accepted():
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "genre": "01Alphastate", "exclusion_window": 20},
    ]}})
    b = tmpl.layout.row_template[0]
    assert (b.strategy, b.genre, b.exclusion_window) == ("mix", "01Alphastate", 20)


def test_mix_block_missing_genre_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "exclusion_window": 20},
    ]}}
    with pytest.raises(TemplateError, match="must specify genre"):
        parse_template(bad)


def test_mix_block_missing_exclusion_window_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "genre": "01Alphastate"},
    ]}}
    with pytest.raises(TemplateError, match="must specify exclusion_window"):
        parse_template(bad)


def test_mix_block_negative_exclusion_window_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "genre": "01Alphastate", "exclusion_window": -1},
    ]}}
    with pytest.raises(TemplateError, match="exclusion_window must be a non-negative integer"):
        parse_template(bad)


def test_mix_block_zero_exclusion_window_accepted():
    """Window of 0 = no exclusion. Valid (e.g. weekly's old default for new
    stations). Engine treats as 'no MixUsage lookup needed'."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "genre": "01Alphastate", "exclusion_window": 0},
    ]}})
    assert tmpl.layout.row_template[0].exclusion_window == 0


def test_exclusion_window_on_non_mix_strategy_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "window", "size": 3, "exclusion_window": 20},
    ]}}
    with pytest.raises(TemplateError, match=r"only valid when strategy in \['mix'\]"):
        parse_template(bad)


def test_genre_on_mix_strategy_accepted():
    """Regression: §14.24 only allowed genre for jingle. §14.25 widens the
    coupling so mix also accepts it."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "genre": "01Alphastate", "exclusion_window": 0},
    ]}})
    assert tmpl.layout.row_template[0].genre == "01Alphastate"


def test_source_by_name_unknown_raises():
    """source_by_name is defensive — upstream validation catches most cases,
    but the method itself must raise when called with an unknown name."""
    tmpl = parse_template(BASE)
    with pytest.raises(TemplateError, match="Unknown source"):
        tmpl.source_by_name("ZZZ")
