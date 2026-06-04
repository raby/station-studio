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
    # 'fnord' isn't a real strategy; until something genuinely unsupported
    # exists for this test to call out, use an obvious nonsense name. Was
    # 'weighted' pre-§14.27 — that strategy is now supported.
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "fnord", "size": 3},
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


def test_mix_block_without_exclusion_window_accepted():
    """§14.29: exclusion_window is now OPTIONAL on mix blocks. When
    absent, the runtime falls back to the station's
    `mix_exclusion_weeks` rule (then to the engine default constant).
    Weekly's port templates still set it explicitly per slot, so
    byte-equality is unaffected; this grammar relaxation just lets
    simpler templates inherit the station default."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "mix", "size": 1, "genre": "01Alphastate"},
    ]}})
    b = tmpl.layout.row_template[0]
    assert b.strategy == "mix"
    assert b.exclusion_window is None


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


# ---------- §14.27: weighted strategy + optional window -----------------


def test_weighted_block_without_window_accepted():
    """`weighted` blocks may omit `window` — generator defaults to 10."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "weighted", "size": 1},
    ]}})
    b = tmpl.layout.row_template[0]
    assert (b.strategy, b.window) == ("weighted", None)


def test_weighted_block_with_explicit_window_accepted():
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "weighted", "size": 1, "window": 20},
    ]}})
    assert tmpl.layout.row_template[0].window == 20


def test_weighted_block_zero_window_accepted():
    """window=0 disables artist dedup entirely (track dedup still applies)."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "weighted", "size": 1, "window": 0},
    ]}})
    assert tmpl.layout.row_template[0].window == 0


def test_weighted_block_negative_window_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "weighted", "size": 1, "window": -1},
    ]}}
    with pytest.raises(TemplateError, match="window must be a non-negative integer"):
        parse_template(bad)


def test_window_on_non_weighted_strategy_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "window", "size": 3, "window": 10},
    ]}}
    with pytest.raises(TemplateError, match=r"only valid when strategy in \['weighted'\]"):
        parse_template(bad)


# ---------- §14.28: prev_ref strategy ----------------------------------


def test_prev_ref_block_with_position_and_fallback_accepted():
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "prev_ref", "size": 1,
         "position": 1742, "fallback_genre": "22Universal"},
    ]}})
    b = tmpl.layout.row_template[0]
    assert (b.strategy, b.position, b.fallback_genre) == ("prev_ref", 1742, "22Universal")


def test_prev_ref_block_without_fallback_genre_accepted():
    """fallback_genre is optional; absent → weighted-fallback path
    (commit 2 will implement, currently raises NotImplementedError at
    generation time)."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "prev_ref", "size": 1, "position": 100},
    ]}})
    b = tmpl.layout.row_template[0]
    assert (b.position, b.fallback_genre) == (100, None)


def test_prev_ref_block_missing_position_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "prev_ref", "size": 1},
    ]}}
    with pytest.raises(TemplateError, match="must specify position"):
        parse_template(bad)


def test_prev_ref_block_zero_position_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "prev_ref", "size": 1, "position": 0},
    ]}}
    with pytest.raises(TemplateError, match="position must be a positive integer"):
        parse_template(bad)


def test_prev_ref_block_negative_position_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "prev_ref", "size": 1, "position": -5},
    ]}}
    with pytest.raises(TemplateError, match="position must be a positive integer"):
        parse_template(bad)


def test_prev_ref_block_empty_fallback_genre_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "prev_ref", "size": 1,
         "position": 100, "fallback_genre": ""},
    ]}}
    with pytest.raises(TemplateError, match="fallback_genre must be a non-empty string"):
        parse_template(bad)


def test_position_on_non_position_bearing_strategy_rejected():
    """§16.14.t6: position is required by prev_ref + archive_ref;
    forbidden for every other strategy. Error message lists both
    valid strategies in sorted order."""
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "window", "size": 3, "position": 100},
    ]}}
    with pytest.raises(
        TemplateError,
        match=r"only valid when strategy in \['archive_ref', 'prev_ref'\]",
    ):
        parse_template(bad)


