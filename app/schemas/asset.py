"""Pydantic schemas for assets."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


VALID_STATUSES = {"pending", "downloaded", "transcribed", "failed"}


class AssetCreate(BaseModel):
    """Payload to register a single asset (Step 6 in architecture_flow.md)."""
    campaign_id: int
    source_url: str = Field(..., min_length=1, max_length=2048)
    source_id: Optional[str] = Field(None, max_length=256)
    source_provider: Optional[str] = Field(None, max_length=64)
    asset_type: str = Field(default="video", max_length=64)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://") or v.startswith("/") or v.startswith("file://")):
            raise ValueError(
                "source_url must start with http://, https://, /, or file://"
            )
        return v


class AssetBulkCreate(BaseModel):
    """Bulk payload from Asset Resolver (Step 5+6 combined)."""
    assets: list[AssetCreate]


class AssetUpdate(BaseModel):
    """Patch for status transitions or metadata updates."""
    status: Optional[str] = None
    local_path: Optional[str] = Field(None, max_length=1024)
    file_size: Optional[int] = None
    duration_seconds: Optional[float] = None
    sha256: Optional[str] = Field(None, max_length=64)
    mime_type: Optional[str] = Field(None, max_length=128)
    extra_metadata: Optional[dict[str, Any]] = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_STATUSES)}"
            )
        return v


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: int
    source_url: str
    source_id: Optional[str] = None
    source_provider: Optional[str] = None
    asset_type: str
    status: str
    local_path: Optional[str] = None
    file_size: Optional[int] = None
    duration_seconds: Optional[float] = None
    sha256: Optional[str] = None
    mime_type: Optional[str] = None
    extra_metadata: dict
    created_at: datetime
    updated_at: datetime
    downloaded_at: Optional[datetime] = None
    transcribed_at: Optional[datetime] = None
