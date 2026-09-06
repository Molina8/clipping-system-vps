"""Clip service."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.candidate import Candidate
from app.models.clip import (
    CLIP_QA_STATUS_VALUES,
    CLIP_STATUS_VALUES,
    Clip,
    ClipQAStatus,
    ClipStatus,
)
from app.schemas.clip import ClipCreate, ClipUpdate

logger = logging.getLogger(__name__)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def create_clip(db: Session, payload: ClipCreate) -> Clip:
    """Create a Clip (typically called when a RENDER job completes — Step 17)."""
    asset = db.get(Asset, payload.asset_id)
    if asset is None:
        raise ValueError(f"Asset {payload.asset_id} not found")
    if asset.campaign_id != payload.campaign_id:
        raise ValueError(
            f"Asset {payload.asset_id} belongs to campaign "
            f"{asset.campaign_id}, not {payload.campaign_id}"
        )

    clip = Clip(
        campaign_id=payload.campaign_id,
        asset_id=payload.asset_id,
        candidate_id=payload.candidate_id,
        render_job_id=payload.render_job_id,
        file_path=payload.file_path,
        duration_seconds=payload.duration_seconds,
        file_size=payload.file_size,
        qa_status=payload.qa_status,
        qa_result=payload.qa_result,
        status=payload.status,
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)
    logger.info(
        "clip created: id=%s campaign=%s asset=%s",
        clip.id, clip.campaign_id, clip.asset_id,
    )
    return clip


def update_clip(
    db: Session, clip_id: uuid.UUID, payload: ClipUpdate
) -> Optional[Clip]:
    clip = db.get(Clip, clip_id)
    if clip is None:
        return None

    if payload.qa_status is not None:
        if payload.qa_status not in CLIP_QA_STATUS_VALUES:
            raise ValueError(
                f"Invalid qa_status '{payload.qa_status}'. "
                f"Must be one of: {', '.join(CLIP_QA_STATUS_VALUES)}"
            )
        clip.qa_status = payload.qa_status
        if payload.qa_status in (
            ClipQAStatus.PASS.value,
            ClipQAStatus.FAIL.value,
            ClipQAStatus.REVIEW.value,
        ):
            from datetime import datetime, timezone
            clip.qa_at = datetime.now(timezone.utc)
    if payload.qa_result is not None:
        merged = {**(clip.qa_result or {}), **payload.qa_result}
        clip.qa_result = merged
    if payload.status is not None:
        if payload.status not in CLIP_STATUS_VALUES:
            raise ValueError(
                f"Invalid status '{payload.status}'. "
                f"Must be one of: {', '.join(CLIP_STATUS_VALUES)}"
            )
        clip.status = payload.status
    if payload.qa_job_id is not None:
        clip.qa_job_id = payload.qa_job_id
    if payload.file_path is not None:
        clip.file_path = payload.file_path
    if payload.duration_seconds is not None:
        clip.duration_seconds = payload.duration_seconds
    if payload.file_size is not None:
        clip.file_size = payload.file_size
    if payload.published_at is not None:
        from datetime import datetime, timezone
        clip.published_at = payload.published_at or datetime.now(timezone.utc)

    db.commit()
    db.refresh(clip)
    logger.info("clip updated: id=%s status=%s qa_status=%s", clip.id, clip.status, clip.qa_status)
    return clip


def get_clip(db: Session, clip_id: uuid.UUID) -> Optional[Clip]:
    return db.get(Clip, clip_id)


def list_clips(
    db: Session,
    campaign_id: Optional[int] = None,
    asset_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    qa_status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Clip]:
    q = (
        select(Clip)
        .order_by(Clip.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if campaign_id is not None:
        q = q.where(Clip.campaign_id == campaign_id)
    if asset_id is not None:
        q = q.where(Clip.asset_id == asset_id)
    if status is not None:
        q = q.where(Clip.status == status)
    if qa_status is not None:
        q = q.where(Clip.qa_status == qa_status)
    return list(db.execute(q).scalars())
