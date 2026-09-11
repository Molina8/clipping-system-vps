"""Discovery package: campaign providers + asset resolver + scoring.

Architecture_flow.md Steps 1, 2, 5, 6:
  Step 1: discover campaigns from external providers (Whop today, more tomorrow).
  Step 2: score each campaign (CPM + prize pool vs difficulty).
  Step 5: resolve asset URLs (Drive, YouTube, Sheets, Dropbox, Mega, external).
  Step 6: register assets as `Asset` rows in PostgreSQL.
"""
from app.services.discovery.asset_resolver import classify_link, resolve_assets_for_campaign
from app.services.discovery.base import CampaignProvider
from app.services.discovery.models import DiscoveredCampaign, ScoreResult
from app.services.discovery.registry import all_providers, get_provider
from app.services.discovery.scoring import score_campaign
from app.services.discovery.upsert import run_discovery, upsert_campaign

__all__ = [
    "CampaignProvider",
    "DiscoveredCampaign",
    "ScoreResult",
    "all_providers",
    "classify_link",
    "get_provider",
    "resolve_assets_for_campaign",
    "run_discovery",
    "score_campaign",
    "upsert_campaign",
]