def test_fallback_genre_on_non_prev_ref_strategy_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "window", "size": 3, "fallback_genre": "X"},
    ]}}
    with pytest.raises(TemplateError, match=r"only valid when strategy in \['prev_ref'\]"):
        parse_template(bad)


# ---------- §14.30: cursor.persist field ----------------------------


def test_cursor_persist_defaults_to_false():
    tmpl = parse_template(BASE)
    assert tmpl.cursor.persist is False


def test_cursor_persist_can_be_set_true():
    tmpl = parse_template({**BASE, "cursor": {
        "base_advance_per_week": 10, "cycle_weeks": 4, "persist": True,
    }})
    assert tmpl.cursor.persist is True


def test_cursor_persist_non_bool_rejected():
    with pytest.raises(TemplateError, match="cursor.persist must be a boolean"):
        parse_template({**BASE, "cursor": {
            "base_advance_per_week": 10, "cycle_weeks": 4, "persist": "yes",
        }})


def test_cursor_multi_source_conflict_rejected():
    """§14.30 review fix: a named cursor referenced by two sequential
    blocks with DIFFERENT sources is a template error. advance_cursors
    would silently pick one source's pool-length for wrap math; the
    other block's cursor advance would be wrong. Parser rejects up
    front."""
    bad = {**BASE, "sources": [
        {"name": "A", "colour": "#111", "role": "content"},
        {"name": "B", "colour": "#222", "role": "content"},
    ], "layout": {
        **BASE["layout"],
        "cursors_init": {"shared": 0},
        "row_template": [
            {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "shared"},
            {"source": "B", "strategy": "sequential", "size": 1, "cursor_id": "shared"},
        ],
    }}
    with pytest.raises(TemplateError, match="conflicting sources"):
        parse_template(bad)


def test_cursor_same_source_two_blocks_accepted():
    """Multiple sequential blocks sharing a cursor + a SOURCE is the
    normal weekly pattern — accepted."""
    tmpl = parse_template({**BASE, "layout": {
        **BASE["layout"],
        "cursors_init": {"shared": 0},
        "row_template": [
            {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "shared"},
            {"source": "A", "strategy": "sequential", "size": 2, "cursor_id": "shared"},
        ],
    }})
    assert tmpl.layout.row_template[0].cursor_id == "shared"
    assert tmpl.layout.row_template[1].cursor_id == "shared"


# ---------- §14.32: flat_slots vs row_template XOR --------------------


def test_flat_slots_template_accepted_without_row_machinery():
    """A flat_slots template omits row_template + has empty row_start_offsets
    and unit_labels (no row dimension). Parser accepts."""
    tmpl = parse_template({
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "flat_slots": [
                {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
                {"source": "A", "strategy": "mix", "size": 1,
                 "genre": "House", "exclusion_window": 5},
            ],
        },
    })
    assert tmpl.layout.flat_slots[0].strategy == "jingle"
    assert tmpl.layout.flat_slots[1].strategy == "mix"
    assert tmpl.layout.row_template == []  # XOR partner empty
    assert tmpl.slots_per_week() == 2


def test_flat_slots_omitted_key_still_works():
    """flat_slots key can be entirely absent — defaults to empty list,
    XOR with row_template still works as before."""
    tmpl = parse_template(BASE)  # BASE has no flat_slots key
    assert tmpl.layout.flat_slots == []
    assert tmpl.layout.row_template  # the existing row_template still parsed


def test_row_template_and_flat_slots_both_set_rejected():
    """XOR violation: both forms non-empty is ambiguous."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day", "unit_labels": ["Mon"], "row_start_offsets": [0],
            "row_template": [{"source": "A", "strategy": "pick", "size": 1}],
            "flat_slots": [{"source": "A", "strategy": "pick", "size": 1}],
        },
    }
    with pytest.raises(TemplateError, match="EXACTLY one"):
        parse_template(bad)


def test_neither_row_template_nor_flat_slots_rejected():
    """XOR violation: both forms empty (or absent) has no slots to
    generate."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day", "unit_labels": [], "row_start_offsets": [],
            # No row_template, no flat_slots.
        },
    }
    with pytest.raises(TemplateError, match="got neither"):
        parse_template(bad)


