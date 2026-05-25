"""Test-only env helpers for byte-equality fixtures.

This module USED to carry the diagnostic helpers (SlotRef,
find_first_divergence, …), the prior-seed validator
(validate_prior_seed_fixture, …), and the chain orchestrator
(generate_chain). They've all moved:

  * `engine/divergence.py` — diagnostic helpers
  * `engine/prior_seed.py` — prior-seed validator + trace_id helper
  * `engine/persistence.py` — generate_chain

…so a user-facing OSS script
(`scripts/generate_user_playlists.py`) doesn't need to import from
`tests/`. See §14.41 post-review sweep 4 in DESIGN.md.

What's left here: just the two env-var-check helpers used by
byte-equality TESTS (not production code). Re-exports below
preserve backwards-compat for any external consumer that imports
from `tests/byte_equality`; new code should import from the
engine modules directly.
"""
from __future__ import annotations

import os

# Re-exports — preserve `from tests.byte_equality import SlotRef`
# style imports during the migration window. Internal callers should
# now import from engine/ directly.
from engine.divergence import (  # noqa: F401
    SlotRef,
    slot_ref_from_block,
    expand_block_slot_refs,
    find_first_divergence,
    render_divergence_context,
    assert_pids_equal,
    assert_chain_equal,
)
from engine.persistence import generate_chain  # noqa: F401
from engine.prior_seed import (  # noqa: F401
    ALLOWED_FIXTURE_TOP_LEVEL,
    REQUIRED_PRIOR_KEYS,
    ALLOWED_PRIOR_KEYS,
    PriorSeedError,
    validate_prior_seed_fixture,
    validate_prior_cursor_names,
    trace_id_for_prior,
)


# ---------- bootstrap mode + CI guard (test-only env helpers) ----------
#
# §14.40 review sweep-up: these were duplicated in
# test_byte_equality_synthetic.py and test_classics_byte_equality.py.
# Hoisted here so all byte-equality tests share one definition; drift
# risk eliminated.
#
# These stay HERE (in tests/) because they're test-workflow env
# helpers — they shouldn't be in `engine/` since they have no
# production caller.


def bootstrap_mode() -> bool:
    """Bootstrap (fixture-regeneration) mode is active when
    ``BOOTSTRAP_BYTE_EQ_FIXTURES`` is set to the literal string ``"1"``.

    Other truthy-looking values (`true`, `yes`, etc.) are silently
    treated as off — narrow accept is deliberate to keep accidental
    activation impossible.
    """
    return os.environ.get("BOOTSTRAP_BYTE_EQ_FIXTURES", "").strip() == "1"


def is_ci() -> bool:
    """Detect whether we're running in CI (GitHub Actions and most
    other providers set ``CI=true``). Used to guard the bootstrap
    branch — a leaked env var in CI would silently re-record fixtures
    and skip-pass the build."""
    return os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
