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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.deps import get_db
from api.job_runner import KIND_GENERATE, JobStore, get_job_store
from api.routers.jobs import _load_engine_inputs
from api.schemas import WeekChangeItem
from engine.generator import generate
from engine.models import (
    Playlist, PlaylistVersion, SourcePlaylist, SourcePlaylistEntry, Station, Track,
    WeekChange,
)
from engine.persistence import append_version, regeneration_seed
from engine.template import TemplateError, parse_template
from engine.trace import TraceSlot
from engine.week_change_store import item_of, prepare_changes
from engine.week_changes import MAX_RULE_CHANGES, changed_slot, find_slot, unchanged_slot

router = APIRouter()


class WeekChangeAdd(BaseModel):
    model_config = ConfigDict(strict=True)

    #: The version of the week the change is made to (the page's).
    expected_version: int = Field(ge=1)
    change: WeekChangeItem


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


def _track_out(t: Track | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {"id": t.id, "name": t.name, "artist": t.artist, "album": t.album}


def _change_out(row: WeekChange, slots: list[dict]) -> dict[str, Any]:
    out = item_of(row)
    i = find_slot(slots, row.row, row.block, row.k)
    out["position"] = i + 1 if i is not None else None
    out["track"] = _track_out(row.track) if row.kind == "swap" else None
    out["created_at"] = row.created_at.isoformat() if row.created_at else None
    return out


def _rule_at(template_raw: dict, row: int, block: int) -> dict | None:
    """The station's rule at (row, block) now, or None if it has none."""
    layout = template_raw.get("layout") or {}
    if layout.get("flat_slots"):
        rows, blocks = 1, layout["flat_slots"]
    else:
        rows, blocks = len(layout.get("row_start_offsets") or []), layout.get("row_template") or []
    if not (1 <= row <= rows and 1 <= block <= len(blocks)):
        return None
    return blocks[block - 1]


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
    unit = f"{slot['unit']} " if slot.get("unit") else ""
    return f"{unit}song {slot['slot']}"


# ---------- routes -----------------------------------------------------


@router.get("/api/playlists/{playlist_id}/changes")
def list_changes(playlist_id: int, db: Session = Depends(get_db)):
    """The week's just-this-week changes, oldest first. `position` is
    the song's place in the week's current version (from 1), or null
    when the week doesn't have that song now (the station's rules have
    changed since; regenerating makes the change again, or drops it)."""
    playlist, version = _playlist(db, playlist_id)
    slots = version.slots or []
    rows = (
        db.query(WeekChange).filter_by(playlist_id=playlist.id)
        .order_by(WeekChange.id).all()
    )
    return {
        "playlist_id": playlist.id,
        "version_no": version.version_no,
        "max_rule_changes": MAX_RULE_CHANGES,
        "changes": [_change_out(r, slots) for r in rows],
    }


@router.post("/api/playlists/{playlist_id}/changes", status_code=201)
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
      * 400 — the week has no such song, the song's rule isn't the one
        the change was made for (`rule_strategy` / `rule_source`) or the
        station's rules have changed since the week was made, the song
        chosen isn't in the station's pools, the changed rule isn't a
        valid rule or picks no song there, or the week already has
        `max_rule_changes` rule changes.
    """
    playlist, version = _playlist(db, playlist_id)
    station: Station = playlist.station
    _check_version(version, payload.expected_version)
    _check_not_generating(job_store, station.slug)
    item = payload.change
    slots: list[dict] = [dict(s) for s in version.slots or []]
    i = find_slot(slots, item.row, item.block, item.k)
    if i is None:
        raise HTTPException(400, "the week has no such song")
    base = unchanged_slot(slots[i])
    if (base["strategy"], base["source"]) != (item.rule_strategy, item.rule_source):
        raise HTTPException(400, "that song's rule isn't the one the change was made for")
    rule_now = _rule_at(station.template, item.row, item.block)
    if rule_now is None or (rule_now.get("strategy"), rule_now.get("source")) != (
        item.rule_strategy, item.rule_source,
    ):
        raise HTTPException(
            400,
            "the station's rules have changed since this week was made; "
            "regenerate it, then change the song",
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
        change, = prepare_changes(
            db, [item.model_dump()], template_raw=station.template, pools={},
        ).changes
        new_slot = changed_slot(_slot_obj(base), change, track).to_dict()
        note = f"Just this week: {_song_label(base)} swapped for {track.name}"
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
        inputs = _load_engine_inputs(db, station, template, playlist.week)
        seed = regeneration_seed(db, station_id=station.id, week=playlist.week)
        prepared = prepare_changes(
            db, [item.model_dump()], template_raw=station.template, pools=inputs.pools,
        )
        change = prepared.changes[0]
        if change.invalid is not None:
            raise HTTPException(400, change.invalid)
        if change.spec is None:
            raise HTTPException(400, "the changed rule isn't a valid rule")
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
        note = f"Just this week: {_song_label(base)} picked by a changed rule"

    slots[i] = new_slot
    if existing is not None:
        db.delete(existing)
        db.flush()
    row = WeekChange(playlist_id=playlist.id, **item.model_dump())
    db.add(row)
    new_version = append_version(db, playlist, slots=slots, like=version, note=note)
    db.commit()
    db.refresh(row)
    return {
        "playlist_id": playlist.id,
        "version_no": new_version.version_no,
        "change": _change_out(row, slots),
        "slot": new_slot,
    }


@router.delete("/api/playlists/{playlist_id}/changes/{change_id}")
def undo_change(
    playlist_id: int,
    change_id: int,
    expected_version: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    job_store: JobStore = Depends(get_job_store),
):
    """Undo a just-this-week change: the rule's own song plays again,
    in a new version of the week. When the week's current version
    doesn't have the change (the station's rules have changed since),
    the change is only forgotten and `version_no` stays as it was."""
    playlist, version = _playlist(db, playlist_id)
    _check_version(version, expected_version)
    _check_not_generating(job_store, playlist.station.slug)
    row = db.query(WeekChange).filter_by(id=change_id, playlist_id=playlist.id).first()
    if row is None:
        raise HTTPException(404, f"change {change_id} not found")
    slots: list[dict] = [dict(s) for s in version.slots or []]
    i = find_slot(slots, row.row, row.block, row.k)
    db.delete(row)
    if i is None or not slots[i].get("change"):
        db.commit()
        return {"playlist_id": playlist.id, "version_no": version.version_no}
    note = f"Just this week: {_song_label(slots[i])} back to its rule's song"
    slots[i] = unchanged_slot(slots[i])
    new_version = append_version(db, playlist, slots=slots, like=version, note=note)
    db.commit()
    return {"playlist_id": playlist.id, "version_no": new_version.version_no}
