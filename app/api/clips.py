"""Clip API endpoints (Steps 16-19 in architecture_flow.md)."""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.clip import ClipCreate, ClipOut, ClipUpdate
from app.services.clip_service import (
    create_clip,
    get_clip,
    list_clips,
    update_clip,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["clips"])


@router.post("", response_model=ClipOut, status_code=201)
@router.post("/", response_model=ClipOut, status_code=201)
def create(
    payload: ClipCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = create_clip(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return c


@router.get("", response_model=List[ClipOut])
@router.get("/", response_model=List[ClipOut])
def list_all(
    campaign_id: Optional[int] = Query(None),
    asset_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    qa_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_clips(
        db,
        campaign_id=campaign_id,
        asset_id=asset_id,
        status=status,
        qa_status=qa_status,
        limit=limit,
        offset=offset,
    )


@router.get("/{clip_id}", response_model=ClipOut)
def get_one(
    clip_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    c = get_clip(db, clip_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return c


@router.patch("/{clip_id}", response_model=ClipOut)
def update(
    clip_id: uuid.UUID,
    payload: ClipUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = update_clip(db, clip_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if c is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return c
