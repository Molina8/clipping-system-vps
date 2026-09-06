"""Campaign model — defines rules for what clips to produce.

Campaigns can come from various providers/websites
(twitter, youtube, instagram, tiktok, etc.). The source_provider +
source_id + source_url + source_metadata fields track where each
campaign came from so OpenClaw/MiniMax can extract rules differently
per source.

The 'spec' field (JSONB) holds the normalized CampaignSpec — a
provider-agnostic structure of clip rules (duration, format, captions, etc.).
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class CampaignSource(str, enum.Enum):
    """Where the campaign came from. OpenClaw/MiniMax may need different
    extraction/parsing logic per source."""
    TWITTER = "twitter"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    REDDIT = "reddit"
    TWITCH = "twitch"
    MANUAL = "manual"   # created via API by user
    OTHER = "other"


CAMPAIGN_SOURCE_VALUES = tuple(s.value for s in CampaignSource)


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"           # created, not yet analyzed
    ANALYZING = "analyzing"   # OpenClaw/MiniMax processing
    READY = "ready"           # spec generated, assets can be resolved
    ACTIVE = "active"         # jobs flowing
    PAUSED = "paused"         # user-paused
    COMPLETED = "completed"   # all clips done
    ARCHIVED = "archived"     # soft-deleted


CAMPAIGN_STATUS_VALUES = tuple(s.value for s in CampaignStatus)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="draft"
    )

    # Source/provider info — campaigns can come from various websites
    source_provider: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=CampaignSource.MANUAL.value
    )
    source_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    source_instructions: Mapped[str | None] = mapped_column(String, nullable=True)
    # CampaignSpec — provider-agnostic normalized rules
    spec: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Stats — denormalized counters updated by job_service
    assets_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    clips_approved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    clips_published: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

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
            f"status IN {CAMPAIGN_STATUS_VALUES!r}",
            name="ck_campaigns_status",
        ),
        CheckConstraint(
            f"source_provider IN {CAMPAIGN_SOURCE_VALUES!r}",
            name="ck_campaigns_source_provider",
        ),
    )