def test_flat_slots_with_nonempty_row_start_offsets_rejected():
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [0, 5],  # non-empty — invalid with flat_slots
            "flat_slots": [{"source": "A", "strategy": "jingle", "size": 1,
                            "genre": "Daytime"}],
        },
    }
    with pytest.raises(TemplateError, match="row_start_offsets must be empty"):
        parse_template(bad)


def test_flat_slots_with_nonempty_unit_labels_rejected():
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": ["Mon"],  # non-empty — invalid with flat_slots
            "row_start_offsets": [],
            "flat_slots": [{"source": "A", "strategy": "jingle", "size": 1,
                            "genre": "Daytime"}],
        },
    }
    with pytest.raises(TemplateError, match="unit_labels must be empty"):
        parse_template(bad)


def test_flat_slots_window_strategy_rejected():
    """window depends on row_start_offsets — meaningless in flat form."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot", "unit_labels": [], "row_start_offsets": [],
            "flat_slots": [
                {"source": "A", "strategy": "window", "size": 3},
            ],
        },
    }
    with pytest.raises(TemplateError, match="strategy 'window' requires row-offset"):
        parse_template(bad)


def test_flat_slots_pick_strategy_rejected():
    """Same: pick depends on row_start_offsets."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot", "unit_labels": [], "row_start_offsets": [],
            "flat_slots": [
                {"source": "A", "strategy": "pick", "size": 1},
            ],
        },
    }
    with pytest.raises(TemplateError, match="strategy 'pick' requires row-offset"):
        parse_template(bad)


def test_flat_slots_supports_all_weekly_strategies():
    """Smoke test: a flat_slots template can carry every non-window/pick
    strategy. Verifies the parser threads each through correctly."""
    tmpl = parse_template({
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"NIGHT_1": 0},
            "flat_slots": [
                {"source": "A", "strategy": "jingle", "size": 1, "genre": "Daytime"},
                {"source": "A", "strategy": "mix", "size": 1, "genre": "House", "exclusion_window": 5},
                {"source": "A", "strategy": "weighted", "size": 1, "window": 10},
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "NIGHT_1"},
                {"source": "A", "strategy": "prev_ref", "size": 1,
                 "position": 1, "fallback_genre": "House"},
            ],
        },
    })
    strategies = [b.strategy for b in tmpl.layout.flat_slots]
    assert strategies == ["jingle", "mix", "weighted", "sequential", "prev_ref"]


def test_flat_slots_cursors_init_still_applies():
    """Sequential blocks in flat_slots can reference declared cursors —
    the cursors_init machinery is not row-form-specific."""
    tmpl = parse_template({
        "station": "rb", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"NIGHT_1": 0, "NIGHT_2": 10},
            "flat_slots": [
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "NIGHT_1"},
                {"source": "A", "strategy": "sequential", "size": 1, "cursor_id": "NIGHT_2"},
            ],
        },
    })
    assert tmpl.layout.cursors_init == {"NIGHT_1": 0, "NIGHT_2": 10}


