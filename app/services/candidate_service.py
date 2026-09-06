"""Candidate service."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import CANDIDATE_STATUS_VALUES, Candidate, CandidateStatus
from app.models.asset import Asset
from app.schemas.candidate import CandidateCreate, CandidateUpdate

logger = logging.getLogger(__name__)


def create_candidate(db: Session, payload: CandidateCreate) -> Candidate:
    """Create a candidate (Step 14 in architecture_flow.md)."""
    asset = db.get(Asset, payload.asset_id)
    if asset is None:
        raise ValueError(f"Asset {payload.asset_id} not found")
    if asset.campaign_id != payload.campaign_id:
        raise ValueError(
            f"Asset {payload.asset_id} belongs to campaign "
            f"{asset.campaign_id}, not {payload.campaign_id}"
        )

    cand = Candidate(
        campaign_id=payload.campaign_id,
        asset_id=payload.asset_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        score=payload.score,
        reasoning=payload.reasoning,
        extra_metadata=payload.extra_metadata,
        status=CandidateStatus.PENDING.value,
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)
    logger.info(
        "candidate created: id=%s campaign=%s asset=%s %.1fs-%.1fs",
        cand.id, cand.campaign_id, cand.asset_id, cand.start_time, cand.end_time,
    )
    return cand


def bulk_create_candidates(
    db: Session, payloads: list[CandidateCreate]
) -> list[Candidate]:
    """Bulk create (used by OpenClaw step 14 with many candidates at once)."""
    cands: list[Candidate] = []
    for p in payloads:
        asset = db.get(Asset, p.asset_id)
        if asset is None or asset.campaign_id != p.campaign_id:
            logger.warning("skip candidate: invalid asset %s", p.asset_id)
            continue
        cand = Candidate(
            campaign_id=p.campaign_id,
            asset_id=p.asset_id,
            start_time=p.start_time,
            end_time=p.end_time,
            score=p.score,
            reasoning=p.reasoning,
            extra_metadata=p.extra_metadata,
            status=CandidateStatus.PENDING.value,
        )
        db.add(cand)
        cands.append(cand)
    db.commit()
    for c in cands:
        db.refresh(c)
    logger.info("bulk created %d candidates", len(cands))
    return cands


def update_candidate(
    db: Session, candidate_id: uuid.UUID, payload: CandidateUpdate
) -> Optional[Candidate]:
    cand = db.get(Candidate, candidate_id)
    if cand is None:
        return None

    if payload.status is not None:
        if payload.status not in CANDIDATE_STATUS_VALUES:
            raise ValueError(
                f"Invalid status '{payload.status}'. "
                f"Must be one of: {', '.join(CANDIDATE_STATUS_VALUES)}"
            )
        cand.status = payload.status
    if payload.score is not None:
        cand.score = payload.score
    if payload.reasoning is not None:
        cand.reasoning = payload.reasoning
    if payload.extra_metadata is not None:
        merged = {**(cand.extra_metadata or {}), **payload.extra_metadata}
        cand.extra_metadata = merged

    db.commit()
    db.refresh(cand)
    logger.info("candidate updated: id=%s status=%s", cand.id, cand.status)
    return cand


def get_candidate(db: Session, candidate_id: uuid.UUID) -> Optional[Candidate]:
    return db.get(Candidate, candidate_id)


def list_candidates(
    db: Session,
    campaign_id: Optional[int] = None,
    asset_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Candidate]:
    q = (
        select(Candidate)
        .order_by(Candidate.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if campaign_id is not None:
        q = q.where(Candidate.campaign_id == campaign_id)
    if asset_id is not None:
        q = q.where(Candidate.asset_id == asset_id)
    if status is not None:
        q = q.where(Candidate.status == status)
    return list(db.execute(q).scalars())
