"""Campaign engine package: Pydantic models + parser + normalizer.

Architecture_flow.md Steps 3 + 13.
"""
from app.campaign_engine.models import CampaignHints, NormalizedSpec
from app.campaign_engine.normalizer import normalize
from app.campaign_engine.parser import parse_instructions

__all__ = [
    "CampaignHints",
    "NormalizedSpec",
    "normalize",
    "parse_instructions",
]
