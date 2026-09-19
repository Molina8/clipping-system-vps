"""Discovery models: Pydantic schemas for what providers return and scoring output.

Redesigned 2026-09-17 (pipeline v2): carry the full Whop API surface so
downstream cron 3a (brief-reader) can read `reference_materials` and
`payouts` directly from `source_metadata.discovered` without re-fetching
the canonical `source_url` (which Whop auth-gates behind owner session).
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class PayoutRule(BaseModel):
    """Per-platform payout from Whop API `payouts[]`.

    For cpm campaigns `rate_cents` is USD cents per 1k views. `min_payout_cents`
    and `max_payout_cents` cap the per-clip payout range.
    """
    platform: str                       # tiktok, instagram, youtube_shorts, x, snapchat, ...
    payout_type: str                    # cpm, flat, bounty, ...
    rate_cents: int | None = None
    min_payout_cents: int | None = None
    max_payout_cents: int | None = None


class ReferenceMaterial(BaseModel):
    """One `referenceMaterials[]` entry from the Whop API.

    URL may point at Drive / Docs / Notion / YouTube / generic web. `media_type`
    discriminates (e.g. "external", "video", "image"). `type` is the role
    ("brandAsset", "tutorial", "footage", "drive_folder", ...).
    """
    media_type: str | None = None
    type: str | None = None
    url: str
    name: str | None = None


class DiscoveredCampaign(BaseModel):
    """A campaign as it comes out of a provider (raw, before DB upsert)."""
    provider: str
    external_id: str
    detail_url: str
    name: str
    description: str | None = None

    # Money / reach (parsed from API).
    cpm_usd_per_1k: float | None = None     # cpmMinRateCents / 100
    prize_pool_usd: float | None = None     # budgetCents / 100
    joined: int | None = None               # approvedSubmissionCount

    # Full Whop API surface (NEW, 2026-09-17).
    payouts: list[PayoutRule] = Field(default_factory=list)
    reference_materials: list[ReferenceMaterial] = Field(default_factory=list)
    organization_name: str | None = None
    organization_verified: bool | None = None
    organization_id: str | None = None
    categories: list[dict[str, Any]] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    status: str | None = None
    requires_application: bool | None = None
    primary_payout_cents: int | None = None

    asset_links: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    cpm_score: float
    prize_pool_score: float
    difficulty_score: float
    final_score: float
    interesting: bool
    reasons: list[str] = Field(default_factory=list)
