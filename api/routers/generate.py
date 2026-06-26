"""Generate a playlist for a station + week."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.models import Station, Playlist, PlaylistVersion
from engine.persistence import (
    advance_cursors, load_alias_map, load_cursor_positions,
    load_excluded_mix_ids, load_excluded_track_pids, load_pools,
    load_previous_playlist, max_mix_exclusion_window, rollback_playlist,
    RollbackError, save_playlist,
)
from engine.rules import get_rule_value
from engine.template import parse_template, TemplateError
from engine.generator import generate
from engine.exporter import (
    export_playlist, export_playlist_to_disk, prune_export_directory,
)
from engine.trace_diff import compute_trace_diff
from api.deps import get_db
from api.schemas import (
    GenerateRequest, GenerateResponse, PlaylistOut, PlaylistEntryOut,
    TrackOut, VersionTagsRequest,
)


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

    # §16.14.t5: resolve cross_pool_dedup_sources to a set of track
    # persistent_ids the weighted strategy must exclude. Empty set
    # when the rule isn't configured. The helper fails loud on a
    # missing-or-misroled reference pool (translated to HTTP 400
    # below) so the operator sees the rule-vs-importer mismatch
    # immediately rather than getting a quietly thinned pick set.
    try:
        excluded_track_pids = load_excluded_track_pids(
            db, station_id=station.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except TemplateError as e:
        raise HTTPException(400, str(e))

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
            # §16.14.t5: cross-pool dedup exclusion set.
            excluded_track_pids=excluded_track_pids,
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
            slot_type=e.slot_type,
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
def export(
    playlist_id: int,
    v: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
):
    """Export a generated playlist as iTunes XML.

    §16.4.t2 — `?v=N` opt-in selects a HISTORICAL PlaylistVersion;
    omit `v` to export the current version (pre-§16.4.t2 behavior).
    The download filename gets a `-v{N}` suffix when set so the
    operator's browser saves it distinctly from the current export.
    """
    p = db.query(Playlist).filter_by(id=playlist_id).first()
    if not p:
        raise HTTPException(404, f"playlist {playlist_id} not found")
    try:
        xml_bytes = export_playlist(db, playlist_id, version_no=v)
    except ValueError as e:
        raise HTTPException(404, str(e))
    suffix = f"-v{v}" if v is not None else ""
    filename = f"{p.station.slug}-{p.week:03d}{suffix}.xml"
    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/playlists/{playlist_id}/export-to-disk")
def export_to_disk(
    playlist_id: int,
    v: int | None = Query(None, ge=1),
    versioned: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Export a generated playlist to the configured on-disk output directory.

    Filename matrix (combinations of `v` + `versioned`):

      * Default (no `v`, `versioned=false`): `{playlist_output_dir}/
        {station-slug}/{week:03d}.xml`. Overwrites silently —
        mirrors regen semantics.
      * `?versioned=true` (§16.4.t6 archival opt-in, current export):
        `{playlist_output_dir}/{station-slug}/{week:03d}-v{current}.xml`.
        Operator opts into accumulating one file per regen rather
        than overwriting.
      * `?v=N` (§16.4.t2 historical export — `versioned` ignored
        since the filename is already suffixed):
        `{playlist_output_dir}/{station-slug}/{week:03d}-v{N}.xml`.
        The version suffix is safety-critical: without it, exporting
        v2 would silently clobber the current's `{week:03d}.xml`,
        sending the wrong content to the operator's OneDrive /
        iTunes box.

    Directory comes from Config key 'playlist_output_dir' (absolute
    path recommended); falls back to 'out/' relative to the process
    CWD.
    """
    p = db.query(Playlist).filter_by(id=playlist_id).first()
    if not p:
        raise HTTPException(404, f"playlist {playlist_id} not found")
    try:
        path = export_playlist_to_disk(
            db, playlist_id, version_no=v, versioned_filename=versioned,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"path": str(path), "bytes": path.stat().st_size}


