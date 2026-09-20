"""One publication attempt per (clip, platform). Submit Whop lives here too."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

from app.models.social_account import SOCIAL_PLATFORM_VALUES


class PublicationStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED = "skipped"


PUBLICATION_STATUS_VALUES = tuple(s.value for s in PublicationStatus)


class SubmitStatus(str, enum.Enum):
    NOT_NEEDED = "not_needed"
    PENDING = "pending"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    FAILED = "failed"


SUBMIT_STATUS_VALUES = tuple(s.value for s in SubmitStatus)


class ClipPublication(Base):
    __tablename__ = "clip_publications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clips.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    post_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)

    submit_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submit_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    submit_error: Mapped[str | None] = mapped_column(String, nullable=True)

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
        UniqueConstraint("clip_id", "platform", name="uq_clip_publications_clip_platform"),
        CheckConstraint(
            f"platform IN {SOCIAL_PLATFORM_VALUES!r}",
            name="ck_clip_publications_platform",
        ),
        CheckConstraint(
            f"status IN {PUBLICATION_STATUS_VALUES!r}",
            name="ck_clip_publications_status",
        ),
        CheckConstraint(
            f"submit_status IN {SUBMIT_STATUS_VALUES!r}",
            name="ck_clip_publications_submit_status",
        ),
    )
