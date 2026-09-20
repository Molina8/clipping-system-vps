"""One connected social account per platform. No tokens in this table."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class SocialPlatform(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


SOCIAL_PLATFORM_VALUES = tuple(s.value for s in SocialPlatform)


class SocialAccountStatus(str, enum.Enum):
    HEALTHY = "healthy"
    EXPIRED = "expired"
    DISABLED = "disabled"


SOCIAL_ACCOUNT_STATUS_VALUES = tuple(s.value for s in SocialAccountStatus)


class SocialAuthKind(str, enum.Enum):
    OAUTH = "oauth"
    BROWSER_PROFILE = "browser_profile"


SOCIAL_AUTH_KIND_VALUES = tuple(s.value for s in SocialAuthKind)


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    handle: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="healthy"
    )
    auth_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="oauth"
    )
    checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
            f"platform IN {SOCIAL_PLATFORM_VALUES!r}",
            name="ck_social_accounts_platform",
        ),
        CheckConstraint(
            f"status IN {SOCIAL_ACCOUNT_STATUS_VALUES!r}",
            name="ck_social_accounts_status",
        ),
        CheckConstraint(
            f"auth_kind IN {SOCIAL_AUTH_KIND_VALUES!r}",
            name="ck_social_accounts_auth_kind",
        ),
    )
