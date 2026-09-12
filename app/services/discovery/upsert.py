"""Upsert helpers: turn DiscoveredCampaign into DB Campaign rows."""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignStatus
from app.services.discovery.models import DiscoveredCampaign

logger = logging.getLogger(__name__)


# --- Representative name builder -------------------------------------------
# Goal: a glanceable name in the UI that always starts with [whop] and shows
# the two numbers operators care about first: CPM ($X/1k) and prize pool
# ($Y). Anything noisy (HTML entities, weird punctuation, duplicate dashes)
# is cleaned so the row is sortable and readable.
#
# Examples:
#   "Yomi Denzel Clipping - 1$ par 1000 vues" + cpm=$1.0 pool=$238k joined=264
#     -> "[whop] $1.0/1k · $238k · Yomi Denzel Clipping"
#   If CPM or pool are missing, the segment is dropped (not replaced with "?").
#   If the raw name is empty, falls back to the Whop slug (last URL segment).
_K_REPR_NUMBER = re.compile(r"^\$?([\d.,]+)\s*([KkMm]?)$")


def _fmt_money_short(value: float | int | None) -> str | None:
    """Format a USD amount as compact string: 1500 -> '$1.5k', 20000 -> '$20k'."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M".replace(".0M", "M")
    if v >= 1_000:
        return f"${v / 1_000:.1f}k".replace(".0k", "k")
    return f"${int(v)}"


def _fmt_cpm(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    # CPM here is already USD per 1k views.
    if v >= 1:
        s = f"${v:.2f}".rstrip("0").rstrip(".")
        return f"{s}/1k"
    return f"${v:.2f}/1k"


def build_representative_name(
    raw_name: str | None,
    *,
    provider: str,
    cpm_usd_per_1k: float | int | None = None,
    prize_pool_usd: float | int | None = None,
    detail_url: str | None = None,
) -> str:
    """Return a clean, glanceable campaign name for the UI.

    Format: "[<provider>] <cpm> · <pool> · <cleaned raw name>"
    Missing segments are skipped. Provider is always present.
    """
    # Clean the raw name: strip html entities, collapse whitespace, drop
    # duplicate dashes, trim.
    name = (raw_name or "").strip()
    if not name and detail_url:
        # Fall back to the last URL segment (campaign uuid-ish).
        name = detail_url.rstrip("/").split("/")[-1]
    name = re.sub(r"\s+", " ", name)
    # collapse repeated dashes / pipes / middots
    name = re.sub(r"[-_]{2,}", "-", name)
    name = name.strip(" -|·•")
    if not name:
        name = "Unnamed"

    segments: list[str] = [f"[{provider}]"]
    cpm = _fmt_cpm(cpm_usd_per_1k)
    if cpm:
        segments.append(cpm)
    pool = _fmt_money_short(prize_pool_usd)
    if pool:
        segments.append(pool)
    segments.append(name)
    return " · ".join(segments)


def _is_representative(name: str | None, provider: str) -> bool:
    """True if `name` already starts with the representative prefix for `provider`."""
    if not name:
        return False
    return name.startswith(f"[{provider}]")


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
        # Backfill representative name if the row predates this convention
        # (e.g. legacy rows from before the rename).
        if not _is_representative(existing.name, discovered.provider):
            base = build_representative_name(
                discovered.name,
                provider=discovered.provider,
                cpm_usd_per_1k=discovered.cpm_usd_per_1k,
                prize_pool_usd=discovered.prize_pool_usd,
                detail_url=discovered.detail_url,
            )
            existing.name = _unique_name(db, base, exclude_id=existing.id)
        db.commit()
        db.refresh(existing)
        logger.debug("upsert_campaign: updated %s", existing.name)
        return existing

    # New campaign — always use a representative, glanceable name.
    base = build_representative_name(
        discovered.name,
        provider=discovered.provider,
        cpm_usd_per_1k=discovered.cpm_usd_per_1k,
        prize_pool_usd=discovered.prize_pool_usd,
        detail_url=discovered.detail_url,
    )
    name = _unique_name(db, base)

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


def _unique_name(db: Session, base: str, *, exclude_id: int | None = None) -> str:
    """Return `base` or `base (N)` so the name column stays unique."""
    candidate = base
    suffix = 1
    while True:
        q = select(Campaign).where(Campaign.name == candidate)
        if exclude_id is not None:
            q = q.where(Campaign.id != exclude_id)
        if db.execute(q).scalar_one_or_none() is None:
            return candidate
        suffix += 1
        candidate = f"{base} ({suffix})"


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
