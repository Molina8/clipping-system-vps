"""Clip model — represents a generated clip (output of a RENDER job).

Per architecture_flow.md Steps 16-19:
  Step 16 [WORKER]: FFmpeg cuts and produces CLIP FINAL
  Step 17 [VPS]: registra clip, crea QA JOB
  Step 18 [WORKER]: FFprobe + QA checks
  Step 19 [VPS]: guarda QA result -> PASS/FAIL/REVIEW

Clip states (post-render):
  created  -> render job finished, clip materialized
  approved -> QA PASS
  rejected -> QA FAIL
  review   -> QA REVIEW (needs human/OpenClaw)
  published -> OpenClaw confirmed publication (Step 21)
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


class ClipQAStatus(str, enum.Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"


CLIP_QA_STATUS_VALUES = tuple(s.value for s in ClipQAStatus)


class ClipStatus(str, enum.Enum):
    CREATED = "created"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVIEW = "review"
    PUBLISHED = "published"


CLIP_STATUS_VALUES = tuple(s.value for s in ClipStatus)


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The job that produced this clip (render_job)
    render_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    qa_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Output
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_size: Mapped[int | None] = mapped_column(
        sa_column_int := None,  # avoid lint - replaced below
        nullable=True,
    )

    # QA
    qa_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    qa_result: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Top-level status (derived from qa + manual decisions)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="created"
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    qa_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"qa_status IN {CLIP_QA_STATUS_VALUES!r}",
            name="ck_clips_qa_status",
        ),
        CheckConstraint(
            f"status IN {CLIP_STATUS_VALUES!r}",
            name="ck_clips_status",
        ),
    )
