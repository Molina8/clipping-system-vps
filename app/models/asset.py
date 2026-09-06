"""Asset model — represents a source video/file from a platform.

Lifecycle per architecture_flow.md:
  pending (Step 6) -> downloaded (Step 9) -> transcribed (Step 11)

Clip states (rejected/approved/review/published) belong to generated
CLIPS, not source assets. Those will come with the Clips model in Phase C.

The 'extra_metadata' JSONB holds provider-specific fields (tweet text,
youtube title/duration, hashtags, etc.). The VPS does NOT store the
video bytes — local_path lives on the Worker.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AssetStatus(str, enum.Enum):
    PENDING = "pending"         # Asset Resolver registered it, no Worker yet
    DOWNLOADED = "downloaded"   # Worker finished DOWNLOAD job
    TRANSCRIBED = "transcribed" # Worker finished TRANSCRIBE job
    FAILED = "failed"           # Any intermediate job ended in `failed`


ASSET_STATUS_VALUES = tuple(s.value for s in AssetStatus)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    campaign_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Source / provider info
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Can differ from campaign.source_provider (e.g. campaign = twitter,
    # individual asset from a youtube link mentioned in a tweet)
    asset_type: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="video"
    )

    # State
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )

    # Local-side info (filled by Worker via /worker/jobs/{id}/result)
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Provider-specific metadata (tweet text, youtube title, hashtags, etc.)
    extra_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
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
    downloaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transcribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN {ASSET_STATUS_VALUES!r}",
            name="ck_assets_status",
        ),
    )
