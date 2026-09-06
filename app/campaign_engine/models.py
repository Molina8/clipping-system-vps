"""Campaign Engine: Pydantic models for parsed instructions.

Architecture_flow.md Steps 3 + 13: OpenClaw (with MiniMax) takes the
free-form `source_instructions` from a Campaign and produces a structured
CampaignSpec. The campaign_engine module gives the parsing + normalization
a typed shape so OpenClaw can iterate quickly.

Two stages:
  - Hints  (parser output, from natural-language instructions)
  - NormalizedSpec (rule_normalizer output, what gets stored in Campaign.spec)

The parser is rule-based for now (keyword extraction). The rule_normalizer
fills in sensible defaults per source_provider. Real implementation will
call Mini­Max for richer extraction.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CampaignHints(BaseModel):
    """Structured extraction from free-form source_instructions.

    Produced by `parser.parse_instructions(text)`. The agent (or
    Mini­Max in real life) fills this in; the rule_normalizer then
    turns it into a final CampaignSpec.

    Note: the normalizer is the one that enforces the duration window
    (duration_max > duration_min). The parser allows any values so
    tests can verify the normalizer's fix-up logic.
    """
    # Duration window in seconds
    duration_min: Optional[float] = Field(
        None, ge=0, description="Minimum clip duration in seconds"
    )
    duration_max: Optional[float] = Field(
        None, gt=0, description="Maximum clip duration in seconds"
    )
    # Captions / subtitles
    captions_required: bool = False
    # Watermark
    watermark_url: Optional[str] = None
    # Aspect ratio: 9:16, 1:1, 16:9, etc.
    format: Optional[str] = None
    # Language
    language: Optional[str] = None
    # Topic filters
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    # Free-form leftovers
    extra_notes: list[str] = Field(default_factory=list)


class NormalizedSpec(BaseModel):
    """Final spec stored in Campaign.spec. Output of rule_normalizer."""
    duration_min: float
    duration_max: float
    captions_required: bool = False
    watermark_url: Optional[str] = None
    format: str = "9:16"  # default vertical shorts
    language: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    source_provider: str  # carried over from the campaign
    extra: dict = Field(default_factory=dict)
