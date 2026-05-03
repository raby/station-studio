"""DB persistence helpers shared between the API layer and CLI scripts.

Centralising these prevents the generate-and-persist logic from drifting
between api/routers/generate.py and scripts/bootstrap_classics.py.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, joinedload

from engine.models import (
    Playlist, PlaylistEntry, PlaylistTrace,
    SourcePlaylist, SourcePlaylistEntry, Station,
)


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


def save_playlist(db: Session, station: Station, result, week: int) -> Playlist:
    """Persist a generation result as a Playlist + entries + trace.

    Replaces any existing playlist for this (station, week) pair — the
    replace logic is intentionally inside the helper so both the API
    endpoint and the bootstrap script see the same behaviour.

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

    return playlist
