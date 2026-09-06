"""Candidate model — represents a candidate clip that OpenClaw picks.

Per architecture_flow.md Steps 13-14:
  Step 13 [OPENCLAW — MINIMAX]: genera CANDIDATOS
  Step 14 [OPENCLAW — CRON/AGENTE]: guarda candidatos, verifica reglas

A candidate is a (campaign, asset, start_time, end_time) tuple with
optional LLM reasoning + score. When OpenClaw decides the candidate passes
campaign rules, a RENDER job is created (Step 15) and a Clip record is
materialized when the render completes (Step 17).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CandidateStatus(str, enum.Enum):
    PENDING = "pending"                       # OpenClaw stored it, rules not checked yet
    APPROVED = "approved"                     # passed campaign rules, ready for render
    REJECTED = "rejected"                     # failed rules, won't be rendered
    RENDERED = "rendered"                     # render job dispatched/finished
    SUPERSEDED = "superseded"                 # another candidate overrode this one


CANDIDATE_STATUS_VALUES = tuple(s.value for s in CandidateStatus)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The clip segment in the source video (seconds)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)

    # LLM rationale
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0.0-1.0
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # State
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN {CANDIDATE_STATUS_VALUES!r}",
            name="ck_candidates_status",
        ),
        CheckConstraint(
            "end_time > start_time",
            name="ck_candidates_time_order",
        ),
    )