def test_flat_slots_unknown_cursor_id_still_rejected():
    """Sequential block validation (cursor_id ∈ cursors_init) applies in
    flat_slots just like in row_template."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {"NIGHT_1": 0},
            "flat_slots": [
                {"source": "A", "strategy": "sequential", "size": 1,
                 "cursor_id": "PHANTOM"},
            ],
        },
    }
    with pytest.raises(TemplateError, match="cursor_id 'PHANTOM' not in"):
        parse_template(bad)


def test_source_by_name_unknown_raises():
    """source_by_name is defensive — upstream validation catches most cases,
    but the method itself must raise when called with an unknown name."""
    tmpl = parse_template(BASE)
    with pytest.raises(TemplateError, match="Unknown source"):
        tmpl.source_by_name("ZZZ")


# ---------- §14.41 sweep 2: parse-time strategy + role checks ---------
#
# Regression tests for the row_template-only walk bug at
# engine/template.py:288 (pre-sweep). The pre-sweep code iterated
# `layout.row_template` only — for flat_slots templates these checks
# silently skipped, so unknown strategy / non-content source slipped
# parse and crashed at dispatch.


def test_flat_slots_unknown_strategy_rejected_at_parse():
    """A flat_slots block with an unknown strategy must fail-loud at
    parse, not silently sail through (pre-sweep) and crash at
    dispatch."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {},
            "flat_slots": [
                {"source": "A", "strategy": "bananatime", "size": 1},
            ],
        },
    }
    with pytest.raises(TemplateError, match="strategy 'bananatime' not supported"):
        parse_template(bad)


def test_flat_slots_block_referencing_reference_role_source_rejected():
    """A flat_slots block pointing at a `role=reference` source must
    fail-loud at parse — only content sources can be referenced by
    the layout. Pre-sweep this silently sailed through for
    flat_slots templates."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [
            {"name": "A", "colour": "#111", "role": "content"},
            {"name": "M", "colour": "#222", "role": "reference"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {},
            "flat_slots": [
                {"source": "M", "strategy": "weighted", "size": 1},
            ],
        },
    }
    with pytest.raises(TemplateError, match="role 'reference'"):
        parse_template(bad)


def test_row_template_unknown_strategy_still_rejected():
    """The pre-sweep code DID catch this case (it iterated row_template).
    Regression test confirms the new all_blocks() walk preserves the
    behaviour for row_template templates."""
    bad = {
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [{"name": "A", "colour": "#111", "role": "content"}],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "day",
            "unit_labels": ["Mon"],
            "row_start_offsets": [0],
            "row_template": [
                {"source": "A", "strategy": "bananatime", "size": 1},
            ],
        },
    }
    with pytest.raises(TemplateError, match="strategy 'bananatime' not supported"):
        parse_template(bad)


# ---------- §16.14.t9 — rules: block grammar -----------------------------


def test_template_without_rules_block_parses():
    """No `rules:` key → both override dicts empty. Backward compat
    with every pre-§16.14.t9 template (i.e. every template
    currently in the repo)."""
    tmpl = parse_template(BASE)
    assert tmpl.rule_int_overrides == {}
    assert tmpl.rule_list_overrides == {}


def test_template_with_empty_rules_block_parses():
    """`rules: {}` is the same as no `rules:` key."""
    tmpl = parse_template({**BASE, "rules": {}})
    assert tmpl.rule_int_overrides == {}
    assert tmpl.rule_list_overrides == {}


def test_template_rules_int_override_parses():
    """An int rule from RULE_DEFAULTS lands in rule_int_overrides."""
    tmpl = parse_template({
        **BASE,
        "rules": {"artist_dedup_window": 15},
    })
    assert tmpl.rule_int_overrides == {"artist_dedup_window": 15}
    assert tmpl.rule_list_overrides == {}


def test_template_rules_list_override_parses():
    """A list rule from RULE_LIST_DEFAULTS lands in rule_list_overrides,
    with whitespace stripped per entry."""
    tmpl = parse_template({
        **BASE,
        "rules": {
            "cross_pool_dedup_sources": ["  MASTER  ", "ARCHIVE"],
        },
    })
    assert tmpl.rule_int_overrides == {}
    assert tmpl.rule_list_overrides == {
        "cross_pool_dedup_sources": ["MASTER", "ARCHIVE"],
    }


def test_template_rules_mixed_int_and_list_parses():
    """Both kinds in the same block dispatch to the right dict."""
    tmpl = parse_template({
        **BASE,
        "rules": {
            "artist_dedup_window": 12,
            "mix_exclusion_weeks": 30,
            "cross_pool_dedup_sources": ["REF"],
        },
    })
    assert tmpl.rule_int_overrides == {
        "artist_dedup_window": 12,
        "mix_exclusion_weeks": 30,
    }
    assert tmpl.rule_list_overrides == {"cross_pool_dedup_sources": ["REF"]}


def test_template_rules_empty_list_value_parses():
    """`[]` is a valid explicit "no exclusion" override."""
    tmpl = parse_template({
        **BASE,
        "rules": {"cross_pool_dedup_sources": []},
    })
    assert tmpl.rule_list_overrides == {"cross_pool_dedup_sources": []}


# ---- rejection paths ----


def test_template_rules_unknown_key_rejected():
    """Unknown key → TemplateError at parse time. Catches typos
    before they reach seed_station."""
    with pytest.raises(TemplateError, match="unknown rule key"):
        parse_template({
            **BASE,
            "rules": {"artist_dedup_widnow": 15},  # typo
        })


def test_template_rules_non_dict_rejected():
    """`rules: [...]` at the top level is a structural error."""
    with pytest.raises(TemplateError, match="rules must be a dict"):
        parse_template({**BASE, "rules": ["artist_dedup_window", 15]})


def test_template_rules_int_with_string_value_rejected():
    """String where an int rule expected. Match is loose around
    the validator's "rule '<key>' value must be int" phrasing
    so a future error-message rewording doesn't break the test."""
    with pytest.raises(TemplateError, match=r"artist_dedup_window.*must be int"):
        parse_template({
            **BASE,
            "rules": {"artist_dedup_window": "fast"},
        })


