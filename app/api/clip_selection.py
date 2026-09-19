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
from app.models.campaign import Campaign

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
    priority_only: bool = Query(
        False,
        description="If true, restrict to assets whose campaign has priority_tier='priority'. "
        "Used by clip-decider-tick to favor high-CPM / high-prize campaigns first.",
    ),
    priority_tier: str | None = Query(
        None,
        description="Filter by exact priority_tier value ('priority'|'standard'|'low_priority'); "
        "ignored if priority_only is true. Use 'all' to disable filtering.",
    ),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> Dict[str, Any]:
    """List transcribed assets that have NOT yet been processed.

    Step 12 of architecture_flow.md. Optionally filters by the campaign's
    priority_tier (populated by scripts/campaign_prioritizer.py every 2h).
    """
    q = (
        select(Asset, Campaign)
        .join(Campaign, Campaign.id == Asset.campaign_id)
        .where(Asset.status == AssetStatus.TRANSCRIBED.value)
        .order_by(Asset.transcribed_at.desc().nullslast())
        .limit(limit)
    )
    rows = db.execute(q).all()

    pending: List[Dict[str, Any]] = []
    for asset, campaign in rows:
        if (asset.extra_metadata or {}).get("clip_selection_at") is not None:
            continue
        # Resolve tier from campaign.source_metadata (JSONB).
        md = campaign.source_metadata or {}
        tier = md.get("priority_tier") or "standard"
        if priority_only and tier != "priority":
            continue
        if priority_tier and priority_tier != "all" and tier != priority_tier:
            continue
        pending.append({
            "id": str(asset.id),
            "campaign_id": asset.campaign_id,
            "campaign_name": campaign.name,
            "priority_tier": tier,
            "priority_score": md.get("priority_score"),
            "status": asset.status,
            "transcribed_at": (
                asset.transcribed_at.isoformat() if asset.transcribed_at else None
            ),
            "duration_seconds": asset.duration_seconds,
            "source_url": asset.source_url,
        })
    return {
        "pending": pending,
        "count": len(pending),
        "filter": {
            "priority_only": priority_only,
            "priority_tier": priority_tier,
        },
    }


@router.post("/process_all")
def process_all(
    max_assets: int = Query(10, ge=1, le=50),
    priority_only: bool = Query(
        False,
        description="If true, only process assets from campaigns with priority_tier='priority'.",
    ),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> Dict[str, Any]:
    """Process up to max_assets pending transcriptions.

    Step 13 of architecture_flow.md. When priority_only is true, processes
    high-priority campaigns first (per scripts/campaign_prioritizer.py).
    """
    candidate_q = (
        select(Asset.id, Campaign.source_metadata)
        .join(Campaign, Campaign.id == Asset.campaign_id)
        .where(Asset.status == AssetStatus.TRANSCRIBED.value)
        .limit(max_assets * 4 if priority_only else max_assets * 2)
    )
    candidate_rows = db.execute(candidate_q).all()

    # Sort: priority assets first, then by transcribed_at desc.
    def _sort_key(row):
        aid, md = row
        tier = (md or {}).get("priority_tier") or "standard"
        # priority < standard < low_priority (None goes last)
        order = {"priority": 0, "standard": 1, "low_priority": 2}.get(tier, 3)
        return (order, 0)

    if priority_only:
        candidate_rows = [
            (aid, md) for aid, md in candidate_rows
            if (md or {}).get("priority_tier") == "priority"
        ]

    agent = ClipSelectionAgent()
    results: List[Dict[str, Any]] = []
    skipped = 0
    for aid, _md in candidate_rows:
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
        "priority_only": priority_only,
        "results": results,
    }
