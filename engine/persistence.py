"""DB persistence helpers shared between the API layer and CLI scripts.

Centralising these prevents the generate-and-persist logic from drifting
between api/routers/generate.py and scripts/bootstrap_classics.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from engine.artist_match import build_alias_map
from engine.models import (
    ArtistAlias, CursorState, MixUsage, Playlist, PlaylistEntry, PlaylistVersion,
    SourcePlaylist, SourcePlaylistEntry, Station, Track,
)
from engine.rules import get_rule_list_value, get_rule_value
from engine.template import Template, TemplateError


def load_pools(db: Session, template) -> dict[str, list]:
    """Load source pools from the DB for the given template.

    Returns a dict mapping source name -> list of Track objects, ordered by
    their position within the source playlist.

    Loads TWO categories of source (§16.14.t6 extension):

    1. **Content sources** (``template.content_source_names``) — the
       pools the content sampler draws from. Must be role='content'
       in the DB; role='reference' raises ``TemplateError`` per the
       §14.35 contract (the dangerous direction where the generator
       would otherwise sample from a pool marked not-for-sampling).
    2. **Reference-sampling sources** (§16.14.t6 —
       ``template.reference_sampling_source_names``) — pools sampled
       at fixed positions by ``archive_ref`` blocks. Must be
       role='reference' in the DB; role='content' raises
       ``TemplateError`` (the OPPOSITE direction — the operator
       declared a reference-strategy pool but imported it as
       content; either the strategy is wrong or the import is wrong).

    Uses joinedload to avoid an N+1 query (one per entry) when accessing
    track metadata.

    Raises ValueError if any required pool hasn't been imported.
    Raises TemplateError on either-direction role mismatch.

    The two error types are deliberately different: missing-pool is
    a setup problem (run the importer), role-mismatch is a config-
    consistency problem (fix the template OR re-import with the right
    role). Callers and the API error mapping can distinguish them.
    """
    pools: dict[str, list] = {}
    # Content sources — the §14.35 cross-check direction (content-in-
    # template, reference-in-DB → error).
    for src_name in template.content_source_names:
        sp = db.query(SourcePlaylist).filter_by(name=src_name).first()
        if sp is None:
            raise ValueError(f"source pool '{src_name}' not imported")
        if sp.role == "reference":
            raise TemplateError(
                f"source pool '{src_name}' is imported with role='reference' "
                f"but the template declares it as content. Either re-import "
                f"the pool with role='content' (the generator would otherwise "
                f"sample from a pool marked not-for-sampling) or fix the "
                f"template's sources[] entry."
            )
        entries = (
            db.query(SourcePlaylistEntry)
            .options(joinedload(SourcePlaylistEntry.track))
            .filter_by(source_playlist_id=sp.id)
            .order_by(SourcePlaylistEntry.position)
            .all()
        )
        pools[src_name] = [e.track for e in entries]
    # §16.14.t6: reference-sampling sources (archive_ref). The
    # opposite-direction role cross-check applies (template wants
    # reference; DB must agree).
    for src_name in template.reference_sampling_source_names:
        if src_name in pools:
            # Defensive: a future template that declared a source as
            # BOTH content (in sources[]) and reference-sampled (by an
            # archive_ref block) would mean the role check already
            # passed for the content interpretation. Parse-time
            # role-aware cross-check makes this impossible today (a
            # source has ONE role + archive_ref REQUIRES reference),
            # so this branch is unreachable; guard anyway so a future
            # grammar change doesn't silently double-load.
            continue
        sp = db.query(SourcePlaylist).filter_by(name=src_name).first()
        if sp is None:
            raise ValueError(
                f"reference source pool '{src_name}' not imported "
                "(used by an archive_ref block; §16.14.t6)"
            )
        if sp.role != "reference":
            raise TemplateError(
                f"source pool '{src_name}' is imported with role="
                f"'{sp.role}' but the template's archive_ref block "
                "requires role='reference'. Re-import the pool with "
                "role='reference' (it'll be deterministically read at "
                "fixed positions, not sampled like a content pool), "
                "or change the strategy to a content-sampling one."
            )
        entries = (
            db.query(SourcePlaylistEntry)
            .options(joinedload(SourcePlaylistEntry.track))
            .filter_by(source_playlist_id=sp.id)
            .order_by(SourcePlaylistEntry.position)
            .all()
        )
        pools[src_name] = [e.track for e in entries]
    # §16.14.t7: allowlist sources (pools named in any block's
    # allowlist_sources field). Same shape as the reference-
    # sampling pass: must be role='reference'; missing → ValueError;
    # wrong role → TemplateError. The dispatch unions multiple
    # pools' pids per-block (D7) — load_pools just makes the
    # pools available; the union math happens in the generator.
    for src_name in template.allowlist_source_names:
        if src_name in pools:
            # Already loaded by the content or reference-sampling
            # pass. Defensive: parse-time checks ensure
            # allowlist_sources entries have role='reference', so
            # they can't overlap with content_source_names (those
            # are role='content'); they CAN overlap with
            # reference_sampling_source_names (a pool could be
            # both archive_ref'd AND allowlisted in different
            # blocks). The if-already-loaded guard skips the
            # double-load cleanly.
            continue
        sp = db.query(SourcePlaylist).filter_by(name=src_name).first()
        if sp is None:
            raise ValueError(
                f"allowlist source pool '{src_name}' not imported "
                "(used by a block's allowlist_sources; §16.14.t7)"
            )
        if sp.role != "reference":
            raise TemplateError(
                f"source pool '{src_name}' is imported with role="
                f"'{sp.role}' but the template's allowlist_sources "
                "block field requires role='reference'. The "
                "allowlist is a positive filter against the "
                "content sampler; naming a content source would "
                "be meaningless (it'd intersect a pool with "
                "itself). Re-import the pool with role='reference', "
                "or remove it from the block's allowlist_sources."
            )
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
    cursor_positions: dict[str, int] | None = None,
) -> Playlist:
    """Persist a generation result as a Playlist + entries + version.

    `cursor_positions` (persistent-cursor templates) is recorded on the
    new version: the positions this generation started from, which a
    later regeneration of the week starts from too (`plan_cursors`).

    §16.4 — multi-version semantics: if a Playlist for (station, week)
    already exists, this APPENDS a new `PlaylistVersion` (version_no =
    max + 1), rewrites the entry rows, and flips
    `Playlist.trace_id` to the new version's id. Pre-§16.4, the
    existing Playlist was deleted (cascade-killing entries + trace)
    and recreated — destructive in a way that lost trace history.

    Append semantics:
      * Playlist row itself is REUSED — keeps the same `id` so
        external pointers (PlaylistEntry.playlist_id, MixUsage.
        playlist_id) stay valid across regenerations.
      * Entries are CLEARED + rewritten (latest-only — TraceSlot
        already carries every field the trace viewer needs for
        historical rendering, so PlaylistEntry mirrors the current
        playable order). See `Playlist.current_version()` + the
        per-version `slots` JSON for historical reads.
      * MixUsage rows are CLEARED + rewritten (same rationale —
        Track-level exclusion windows operate on the current
        version's content; historical mix-decision detail lives in
        the per-version `slots` JSON).
      * PlaylistVersion row is APPENDED — the trace history grows.

    The optional `template` argument enables §14.25 MixUsage recording:
    for every slot whose block uses `strategy='mix'`, one MixUsage row is
    written (playlist_id, track_id, genre). Templates with no mix blocks
    skip this entirely. Callers that already parsed the template can pass
    it through to avoid re-parsing; if omitted (legacy / Classics path),
    MixUsage is not recorded (Classics has no mix blocks, so no data is
    lost).

    Returns the (possibly-reused) Playlist (flushed but NOT committed).
    The caller is responsible for calling db.commit().
    """
    existing = db.query(Playlist).filter_by(
        station_id=station.id, week=week,
    ).first()
    if existing is not None:
        # §16.4 — REUSE the Playlist row. Clear the latest-only
        # children (entries + mix_usage) so the regen can repopulate
        # them; PlaylistVersion is APPENDED (history preserved).
        playlist = existing
        # Manual delete via the relationship attribute — assigning
        # `playlist.entries = []` would defer to flush, and the
        # add-loop below would race the deferred delete. Direct query
        # delete keeps the order deterministic.
        for e in list(playlist.entries):
            db.delete(e)
        # `synchronize_session=False`: skips the in-session-object
        # invalidation pass that the default ('auto') would run.
        # Playlist has no `mix_usages` relationship + the immediate
        # `db.flush()` below + the per-station session lifetime mean
        # there are no stale MixUsage references to invalidate. The
        # skip is a perf win for stations with thousands of mix
        # blocks (Classics ships zero today; future stations could
        # have hundreds per week).
        db.query(MixUsage).filter_by(playlist_id=playlist.id).delete(
            synchronize_session=False,
        )
        db.flush()
        # Bump `trace_id` to point at the new version (the
        # denormalized pointer column for fast PlaylistOut serves).
        playlist.trace_id = result.trace.trace_id
        # §16.4 semantic shift: `Playlist.seed` now ALSO flips on
        # regen to mirror the current version's seed. Pre-§16.4 the
        # column was set once at row creation + never updated; now
        # it tracks the latest version so `PlaylistOut` reflects
        # current state (PlaylistVersion.seed carries the per-
        # version history independently).
        playlist.seed = result.trace.seed
        next_version_no = (
            max((v.version_no for v in playlist.versions), default=0) + 1
        )
    else:
        playlist = Playlist(
            station_id=station.id,
            week=week,
            seed=result.trace.seed,
            trace_id=result.trace.trace_id,
        )
        db.add(playlist)
        db.flush()
        next_version_no = 1

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
    db.add(PlaylistVersion(
        playlist_id=playlist.id,
        version_no=next_version_no,
        trace_id=result.trace.trace_id,
        station_slug=station.slug,
        week=week,
        rng=result.trace.rng,
        seed=result.trace.seed,
        generated_at=result.trace.generated_at,
        slots=[s.to_dict() for s in result.trace.slots],
        cursors=dict(cursor_positions) if cursor_positions is not None else None,
    ))

    # §16.4.t3 — prune old versions if the station's
    # `version_retention_cap` rule is set to a positive integer.
    # Default (0) = no cap. Pruning runs atomic with the regen so
    # the cap never lags an extra version. The current version is
    # ALWAYS preserved (we just appended it as the max version_no);
    # only older history is removed.
    _prune_old_versions(db, station=station, playlist_id=playlist.id)

    if template is not None:
        _save_mix_usages(db, playlist_id=playlist.id, template=template, result=result)

    return playlist


def _prune_old_versions(db: Session, *, station: Station, playlist_id: int) -> None:
    """§16.4.t3 retention cap. Reads the station's
    `version_retention_cap` int rule; cap=0 (default) is a no-op.
    Otherwise, counts the playlist's versions and deletes the
    OLDEST (count - cap) rows so exactly `cap` remain.

    Safe to call after the new version is appended — the new max-
    version row is part of `count` and is among the rows we keep
    (we sort ascending and delete the head).
    """
    cap = get_rule_value(db, station_id=station.id, key="version_retention_cap")
    if cap <= 0:
        return
    # Explicit flush: the new PlaylistVersion was just added but not
    # yet committed. Pruning needs to count the FULL post-append set
    # (including the just-added row). Autoflush normally handles this
    # on query, but the bulk-delete below uses `synchronize_session=
    # False` which can bypass the autoflush guarantee in some
    # SQLAlchemy versions; calling flush() makes the contract explicit.
    db.flush()
    existing = (
        db.query(PlaylistVersion.id)
        .filter_by(playlist_id=playlist_id)
        .order_by(PlaylistVersion.version_no)
        .all()
    )
    if len(existing) <= cap:
        return
    to_delete_count = len(existing) - cap
    to_delete_ids = [row.id for row in existing[:to_delete_count]]
    db.query(PlaylistVersion).filter(
        PlaylistVersion.id.in_(to_delete_ids),
    ).delete(synchronize_session=False)


class RollbackError(ValueError):
    """Raised when a rollback can't proceed (unknown version, target
    already current, deleted referenced track). The API layer
    translates these to 4xx HTTPExceptions."""


def rollback_playlist(
    db: Session,
    playlist_id: int,
    *,
    to_version: int,
) -> PlaylistVersion:
    """§16.4.t4 — restore a prior PlaylistVersion as the new current
    by APPENDING a new max-version row with the target's content.

    Design choice (option 'b' from the original §16.4 paper sketch):
    rather than mutating the current-pointer (option c, requires a
    new column on Playlist) or deleting the unwanted history (option
    a, loses audit trail), rollback CREATES a fresh PlaylistVersion
    whose slots / rng / seed match `to_version`'s. version_no = max
    + 1; trace_id = new UUID (this IS a new generation, even though
    the content is copied). `generated_at` = now (the rollback
    happened now).

    Side effects (mirrors save_playlist's reuse-and-rewrite shape):
      * PlaylistEntry rows are cleared + rewritten from the source
        version's slots — entries stay latest-only.
      * Playlist.trace_id is flipped to point at the new max version
        (denormalized pointer).
      * Playlist.seed is updated to the source version's seed.
      * §16.4.t3 retention cap is applied after the append (the new
        version counts toward the cap; oldest may be pruned).

    Not-touched (documented limitation):
      * MixUsage rows are NOT rewritten — rebuilding them requires
        the template, which isn't preserved per-PlaylistVersion. The
        next regenerate recomputes MixUsage normally; only the brief
        window between rollback + next regen has stale mix-exclusion
        history. Affects stations with mix-strategy blocks (not
        Classics).

    Raises:
      * `RollbackError` if the playlist doesn't exist, the requested
        version doesn't exist, the requested version is already
        current, or a slot's track has since been deleted (the
        rolled-back content can't be physically replayed).

    Returns the new PlaylistVersion (flushed, NOT committed). Caller
    runs db.commit().
    """
    playlist = db.query(Playlist).filter_by(id=playlist_id).first()
    if playlist is None:
        raise RollbackError(f"Playlist {playlist_id} not found")

    source = (
        db.query(PlaylistVersion)
        .filter_by(playlist_id=playlist_id, version_no=to_version)
        .first()
    )
    if source is None:
        # Surface the available range so the operator can correct
        # the call without a separate /versions round-trip.
        available = sorted(
            v.version_no for v in (
                db.query(PlaylistVersion.version_no)
                .filter_by(playlist_id=playlist_id)
                .all()
            )
        )
        if not available:
            raise RollbackError(
                f"Playlist {playlist_id} has no versions to roll back to"
            )
        raise RollbackError(
            f"Playlist {playlist_id} has no version_no={to_version} "
            f"(available: {available[0]}..{available[-1]})"
        )

    max_version_no = (
        max(v.version_no for v in playlist.versions) if playlist.versions else 0
    )
    if to_version == max_version_no:
        raise RollbackError(
            f"Playlist {playlist_id} v{to_version} is already current; "
            f"rollback would be a no-op."
        )

    # Resolve track ids in bulk — same pattern as the §16.4.t2
    # historical-export path. Deleted tracks make the rollback
    # physically impossible.
    src_slots = source.slots or []
    track_ids_in_order: list[int | None] = [
        s.get("track_id") for s in src_slots
    ]
    unique_ids = {tid for tid in track_ids_in_order if tid is not None}
    tracks_by_id = {
        t.id: t
        for t in db.query(Track).filter(Track.id.in_(unique_ids)).all()
    } if unique_ids else {}
    for pos, tid in enumerate(track_ids_in_order, start=1):
        if tid is None:
            raise RollbackError(
                f"Playlist {playlist_id} v{to_version} slot at position "
                f"{pos}: track_id is null; cannot roll back to this version."
            )
        if tid not in tracks_by_id:
            raise RollbackError(
                f"Playlist {playlist_id} v{to_version} slot at position "
                f"{pos}: track {tid} has been deleted; cannot roll back."
            )

    # Clear latest-only children (mirrors save_playlist).
    for e in list(playlist.entries):
        db.delete(e)
    db.query(MixUsage).filter_by(playlist_id=playlist.id).delete(
        synchronize_session=False,
    )
    db.flush()

    # Append new PlaylistVersion with source's content.
    new_trace_id = str(uuid.uuid4())
    new_generated_at = datetime.now(timezone.utc).isoformat()
    next_version_no = max_version_no + 1
    db.add(PlaylistVersion(
        playlist_id=playlist.id,
        version_no=next_version_no,
        trace_id=new_trace_id,
        station_slug=source.station_slug,
        week=source.week,
        rng=source.rng,
        seed=source.seed,
        generated_at=new_generated_at,
        # Deep-copy via list-comprehension over the slot dicts. The
        # source row stays addressable (rollback is non-destructive);
        # mutating it via shared references would silently corrupt
        # history.
        slots=[dict(s) for s in src_slots],
        # The rolled-back content was made from these positions; a
        # later regeneration of the week starts from them again.
        cursors=dict(source.cursors) if source.cursors is not None else None,
    ))

    # Rewrite PlaylistEntry rows from the slots — latest-only.
    for pos, tid in enumerate(track_ids_in_order, start=1):
        # `tid` already validated above; the guard's there for type
        # narrowing.
        if tid is None:
            continue
        slot = src_slots[pos - 1]
        db.add(PlaylistEntry(
            playlist_id=playlist.id,
            track_id=tid,
            position=pos,
            source_name=slot.get("source"),
            slot_type=slot.get("strategy"),
        ))

    # Flip denormalized pointers.
    playlist.trace_id = new_trace_id
    playlist.seed = source.seed

    # Apply retention cap — the new version counts toward it.
    _prune_old_versions(
        db, station=playlist.station, playlist_id=playlist.id,
    )

    db.flush()
    return (
        db.query(PlaylistVersion)
        .filter_by(playlist_id=playlist.id, version_no=next_version_no)
        .one()
    )


def _save_mix_usages(db: Session, *, playlist_id: int, template: Template, result) -> None:
    """Record MixUsage rows for every mix-strategy slot in the generation.

    Walk the trace slots. Each TraceSlot carries its (row, block) coordinate
    so we can look up the originating BlockSpec; if that block is
    strategy='mix', the picked track is recorded with the block's genre.
    Genre lives on the spec (not the slot) because slot-level redundancy
    would have required a TraceSlot schema change — keeping it derivable
    from the template is cheaper and the data round-trips identically.

    §14.32: works in both layout forms (row_template + flat_slots).
    The block-list source switches based on which form the template
    carries — parser enforces XOR so only one is non-empty.
    """
    # The trace slot's `block` index is 1-indexed into whichever
    # BlockSpec list the layout form provides. For row_template form,
    # that's the per-row block list (slots from the same block across
    # different rows share a block index, matching the dispatch order).
    # For flat_slots form, each slot's block index is its position in
    # the flat list — also 1-indexed. all_blocks() returns the active
    # form's list (XOR-enforced so one is always empty); same indexing
    # math regardless.
    block_list = template.layout.all_blocks()
    for track, slot in zip(result.picks, result.trace.slots):
        # block / row are 1-indexed on the slot.
        block_spec = block_list[slot.block - 1]
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
    # §14.32: walk both layout forms via all_blocks() — the §14.32-era
    # _save_mix_usages bug taught us "only iterating row_template" is
    # a footgun on flat_slots templates. Rebirth Radio's port uses
    # flat_slots; its mix blocks would silently get window=0 here
    # without the all_blocks() switch.
    windows = [
        (b.exclusion_window if b.exclusion_window is not None else default)
        for b in template.layout.all_blocks()
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


# ---------- §16.14.t5 cross-pool dedup -----------------------------------
#
# Resolves the `cross_pool_dedup_sources` list-rule into a set of track
# persistent_ids that the weighted strategy must NOT pick. The weighted
# filter (engine.strategies.weighted.apply_weighted) drops any track
# whose `persistent_id` appears in this set BEFORE running its
# weight/window machinery, so excluded tracks neither pollute the pick
# distribution nor count toward dedup windows.
#
# Per D5 (fail-loud) + D6 (role=reference required):
#
#   * Missing source → ValueError ("not imported"). Same exception type
#     as load_pools' missing-pool error so the API surface stays
#     consistent.
#   * Source imported with role='content' → TemplateError. Same shape
#     as load_pools' role mismatch (§14.35) but inverted: here the
#     rule names a CONTENT pool that should be reference. Catching
#     this at load-time prevents the operator from accidentally
#     starving a regular content pool by referencing it in
#     cross_pool_dedup_sources.
#
# A future widening (D6's punt) would allow role='content' too, but
# only once the engine has logic to detect circular exclusion (a pool
# excluding itself or its own siblings).


def load_excluded_track_pids(
    db: Session, *, station_id: int,
) -> frozenset[str]:
    """Resolve ``cross_pool_dedup_sources`` to a frozenset of
    ``Track.persistent_id`` values.

    Returns an empty frozenset when no sources are configured —
    callers can pass the result through to ``generate()`` unconditionally.

    Reads the per-station list-rule via ``get_rule_list_value`` and,
    for each named source pool:

      1. Looks up the ``SourcePlaylist`` row by name.
      2. Verifies ``role == 'reference'`` (D6).
      3. Collects every entry's ``Track.persistent_id`` into the
         result set.

    Raises ValueError (matching ``load_pools``) if a named source
    isn't imported; raises ``TemplateError`` if it exists but has
    ``role='content'``.
    """
    source_names = get_rule_list_value(
        db, station_id=station_id, key="cross_pool_dedup_sources",
    )
    if not source_names:
        return frozenset()

    pids: set[str] = set()
    for src_name in source_names:
        sp = db.query(SourcePlaylist).filter_by(name=src_name).first()
        if sp is None:
            # D5 — fail loud rather than silently dropping a missing
            # reference pool. Mirrors load_pools' wording so operators
            # see consistent diagnostics across both error sites.
            raise ValueError(
                f"cross_pool_dedup_sources references pool "
                f"'{src_name}' which is not imported. Import it with "
                f"role='reference' or remove it from the rule."
            )
        if sp.role != "reference":
            # D6 — only role='reference' pools may be named here. A
            # content pool would be a footgun: excluding it from
            # itself starves the strategy. The engine could enforce
            # this differently in a future slice (§16.14.t5 punt:
            # allow content pools once self-reference detection is
            # in).
            raise TemplateError(
                f"cross_pool_dedup_sources names source pool "
                f"'{src_name}' but it has role={sp.role!r}, not "
                f"'reference'. Re-import the pool with "
                f"role='reference' or rename the rule entry."
            )
        rows = (
            db.query(Track.persistent_id)
            .join(SourcePlaylistEntry, SourcePlaylistEntry.track_id == Track.id)
            .filter(SourcePlaylistEntry.source_playlist_id == sp.id)
            .all()
        )
        pids.update(pid for (pid,) in rows)
    return frozenset(pids)


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


@dataclass(frozen=True)
class CursorPlan:
    """Where one generation of a week starts its persistent cursors,
    and whether it moves the station's live cursors on afterwards.

    `positions` is None for stateless templates (the generator works
    positions out from the week number). `moves_live` is true only for
    the station's latest week (or a new later one): regenerating a past
    week mustn't move the cursors the next new week continues from.
    """
    positions: dict[str, int] | None
    moves_live: bool


def recorded_cursor_positions(
    db: Session, *, station_id: int, week: int,
) -> dict[str, int] | None:
    """The cursor positions the week's newest recorded generation
    started from, or None when none was recorded (stateless template,
    or generated before versions recorded them)."""
    row = (
        db.query(PlaylistVersion.cursors)
        .join(Playlist, Playlist.id == PlaylistVersion.playlist_id)
        .filter(
            Playlist.station_id == station_id,
            Playlist.week == week,
            PlaylistVersion.cursors.isnot(None),
        )
        .order_by(PlaylistVersion.version_no.desc())
        .first()
    )
    return dict(row[0]) if row is not None and row[0] is not None else None


def plan_cursors(
    db: Session,
    *,
    station_id: int,
    template: Template,
    week: int,
) -> CursorPlan:
    """Plan a generation (or preview) of `week`'s persistent cursors.

    * A week generated before with its positions recorded starts from
      those, so regenerating a past week (after a rule change, say)
      gives what that week would have been with the new rules.
    * Otherwise it starts from the station's live cursors, as every
      generation did before positions were recorded.
    * Only the latest week (or a new one after it) moves the live
      cursors on. Regenerating weeks 3–6 of six, in order, therefore
      leaves the live cursors where week 7 expects them.

    Cursors added to the template since a week was recorded start
    from the live positions.
    """
    if not template.cursor.persist:
        return CursorPlan(positions=None, moves_live=False)
    live = load_cursor_positions(db, station_id=station_id, template=template)
    recorded = recorded_cursor_positions(db, station_id=station_id, week=week)
    positions = live if recorded is None else {
        name: recorded.get(name, pos) for name, pos in live.items()
    }
    latest = (
        db.query(func.max(Playlist.week))
        .filter(Playlist.station_id == station_id)
        .scalar()
    )
    return CursorPlan(positions=positions, moves_live=latest is None or week >= latest)


def finish_cursors(
    db: Session,
    *,
    plan: CursorPlan,
    station_id: int,
    template: Template,
    pools: dict[str, list],
    advance_step: int,
    reset_cycle: int,
    week: int,
) -> None:
    """After a generation is saved: when it was of the latest week, the
    live cursors become its starting positions moved on by one
    generation (`advance_cursors`). A past week leaves them alone.
    Caller commits."""
    if plan.positions is None or not plan.moves_live:
        return
    for name, position in plan.positions.items():
        _upsert_cursor_position(db, station_id=station_id, name=name, position=position)
    db.flush()
    advance_cursors(
        db,
        station_id=station_id,
        template=template,
        pools=pools,
        advance_step=advance_step,
        reset_cycle=reset_cycle,
        generation_count=week - 1,
    )


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
    blocks. A cursor referenced by multiple sequential blocks must
    point at the SAME source on all of them (parser enforces this
    cross-block consistency check; see engine.template).

    §14.32 fix: walks both layout forms via all_blocks(). Before this
    fix, flat_slots templates with sequential blocks + cursor.persist
    silently saw `cursor_to_source = {}`, and advance_cursors skipped
    every cursor without advancing — meaning persistent cursors never
    moved for the very class of template (Rebirth Radio's port) that
    needs them most.
    """
    mapping: dict[str, str] = {}
    for b in template.layout.all_blocks():
        if b.strategy == "sequential" and b.cursor_id is not None:
            mapping.setdefault(b.cursor_id, b.source)
    return mapping


# ---------- multi-week chain orchestration -----------------------------
#
# §14.41 post-review sweep 4: moved here from tests/byte_equality.py
# so user-facing scripts (scripts/generate_user_playlists.py) +
# future API endpoints can reuse it without importing from `tests/`.
#
# `generate_chain` mirrors `api/routers/generate.py`'s call sequence
# exactly. The byte-equality slice's correctness hinges on the test
# path matching the production path; any deviation means a test
# passes while the API regresses.


def generate_chain(
    db: Session,
    *,
    station_slug: str,
    weeks,  # Iterable[int]
    seed_base: int,
    advance_step: int = 120,
    reset_cycle: int = 0,
) -> dict[int, list[str]]:
    """Run a multi-week chain through the full API persistence flow.

    Returns week → list of persistent IDs.

    Threaded inputs (each maps to an API helper):

      * ``load_alias_map`` (§14.26) — artist alias normalisation in
        `weighted`. Live even when the DB has no aliases.
      * ``max_mix_exclusion_window`` (§14.32 review fix) — walks
        ``layout.all_blocks()``; works for row_template AND flat_slots.
        Hardcoding the window would bypass exactly the stale-helper
        bug class that's bitten this project repeatedly.
      * ``weighted_window_default=None`` — no station-rule seeded in
        the chain DB; engine falls back to its constant. The path
        documents the rule-precedence flow.
      * ``previous_playlist=load_previous_playlist(current_week=week)``
        — prev_ref reads the most-recent-prior; week 1 falls back
        unless the caller pre-seeded a prior playlist (the §14.37 c2
        real-XML test does this with WEEKLY 001).

    Cursor advance defaults (120 / 0) match weekly's rules. Override
    via ``advance_step`` / ``reset_cycle`` for tests using compressed
    pools (e.g. the §14.37 c1 synthetic test).
    """
    # Imported lazily — `generator.generate` lives in a sibling module
    # and importing at module-load creates a circular dep.
    from engine.generator import generate
    from engine.template import parse_template

    station = db.query(Station).filter_by(slug=station_slug).one()
    tmpl = parse_template(station.template)
    pools = load_pools(db, tmpl)
    alias_map = load_alias_map(db)
    # §16.14.t5 (c1 review F2): resolved ONCE before the loop. The
    # rule + reference pools are chain-invariant — sibling pre-loop
    # state is load_pools + load_alias_map (the other chain-invariant
    # reads). Hoisting saves a per-week DB roundtrip without
    # changing semantics.
    excluded_track_pids = load_excluded_track_pids(
        db, station_id=station.id,
    )
    # Explicit track_id → persistent_id resolution (defends against the
    # stale-Track-in-identity-map bug class noted in §14.26's review).
    track_pids = {t.id: t.persistent_id for t in db.query(Track).all()}

    pids_by_week: dict[int, list[str]] = {}
    for week in weeks:
        plan = plan_cursors(db, station_id=station.id, template=tmpl, week=week)
        positions = (
            plan.positions if plan.positions is not None
            else load_cursor_positions(db, station_id=station.id, template=tmpl)
        )
        excluded_mix_ids = load_excluded_mix_ids(
            db, station_id=station.id,
            window=max_mix_exclusion_window(tmpl),
        )
        previous_playlist = load_previous_playlist(
            db, station_id=station.id, current_week=week,
        )
        result = generate(
            tmpl, pools,
            week=week,
            seed=seed_base + week,
            excluded_mix_ids=excluded_mix_ids,
            alias_map=alias_map,
            previous_playlist=previous_playlist,
            weighted_window_default=None,
            named_cursor_positions=positions,
            excluded_track_pids=excluded_track_pids,
        )
        pids_by_week[week] = [track_pids[t.id] for t in result.picks]
        save_playlist(
            db, station=station, result=result, week=week, template=tmpl,
            cursor_positions=plan.positions,
        )
        finish_cursors(
            db, plan=plan, station_id=station.id, template=tmpl, pools=pools,
            advance_step=advance_step, reset_cycle=reset_cycle, week=week,
        )
        db.commit()

    return pids_by_week
