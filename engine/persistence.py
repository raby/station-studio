"""DB persistence helpers shared between the API layer and CLI scripts.

Centralising these prevents the generate-and-persist logic from drifting
between api/routers/generate.py and scripts/bootstrap_classics.py.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from engine.artist_match import build_alias_map
from engine.models import (
    ArtistAlias, CursorState, MixUsage, Playlist, PlaylistEntry, PlaylistTrace,
    SourcePlaylist, SourcePlaylistEntry, Station,
)
from engine.template import Template


def load_pools(db: Session, template) -> dict[str, list]:
    """Load content source pools from the DB for the given template.

    Returns a dict mapping source name -> list of Track objects, ordered by
    their position within the source playlist.

    Uses joinedload to avoid an N+1 query (one per entry) when accessing
    track metadata.

    Raises ValueError if a required content pool hasn't been imported.
    """
    pools: dict[str, list] = {}
    for src_name in template.content_source_names:
        sp = db.query(SourcePlaylist).filter_by(name=src_name).first()
        if sp is None:
            raise ValueError(f"source pool '{src_name}' not imported")
        entries = (
            db.query(SourcePlaylistEntry)
            .options(joinedload(SourcePlaylistEntry.track))
            .filter_by(source_playlist_id=sp.id)
            .order_by(SourcePlaylistEntry.position)
            .all()
        )
        pools[src_name] = [e.track for e in entries]
    return pools


def save_playlist(
    db: Session,
    station: Station,
    result,
    week: int,
    *,
    template: Template | None = None,
) -> Playlist:
    """Persist a generation result as a Playlist + entries + trace.

    Replaces any existing playlist for this (station, week) pair — the
    replace logic is intentionally inside the helper so both the API
    endpoint and the bootstrap script see the same behaviour.

    The optional `template` argument enables §14.25 MixUsage recording:
    for every slot whose block uses `strategy='mix'`, one MixUsage row is
    written (playlist_id, track_id, genre). Templates with no mix blocks
    skip this entirely. Callers that already parsed the template can pass
    it through to avoid re-parsing; if omitted (legacy / Classics path),
    MixUsage is not recorded (Classics has no mix blocks, so no data is
    lost).

    Returns the newly created Playlist (flushed but NOT committed).
    The caller is responsible for calling db.commit().
    """
    existing = db.query(Playlist).filter_by(station_id=station.id, week=week).first()
    if existing:
        db.delete(existing)
        db.flush()

    playlist = Playlist(
        station_id=station.id,
        week=week,
        seed=result.trace.seed,
        trace_id=result.trace.trace_id,
    )
    db.add(playlist)
    db.flush()

    for i, (track, slot) in enumerate(zip(result.picks, result.trace.slots), start=1):
        # §14.31: slot_type denormalises the trace's strategy onto the
        # entry so SQL queries like "all mix entries this week" don't
        # need to crack the trace JSON. Trace remains the source of
        # truth (carries diagnostic detail this column doesn't); the
        # entry's slot_type is a fast-path tag.
        db.add(PlaylistEntry(
            playlist_id=playlist.id,
            track_id=track.id,
            position=i,
            source_name=slot.source,
            slot_type=slot.strategy,
        ))
    db.add(PlaylistTrace(
        trace_id=result.trace.trace_id,
        playlist_id=playlist.id,
        station_slug=station.slug,
        week=week,
        rng=result.trace.rng,
        seed=result.trace.seed,
        generated_at=result.trace.generated_at,
        slots=[s.to_dict() for s in result.trace.slots],
    ))

    if template is not None:
        _save_mix_usages(db, playlist_id=playlist.id, template=template, result=result)

    return playlist


def _save_mix_usages(db: Session, *, playlist_id: int, template: Template, result) -> None:
    """Record MixUsage rows for every mix-strategy slot in the generation.

    Walk the trace slots. Each TraceSlot carries its (row, block) coordinate
    so we can look up the originating BlockSpec; if that block is
    strategy='mix', the picked track is recorded with the block's genre.
    Genre lives on the spec (not the slot) because slot-level redundancy
    would have required a TraceSlot schema change — keeping it derivable
    from the template is cheaper and the data round-trips identically.
    """
    row_template = template.layout.row_template
    for track, slot in zip(result.picks, result.trace.slots):
        # block / row are 1-indexed on the slot.
        block_spec = row_template[slot.block - 1]
        if block_spec.strategy != "mix":
            continue
        db.add(MixUsage(
            playlist_id=playlist_id,
            track_id=track.id,
            genre=block_spec.genre,  # required by parser for mix blocks
        ))

    # §14.28: prev_ref slots that fell back to a mix-style pick are
    # ALSO recorded — the fallback hit apply_mix and consumed an
    # exclusion-window slot, so it should join MixUsage too. Mirror
    # outcomes (the resolved track came straight from the previous
    # playlist) are explicitly NOT recorded, matching weekly's
    # behaviour: a mirror is "the same content as last week" and
    # shouldn't double-count against the exclusion window. The
    # generator publishes the fallback list on result.prev_ref_fallback_mixes
    # because we can't tell from the trace slot's strategy field alone
    # ("prev_ref" covers all three outcomes).
    # `prev_ref_fallback_mixes` always exists on a freshly-constructed
    # GenerationResult (dataclass field with default_factory=list); no
    # defensive getattr needed.
    for (_slot_idx, genre, track) in result.prev_ref_fallback_mixes:
        db.add(MixUsage(
            playlist_id=playlist_id,
            track_id=track.id,
            genre=genre,
        ))


def max_mix_exclusion_window(template: Template, *, default: int = 0) -> int:
    """Return the widest exclusion_window across all mix blocks (0 if none).

    The generator only sees one excluded_mix_ids set per generation, and a
    wider window is a strictly stronger constraint (the strategy can always
    ignore extra excluded ids, but can't manufacture missing ones). So when
    a template carries multiple mix blocks with different windows, the
    union — `max(...)` — is the safe choice; per-block window enforcement
    is a punt (§14.25). Extracted so the API route reads as a one-liner
    and the multi-mix semantics get unit coverage.

    `default` (§14.29) is applied to mix blocks whose exclusion_window is
    None — i.e. the per-block field was omitted and the runtime should
    inherit the station's `mix_exclusion_weeks` rule. Callers (the API
    route) pass the resolved rule value here so the lookback depth
    reflects per-station configuration. Templates with no mix blocks
    still return 0 regardless of default.
    """
    windows = [
        (b.exclusion_window if b.exclusion_window is not None else default)
        for b in template.layout.row_template
        if b.strategy == "mix"
    ]
    return max(windows) if windows else 0


def load_previous_playlist(
    db: Session,
    *,
    station_id: int,
    current_week: int,
) -> list | None:
    """Load the most recent playlist *prior to current_week* for this
    station, as an ordered list of Track objects.

    Powers the §14.28 `prev_ref` strategy. Returns None when no prior
    playlist exists (first generation for this station, or regenerating
    week 1). Most-recent-prior semantics match weekly's
    `_load_previous_weekly`, but station-scoped via Playlist.station_id
    since station-studio is multi-station.

    `current_week` is excluded explicitly. Otherwise a regeneration of
    the current week (which deletes the existing playlist before
    saving the new one — see save_playlist) would race with the
    "most recent" query, and a regenerated week W shouldn't reference
    its own pre-deletion contents.
    """
    prev = (
        db.query(Playlist)
        .filter(Playlist.station_id == station_id, Playlist.week < current_week)
        .order_by(Playlist.week.desc())
        .first()
    )
    if prev is None:
        return None
    entries = (
        db.query(PlaylistEntry)
        .options(joinedload(PlaylistEntry.track))
        .filter_by(playlist_id=prev.id)
        .order_by(PlaylistEntry.position)
        .all()
    )
    return [e.track for e in entries]


def load_alias_map(db: Session) -> dict[str, str]:
    """Load the global ArtistAlias table into the dict shape that
    ``engine.artist_match.resolve_artist_keys`` consumes.

    Returns ``{lowercase_alias: lowercase_canonical}`` with self-mappings
    included for canonical names (so resolving the canonical itself
    yields the same key as resolving any of its aliases). Empty dict
    when the table has no rows. Ported from weekly's `app/services/
    generator.py:load_alias_map` (a one-liner that wraps build_alias_map).

    Whitespace + casing normalisation happens inside ``build_alias_map``
    (both names are ``strip().lower()``-ed), so callers can trust the
    returned dict's keys are already canonical-form. Don't re-normalise
    at the call site.
    """
    rows = db.query(ArtistAlias.alias_name, ArtistAlias.canonical_name).all()
    return build_alias_map([(a, c) for (a, c) in rows])


def load_excluded_mix_ids(db: Session, *, station_id: int, window: int) -> set[int]:
    """Build the §14.25 exclusion set: track ids used as mixes in the
    station's last `window` playlists.

    Returns an empty set when window == 0 or when the station has no
    prior playlists. Matches weekly's `_load_excluded_mix_ids` semantics
    but scopes by station_id (station-studio is multi-station; weekly is
    single-station).
    """
    if window <= 0:
        return set()
    recent = (
        db.query(Playlist.id)
        .filter(Playlist.station_id == station_id)
        .order_by(Playlist.week.desc())
        .limit(window)
        .all()
    )
    if not recent:
        return set()
    playlist_ids = [pid for (pid,) in recent]
    rows = (
        db.query(MixUsage.track_id)
        .filter(MixUsage.playlist_id.in_(playlist_ids))
        .all()
    )
    return {tid for (tid,) in rows}


# ---------- §14.30 stateful named-cursor persistence --------------------


def load_cursor_positions(
    db: Session,
    *,
    station_id: int,
    template: Template,
) -> dict[str, int]:
    """Read persistent named-cursor positions from cursor_state for this
    station; fall back to `template.layout.cursors_init` for cursors
    that don't yet have a row.

    Only called when `template.cursor.persist` is True (§14.30). For
    stateless templates the generator computes positions directly from
    `cursors_init + week`, ignoring this helper.

    Returns a complete `{name: position}` dict covering every cursor
    declared in `cursors_init`. New cursors (no DB row yet) get their
    `cursors_init` value verbatim — matching weekly's `initialize_cursors`
    behaviour on a fresh database.
    """
    init = dict(template.layout.cursors_init)
    rows = (
        db.query(CursorState.name, CursorState.position)
        .filter(CursorState.station_id == station_id)
        .all()
    )
    persisted = {name: pos for (name, pos) in rows}
    return {name: persisted.get(name, init[name]) for name in init}


def advance_cursors(
    db: Session,
    *,
    station_id: int,
    template: Template,
    pools: dict[str, list],
    advance_step: int,
    reset_cycle: int,
    generation_count: int,
) -> None:
    """Mutate cursor_state after a generation (§14.30, called by the
    API post-save_playlist).

    Logic matches weekly's `advance_cursors` flow:

    1. If `reset_cycle > 0` and `generation_count > 0` and `generation_count
       % reset_cycle == 0`, reset every cursor to its `cursors_init`
       position. (See weekly's generator.py lines 666–676 + cursor.py
       `reset_cursors_to_initial`.)
    2. Otherwise advance each cursor by `advance_step`, wrapping on the
       source-pool length (`(position + step) % len(pool)`).

    Caller commits. No-op if `template.cursor.persist` is False — the
    API layer is responsible for not calling this for stateless
    templates, but we double-check defensively.
    """
    if not template.cursor.persist:
        return  # stateless template — nothing to advance

    cursor_to_source = _cursors_to_sources(template)
    init = template.layout.cursors_init

    if reset_cycle > 0 and generation_count > 0 and generation_count % reset_cycle == 0:
        # Reset all cursors to their initial positions.
        for name, init_pos in init.items():
            _upsert_cursor_position(db, station_id=station_id, name=name, position=init_pos)
        db.flush()
        return

    # Advance each cursor by `advance_step`, wrapping on its source's
    # pool length. Cursors not yet persisted use cursors_init as the
    # starting point (matches weekly's first-generation behaviour).
    current = load_cursor_positions(db, station_id=station_id, template=template)
    for name, pos in current.items():
        source_name = cursor_to_source.get(name)
        if source_name is None or source_name not in pools:
            # Cursor declared but never referenced by a sequential block,
            # OR the pool wasn't loaded. Skip — no source to wrap against.
            continue
        source_len = len(pools[source_name])
        if source_len <= 0:
            continue
        new_pos = (pos + advance_step) % source_len
        _upsert_cursor_position(
            db, station_id=station_id, name=name, position=new_pos,
        )
    db.flush()


def _upsert_cursor_position(
    db: Session,
    *,
    station_id: int,
    name: str,
    position: int,
) -> None:
    """Idempotent upsert into cursor_state for (station_id, name).

    Helper used by advance_cursors. No commit; caller batches.
    """
    row = (
        db.query(CursorState)
        .filter(CursorState.station_id == station_id, CursorState.name == name)
        .first()
    )
    if row is None:
        db.add(CursorState(station_id=station_id, name=name, position=position))
    else:
        row.position = position


def _cursors_to_sources(template: Template) -> dict[str, str]:
    """Map cursor_id → source_name based on the template's sequential
    blocks. A cursor referenced by multiple sequential blocks is assumed
    to point at the SAME source on all of them — weekly's behaviour. The
    parser should enforce this if it ever proves contestable (TODO).
    """
    mapping: dict[str, str] = {}
    for b in template.layout.row_template:
        if b.strategy == "sequential" and b.cursor_id is not None:
            mapping.setdefault(b.cursor_id, b.source)
    return mapping
