"""Asset Resolver — Step 5/6 of architecture_flow.md.

Given a campaign + a list of candidate asset URLs, register each as an `Asset`
row with `source_url` + `kind` (drive/youtube/googlesheets/dropbox/mega/external).
Idempotent on (campaign_id, source_url).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetStatus

logger = logging.getLogger(__name__)


def classify_link(url: str) -> str:
    """Return the asset kind: drive, youtube, googlesheets, dropbox, mega, external."""
    u = url.lower()
    if "drive.google.com" in u:
        return "drive"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "docs.google.com/spreadsheets" in u:
        return "googlesheets"
    if "dropbox.com" in u:
        return "dropbox"
    if "mega.nz" in u:
        return "mega"
    return "external"


def _extract_id(kind: str, url: str) -> str | None:
    """Extract a platform-specific id from a URL."""
    if kind == "youtube":
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
        return m.group(1) if m else None
    if kind == "googlesheets":
        m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else None
    if kind == "mega":
        m = re.search(r"/file/([^#]+)", url)
        return m.group(1) if m else None
    return None


def resolve_assets_for_campaign(
    db: Session,
    campaign_id: int,
    candidate_links: Iterable[str],
    *,
    discovery_provider: str | None = None,
) -> list[Asset]:
    """Register each candidate link as an Asset row if not already present.

    `discovery_provider` is the provider that *found* the asset (e.g. "whop"),
    while `source_provider` is the platform the asset actually lives on
    (e.g. "youtube", "drive", "dropbox"). The asset row needs both, plus a
    `kind` for compatibility with the existing classification.

    Returns the list of newly created Asset rows (existing ones are skipped).
    """
    created: list[Asset] = []
    for url in candidate_links:
        url = url.strip()
        if not url or not url.startswith(("http://", "https://")):
            continue
        kind = classify_link(url)
        # Dedupe
        existing = db.execute(
            select(Asset).where(
                Asset.campaign_id == campaign_id,
                Asset.source_url == url,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        asset = Asset(
            campaign_id=campaign_id,
            source_url=url,
            source_id=_extract_id(kind, url),
            source_provider=discovery_provider or kind,
            asset_type="video" if kind in ("youtube", "drive", "dropbox", "mega", "external") else "sheet",
            status=AssetStatus.PENDING.value,
            extra_metadata={"kind": kind, "discovered_by": discovery_provider or "manual"},
        )
        db.add(asset)
        created.append(asset)
    if created:
        db.commit()
        for a in created:
            db.refresh(a)
        logger.info(
            "asset_resolver: created %d new assets for campaign %s",
            len(created), campaign_id,
        )
    return created
