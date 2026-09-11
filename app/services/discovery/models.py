"""Discovery models: Pydantic schemas for what providers return and scoring output."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class DiscoveredCampaign(BaseModel):
    """A campaign as it comes out of a provider (raw, before DB upsert).

    After upsert into the DB, this becomes a Campaign row + Asset rows.
    """
    provider: str                           # "whop", "twitch", ...
    external_id: str                        # provider-stable id (e.g. "exp_xxx/UUID")
    detail_url: str                         # canonical URL to the campaign detail
    name: str
    description: str | None = None
    cpm_usd_per_1k: float | None = None     # parsed from card text
    prize_pool_usd: float | None = None     # parsed total pool (recaudado/total second number)
    joined: int | None = None               # number of participants
    asset_links: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    """Output of the scoring algorithm.

    A campaign is "interesting" when `interesting=True`. Even when not interesting
    we still create the Campaign row (history), but we skip running clip_selection.
    """
    cpm_score: float
    prize_pool_score: float
    difficulty_score: float
    final_score: float
    interesting: bool
    reasons: list[str] = Field(default_factory=list)
