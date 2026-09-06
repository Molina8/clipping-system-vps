"""Pydantic schemas for Candidate."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VALID_STATUSES = {"pending", "approved", "rejected", "rendered", "superseded"}


class CandidateCreate(BaseModel):
    campaign_id: int
    asset_id: uuid.UUID
    start_time: float = Field(..., ge=0)
    end_time: float = Field(..., gt=0)
    score: Optional[float] = Field(None, ge=0, le=1)
    reasoning: Optional[str] = None
    extra_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be > start_time")
        return self


class CandidateUpdate(BaseModel):
    status: Optional[str] = None
    score: Optional[float] = Field(None, ge=0, le=1)
    reasoning: Optional[str] = None
    extra_metadata: Optional[dict[str, Any]] = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
        return v


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: int
    asset_id: uuid.UUID
    start_time: float
    end_time: float
    score: Optional[float] = None
    reasoning: Optional[str] = None
    extra_metadata: dict
    status: str
    created_at: datetime
    updated_at: datetime