def test_template_rules_int_with_bool_value_rejected():
    """Python booleans pass `isinstance(True, int)` — explicit reject
    so `true` (which an operator might write meaning 'enabled')
    doesn't sneak through as `1`."""
    with pytest.raises(TemplateError, match="must be int"):
        parse_template({
            **BASE,
            "rules": {"artist_dedup_window": True},
        })


def test_template_rules_int_negative_rejected():
    with pytest.raises(TemplateError, match="non-negative"):
        parse_template({
            **BASE,
            "rules": {"artist_dedup_window": -5},
        })


def test_template_rules_int_above_max_rejected():
    """RULE_DEFAULTS["artist_dedup_window"].max_value is 1000.
    Validator message is "at most 1000" (post-§16.14.t10
    extraction); pattern matches loosely so a wording change
    doesn't break the test."""
    with pytest.raises(TemplateError, match=r"at most 1000|≤ 1000"):
        parse_template({
            **BASE,
            "rules": {"artist_dedup_window": 5000},
        })


def test_template_rules_list_with_dict_value_rejected():
    with pytest.raises(TemplateError, match=r"cross_pool_dedup_sources.*must be a list"):
        parse_template({
            **BASE,
            "rules": {"cross_pool_dedup_sources": {"a": "b"}},
        })


def test_template_rules_list_with_non_string_entry_rejected():
    """Validator message includes the bad entry's index in
    `key'[i]` form; pattern matches the index marker loosely."""
    with pytest.raises(TemplateError, match=r"cross_pool_dedup_sources.*\[1\].*must be a string"):
        parse_template({
            **BASE,
            "rules": {"cross_pool_dedup_sources": ["MASTER", 42]},
        })


def test_template_rules_list_empty_entry_rejected():
    """Empty / whitespace-only entries are operator typos."""
    with pytest.raises(TemplateError, match="non-empty string"):
        parse_template({
            **BASE,
            "rules": {"cross_pool_dedup_sources": ["MASTER", "  "]},
        })


def test_template_rules_list_comma_in_entry_rejected():
    """The on-disk format is comma-separated; entries with commas
    would split silently on read. Reject at parse time."""
    with pytest.raises(TemplateError, match="separator character"):
        parse_template({
            **BASE,
            "rules": {"cross_pool_dedup_sources": ["Rare, Imports"]},
        })


