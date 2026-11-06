"""Just-this-week changes to a generated week (engine.week_changes).

  * GET    /api/playlists/{id}/changes             — the week's changes
  * POST   /api/playlists/{id}/changes             — change one song
  * DELETE /api/playlists/{id}/changes/{change_id} — undo a change

Changing or undoing makes a new version of the week at once, with only
that song different (and a note saying so), so it's in the version
history and Rollback can undo it. Each is made against the version the
page shows (`expected_version`): if the week has changed since, it's
refused (409) and the page reloads. They're refused while the station's
week is being generated, too (409), since the generation would save
over them.
"""
from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api.deps import get_db
from api.job_runner import KIND_GENERATE, JobStore, get_job_store
from api.routers.jobs import _load_engine_inputs
from api.schemas import (
    StationSongsResponse,
    WeekChangeAdd, WeekChangeAddResponse, WeekChangesResponse,
    WeekChangeUndoResponse,
)
from engine.generator import generate
from engine.models import (
    Playlist, PlaylistVersion, SourcePlaylist, SourcePlaylistEntry, Station, Track,
    WeekChange,
)
from engine.persistence import append_version, regeneration_seed
from engine.template import TemplateError, parse_template
from engine.trace import TraceSlot
from engine.week_change_store import changed_fields, item_of, prepare_changes
from engine.week_changes import (
    MAX_RULE_CHANGES, change_matches_slot, changed_rule, changed_slot, find_slot,
    unchanged_slot,
    week_matches_rules,
)

router = APIRouter()


# ---------- helpers ----------------------------------------------------


def _playlist(db: Session, playlist_id: int) -> tuple[Playlist, PlaylistVersion]:
    playlist = db.query(Playlist).filter_by(id=playlist_id).first()
    if playlist is None:
        raise HTTPException(404, f"playlist {playlist_id} not found")
    version = playlist.current_version()
    if version is None:
        raise HTTPException(404, f"playlist {playlist_id} has no versions")
    return playlist, version


def _check_version(version: PlaylistVersion, expected: int) -> None:
    if version.version_no != expected:
        raise HTTPException(409, detail={
            "message": (
                f"the week has changed since (it's now version {version.version_no}); "
                "reload it and try again"
            ),
            "current_version": version.version_no,
        })


def _check_not_generating(store: JobStore, slug: str) -> None:
    job_id = store.running_job_id(slug, KIND_GENERATE)
    if job_id is not None:
        raise HTTPException(409, detail={
            "message": "the station's week is being generated; try again when it's done",
            "existing_job_id": job_id,
        })


@contextmanager
def _writing(db: Session, store: JobStore, slug: str):
    """Write and commit a new version; a version made at the same moment
    by another request (the same version number) is a 409 like a stale
    one, with nothing written. So is a generation of the station started
    meanwhile (a rule change can take seconds to work out): it would
    save the week without this change."""
    try:
        yield
        try:
            _check_not_generating(store, slug)
        except HTTPException:
            db.rollback()
            raise
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail={
            "message": "the week changed at the same moment; reload it and try again",
            "current_version": None,
        })


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # SQLite hands back naive datetimes; they're stored in UTC.
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _slot_of(row: WeekChange, slots: list[dict]) -> int | None:
    """Where the week's current version plays `row`'s change: the song at
    its place, if that song carries it. None once the station's rules
    have moved and the week hasn't been generated since (the change
    follows its rule; the songs don't yet)."""
    i = find_slot(slots, row.row, row.block, row.k)
    if i is None or not change_matches_slot(
        slots[i], kind=row.kind, track_id=row.track_id, rule=row.rule,
        rule_strategy=row.rule_strategy, rule_source=row.rule_source,
    ):
        return None
    return i


