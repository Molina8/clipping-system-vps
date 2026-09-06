"""Pydantic models for the clip selection LLM call.

The LLM is asked to read a transcription + a CampaignSpec and propose
several candidate clip segments. The output is structured as JSON with
shape `{ proposals: [ ClipProposal, ... ], notes: str | null }`.

Each `ClipProposal` is validated by `validator.validate_proposal` against
a `NormalizedSpec` before being persisted as a Candidate (or rejected).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ClipProposal(BaseModel):
    """A single candidate clip proposed by the LLM (post-validation)."""

    start_time: float = Field(..., ge=0, description="Start time in seconds")
    end_time: float = Field(..., gt=0, description="End time in seconds")
    score: float = Field(..., ge=0, le=1, description="LLM confidence 0.0-1.0")
    reasoning: str = Field(..., min_length=1, description="LLM rationale")
    matched_keywords: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be > start_time")
        return self


class ClipSelectionRequest(BaseModel):
    """Input passed to the LLM client (kept for future HTTP clients)."""

    source_url: str
    duration_seconds: float
    transcription: dict
    spec: dict


class ClipSelectionResponse(BaseModel):
    """Raw LLM output, parsed into structured proposals."""

    proposals: List[ClipProposal] = Field(default_factory=list)
    model: Optional[str] = None
    raw_response: Optional[str] = None
    notes: Optional[str] = None
