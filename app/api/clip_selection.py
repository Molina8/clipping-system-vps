"""Clip Selection API (Steps 12-13 of architecture_flow.md).

Endpoints:
  POST /clip_selection/process/{asset_id}  -> run agent for one asset
  POST /clip_selection/process_all         -> process up to N pending
  GET  /clip_selection/queue               -> list transcribed assets
                                              that have NOT yet been
                                              processed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.clip_selection.agent import ClipSelectionAgent
from app.db.database import get_db
from app.models.asset import Asset, AssetStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clip_selection", tags=["clip_selection"])


@router.post("/process/{asset_id}")
def process_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> Dict[str, Any]:
    """Run clip selection for a single transcribed asset."""
    agent = ClipSelectionAgent()
    try:
        result = agent.run(db, str(asset_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.get("/queue")
def list_pending(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> Dict[str, Any]:
    """List transcribed assets that have NOT yet been processed."""
    rows = db.execute(
        select(Asset)
        .where(Asset.status == AssetStatus.TRANSCRIBED.value)
        .order_by(Asset.transcribed_at.desc().nullslast())
        .limit(limit)
    ).scalars().all()
    pending: List[Dict[str, Any]] = []
    for a in rows:
        if (a.extra_metadata or {}).get("clip_selection_at") is not None:
            continue
        pending.append({
            "id": str(a.id),
            "campaign_id": a.campaign_id,
            "status": a.status,
            "transcribed_at": (
                a.transcribed_at.isoformat() if a.transcribed_at else None
            ),
            "duration_seconds": a.duration_seconds,
            "source_url": a.source_url,
        })
    return {"pending": pending, "count": len(pending)}


@router.post("/process_all")
def process_all(
    max_assets: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> Dict[str, Any]:
    """Process up to max_assets pending transcriptions."""
    candidate_ids = db.execute(
        select(Asset.id)
        .where(Asset.status == AssetStatus.TRANSCRIBED.value)
        .limit(max_assets * 2)
    ).scalars().all()

    agent = ClipSelectionAgent()
    results: List[Dict[str, Any]] = []
    skipped = 0
    for aid in candidate_ids:
        asset = db.get(Asset, aid)
        if asset is None:
            continue
        if (asset.extra_metadata or {}).get("clip_selection_at") is not None:
            skipped += 1
            continue
        try:
            results.append(agent.run(db, str(aid)))
        except Exception as e:  # noqa: BLE001
            logger.exception("process_all failed for asset %s: %s", aid, e)
            results.append({"asset_id": str(aid), "error": str(e)})
        if len(results) >= max_assets:
            break

    return {
        "processed": len(results),
        "skipped": skipped,
        "results": results,
    }