def test_template_unknown_top_level_key_rejected_when_rule_singular():
    """A misplaced `rule:` (singular) at template top level should
    fail the strict-keys check, not be silently accepted as a typo
    for `rules`."""
    with pytest.raises(TemplateError, match=r"unknown keys.*'rule'"):
        parse_template({**BASE, "rule": {"artist_dedup_window": 15}})


# ---------- §16.14.t9 slice-review S4 — bundled-stations pin ----------


def test_every_bundled_station_parses_under_t9_grammar():
    """§16.14.t9 slice-review S4: every committed `stations/*.json`
    parses cleanly under the new grammar.

    A broken `rules:` block (or any other parser regression) in a
    bundled station would otherwise only surface as a silent
    lifespan skip — the auto-seed catches TemplateError + continues
    (see api/main.py + tests/test_api.py::test_lifespan_continues_
    on_broken_template). The server still comes up; the broken
    station just never lands. This pin makes the failure loud at
    test time.

    Iterates the actual on-disk JSONs (not a fixed list) so a
    future-added bundled station is automatically covered without
    needing a per-station test.
    """
    import glob
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    station_paths = sorted(
        p for p in glob.glob(str(repo_root / "stations" / "*.json"))
        if not (p.endswith(".editor.json") or p.endswith(".pool-manifest.json"))
    )
    assert station_paths, "no bundled stations found under stations/*.json"
    for path in station_paths:
        with open(path) as f:
            raw = json.load(f)
        # Should not raise. Any TemplateError surfaces here with
        # the offending station path in the assertion message.
        try:
            parse_template(raw)
        except TemplateError as e:
            raise AssertionError(
                f"bundled station {path} failed t9-grammar parse: {e}"
            )


# ---------- §16.14.t6 — archive_ref grammar -----------------------------
#
# BASE declares two sources: 'A' (content) and 'B' (reference). archive_ref
# blocks require role=reference, so they reference 'B'. The existing
# §14.35 cross-check (only-content-sources-in-layout) is what t6
# extends — these tests pin both the happy path AND the strategy-
# aware role rejections.


