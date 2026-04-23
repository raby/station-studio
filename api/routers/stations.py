"""Stations CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from engine.models import Station
from engine.template import parse_template, TemplateError
from api.deps import get_db
from api.schemas import StationCreate, StationOut


router = APIRouter(prefix="/api/stations", tags=["stations"])


@router.get("", response_model=list[StationOut])
def list_stations(db: Session = Depends(get_db)):
    return db.query(Station).order_by(Station.slug).all()


@router.get("/{slug}", response_model=StationOut)
def get_station(slug: str, db: Session = Depends(get_db)):
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")
    return s


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


@router.put("/{slug}", response_model=StationOut)
def update_station(slug: str, payload: StationCreate, db: Session = Depends(get_db)):
    s = db.query(Station).filter(Station.slug == slug).first()
    if not s:
        raise HTTPException(404, f"station '{slug}' not found")
    try:
        parse_template(payload.template)
    except TemplateError as e:
        raise HTTPException(400, f"invalid template: {e}")
    s.name = payload.name
    s.template = payload.template
    db.commit()
    db.refresh(s)
    return s
