"""Generate a playlist for a station + week."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import Station, Playlist, PlaylistTrace
from engine.persistence import (
    advance_cursors, load_alias_map, load_cursor_positions,
    load_excluded_mix_ids, load_pools, load_previous_playlist,
    max_mix_exclusion_window, save_playlist,
)
from engine.rules import get_rule_value
from engine.template import parse_template, TemplateError
from engine.generator import generate
from engine.exporter import export_playlist, export_playlist_to_disk
from api.deps import get_db
from api.schemas import GenerateRequest, GenerateResponse, PlaylistOut, PlaylistEntryOut, TrackOut


logger = logging.getLogger(__name__)

router = APIRouter(tags=["generate"])


@router.post("/api/stations/{slug}/generate", response_model=GenerateResponse, status_code=201)
def generate_playlist(slug: str, payload: GenerateRequest, db: Session = Depends(get_db)):
    station = db.query(Station).filter(Station.slug == slug).first()
    if not station:
        raise HTTPException(404, f"station '{slug}' not found")

    try:
        template = parse_template(station.template)
    except TemplateError as e:
        raise HTTPException(500, f"stored template is invalid: {e}")

    try:
        pools = load_pools(db, template)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # §14.29: per-station rule defaults. Resolved BEFORE the mix
    # exclusion set so a station that omits BlockSpec.exclusion_window
    # on some mix slots still feeds the right lookback into the query.
    artist_dedup_window = get_rule_value(
        db, station_id=station.id, key="artist_dedup_window",
    )
    mix_exclusion_weeks = get_rule_value(
        db, station_id=station.id, key="mix_exclusion_weeks",
    )
    # §14.30: cursor rules are read regardless of template.cursor.persist
    # so the values are available for the post-save advance call. For
    # stateless templates the values are stored-but-not-applied (the
    # generator's stateless cursor math isn't influenced by these);
    # for persistent templates they drive advance + reset semantics.
    cursor_advance_step = get_rule_value(
        db, station_id=station.id, key="cursor_advance_step",
    )
    cursor_reset_cycle = get_rule_value(
        db, station_id=station.id, key="cursor_reset_cycle",
    )

    # §14.25: build mix-exclusion set from the station's recent playlists.
    # See engine.persistence.max_mix_exclusion_window for the multi-mix-block
    # semantics. Window=0 short-circuits to an empty set in load_*.
    # §14.29: mix blocks whose BlockSpec.exclusion_window is None
    # inherit the station's `mix_exclusion_weeks` rule via the
    # `default=` argument.
    excluded_mix_ids = load_excluded_mix_ids(
        db,
        station_id=station.id,
        window=max_mix_exclusion_window(template, default=mix_exclusion_weeks),
    )

    # §14.26 + §14.27: alias map for the `weighted` strategy's artist-key
    # resolution. Cheap to load even when no weighted blocks exist (just
    # an empty ArtistAlias table read); the strategy ignores it if there
    # are no weighted blocks in the template.
    alias_map = load_alias_map(db)

    # §14.28: previous playlist for prev_ref slots. Always loaded so the
    # generator can pass it through; templates without prev_ref blocks
    # silently ignore it. None when no prior playlist exists for this
    # station (first generation, or regenerating before any prior week).
    previous_playlist = load_previous_playlist(
        db, station_id=station.id, current_week=payload.week,
    )

    # §14.30: load persistent named-cursor positions when the template
    # opts in. Stateless templates ignore this — the generator falls
    # back to the §14.22 stateless computation.
    named_cursor_positions = None
    if template.cursor.persist:
        named_cursor_positions = load_cursor_positions(
            db, station_id=station.id, template=template,
        )

    try:
        result = generate(
            template, pools, week=payload.week, seed=payload.seed,
            excluded_mix_ids=excluded_mix_ids,
            alias_map=alias_map,
            previous_playlist=previous_playlist,
            # §14.29: weighted blocks without BlockSpec.window inherit
            # the station rule. The generator's _resolve_weighted_window
            # helper makes this precedence explicit.
            weighted_window_default=artist_dedup_window,
            # §14.30: stateful cursor positions when persist=True.
            named_cursor_positions=named_cursor_positions,
        )
    except Exception as e:
        raise HTTPException(400, f"generation failed: {e}")

    try:
        playlist = save_playlist(db, station, result, week=payload.week, template=template)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409,
            "a playlist for this station and week was concurrently generated; retry",
        )

    # §14.30: advance persistent named cursors AFTER the playlist
    # commits. `generation_count = week - 1` matches weekly's
    # `next_number - 1` convention: WEEKLY_001 = generation 0,
    # WEEKLY_002 = generation 1, etc. Regenerating week W keeps the
    # same generation count → deterministic byte-equality with weekly's
    # stateful cursor model.
    if template.cursor.persist:
        advance_cursors(
            db,
            station_id=station.id,
            template=template,
            pools=dict(pools),
            advance_step=cursor_advance_step,
            reset_cycle=cursor_reset_cycle,
            generation_count=payload.week - 1,
        )
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
        raise HTTPException(404, f"playlist {playlist_id} not found")
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
        raise HTTPException(404, f"playlist {playlist_id} not found")
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


@router.post("/api/playlists/{playlist_id}/export-to-disk")
def export_to_disk(playlist_id: int, db: Session = Depends(get_db)):
    """Export a generated playlist to the configured on-disk output directory.

    Destination: {playlist_output_dir}/{station-slug}/{week:03d}.xml.
    Directory comes from Config key 'playlist_output_dir' (absolute path
    recommended); falls back to 'out/' relative to the process CWD.
    Overwrites silently if the file already exists.
    """
    p = db.query(Playlist).filter_by(id=playlist_id).first()
    if not p:
        raise HTTPException(404, f"playlist {playlist_id} not found")
    try:
        path = export_playlist_to_disk(db, playlist_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"path": str(path), "bytes": path.stat().st_size}


@router.delete("/api/playlists/{playlist_id}", status_code=204)
def delete_playlist(playlist_id: int, db: Session = Depends(get_db)):
    """Delete a playlist and its entries + trace."""
    p = db.query(Playlist).filter_by(id=playlist_id).first()
    if not p:
        raise HTTPException(404, f"playlist {playlist_id} not found")
    # Capture identifying fields before delete — `p.station.slug` requires
    # a query through the relationship, which becomes invalid post-delete.
    week, station_slug = p.week, p.station.slug
    db.delete(p)
    db.commit()
    logger.info(
        "Deleted playlist %d (week %d) of station '%s'",
        playlist_id, week, station_slug,
    )


@router.get("/api/playlists/{playlist_id}/trace")
def get_trace(playlist_id: int, db: Session = Depends(get_db)):
    t = db.query(PlaylistTrace).filter_by(playlist_id=playlist_id).first()
    if not t:
        raise HTTPException(404, f"trace for playlist {playlist_id} not found")
    return {
        "trace_id": t.trace_id,
        "station": t.station_slug,
        "week": t.week,
        "rng": t.rng,
        "seed": t.seed,
        "generated_at": t.generated_at,
        "slots": t.slots,
    }