def test_archive_ref_block_with_position_accepted():
    """Happy path: archive_ref + reference source + position parses."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "archive_ref", "size": 1, "position": 1},
    ]}})
    b = tmpl.layout.row_template[0]
    assert (b.source, b.strategy, b.position) == ("B", "archive_ref", 1)
    # archive_ref has no fallback_genre concept (D5).
    assert b.fallback_genre is None


def test_archive_ref_block_missing_position_rejected():
    """position is required for archive_ref (D2) — same contract as
    prev_ref. Shares the parser's `required_for={"prev_ref",
    "archive_ref"}` set."""
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "archive_ref", "size": 1},
    ]}}
    with pytest.raises(TemplateError, match="must specify position"):
        parse_template(bad)


def test_archive_ref_block_zero_position_rejected():
    """1-indexed; 0 is below range."""
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "archive_ref", "size": 1, "position": 0},
    ]}}
    with pytest.raises(TemplateError, match="position must be a positive integer"):
        parse_template(bad)


def test_archive_ref_block_negative_position_rejected():
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "archive_ref", "size": 1, "position": -3},
    ]}}
    with pytest.raises(TemplateError, match="position must be a positive integer"):
        parse_template(bad)


def test_archive_ref_block_with_content_source_rejected():
    """§16.14.t6 D3 — archive_ref REQUIRES role: reference. Pointing
    at a content source fails parse with a message naming the
    strategy + the required role, so the operator knows whether to
    fix the strategy or the source declaration."""
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "A", "strategy": "archive_ref", "size": 1, "position": 1},
    ]}}
    with pytest.raises(
        TemplateError,
        match=r"strategy 'archive_ref' requires role='reference'",
    ):
        parse_template(bad)


def test_archive_ref_block_with_fallback_genre_rejected():
    """archive_ref has no fallback (D5). fallback_genre stays
    prev_ref-only; setting it on archive_ref fails with the
    'only valid when strategy in ['prev_ref']' message."""
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "archive_ref", "size": 1,
         "position": 1, "fallback_genre": "Whatever"},
    ]}}
    with pytest.raises(
        TemplateError,
        match=r"fallback_genre is only valid when strategy in \['prev_ref'\]",
    ):
        parse_template(bad)


def test_archive_ref_in_flat_slots_accepted():
    """archive_ref doesn't need row-offset addressing (no cursor,
    no row math); it's valid in flat_slots templates too — same
    as prev_ref. The flat-form strategy-exclusion list (window +
    pick) doesn't include archive_ref."""
    tmpl = parse_template({
        "station": "x", "cadence": "weekly", "rng": "off",
        "sources": [
            {"name": "A", "colour": "#111", "role": "content"},
            {"name": "B", "colour": "#222", "role": "reference"},
        ],
        "cursor": {"base_advance_per_week": 0, "cycle_weeks": 1},
        "layout": {
            "unit": "slot",
            "unit_labels": [],
            "row_start_offsets": [],
            "cursors_init": {},
            "flat_slots": [
                {"source": "B", "strategy": "archive_ref",
                 "size": 1, "position": 1},
            ],
        },
    })
    assert tmpl.layout.flat_slots[0].strategy == "archive_ref"


def test_archive_ref_size_greater_than_one_accepted():
    """size > 1 reads consecutive positions starting at `position`
    (P, P+1, P+2, ...). Parser doesn't constrain size."""
    tmpl = parse_template({**BASE, "layout": {**BASE["layout"], "row_template": [
        {"source": "B", "strategy": "archive_ref", "size": 4, "position": 1},
    ]}})
    b = tmpl.layout.row_template[0]
    assert (b.size, b.position) == (4, 1)


def test_reference_sampling_source_names_lists_archive_ref_sources():
    """§16.14.t6: Template.reference_sampling_source_names returns
    the deduplicated, sorted list of reference-role sources used
    by archive_ref blocks. Drives load_pools' second pass."""
    tmpl = parse_template({
        **BASE,
        "sources": [
            {"name": "A", "colour": "#111", "role": "content"},
            {"name": "B", "colour": "#222", "role": "reference"},
            {"name": "C", "colour": "#333", "role": "reference"},
        ],
        "layout": {**BASE["layout"], "row_template": [
            {"source": "A", "strategy": "window", "size": 1},
            {"source": "C", "strategy": "archive_ref", "size": 1, "position": 1},
            {"source": "B", "strategy": "archive_ref", "size": 1, "position": 2},
            {"source": "C", "strategy": "archive_ref", "size": 1, "position": 3},
        ]},
    })
    # Deduplicated + sorted; 'A' content source NOT included.
    assert tmpl.reference_sampling_source_names == ["B", "C"]
    # 'A' is in content_source_names; reference sources are NOT.
    assert tmpl.content_source_names == ["A"]
    assert "B" not in tmpl.content_source_names
    assert "C" not in tmpl.content_source_names


def test_reference_sampling_source_names_empty_when_no_archive_ref():
    """The §14.35 dangerous-content-in-reference cross-check still
    fires; a template with NO archive_ref blocks gets an empty
    reference-sampling list."""
    tmpl = parse_template(BASE)
    assert tmpl.reference_sampling_source_names == []


def test_content_block_pointing_at_reference_source_still_rejected():
    """§14.35 contract preserved: content strategies (window, weighted,
    etc.) still cannot reference reference-role sources. The t6
    strategy-aware role check only relaxes for archive_ref."""
    bad = {**BASE, "layout": {**BASE["layout"], "row_template": [
        # weighted is a content sampler; 'B' is reference.
        {"source": "B", "strategy": "weighted", "size": 1},
    ]}}
    with pytest.raises(
        TemplateError,
        match=r"only 'content' sources can be referenced by the layout",
    ):
        parse_template(bad)
