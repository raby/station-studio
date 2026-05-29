"""Stations CRUD."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from engine.models import Station, Playlist, PlaylistEntry
from engine.template import parse_template, TemplateError
from api.deps import get_db
from api.schemas import StationCreate, StationOut, StationSummary, PlaylistSummary
from sqlalchemy import func


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stations", tags=["stations"])


@router.get("", response_model=list[StationSummary])
def list_stations(db: Session = Depends(get_db)):
    """List all stations with per-station aggregates (§16.12).

    Returns the lighter `StationSummary` (no template blob) enriched
    with cadence + playlist_count + last-generation week/timestamp +
    updated_at — everything the landing page needs to search / filter
    / sort without a second round-trip per row.

    Aggregates are computed in TWO bounded queries (not N+1):

      1. Per-station count + max(generated_at).
      2. The week of the latest-by-timestamp playlist per station,
         via a ROW_NUMBER() window (argmax — a regen of an old week
         makes THAT week the latest event, so max(week) would be
         wrong).

    Default order: slug (the frontend re-sorts client-side).
    """
    stations = db.query(Station).order_by(Station.slug).all()

    # (1) count + last-generated timestamp per station.
    agg_rows = (
        db.query(
            Playlist.station_id.label("station_id"),
            func.count(Playlist.id).label("playlist_count"),
            func.max(Playlist.generated_at).label("last_generated_at"),
        )
        .group_by(Playlist.station_id)
        .all()
    )
    agg_by_station = {r.station_id: r for r in agg_rows}

    # (2) week of the latest-by-timestamp playlist per station.
    # ROW_NUMBER() partitioned by station, ordered by generated_at
    # DESC — rn==1 is the most-recent generation event. NULLs in
    # generated_at sort last, so a station with only null-timestamp
    # playlists yields whatever row the DB picks; acceptable (those
    # are pre-§14.x rows that predate generated_at).
    ranked = (
        db.query(
            Playlist.station_id.label("station_id"),
            Playlist.week.label("week"),
            func.row_number()
            .over(
                partition_by=Playlist.station_id,
                order_by=Playlist.generated_at.desc(),
            )
            .label("rn"),
        )
        .subquery()
    )
    latest_week_by_station = {
        row.station_id: row.week
        for row in db.query(ranked.c.station_id, ranked.c.week).filter(
            ranked.c.rn == 1
        )
    }

    out: list[StationSummary] = []
    for s in stations:
        agg = agg_by_station.get(s.id)
        count = agg.playlist_count if agg else 0
        last_at = agg.last_generated_at if agg else None
        out.append(
            StationSummary(
                id=s.id,
                slug=s.slug,
                name=s.name,
                # template is validated at creation, so "cadence" is
                # present; .get keeps a corrupted row from 500-ing
                # the whole list.
                cadence=s.template.get("cadence", "unknown"),
                playlist_count=count,
                last_generated_week=latest_week_by_station.get(s.id),
                last_generated_at=last_at.isoformat() if last_at else None,
                updated_at=s.updated_at.isoformat() if s.updated_at else None,
            )
        )
    return out


@router.get("/{slug}", response_model=StationOut)
def get_station(slug: str, db: Session = Depends(get_db)):
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")
    return s


@router.post("", response_model=StationOut, status_code=201)
def create_station(payload: StationCreate, db: Session = Depends(get_db)):
    try:
        parse_template(payload.template)  # validate
    except TemplateError as e:
        raise HTTPException(400, f"invalid template: {e}")

    if db.query(Station).filter(Station.slug == payload.slug).first():
        raise HTTPException(409, f"station '{payload.slug}' already exists")

    s = Station(slug=payload.slug, name=payload.name, template=payload.template)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/{slug}/playlists", response_model=list[PlaylistSummary])
def list_station_playlists(slug: str, db: Session = Depends(get_db)):
    """Summarise all generated playlists for a station, one row per week."""
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")

    # One query: playlists with their entry counts, ordered by week.
    rows = (
        db.query(Playlist, func.count(PlaylistEntry.id).label("entry_count"))
        .outerjoin(PlaylistEntry, PlaylistEntry.playlist_id == Playlist.id)
        .filter(Playlist.station_id == s.id)
        .group_by(Playlist.id)
        .order_by(Playlist.week)
        .all()
    )
    return [
        PlaylistSummary(
            id=p.id,
            week=p.week,
            trace_id=p.trace_id,
            generated_at=p.generated_at.isoformat() if p.generated_at else None,
            entry_count=entry_count,
        )
        for p, entry_count in rows
    ]


@router.delete("/{slug}", status_code=204)
def delete_station(slug: str, db: Session = Depends(get_db)):
    """Delete a station and all its playlists, entries, traces and cursor state."""
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")
    # Capture the cascade fan-out before delete — accessing `s.playlists`
    # post-commit returns an empty/detached collection.
    playlist_count = len(s.playlists)
    db.delete(s)
    db.commit()
    logger.info(
        "Deleted station '%s' (cascaded %d playlist%s)",
        slug, playlist_count, "" if playlist_count == 1 else "s",
    )


@router.put("/{slug}", response_model=StationOut)
def update_station(slug: str, payload: StationCreate, db: Session = Depends(get_db)):
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")
    if payload.slug != slug:
        raise HTTPException(400, "slug is immutable; create a new station to change it")
    try:
        parse_template(payload.template)
    except TemplateError as e:
        raise HTTPException(400, f"invalid template: {e}")
    s.name = payload.name
    s.template = payload.template
    db.commit()
    db.refresh(s)
    return s
