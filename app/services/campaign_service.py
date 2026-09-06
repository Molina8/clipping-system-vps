"""Campaign service — CRUD + state transitions."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import (
    CAMPAIGN_STATUS_VALUES,
    Campaign,
    CampaignStatus,
)
from app.schemas.campaign import CampaignCreate, CampaignUpdate

logger = logging.getLogger(__name__)


def create_campaign(db: Session, payload: CampaignCreate) -> Campaign:
    """Idempotent on name — raises ValueError if name exists."""
    existing = db.execute(
        select(Campaign).where(Campaign.name == payload.name)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Campaign '{payload.name}' already exists")

    spec_dict = payload.spec.model_dump() if payload.spec else {}
    campaign = Campaign(
        name=payload.name,
        status=CampaignStatus.DRAFT.value,
        source_provider=payload.source_provider,
        source_id=payload.source_id,
        source_url=payload.source_url,
        source_metadata=payload.source_metadata,
        source_instructions=payload.source_instructions,
        spec=spec_dict,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    logger.info(
        "campaign created: id=%s name=%s source=%s",
        campaign.id, campaign.name, campaign.source_provider,
    )
    return campaign


def update_campaign(
    db: Session, campaign_id: int, payload: CampaignUpdate
) -> Optional[Campaign]:
    """Returns None if campaign not found."""
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None

    if payload.name is not None:
        campaign.name = payload.name
    if payload.source_instructions is not None:
        campaign.source_instructions = payload.source_instructions
    if payload.status is not None:
        if payload.status not in CAMPAIGN_STATUS_VALUES:
            raise ValueError(
                f"Invalid status '{payload.status}'. "
                f"Must be one of: {', '.join(CAMPAIGN_STATUS_VALUES)}"
            )
        campaign.status = payload.status
    if payload.spec is not None:
        campaign.spec = payload.spec.model_dump()
    if payload.source_metadata is not None:
        # Merge on top of existing
        merged = {**(campaign.source_metadata or {}), **payload.source_metadata}
        campaign.source_metadata = merged

    db.commit()
    db.refresh(campaign)
    logger.info(
        "campaign updated: id=%s status=%s source=%s",
        campaign.id, campaign.status, campaign.source_provider,
    )
    return campaign


def get_campaign(db: Session, campaign_id: int) -> Optional[Campaign]:
    return db.get(Campaign, campaign_id)


def get_campaign_by_name(db: Session, name: str) -> Optional[Campaign]:
    return db.execute(
        select(Campaign).where(Campaign.name == name)
    ).scalar_one_or_none()


def list_campaigns(
    db: Session,
    status: Optional[str] = None,
    source_provider: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Campaign]:
    q = (
        select(Campaign)
        .order_by(Campaign.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        q = q.where(Campaign.status == status)
    if source_provider is not None:
        q = q.where(Campaign.source_provider == source_provider)
    return list(db.execute(q).scalars())
