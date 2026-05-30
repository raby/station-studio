"""Source pool imports."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from engine.models import SourcePlaylist, SourcePlaylistEntry
from engine.importer import import_source
from api.deps import get_db
from api.schemas import ImportRequest, SourceOut


router = APIRouter(prefix="/api/sources", tags=["sources"])


def _validate_name(raw: str) -> str:
    """Strip and reject empty / whitespace-only pool names.

    Pre-c1 both `/import` (JSON `ImportRequest.name: str`) and the new
    `/upload` (`Form(name)`) accepted `""`, `"   "`, and `"  daytime  "`
    verbatim. `import_source` would then persist a `SourcePlaylist`
    with a blank or odd-whitespace name, surfacing on `GET /api/sources`
    as a confusing row. Closed in the c1 mid-slice review (🟡-1) —
    one helper, both endpoints, one error contract.
    """
    stripped = raw.strip()
    if not stripped:
        raise HTTPException(
            400, "name must not be empty or whitespace-only",
        )
    return stripped


def _import_to_pool(db: Session, *, path: Path, name: str, role: str) -> dict:
    """Run `import_source` and map its exception contract to HTTPExceptions.

    Shared by `/import` (server-path) and `/upload` (multipart) so the two
    endpoints agree on the error surface byte-for-byte:

    * ValueError → 400 — `import_source`'s contract for "invalid role" or
      "not an iTunes library XML." User-correctable input error.
    * Anything else → 500 — plist parse failures (ExpatError, etc),
      unexpected I/O. Operator-visible bug or system fault.
    """
    try:
        return import_source(db, path, name, role=role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"import failed: {e}")


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
    """Import an iTunes XML by server-side filesystem path.

    Works when the API runs on the same host as the XML (the `pipx`-local
    case). For the docker case — or any browser-first flow — see /upload.
    """
    path = _validate_import_path(payload.path)
    if not path.exists():
        raise HTTPException(400, f"path not found: {path}")
    name = _validate_name(payload.name)
    return _import_to_pool(db, path=path, name=name, role=payload.role)


@router.post("/upload")
def post_upload(
    db: Session = Depends(get_db),
    name: str = Form(...),
    role: Literal["content", "reference"] = Form("content"),
    file: UploadFile = File(...),
):
    """§16.25 c1 — import an iTunes XML uploaded directly from the browser.

    The multipart sibling of /import: same `import_source` underneath, but
    the user hands the API the XML *bytes* instead of a server-side path.
    Closes the §16.13 D1 gate — a `pipx`- or docker-deployed user has no
    Makefile to run; their library lives on their machine, not the
    server's, so a path is useless to them.

    Streaming + cleanup contract (§16.25 c1-review G1/G2):

    * **G1 — `UploadFile`, not `bytes`.** Starlette's `UploadFile` is a
      `SpooledTemporaryFile` (≤ 1 MB in RAM, spills to disk above), so
      `shutil.copyfileobj(file.file, …)` streams a 100 MB library XML
      without buffering the whole body in memory.
    * **G2 — `tempfile.mkstemp` + `finally` unlink.** Keeps
      `import_source(path: Path)` untouched (the CLI manifest path
      stays uniform). The temp file's path outlives the open write
      handle (`os.fdopen` `with`-closes before we hand the path off),
      which is required on Windows where an open writer would block
      the importer's re-open. Unlink runs in `finally` so a parse
      failure or unexpected error still cleans up.

    Error contract mirrors /import via the shared `_import_to_pool`:
    ValueError → 400, anything else → 500. Invalid role is rejected at
    the Form layer (`Literal`) with 422, mirroring /import's JSON
    `Literal` behaviour.
    """
    name = _validate_name(name)
    fd, tmp_path_str = tempfile.mkstemp(suffix=".xml", prefix="ss-upload-")
    tmp_path = Path(tmp_path_str)
    try:
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(file.file, out)
        except OSError as e:
            # c1 mid-slice review 🟡-2: disk-full / temp-partition I/O
            # error during streaming surfaces as an opaque 500 by
            # default. Map it to an honest message so the user knows
            # it was the WRITE that failed, not the parse. `finally`
            # cleanup below still fires (mkstemp succeeded; the fd may
            # have been consumed by os.fdopen's `with` — unlink is
            # best-effort either way).
            raise HTTPException(500, f"upload write failed: {e}")
        return _import_to_pool(db, path=tmp_path, name=name, role=role)
    finally:
        # Best-effort: import_source raised → still cleanup. Unlink fails
        # for an unexpected reason → swallow so the original HTTP response
        # (success or the mapped exception) survives.
        try:
            tmp_path.unlink()
        except OSError:
            pass


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
