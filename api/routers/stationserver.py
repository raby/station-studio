"""Settings › Station Server, and sending weeks to it (engine/stationserver.py).

- GET  /api/station-server                     — the address, whether a code is kept, each station's target
- PUT  /api/station-server                     — change the address and/or link code
- GET  /api/station-server/status              — Station Server's stations, what's on air, the weeks it holds
- PUT  /api/station-server/stations/{slug}     — which Station Server station a Studio station feeds, and
                                                 whether Export to disk also sends it
- POST /api/playlists/{id}/send-to-station-server — send the week now

The link code is kept but never sent back to the page: it's typed once,
from Station Server's Settings › Station Studio.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.deps import get_db
from engine import stationserver
from engine.models import Station

router = APIRouter(tags=["station-server"])


class LinkSettingsIn(BaseModel):
    address: str | None = None
    code: str | None = None


class TargetIn(BaseModel):
    station: str = ""
    auto: bool = False


def _public(data: dict) -> dict:
    return {"address": data["address"], "has_code": bool(data["code"]), "stations": data["stations"]}


@router.get("/api/station-server")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return _public(stationserver.load(db))


@router.put("/api/station-server")
def put_settings(payload: LinkSettingsIn, db: Session = Depends(get_db)) -> dict:
    data = stationserver.load(db)
    if payload.address is not None:
        address = payload.address.strip()
        if address:
            try:
                stationserver.base_url(address)
            except stationserver.LinkError as exc:
                raise HTTPException(400, str(exc))
        data["address"] = address
    if payload.code is not None:
        data["code"] = payload.code.strip()
    return _public(stationserver.save(db, data))


@router.get("/api/station-server/status")
def get_status(db: Session = Depends(get_db)) -> dict:
    return stationserver.status(db)


@router.put("/api/station-server/stations/{slug}")
def put_target(slug: str, payload: TargetIn, db: Session = Depends(get_db)) -> dict:
    if not db.query(Station).filter(Station.slug == slug).first():
        raise HTTPException(404, f"station '{slug}' not found")
    data = stationserver.load(db)
    if payload.station.strip():
        data["stations"][slug] = {"station": payload.station.strip(), "auto": payload.auto}
    else:
        data["stations"].pop(slug, None)
    return _public(stationserver.save(db, data))


@router.post("/api/playlists/{playlist_id}/send-to-station-server")
def send(playlist_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return stationserver.send_week(db, playlist_id)
    except stationserver.LinkError as exc:
        raise HTTPException(exc.status, str(exc))
