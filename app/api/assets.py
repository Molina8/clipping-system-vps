"""Asset API endpoints (Steps 5-6 in architecture_flow.md)."""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.asset import (
    AssetBulkCreate,
    AssetCreate,
    AssetOut,
    AssetUpdate,
)
from app.services.asset_service import (
    create_asset,
    create_assets_bulk,
    get_asset,
    list_assets,
    resolve_assets_for_campaign,
    update_asset,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("", response_model=AssetOut, status_code=201)
@router.post("/", response_model=AssetOut, status_code=201)
def create(
    payload: AssetCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Register a single asset (Step 6)."""
    try:
        a = create_asset(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return a


@router.post("/bulk", response_model=List[AssetOut], status_code=201)
def bulk_create(
    payload: AssetBulkCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Bulk register — used by Asset Resolver (Step 5+6)."""
    return create_assets_bulk(db, payload.assets)


@router.post("/resolve/{campaign_id}", response_model=List[AssetOut])
def resolve(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Run Asset Resolver for a campaign (Step 5).

    Stub for now — returns empty list. Real impl will hit platform APIs.
    """
    try:
        return resolve_assets_for_campaign(db, campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("", response_model=List[AssetOut])
@router.get("/", response_model=List[AssetOut])
def list_all(
    campaign_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    source_provider: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_assets(
        db,
        campaign_id=campaign_id,
        status=status,
        source_provider=source_provider,
        limit=limit,
        offset=offset,
    )


@router.get("/{asset_id}", response_model=AssetOut)
def get_one(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    a = get_asset(db, asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return a


@router.patch("/{asset_id}", response_model=AssetOut)
def update(
    asset_id: uuid.UUID,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        a = update_asset(db, asset_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if a is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return a
