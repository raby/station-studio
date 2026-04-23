"""Source pool imports."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from engine.models import SourcePlaylist, SourcePlaylistEntry
from engine.importer import import_source
from api.deps import get_db
from api.schemas import ImportRequest, SourceOut


router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    out = []
    for sp in db.query(SourcePlaylist).order_by(SourcePlaylist.name).all():
        count = db.query(SourcePlaylistEntry).filter_by(source_playlist_id=sp.id).count()
        out.append(SourceOut(id=sp.id, name=sp.name, track_count=count))
    return out


@router.post("/import")
def post_import(payload: ImportRequest, db: Session = Depends(get_db)):
    path = Path(payload.path)
    if not path.exists():
        raise HTTPException(400, f"path not found: {path}")
    try:
        return import_source(db, path, payload.name)
    except Exception as e:
        raise HTTPException(500, f"import failed: {e}")
