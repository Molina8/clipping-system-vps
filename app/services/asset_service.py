"""Asset service — CRUD + state transitions + Asset Resolver stub."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import ASSET_STATUS_VALUES, Asset, AssetStatus
from app.models.campaign import Campaign
from app.schemas.asset import AssetCreate, AssetUpdate

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_asset(db: Session, payload: AssetCreate) -> Asset:
    """Register a single asset (called by Asset Resolver or manually)."""
    campaign = db.get(Campaign, payload.campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {payload.campaign_id} not found")

    asset = Asset(
        campaign_id=payload.campaign_id,
        source_url=payload.source_url,
        source_id=payload.source_id,
        source_provider=payload.source_provider or campaign.source_provider,
        asset_type=payload.asset_type,
        extra_metadata=payload.extra_metadata,
        status=AssetStatus.PENDING.value,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    logger.info(
        "asset registered: id=%s campaign=%s source=%s",
        asset.id, asset.campaign_id, asset.source_provider,
    )
    return asset


def create_assets_bulk(
    db: Session, payloads: list[AssetCreate]
) -> list[Asset]:
    """Bulk register (used by Asset Resolver for batch discovery)."""
    assets: list[Asset] = []
    for p in payloads:
        campaign = db.get(Campaign, p.campaign_id)
        if campaign is None:
            logger.warning(
                "skip asset for missing campaign %s: url=%s", p.campaign_id, p.source_url
            )
            continue
        a = Asset(
            campaign_id=p.campaign_id,
            source_url=p.source_url,
            source_id=p.source_id,
            source_provider=p.source_provider or campaign.source_provider,
            asset_type=p.asset_type,
            extra_metadata=p.extra_metadata,
            status=AssetStatus.PENDING.value,
        )
        db.add(a)
        assets.append(a)
    db.commit()
    for a in assets:
        db.refresh(a)
    logger.info("bulk registered %d assets", len(assets))
    return assets


def update_asset(
    db: Session, asset_id: uuid.UUID, payload: AssetUpdate
) -> Optional[Asset]:
    """Returns None if not found."""
    asset = db.get(Asset, asset_id)
    if asset is None:
        return None

    if payload.status is not None:
        if payload.status not in ASSET_STATUS_VALUES:
            raise ValueError(
                f"Invalid status '{payload.status}'. Must be one of: {', '.join(ASSET_STATUS_VALUES)}"
            )
        asset.status = payload.status
        # Set timestamps on transitions
        now = _now()
        if payload.status == AssetStatus.DOWNLOADED.value and asset.downloaded_at is None:
            asset.downloaded_at = now
        if payload.status == AssetStatus.TRANSCRIBED.value and asset.transcribed_at is None:
            asset.transcribed_at = now

    if payload.local_path is not None:
        asset.local_path = payload.local_path
    if payload.file_size is not None:
        asset.file_size = payload.file_size
    if payload.duration_seconds is not None:
        asset.duration_seconds = payload.duration_seconds
    if payload.sha256 is not None:
        asset.sha256 = payload.sha256
    if payload.mime_type is not None:
        asset.mime_type = payload.mime_type
    if payload.extra_metadata is not None:
        merged = {**(asset.extra_metadata or {}), **payload.extra_metadata}
        asset.extra_metadata = merged

    db.commit()
    db.refresh(asset)
    logger.info("asset updated: id=%s status=%s", asset.id, asset.status)
    return asset


def get_asset(db: Session, asset_id: uuid.UUID) -> Optional[Asset]:
    return db.get(Asset, asset_id)


def list_assets(
    db: Session,
    campaign_id: Optional[int] = None,
    status: Optional[str] = None,
    source_provider: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Asset]:
    q = (
        select(Asset)
        .order_by(Asset.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if campaign_id is not None:
        q = q.where(Asset.campaign_id == campaign_id)
    if status is not None:
        q = q.where(Asset.status == status)
    if source_provider is not None:
        q = q.where(Asset.source_provider == source_provider)
    return list(db.execute(q).scalars())


# ----------------------------------------------------------------------------
# Asset Resolver stub
# ----------------------------------------------------------------------------

def resolve_assets_for_campaign(
    db: Session, campaign_id: int
) -> list[Asset]:
    """Asset Resolver (Step 5 in architecture_flow.md).

    STUB: returns empty list for now.

    Real implementation will:
    1. Read campaign.spec + campaign.source_provider
    2. Query platform APIs (YouTube Data API, Twitter API, etc.) based
       on source_provider and the spec.keywords / spec.exclude_keywords
    3. Deduplicate against existing assets in this campaign
    4. Call create_assets_bulk() to register them

    Until that platform-API integration is implemented, OpenClaw/MiniMax
    can call POST /assets directly to register assets they discover.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    logger.info(
        "asset resolver stub: campaign=%s source=%s (no-op for now)",
        campaign_id, campaign.source_provider,
    )
    return []
