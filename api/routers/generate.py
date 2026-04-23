"""Generate a playlist for a station + week."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from engine.models import (
    Station, SourcePlaylist, SourcePlaylistEntry,
    Playlist, PlaylistEntry, PlaylistTrace,
)
from engine.template import parse_template, TemplateError
from engine.generator import generate
from engine.exporter import export_playlist
from api.deps import get_db
from api.schemas import GenerateRequest, GenerateResponse, PlaylistOut, PlaylistEntryOut, TrackOut


router = APIRouter(tags=["generate"])


@router.post("/api/stations/{slug}/generate", response_model=GenerateResponse)
def generate_playlist(slug: str, payload: GenerateRequest, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.slug == slug).first()
    if not station:
        raise HTTPException(404, f"station '{slug}' not found")

    try:
        template = parse_template(station.template)
    except TemplateError as e:
        raise HTTPException(500, f"stored template is invalid: {e}")

    # Load pools from DB for every content source the template references
    pools: dict[str, list] = {}
    for src_name in template.content_source_names:
        sp = db.query(SourcePlaylist).filter(SourcePlaylist.name == src_name).first()
        if not sp:
            raise HTTPException(400, f"source pool '{src_name}' not imported")
        entries = (
            db.query(SourcePlaylistEntry)
            .filter_by(source_playlist_id=sp.id)
            .order_by(SourcePlaylistEntry.position)
            .all()
        )
        pools[src_name] = [e.track for e in entries]

    try:
        result = generate(template, pools, week=payload.week, seed=payload.seed)
    except Exception as e:
        raise HTTPException(400, f"generation failed: {e}")

    # Replace any existing playlist for this (station, week)
    existing = db.query(Playlist).filter_by(station_id=station.id, week=payload.week).first()
    if existing:
        db.delete(existing)
        db.flush()

    playlist = Playlist(
        station_id=station.id,
        week=payload.week,
        seed=payload.seed,
        trace_id=result.trace.trace_id,
    )
    db.add(playlist)
    db.flush()

    # Populate entries; look up source name per slot from the trace
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
        week=payload.week,
        rng=result.trace.rng,
        seed=result.trace.seed,
        generated_at=result.trace.generated_at,
        slots=[s.to_dict() for s in result.trace.slots],
    ))

    db.commit()

    return GenerateResponse(
        playlist_id=playlist.id,
        trace_id=result.trace.trace_id,
        station=station.slug,
        week=payload.week,
        slot_count=len(result.picks),
    )


@router.get("/api/playlists/{playlist_id}", response_model=PlaylistOut)
def get_playlist(playlist_id: int, db: Session = Depends(get_db)):
    p = db.query(Playlist).filter_by(id=playlist_id).first()
    if not p:
        raise HTTPException(404, "playlist not found")
    entries = [
        PlaylistEntryOut(
            position=e.position,
            source_name=e.source_name,
            track=TrackOut.model_validate(e.track),
        )
        for e in p.entries
    ]
    return PlaylistOut(
        id=p.id,
        station=p.station.slug,
        week=p.week,
        trace_id=p.trace_id,
        entries=entries,
    )


@router.get("/api/playlists/{playlist_id}/export")
def export(playlist_id: int, db: Session = Depends(get_db)):
    """Export a generated playlist as iTunes XML."""
    p = db.query(Playlist).filter_by(id=playlist_id).first()
    if not p:
        raise HTTPException(404, "playlist not found")
    try:
        xml_bytes = export_playlist(db, playlist_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    filename = f"{p.station.slug}-{p.week:03d}.xml"
    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/playlists/{playlist_id}/trace")
def get_trace(playlist_id: int, db: Session = Depends(get_db)):
    t = db.query(PlaylistTrace).filter_by(playlist_id=playlist_id).first()
    if not t:
        raise HTTPException(404, "trace not found")
    return {
        "trace_id": t.trace_id,
        "station": t.station_slug,
        "week": t.week,
        "rng": t.rng,
        "seed": t.seed,
        "generated_at": t.generated_at,
        "slots": t.slots,
    }
