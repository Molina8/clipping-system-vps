"""Discovery API endpoints (Steps 1 + 5 of architecture_flow.md).

POST /discovery/run          → run all providers now, upsert campaigns, resolve assets.
GET  /discovery/providers    → list registered providers.
GET  /discovery/scoring/{id} → score a specific campaign.
POST /discovery/score_due    → score all draft+ready campaigns that haven't been scored.
"""
from __future__ import annotations

import logging
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.models.campaign import Campaign
from app.services.campaign_analyzer import analyze_due_campaigns
from app.services.discovery.registry import all_providers
from app.services.discovery.scoring import score_campaign
from app.services.discovery.upsert import run_discovery
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/providers", response_model=dict)
def list_providers(_: bool = Depends(require_bearer)):
    """List registered campaign providers (Whop today, more tomorrow)."""
    return {
        "providers": [
            {"name": p.name, "type": type(p).__name__}
            for p in all_providers()
        ],
        "config": {
            "cpm_min_usd_per_1k": getattr(settings, "cpm_min_usd_per_1k", 1.0),
            "prize_pool_min_usd": getattr(settings, "prize_pool_min_usd", 20000.0),
        },
    }


@router.post("/run", response_model=dict)
def run_now(
    limit: int = Query(50, ge=1, le=200),
    fetch_detail: bool = Query(True),
    analyze_after: bool = Query(True),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Run discovery for all providers now, upsert, resolve assets, optionally analyze."""
    summary = run_discovery(db, fetch_detail=fetch_detail, limit=limit)
    if analyze_after:
        try:
            results = analyze_due_campaigns(db, limit=limit)
            summary["analyzed"] = len(results)
            summary["analyze_results"] = [
                {"campaign_id": r["campaign_id"], "status": r["status"], "enriched_from": r.get("enriched_from")}
                for r in results
            ]
        except Exception as e:  # noqa: BLE001
            logger.exception("analyze_after failed: %s", e)
            summary["analyze_error"] = str(e)
    return summary


@router.get("/scoring/{campaign_id}", response_model=dict)
def get_scoring(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Score a specific campaign using the scoring algorithm."""
    c = db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    s = score_campaign(c)
    return {
        "campaign_id": c.id,
        "name": c.name,
        "score": s.model_dump(),
    }


@router.post("/score_due", response_model=dict)
def score_due(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Score all draft+ready campaigns that haven't been scored yet.

    Marks each Campaign with source_metadata.score (so subsequent runs skip it
    unless we invalidate).
    """
    from sqlalchemy import select

    q = (
        select(Campaign)
        .where(Campaign.status.in_(["draft", "ready"]))
        .where(Campaign.source_metadata["score"].is_(None))
        .limit(limit)
    )
    rows = list(db.execute(q).scalars())
    results = []
    for c in rows:
        s = score_campaign(c)
        meta = dict(c.source_metadata or {})
        meta["score"] = s.model_dump()
        c.source_metadata = meta
        results.append({"campaign_id": c.id, "name": c.name, "score": s.model_dump()})
    db.commit()
    return {"scored": len(results), "results": results}
