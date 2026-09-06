"""Candidate API endpoints (Steps 13-14 in architecture_flow.md)."""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.candidate import CandidateCreate, CandidateOut, CandidateUpdate
from app.services.candidate_service import (
    bulk_create_candidates,
    create_candidate,
    get_candidate,
    list_candidates,
    update_candidate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.post("", response_model=CandidateOut, status_code=201)
@router.post("/", response_model=CandidateOut, status_code=201)
def create(
    payload: CandidateCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = create_candidate(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return c


@router.post("/bulk", response_model=List[CandidateOut], status_code=201)
def bulk_create(
    payloads: list[CandidateCreate],
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return bulk_create_candidates(db, payloads)


@router.get("", response_model=List[CandidateOut])
@router.get("/", response_model=List[CandidateOut])
def list_all(
    campaign_id: Optional[int] = Query(None),
    asset_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_candidates(
        db,
        campaign_id=campaign_id,
        asset_id=asset_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/{candidate_id}", response_model=CandidateOut)
def get_one(
    candidate_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    c = get_candidate(db, candidate_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return c


@router.patch("/{candidate_id}", response_model=CandidateOut)
def update(
    candidate_id: uuid.UUID,
    payload: CandidateUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = update_candidate(db, candidate_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if c is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return c
