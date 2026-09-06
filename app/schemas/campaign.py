"""Pydantic schemas for campaigns.

CampaignSpec is provider-agnostic (Step 3 of architecture_flow.md).
Source info (provider, id, url, metadata) tells OpenClaw/MiniMax where
the campaign came from so it can extract rules differently per source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Source providers we currently support
ALLOWED_SOURCES = {
    "twitter", "youtube", "instagram", "tiktok",
    "reddit", "twitch", "manual", "other",
}


class CampaignSpec(BaseModel):
    """Provider-agnostic normalized rules — same shape regardless of source."""
    duration_min: Optional[float] = None
    duration_max: Optional[float] = None
    captions_required: bool = False
    watermark_url: Optional[str] = None
    format: Optional[str] = None  # e.g. "9:16", "1:1", "16:9"
    language: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    # Future-proofing for richer specs (campaign-specific rules, asset filters, etc.)
    extra: dict[str, Any] = Field(default_factory=dict)


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    # source_* are optional but source_provider defaults to 'manual'
    source_provider: str = "manual"
    source_id: Optional[str] = Field(None, max_length=256)
    source_url: Optional[str] = Field(None, max_length=2048)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    source_instructions: Optional[str] = None
    spec: Optional[CampaignSpec] = None

    @field_validator("source_provider")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if v not in ALLOWED_SOURCES:
            raise ValueError(
                f"source_provider must be one of {sorted(ALLOWED_SOURCES)}"
            )
        return v


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=256)
    source_instructions: Optional[str] = None
    status: Optional[str] = None
    spec: Optional[CampaignSpec] = None
    source_metadata: Optional[dict[str, Any]] = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    status: str
    source_provider: str
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    source_metadata: dict
    source_instructions: Optional[str] = None
    spec: dict
    assets_count: int
    clips_approved: int
    clips_published: int
    created_at: datetime
    updated_at: datetime
