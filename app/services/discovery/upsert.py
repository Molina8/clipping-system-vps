"""Upsert helpers: turn DiscoveredCampaign into DB Campaign rows."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignStatus
from app.services.discovery.models import DiscoveredCampaign

logger = logging.getLogger(__name__)


def upsert_campaign(
    db: Session,
    discovered: DiscoveredCampaign,
) -> Campaign:
    """Create or update a Campaign from a DiscoveredCampaign.

    Idempotent on (source_provider, external_id) — we encode it into
    `source_metadata.discovered.external_id` and the Campaign name.

    If a Campaign with the same name exists, we update its source metadata
    + spec extras without changing its status.
    """
    # Lookup by source_provider + source_url (detail_url) — the canonical key.
    # JSONB lookup would be cleaner but psycopg2 JSON access is finicky with NULLs.
    existing = db.execute(
        select(Campaign).where(
            Campaign.source_provider == discovered.provider,
            Campaign.source_url == discovered.detail_url,
        )
    ).scalar_one_or_none()

    metadata: dict[str, Any] = {
        "discovered": discovered.model_dump(),
        "asset_links": list(discovered.asset_links),
        "cpm_usd_per_1k": discovered.cpm_usd_per_1k,
        "prize_pool_usd": discovered.prize_pool_usd,
        "joined": discovered.joined,
    }

    if existing is not None:
        existing.source_metadata = {**(existing.source_metadata or {}), **metadata}
        existing.source_url = discovered.detail_url
        db.commit()
        db.refresh(existing)
        logger.debug("upsert_campaign: updated %s", existing.name)
        return existing

    # New campaign — name must be unique. Disambiguate if collision.
    name = discovered.name
    base_name = name
    suffix = 1
    while db.execute(
        select(Campaign).where(Campaign.name == name)
    ).scalar_one_or_none() is not None:
        suffix += 1
        name = f"{base_name} ({suffix})"

    c = Campaign(
        name=name,
        status=CampaignStatus.DRAFT.value,
        source_provider=discovered.provider,
        source_url=discovered.detail_url,
        source_metadata=metadata,
        source_instructions=discovered.description,
        spec={},
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    logger.info("upsert_campaign: created %s (external_id=%s)", c.name, discovered.external_id)
    return c


def run_discovery(db: Session, *, fetch_detail: bool = True, limit: int = 50) -> dict:
    """Run all registered providers, upsert campaigns, resolve assets.

    Returns a summary dict with per-provider stats.
    """
    from app.services.discovery.asset_resolver import resolve_assets_for_campaign
    from app.services.discovery.registry import all_providers

    summary: dict[str, Any] = {"providers": {}, "total_discovered": 0, "total_upserted": 0, "total_assets": 0}
    for provider in all_providers():
        try:
            discovered = provider.discover(limit=limit)
        except Exception as e:  # noqa: BLE001
            logger.exception("provider %s discover failed: %s", provider.name, e)
            summary["providers"][provider.name] = {"error": str(e)}
            continue

        if fetch_detail:
            for d in discovered:
                try:
                    provider.fetch_detail(d)
                except Exception as e:  # noqa: BLE001
                    logger.info("provider %s fetch_detail failed for %s: %s", provider.name, d.external_id, e)

        upserted = 0
        assets_created = 0
        for d in discovered:
            c = upsert_campaign(db, d)
            upserted += 1
            if d.asset_links:
                new_assets = resolve_assets_for_campaign(db, c.id, d.asset_links, discovery_provider=provider.name)
                assets_created += len(new_assets)

        summary["providers"][provider.name] = {
            "discovered": len(discovered),
            "upserted": upserted,
            "assets_created": assets_created,
        }
        summary["total_discovered"] += len(discovered)
        summary["total_upserted"] += upserted
        summary["total_assets"] += assets_created

    logger.info(
        "run_discovery done: %d discovered, %d upserted, %d new assets",
        summary["total_discovered"], summary["total_upserted"], summary["total_assets"],
    )
    return summary