@router.post("/api/playlists/export/prune")
def prune_exports(
    confirm: bool = Query(False),
    station: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """§16.16 — find + (optionally) delete orphaned disk-export files.

    Scans the configured `playlist_output_dir` for `.xml` files and
    classifies each against the `Playlist` / `PlaylistVersion` tables.
    Orphan = no DB row matches the file's (slug, week[, version_no])
    key.

    Query params:
      * `confirm=true` — actually delete the orphans (otherwise
        dry-run; the default).
      * `station=<slug>` — restrict the scan to a single station's
        subdirectory.

    Response shape:

        {
          "scanned":  int,         # files inspected
          "orphans":  [{"path": str, "reason": str}, ...],
          "deleted":  int,         # 0 unless confirm=true
        }

    Idempotent: a second call with confirm=true after a successful
    prune returns `{scanned: ..., orphans: [], deleted: 0}`.

    Symlink-traversal escapes from the configured output dir are
    skipped even with confirm=true (canonicalize-then-relative_to
    check). This does NOT protect against an operator who
    *configured* `playlist_output_dir` to point at a sensitive
    location — see the helper docstring for the trust boundary.
    """
    try:
        return prune_export_directory(
            db, confirm=confirm, station_slug=station,
        )
    except ValueError as e:
        # Unknown station slug — surfaces operator typos that would
        # otherwise look like "directory is clean".
        raise HTTPException(404, str(e))


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
def get_trace(
    playlist_id: int,
    v: int | None = None,
    db: Session = Depends(get_db),
):
    """Return a specific version of the playlist's trace.

    §16.4 — multi-version semantics. `?v=N` selects version N
    explicitly; omitting `v` returns the CURRENT (max) version. The
    wire shape is unchanged from pre-§16.4 (`trace_id`, `station`,
    `week`, `rng`, `seed`, `generated_at`, `slots`) — adding a field
    would be a separate slice (the `/versions` endpoint below is the
    explicit history-listing surface).

    Errors:
      * 404 — playlist has no PlaylistVersion rows (fresh row pre-
        generate, or a partial-write recovery scenario).
      * 404 — `?v=N` references a version_no that doesn't exist for
        this playlist. Message names both the requested and the
        available range so the operator can correct the URL.
    """
    # Filter on (playlist_id, version_no) when v is set; else fall
    # back to the latest. Single ORM query in both branches.
    q = db.query(PlaylistVersion).filter_by(playlist_id=playlist_id)
    if v is not None:
        t = q.filter_by(version_no=v).first()
        if t is None:
            # Surface what IS available so the operator can fix the
            # URL without needing a separate /versions round-trip.
            available = sorted(
                row.version_no for row in q.all()
            )
            if not available:
                raise HTTPException(
                    404, f"trace for playlist {playlist_id} not found",
                )
            raise HTTPException(
                404,
                f"version {v} not found for playlist {playlist_id} "
                f"(available: {available[0]}..{available[-1]}).",
            )
    else:
        t = q.order_by(PlaylistVersion.version_no.desc()).first()
        if t is None:
            raise HTTPException(
                404, f"trace for playlist {playlist_id} not found",
            )
    return {
        "trace_id": t.trace_id,
        "station": t.station_slug,
        "week": t.week,
        "rng": t.rng,
        "seed": t.seed,
        "generated_at": t.generated_at,
        "slots": t.slots,
    }


@router.get("/api/playlists/{playlist_id}/versions")
def get_versions(playlist_id: int, db: Session = Depends(get_db)):
    """List every PlaylistVersion for the playlist (oldest first).

    §16.4 — feeds the trace viewer's version-picker dropdown. Returns
    enough per-row data for the picker labels + permalinks (`?v=N`)
    without forcing N round-trips through `/trace?v=N` just to render
    the dropdown.

    Slots JSON is INTENTIONALLY OMITTED here (~600 KB per version for
    Classics; multiplied by the version count, the listing payload
    would dominate over per-version trace fetches). Callers wanting
    the full trace go through `/trace?v=N`.

    Returns 404 only when the playlist itself doesn't exist; a
    playlist with zero versions returns `{playlist_id, current_v: 0,
    versions: []}` — defensive shape for a freshly-inserted row that
    hasn't reached its first generation yet.
    """
    if not db.query(Playlist.id).filter_by(id=playlist_id).first():
        raise HTTPException(404, f"playlist {playlist_id} not found")

    rows = (
        db.query(PlaylistVersion)
        .filter_by(playlist_id=playlist_id)
        .order_by(PlaylistVersion.version_no)
        .all()
    )
    return {
        "playlist_id": playlist_id,
        "current_v": rows[-1].version_no if rows else 0,
        "versions": [
            {
                "version_no": r.version_no,
                "trace_id": r.trace_id,
                "generated_at": r.generated_at,
                "seed": r.seed,
                # §16.4.t5 — operator-applied annotations. Always a
                # list (DB column is NOT NULL DEFAULT []).
                "tags": r.tags,
            }
            for r in rows
        ],
    }


# §16.4.t5 — tag validation constants. Shared between the PATCH
# handler + any future surface that constructs tag lists.
_MAX_TAGS_PER_VERSION = 10
_MAX_TAG_LENGTH = 40


def _normalize_tags(raw: list[str]) -> list[str]:
    """Trim, dedupe (case-sensitive — operator-typed strings),
    reject empties / commas / over-length. Returns the canonical
    list to persist."""
    seen: set[str] = set()
    out: list[str] = []
    for raw_tag in raw:
        if not isinstance(raw_tag, str):
            raise HTTPException(
                422, f"tag must be a string; got {type(raw_tag).__name__}",
            )
        tag = raw_tag.strip()
        if not tag:
            raise HTTPException(
                422, "empty tags aren't allowed (trim before send)",
            )
        if "," in tag:
            # Defense-in-depth: ChipMultiInput already rejects commas
            # at input time (`Names can't contain commas.` inline
            # error). The server check handles non-UI callers — CLI
            # scripts, future batch endpoints, direct API clients —
            # that could submit an array with embedded commas. Same
            # rationale as ListRuleRow's comma guard upstream.
            raise HTTPException(
                422,
                f"tag {raw_tag!r} contains a comma; commas are "
                f"reserved (UI rejects them too). Pick a different name.",
            )
        if len(tag) > _MAX_TAG_LENGTH:
            raise HTTPException(
                422,
                f"tag {tag!r} exceeds the {_MAX_TAG_LENGTH}-char cap "
                f"({len(tag)} chars).",
            )
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    if len(out) > _MAX_TAGS_PER_VERSION:
        raise HTTPException(
            422,
            f"too many tags ({len(out)}); cap is {_MAX_TAGS_PER_VERSION} "
            f"per version. Pick a more focused subset.",
        )
    return out


@router.patch("/api/playlists/{playlist_id}/versions/{version_no}/tags")
def patch_version_tags(
    playlist_id: int,
    version_no: int,
    payload: VersionTagsRequest,
    db: Session = Depends(get_db),
):
    """§16.4.t5 — replace a PlaylistVersion's tag list.

    Body: `{"tags": ["sent-to-iTunes", "jon-approved"]}`.

    Validation: max 10 tags per version, 1-40 chars each, no commas,
    duplicates dropped (case-sensitive), trim+empty-reject. Returns
    the normalized list back so the client can adopt the server's
    canonical form (operator typed `  foo  ` → stored as `foo`).

    Errors:
      * 404 — playlist or version not found.
      * 422 — malformed tags (see _normalize_tags).
    """
    if not db.query(Playlist.id).filter_by(id=playlist_id).first():
        raise HTTPException(404, f"playlist {playlist_id} not found")
    version = (
        db.query(PlaylistVersion)
        .filter_by(playlist_id=playlist_id, version_no=version_no)
        .first()
    )
    if version is None:
        raise HTTPException(
            404,
            f"version {version_no} not found for playlist {playlist_id}",
        )

    normalized = _normalize_tags(payload.tags)
    version.tags = normalized
    db.commit()
    db.refresh(version)
    return {
        "playlist_id": playlist_id,
        "version_no": version.version_no,
        "trace_id": version.trace_id,
        "generated_at": version.generated_at,
        "seed": version.seed,
        "tags": version.tags,
    }


# §16.23 c1: diff helpers extracted to `engine/trace_diff.py` so this
# router + the §16.23 station diff route (`api/routers/stations.py`)
# share one walker + one slot-projection definition.


@router.get("/api/playlists/{playlist_id}/diff")
def get_trace_diff(
    playlist_id: int,
    from_: int = Query(..., alias="from", ge=1),
    to: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    """Compare two PlaylistVersions of the same playlist (§16.4.t1).

    Returns every slot with both sides + a `changed` flag the frontend
    uses to render diff highlighting. The "changed" predicate fires on
    any difference in source / strategy / pool_index / track_id —
    positional row keys (slot/unit/row/block) are constant across
    versions by construction, so they're not part of the predicate.

    Wire shape:

        {
          "playlist_id": ...,
          "from": {"version_no": N, "trace_id": "...", "generated_at": "..."},
          "to":   {"version_no": M, "trace_id": "...", "generated_at": "..."},
          "summary": {"total_slots": N, "changed": X, "unchanged": Y},
          "slots": [
            {
              "slot": 1, "unit": "Mon", "row": 1, "block": 1,
              "changed": true,
              "from": {projection},
              "to":   {projection},
            },
            ...
          ],
        }

    The slot list is ALWAYS the full trace (not filtered to changed
    rows) so the frontend's "Show all" toggle is purely a render-time
    state flip — no second round-trip. Wire payload at Classics scale
    (3010 slots × ~200 bytes × 2 sides ≈ 1.2 MB raw, ~80 KB gzipped)
    is acceptable for a single-user tool; if growth becomes real, a
    `?changed_only=true` filter is a one-line addition.

    Errors:
      * 404 — playlist not found.
      * 404 — `from` or `to` references a version_no that doesn't exist
        for this playlist (message names the available range).
      * 400 — `from == to` (the diff would be all-unchanged; the
        operator probably meant /trace?v=N).
    """
    if not db.query(Playlist.id).filter_by(id=playlist_id).first():
        raise HTTPException(404, f"playlist {playlist_id} not found")
    if from_ == to:
        raise HTTPException(
            400,
            f"from ({from_}) and to ({to}) are the same version — "
            f"use /trace?v={from_} for a single-version view.",
        )

    versions = (
        db.query(PlaylistVersion)
        .filter_by(playlist_id=playlist_id)
        .order_by(PlaylistVersion.version_no)
        .all()
    )
    by_no = {v.version_no: v for v in versions}
    available = sorted(by_no.keys())

    def _resolve(label: str, n: int) -> PlaylistVersion:
        v = by_no.get(n)
        if v is None:
            if not available:
                raise HTTPException(
                    404, f"playlist {playlist_id} has no versions",
                )
            raise HTTPException(
                404,
                f"version {n} ({label}) not found for playlist "
                f"{playlist_id} (available: "
                f"{available[0]}..{available[-1]}).",
            )
        return v

    src = _resolve("from", from_)
    dst = _resolve("to", to)

    rows, summary = compute_trace_diff(src.slots or [], dst.slots or [])
    return {
        "playlist_id": playlist_id,
        "from": {
            "version_no": src.version_no,
            "trace_id": src.trace_id,
            "generated_at": src.generated_at,
        },
        "to": {
            "version_no": dst.version_no,
            "trace_id": dst.trace_id,
            "generated_at": dst.generated_at,
        },
        "summary": summary,
        "slots": rows,
    }


@router.post("/api/playlists/{playlist_id}/rollback")
def rollback(
    playlist_id: int,
    to: int = Query(..., ge=1),
    db: Session = Depends(get_db),
):
    """§16.4.t4 — restore a prior PlaylistVersion as the new current.

    APPENDS a new max-version row with the target's content (option
    'b' from the original §16.4 paper sketch — preserves audit trail
    without needing a new column on Playlist). The Playlist row +
    foreign-key pointers stay valid; PlaylistEntry rows are rewritten
    from the target's slots; `Playlist.trace_id` flips to the new
    version's trace_id.

    Errors:
      * 404 — playlist not found.
      * 404 — `to` references a version_no that doesn't exist
        (message names the available range).
      * 400 — `to` is already the current version (no-op).
      * 400 — a slot's track has since been deleted (the rolled-back
        content can't be physically replayed).

    Returns the new version's metadata (the new current).
    """
    try:
        new_version = rollback_playlist(
            db, playlist_id, to_version=to,
        )
    except RollbackError as e:
        # 404 for "not found" cases, 400 for "already current" /
        # "deleted track". Disambiguate by the message prefix —
        # rollback_playlist's error texts are stable.
        msg = str(e)
        if "not found" in msg or "no version_no" in msg or "no versions" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    db.commit()
    return {
        "playlist_id": playlist_id,
        "version_no": new_version.version_no,
        "trace_id": new_version.trace_id,
        "generated_at": new_version.generated_at,
        "seed": new_version.seed,
    }