def _positions_by_track(slots: list[dict]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for n, s in enumerate(slots, start=1):
        if s.get("track_id") is not None:
            out.setdefault(s["track_id"], []).append(n)
    return out


def _change_out(row: WeekChange, slots: list[dict],
                by_track: dict[int, list[int]] | None = None) -> dict[str, Any]:
    out = item_of(row)
    i = _slot_of(row, slots)
    out["position"] = i + 1 if i is not None else None
    t = row.track if row.kind == "swap" else None
    out["track"] = (
        {"id": t.id, "name": t.name, "artist": t.artist, "album": t.album}
        if t is not None else None
    )
    # The song also plays elsewhere in the week (a repeat the page can
    # point out). Of the song at its place, when it plays there.
    if by_track is None:
        by_track = _positions_by_track(slots)
    tid = slots[i].get("track_id") if i is not None else None
    out["also_plays_at"] = [p for p in by_track.get(tid, []) if p != i + 1] if tid else []
    out["created_at"] = _iso_utc(row.created_at)
    return out


def _station_pools(template_raw: dict) -> list[str]:
    return [s["name"] for s in template_raw.get("sources") or [] if "name" in s]


def _in_station_pools(db: Session, track_id: int, template_raw: dict) -> bool:
    names = _station_pools(template_raw)
    if not names:
        return False
    return db.query(SourcePlaylistEntry.id).join(
        SourcePlaylist, SourcePlaylist.id == SourcePlaylistEntry.source_playlist_id,
    ).filter(
        SourcePlaylistEntry.track_id == track_id,
        SourcePlaylist.name.in_(names),
    ).first() is not None


def _slot_obj(d: dict) -> TraceSlot:
    names = {f.name for f in dataclasses.fields(TraceSlot)}
    return TraceSlot(**{k: v for k, v in d.items() if k in names})


def _song_label(slot: dict) -> str:
    """"song 12", or "song 12 (Tue)" on a row station: the week's song
    number, and its day."""
    unit = f" ({slot['unit']})" if slot.get("unit") else ""
    return f"song {slot['slot']}{unit}"


def _track_label(t: Track) -> str:
    return f"{t.name} by {t.artist}" if t.artist else t.name


def _fields_label(fields: dict) -> str:
    """What a rule change changed, in words: "source B, window 20"
    (its source and strategy first)."""
    order = sorted(fields, key=lambda k: ({"source": 0, "strategy": 1}.get(k, 2), k))
    return ", ".join(
        f"{k} {fields[k]}" if fields[k] is not None else f"no {k}" for k in order
    )


# ---------- routes -----------------------------------------------------


#: At most this many songs a search lists, and rows it reads.
SONG_SEARCH_LIMIT = 50
SONG_SEARCH_ROW_CAP = 5000


def _like(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/api/stations/{slug}/songs", response_model=StationSongsResponse)
def search_songs(
    slug: str,
    q: str = Query("", max_length=200),
    prefer: str | None = Query(None, max_length=256),
    limit: int = Query(SONG_SEARCH_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Songs in the station's pools (any of them: content and
    reference) whose name, artist or album has every word of `q`, for
    swapping a song just this week. Songs in `prefer` (the rule's own
    pool) come first, then by name. With no `q`, `prefer`'s songs in
    their pool's order (or, without it, none)."""
    station = db.query(Station).filter_by(slug=slug).first()
    if station is None:
        raise HTTPException(404, f"station '{slug}' not found")
    pools = _station_pools(station.template)
    if prefer is not None and prefer not in pools:
        raise HTTPException(400, f"'{prefer}' isn't one of the station's pools")
    words = [w for w in q.lower().split() if w]
    query = (
        db.query(Track, SourcePlaylist.name, SourcePlaylistEntry.position)
        .join(SourcePlaylistEntry, SourcePlaylistEntry.track_id == Track.id)
        .join(SourcePlaylist, SourcePlaylist.id == SourcePlaylistEntry.source_playlist_id)
    )
    if not words:
        if prefer is None:
            return {"songs": [], "more": False}
        query = query.filter(SourcePlaylist.name == prefer).order_by(SourcePlaylistEntry.position)
    else:
        query = query.filter(SourcePlaylist.name.in_(pools))
        text = (
            func.lower(func.coalesce(Track.name, "")) + " "
            + func.lower(func.coalesce(Track.artist, "")) + " "
            + func.lower(func.coalesce(Track.album, ""))
        )
        for w in words:
            query = query.filter(text.like(_like(w), escape="\\"))
    # Every match is sorted (the preferred pool's first), so rows aren't
    # limited before that, only capped: a search this broad says there's
    # more, and narrowing it finds the rest.
    rows = query.limit(SONG_SEARCH_ROW_CAP + 1).all()
    capped = len(rows) > SONG_SEARCH_ROW_CAP
    rows = rows[:SONG_SEARCH_ROW_CAP]
    found: dict[int, dict[str, Any]] = {}
    for track, pool, position in rows:
        song = found.setdefault(track.id, {
            "id": track.id, "name": track.name, "artist": track.artist,
            "album": track.album, "pools": [], "_pos": position,
        })
        if pool not in song["pools"]:
            song["pools"].append(pool)
    for song in found.values():
        song["pools"].sort(key=lambda p: (p != prefer, pools.index(p)))
    order = sorted(
        found.values(),
        key=(lambda x: x["_pos"]) if not words else
        (lambda x: (prefer not in x["pools"], (x["name"] or "").lower(), x["id"])),
    )
    for song in order:
        del song["_pos"]
    return {"songs": order[:limit], "more": capped or len(order) > limit}


@router.get("/api/playlists/{playlist_id}/changes", response_model=WeekChangesResponse)
def list_changes(playlist_id: int, db: Session = Depends(get_db)):
    """The week's just-this-week changes, oldest first. `position` is
    the song's place in the week's current version (from 1), or null
    when the week doesn't play the change now: the station's rules have
    moved or changed since it was generated, and regenerating makes the
    change again on its rule, or drops it."""
    playlist, version = _playlist(db, playlist_id)
    slots = version.slots or []
    rows = (
        db.query(WeekChange).filter_by(playlist_id=playlist.id)
        .order_by(WeekChange.id).all()
    )
    by_track = _positions_by_track(slots)
    return {
        "playlist_id": playlist.id,
        "version_no": version.version_no,
        "max_rule_changes": MAX_RULE_CHANGES,
        "changes": [_change_out(r, slots, by_track) for r in rows],
    }


@router.post(
    "/api/playlists/{playlist_id}/changes",
    status_code=201, response_model=WeekChangeAddResponse,
)
def add_change(
    playlist_id: int,
    payload: WeekChangeAdd,
    db: Session = Depends(get_db),
    job_store: JobStore = Depends(get_job_store),
):
    """Change one song of the week, for this week only, and make the
    week's new version with it.

    A song already changed has its change replaced. Errors:
      * 404 — no such playlist;
      * 409 — the week has changed since `expected_version`, or is
        being generated;
      * 400 — the week has no such song; the song's rule isn't the one
        the change was made for (`rule_strategy` / `rule_source`); the
        week isn't the station's rules as they are now (regenerate it
        first); the song chosen isn't in the station's pools, or is the
        one its rule picks; the changed rule isn't a valid rule, is the
        rule as it is, or can't be played; or the week already has
        `max_rule_changes` rule changes.
    """
    playlist, version = _playlist(db, playlist_id)
    station: Station = playlist.station
    _check_version(version, payload.expected_version)
    slug = station.slug
    _check_not_generating(job_store, slug)
    item = payload.change
    slots: list[dict] = [dict(s) for s in version.slots or []]
    i = find_slot(slots, item.row, item.block, item.k)
    if i is None:
        raise HTTPException(400, "the week has no such song")
    base = unchanged_slot(slots[i])
    if (base["strategy"], base["source"]) != (item.rule_strategy, item.rule_source):
        raise HTTPException(400, "that song's rule isn't the one the change was made for")
    if not week_matches_rules(slots, station.template):
        raise HTTPException(
            400,
            "the station's rules have changed since this week was made; "
            "regenerate it, then change the song",
        )
    # A rule can play fewer songs than the week shows once its size is
    # lowered (counts aren't part of the check above): a change past its
    # size now would only be dropped when the week is generated.
    layout = station.template.get("layout") or {}
    rule_now = (layout.get("flat_slots") or layout.get("row_template") or [])[item.block - 1]
    if item.k >= int(rule_now.get("size") or 0):
        raise HTTPException(
            400,
            f"that rule plays {rule_now.get('size')} songs now; "
            "regenerate the week, then change the song",
        )
    existing = (
        db.query(WeekChange)
        .filter_by(playlist_id=playlist.id, row=item.row, block=item.block, k=item.k)
        .first()
    )

    if item.kind == "swap":
        track = db.query(Track).filter_by(id=item.track_id).first()
        if track is None:
            raise HTTPException(400, f"track {item.track_id} not found")
        if not _in_station_pools(db, track.id, station.template):
            raise HTTPException(400, "that song isn't in any of the station's pools")
        if track.id == base.get("track_id"):
            raise HTTPException(
                400,
                "that's the song its rule picks"
                + ("; undo the change instead" if existing is not None else ""),
            )
        change, = prepare_changes(
            db, [item.model_dump()], template_raw=station.template, pools={},
        ).changes
        new_slot = changed_slot(_slot_obj(base), change, track).to_dict()
        note = f"Just this week: {_song_label(base)} swapped for {_track_label(track)}"
    else:
        rule_changes = (
            db.query(WeekChange)
            .filter(WeekChange.playlist_id == playlist.id, WeekChange.kind == "rule")
            .count()
        ) - (1 if existing is not None and existing.kind == "rule" else 0)
        if rule_changes >= MAX_RULE_CHANGES:
            raise HTTPException(
                400, f"a week can have at most {MAX_RULE_CHANGES} rule changes",
            )
        try:
            template = parse_template(station.template)
        except TemplateError as e:  # pragma: no cover — stored templates are valid
            raise HTTPException(500, f"stored template is invalid: {e}")
        if changed_rule(station.template, item.block, item.rule) is None:
            raise HTTPException(400, "the changed rule isn't a valid rule")
        if not changed_fields(station.template, item.block, item.rule):
            raise HTTPException(400, "that's the rule as it is; change something about it")
        # The week's own inputs, as regenerating it would use them, so
        # the song shown now is the one regenerating keeps.
        inputs = _load_engine_inputs(db, station, template, playlist.week)
        seed = regeneration_seed(db, station_id=station.id, week=playlist.week)
        prepared = prepare_changes(
            db, [item.model_dump()], template_raw=station.template, pools=inputs.pools,
        )
        change = prepared.changes[0]
        if change.invalid is not None:
            raise HTTPException(400, change.invalid)
        at = (item.row, item.block)
        try:
            shadow = generate(
                template, prepared.pools,
                **inputs.engine(week=playlist.week, seed=seed).kwargs(),
                stop_after=at, override_block=(item.row, item.block, change.spec),
            )
        except Exception as e:  # noqa: BLE001 — as /generate
            raise HTTPException(400, f"the changed rule can't be played: {e}")
        j = find_slot(shadow.trace.slots, item.row, item.block, item.k)
        if j is None:  # pragma: no cover — the size is the rule's own
            raise HTTPException(400, "the changed rule picks no song there")
        new_slot = changed_slot(
            _slot_obj(base), change, shadow.picks[j], picked=shadow.trace.slots[j],
        ).to_dict()
        note = (
            f"Just this week: {_song_label(base)} picked by its rule changed: "
            f"{_fields_label(change.fields)}"
        )

    slots[i] = new_slot
    with _writing(db, job_store, slug):
        if existing is not None:
            db.delete(existing)
            db.flush()
        row = WeekChange(playlist_id=playlist.id, **item.model_dump())
        db.add(row)
        new_version = append_version(db, playlist, slots=slots, like=version, note=note)
    db.refresh(row)
    return {
        "playlist_id": playlist.id,
        "version_no": new_version.version_no,
        "note": note,
        "change": _change_out(row, slots),
        "slot": new_slot,
    }


@router.delete(
    "/api/playlists/{playlist_id}/changes/{change_id}",
    response_model=WeekChangeUndoResponse,
)
def undo_change(
    playlist_id: int,
    change_id: int,
    expected_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    job_store: JobStore = Depends(get_job_store),
):
    """Undo a just-this-week change: the rule's own song plays again,
    in a new version of the week. When the week's current version
    doesn't play the change (the station's rules have moved or changed
    since it was generated), the change is only forgotten and
    `version_no` stays as it was."""
    playlist, version = _playlist(db, playlist_id)
    _check_version(version, expected_version)
    slug = playlist.station.slug
    _check_not_generating(job_store, slug)
    row = db.query(WeekChange).filter_by(id=change_id, playlist_id=playlist.id).first()
    if row is None:
        raise HTTPException(404, f"change {change_id} not found")
    slots: list[dict] = [dict(s) for s in version.slots or []]
    i = _slot_of(row, slots)
    if i is None:
        with _writing(db, job_store, slug):
            db.delete(row)
        return {"playlist_id": playlist.id, "version_no": version.version_no, "note": None}
    note = f"Just this week: {_song_label(slots[i])} back to its rule's song"
    slots[i] = unchanged_slot(slots[i])
    with _writing(db, job_store, slug):
        db.delete(row)
        new_version = append_version(db, playlist, slots=slots, like=version, note=note)
    return {"playlist_id": playlist.id, "version_no": new_version.version_no, "note": note}
