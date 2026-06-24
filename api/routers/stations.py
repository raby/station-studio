"""Stations CRUD."""
from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from engine.models import (
    Station, Playlist, PlaylistEntry, PlaylistVersion,
    SourcePlaylist, SourcePlaylistEntry, CursorState,
)
from engine.template import parse_template, TemplateError
from engine.trace_diff import compute_trace_diff
from api.deps import get_db
from api.schemas import (
    CursorPosition,
    GenerationsSummary,
    PlaylistSummary,
    PoolStatus,
    StationCreate,
    StationForkRequest,
    StationOut,
    StationOverview,
    StationSummary,
    TemplateSummary,
)
from sqlalchemy import func
from api.timefmt import iso as _iso


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
    #
    # Mid-slice review 🟡-1 — `updated_at` is a PROXY for "template
    # changed": the wizard-edit PUT reassigns s.template unconditionally
    # (onupdate fires even on a name-only edit with identical template
    # bytes), so a rename can falsely flag existing playlists as stale.
    # Impact is mild (the hint says "regenerate to apply edits"; an
    # unchanged template reproduces identical output). The precise fix —
    # a dedicated `template_updated_at` column bumped only on real
    # template change — is a migration + write-path touch, queued as
    # §16.19.t1. v1 accepts the over-approximation.
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


@router.post("/{slug}/fork", response_model=StationOut, status_code=201)
def fork_station(
    slug: str, payload: StationForkRequest, db: Session = Depends(get_db),
):
    """§16.2.8 — clone a station's template + sidecar under a new
    slug + name.

    Source 404 if `slug` doesn't exist; 409 if `new_slug` already
    exists. Slug-shape rejects are handled by Pydantic at the wire
    (422 — matches the create-station path).

    Explicitly NOT copied (per the design D1 punts):
      - Generated Playlists / PlaylistEntry rows (each belongs to
        the source station; forking is "clone structure", not
        "clone history").
      - per-station Rule rows (§14.29). Operator's typical "fork
        and tweak" pattern lets the new station accumulate its
        own rule set fresh; cloning rules can be a follow-up
        `§16.2.8.tX` if asked.
      - CursorState (regeneration-derived).
      - SourcePlaylist + entries (shared globally; nothing to
        copy at fork time).

    The new Station's `template` + `sidecar` are deep-copied so
    later mutations to either don't perturb the source. `created_at`
    stamps fresh.
    """
    src = db.query(Station).filter(Station.slug == slug).first()
    if src is None:
        raise HTTPException(404, f"station '{slug}' not found")

    if db.query(Station).filter(Station.slug == payload.new_slug).first():
        raise HTTPException(409, f"station '{payload.new_slug}' already exists")

    import copy
    new_station = Station(
        slug=payload.new_slug,
        name=payload.new_name,
        # Deep-copy so the new station's mutations don't reach back
        # into the source's JSON blobs (SQLAlchemy ORM-attribute
        # sharing would otherwise alias the dicts).
        template=copy.deepcopy(src.template),
        sidecar=copy.deepcopy(src.sidecar) if src.sidecar is not None else None,
    )
    db.add(new_station)
    db.commit()
    db.refresh(new_station)
    return new_station


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


@router.get("/{slug}/diff")
def get_station_week_diff(
    slug: str,
    from_: int = Query(..., alias="from", ge=1),
    to: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    """§16.23 — compare two weeks' CURRENT versions side-by-side.

    Completes the diff-surfaces trio:
      * version-vs-version (§16.4.t1, `/api/playlists/{id}/diff`)
      * staged-vs-saved (§16.2.7, in the visual editor)
      * week-vs-week (THIS) — same station, two different weeks

    Always reads the CURRENT PlaylistVersion (max version_no) for
    each week (the design-pass decision). Future surfaces can extend
    via opt-in `?from_v=N&to_v=M` if a real ask appears.

    Wire shape mirrors §16.4.t1 — same per-slot from/to projection,
    same `changed` flag, same summary — with `from`/`to` carrying
    `{week, playlist_id, trace_id, generated_at, version_no}` rather
    than `{version_no, trace_id, generated_at}`. Frontend reuses the
    same diff-table component.

    Errors:
      * 404 — station not found.
      * 404 — either week has no playlist for this station (message
        names which side + the available range when there are any).
      * 400 — `from == to` (degenerate; redirect operator to /trace).
    """
    station = db.query(Station).filter(Station.slug == slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")
    if from_ == to:
        raise HTTPException(
            400,
            f"from ({from_}) and to ({to}) are the same week — "
            f"use the trace viewer for a single-week view.",
        )

    # Pull every playlist for this station (cheap — typically <100 rows
    # per station) so the not-found path can name the available range.
    playlists = (
        db.query(Playlist)
        .filter_by(station_id=station.id)
        .order_by(Playlist.week)
        .all()
    )
    by_week = {p.week: p for p in playlists}
    available = sorted(by_week.keys())

    def _resolve_week(label: str, w: int) -> Playlist:
        pl = by_week.get(w)
        if pl is None:
            if not available:
                raise HTTPException(
                    404,
                    f"station '{slug}' has no playlists; cannot diff weeks",
                )
            raise HTTPException(
                404,
                f"no playlist for week {w} ({label}) in station '{slug}' "
                f"(available weeks: {available[0]}..{available[-1]}).",
            )
        return pl

    src_pl = _resolve_week("from", from_)
    dst_pl = _resolve_week("to", to)

    # Read the CURRENT PlaylistVersion for each side (max version_no).
    # Fail loud if a playlist row exists but has zero versions — that's
    # a "partial-write recovery" / freshly-inserted-row state, not the
    # operator's intent.
    def _current_version(pl: Playlist, label: str) -> PlaylistVersion:
        v = (
            db.query(PlaylistVersion)
            .filter_by(playlist_id=pl.id)
            .order_by(PlaylistVersion.version_no.desc())
            .first()
        )
        if v is None:
            raise HTTPException(
                404,
                f"week {pl.week} ({label}) playlist has no versions yet",
            )
        return v

    src_v = _current_version(src_pl, "from")
    dst_v = _current_version(dst_pl, "to")

    rows, summary = compute_trace_diff(src_v.slots or [], dst_v.slots or [])
    return {
        "station": slug,
        "from": {
            "week": src_pl.week,
            "playlist_id": src_pl.id,
            "version_no": src_v.version_no,
            "trace_id": src_v.trace_id,
            "generated_at": src_v.generated_at,
        },
        "to": {
            "week": dst_pl.week,
            "playlist_id": dst_pl.id,
            "version_no": dst_v.version_no,
            "trace_id": dst_v.trace_id,
            "generated_at": dst_v.generated_at,
        },
        "summary": summary,
        "slots": rows,
    }
