"""Stations CRUD."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from engine.models import (
    Station, Playlist, PlaylistEntry, SourcePlaylist, SourcePlaylistEntry, CursorState,
)
from engine.template import parse_template, TemplateError
from api.deps import get_db
from api.schemas import (
    CursorPosition,
    GenerationsSummary,
    PlaylistSummary,
    PoolStatus,
    StationCreate,
    StationOut,
    StationOverview,
    StationSummary,
    TemplateSummary,
)
from sqlalchemy import func


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


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
    # DESC — rn==1 is the most-recent generation event.
    #
    # Mid-slice review 🟡-1: filter out NULL generated_at rows. Without
    # this, a station whose playlists ALL have null timestamps would
    # still produce an rn==1 row (week=X), while query (1)'s max()
    # IGNORES nulls (last_generated_at=None) — leaving the two fields
    # contradicting each other ("week 5, generated never"). Filtering
    # here means null-timestamp-only stations yield NO ranked row, so
    # both fields agree on None.
    #
    # 🟢-3: secondary order key `week.desc()` makes ties deterministic
    # — two playlists with identical generated_at (batch insert,
    # second-granularity collision) pick the higher week instead of an
    # arbitrary row that could flip between requests.
    ranked = (
        db.query(
            Playlist.station_id.label("station_id"),
            Playlist.week.label("week"),
            func.row_number()
            .over(
                partition_by=Playlist.station_id,
                order_by=(Playlist.generated_at.desc(), Playlist.week.desc()),
            )
            .label("rn"),
        )
        .filter(Playlist.generated_at.isnot(None))
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


@router.get("/{slug}/overview", response_model=StationOverview)
def get_station_overview(slug: str, db: Session = Depends(get_db)):
    """Rich station-detail bundle (§16.19): identity + template summary
    + pool status + cursor positions + generation rollup, in one fetch.

    500 on a corrupted stored template (parse failure) — same posture
    as the other template-reading endpoints; the row was validated at
    creation, so a parse failure means data corruption, not user error.
    """
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")

    try:
        template = parse_template(s.template)
    except TemplateError as e:  # pragma: no cover — corrupted stored row
        raise HTTPException(500, f"stored template is invalid: {e}")

    # ----- template summary -----------------------------------------
    layout_form = "flat_slots" if template.layout.flat_slots else "row_template"
    strategy_counts = dict(
        Counter(b.strategy for b in template.layout.all_blocks())
    )
    summary = TemplateSummary(
        slot_count=template.slots_per_week(),
        layout_form=layout_form,
        strategy_counts=strategy_counts,
        cursor_persist=template.cursor.persist,
        source_count=len(template.sources),
    )

    # ----- pools: each declared source joined to its imported pool ---
    # `imported=False` flags a declared-but-not-imported source — a
    # setup gap that would 400 the generate endpoint.
    # One grouped query for track counts (mirrors list_sources) — keyed
    # by name so the per-source join below is O(1) lookups, no N+1.
    counts_by_name = {
        sp.name: (sp, track_count)
        for sp, track_count in (
            db.query(SourcePlaylist, func.count(SourcePlaylistEntry.id))
            .outerjoin(
                SourcePlaylistEntry,
                SourcePlaylistEntry.source_playlist_id == SourcePlaylist.id,
            )
            .group_by(SourcePlaylist.id)
            .all()
        )
    }
    pools: list[PoolStatus] = []
    for src in template.sources:
        hit = counts_by_name.get(src.name)
        if hit is None:
            # Declared in the template but never imported — a setup gap
            # the generate endpoint would 400 on.
            pools.append(PoolStatus(name=src.name, role=src.role, imported=False))
        else:
            sp, track_count = hit
            pools.append(PoolStatus(
                name=src.name,
                role=src.role,
                imported=True,
                track_count=track_count,
                imported_at=_iso(sp.imported_at),
            ))

    # ----- cursors: persistent named-cursor positions ---------------
    cursors: list[CursorPosition] = []
    if template.cursor.persist:
        rows = (
            db.query(CursorState)
            .filter(CursorState.station_id == s.id)
            .order_by(CursorState.name)
            .all()
        )
        cursors = [CursorPosition(name=c.name, position=c.position) for c in rows]

    # ----- generations rollup ---------------------------------------
    count = (
        db.query(func.count())
        .select_from(Playlist)
        .filter(Playlist.station_id == s.id)
        .scalar()
    ) or 0
    latest = (
        db.query(Playlist.week, Playlist.generated_at)
        .filter(Playlist.station_id == s.id, Playlist.generated_at.isnot(None))
        .order_by(Playlist.generated_at.desc(), Playlist.week.desc())
        .first()
    )
    # Stale = generated BEFORE the last template edit (§16.2.3 hint).
    # NULL generated_at rows aren't counted (NULL < x is unknown).
    stale_count = (
        db.query(func.count())
        .select_from(Playlist)
        .filter(
            Playlist.station_id == s.id,
            Playlist.generated_at.isnot(None),
            Playlist.generated_at < s.updated_at,
        )
        .scalar()
    ) or 0
    generations = GenerationsSummary(
        count=count,
        last_generated_week=latest[0] if latest else None,
        last_generated_at=_iso(latest[1]) if latest else None,
        stale_count=stale_count,
    )

    return StationOverview(
        slug=s.slug,
        name=s.name,
        cadence=template.cadence,
        rng=template.rng,
        updated_at=_iso(s.updated_at),
        template_summary=summary,
        pools=pools,
        cursors=cursors,
        generations=generations,
    )


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
