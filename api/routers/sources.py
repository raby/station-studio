"""Source pool imports."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from engine.models import SourcePlaylist, SourcePlaylistEntry
from engine.importer import import_source
from api.deps import get_db
from api.schemas import ImportRequest, SourceOut


router = APIRouter(prefix="/api/sources", tags=["sources"])


def _validate_import_path(raw: str) -> Path:
    """Resolve the requested import path and enforce any configured restriction.

    If STATION_STUDIO_IMPORT_BASE_DIR is set, the resolved path must sit within
    that directory. This prevents path-traversal when the API is network-exposed.
    When the env var is unset (the default for local dev), any readable file is
    accepted — but the path is always resolved so that ../.. tricks are neutralised.
    """
    path = Path(raw).resolve()
    base_dir = os.environ.get("STATION_STUDIO_IMPORT_BASE_DIR")
    if base_dir:
        base = Path(base_dir).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            raise HTTPException(
                400,
                f"import path must be within {base}. "
                "Set STATION_STUDIO_IMPORT_BASE_DIR to configure the allowed directory.",
            )
    return path


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    rows = (
        db.query(SourcePlaylist, func.count(SourcePlaylistEntry.id).label("track_count"))
        .outerjoin(SourcePlaylistEntry, SourcePlaylistEntry.source_playlist_id == SourcePlaylist.id)
        .group_by(SourcePlaylist.id)
        .order_by(SourcePlaylist.name)
        .all()
    )
    return [
        SourceOut(id=sp.id, name=sp.name, track_count=count, role=sp.role)
        for sp, count in rows
    ]


@router.post("/import")
def post_import(payload: ImportRequest, db: Session = Depends(get_db)):
    path = _validate_import_path(payload.path)
    if not path.exists():
        raise HTTPException(400, f"path not found: {path}")
    try:
        return import_source(db, path, payload.name, role=payload.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"import failed: {e}")


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: int, db: Session = Depends(get_db)):
    """Delete a source pool and all its entries.

    Generated playlists are unaffected — PlaylistEntry rows reference tracks
    directly, not source pools.
    """
    sp = db.query(SourcePlaylist).filter_by(id=source_id).first()
    if not sp:
        raise HTTPException(404, f"source '{source_id}' not found")
    db.delete(sp)
    db.commit()
