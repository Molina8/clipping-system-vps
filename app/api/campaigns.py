"""Campaign API endpoints (Step 4 in architecture_flow.md)."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.campaign import (
    CampaignCreate,
    CampaignOut,
    CampaignUpdate,
)
from app.services.campaign_service import (
    create_campaign,
    get_campaign,
    list_campaigns,
    update_campaign,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=CampaignOut, status_code=201)
@router.post("/", response_model=CampaignOut, status_code=201)
def create(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = create_campaign(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return c


@router.get("", response_model=List[CampaignOut])
@router.get("/", response_model=List[CampaignOut])
def list_all(
    status_filter: Optional[str] = Query(None, alias="status"),
    source_provider: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_campaigns(
        db,
        status=status_filter,
        source_provider=source_provider,
        limit=limit,
        offset=offset,
    )


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_one(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    c = get_campaign(db, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = update_campaign(db, campaign_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c
