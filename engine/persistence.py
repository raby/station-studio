"""DB persistence helpers shared between the API layer and CLI scripts.

Centralising these prevents the generate-and-persist logic from drifting
between api/routers/generate.py and scripts/bootstrap_classics.py.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from engine.artist_match import build_alias_map
from engine.models import (
    ArtistAlias, MixUsage, Playlist, PlaylistEntry, PlaylistTrace,
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
        db.add(PlaylistEntry(
            playlist_id=playlist.id,
            track_id=track.id,
            position=i,
            source_name=slot.source,
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


def max_mix_exclusion_window(template: Template) -> int:
    """Return the widest exclusion_window across all mix blocks (0 if none).

    The generator only sees one excluded_mix_ids set per generation, and a
    wider window is a strictly stronger constraint (the strategy can always
    ignore extra excluded ids, but can't manufacture missing ones). So when
    a template carries multiple mix blocks with different windows, the
    union — `max(...)` — is the safe choice; per-block window enforcement
    is a punt (§14.25). Extracted so the API route reads as a one-liner
    and the multi-mix semantics get unit coverage.
    """
    windows = [
        b.exclusion_window for b in template.layout.row_template
        if b.strategy == "mix" and b.exclusion_window is not None
    ]
    return max(windows) if windows else 0


def load_alias_map(db: Session) -> dict[str, str]:
    """Load the global ArtistAlias table into the dict shape that
    ``engine.artist_match.resolve_artist_keys`` consumes.

    Returns ``{lowercase_alias: lowercase_canonical}`` with self-mappings
    included for canonical names (so resolving the canonical itself
    yields the same key as resolving any of its aliases). Empty dict
    when the table has no rows. Ported from weekly's `app/services/
    generator.py:load_alias_map` (a one-liner that wraps build_alias_map).
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
