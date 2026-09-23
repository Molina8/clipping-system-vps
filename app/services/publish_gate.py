"""Human gate before a clip can enter the publish enqueue tick."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.social_account import SOCIAL_PLATFORM_VALUES, SocialAccount

logger = logging.getLogger(__name__)

DEFAULT_PLATFORMS = ("youtube",)
OK_LOCATIONS = ("pending_upload", "uploaded")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PublishGateError(ValueError):
    """Raised when the clip is not eligible for publish approval."""


def list_social_accounts(db: Session) -> list[SocialAccount]:
    return list(db.execute(select(SocialAccount).order_by(SocialAccount.platform)).scalars())


def list_publications_for_clip(db: Session, clip_id: uuid.UUID) -> list[ClipPublication]:
    stmt = (
        select(ClipPublication)
        .where(ClipPublication.clip_id == clip_id)
        .order_by(ClipPublication.platform)
    )
    return list(db.execute(stmt).scalars())


def approve_clip_publish(
    db: Session,
    clip_id: uuid.UUID,
    platforms: Optional[list[str]] = None,
) -> tuple[Clip, list[ClipPublication], bool]:
    wanted = list(platforms) if platforms else list(DEFAULT_PLATFORMS)
    wanted = [p.strip().lower() for p in wanted if p and p.strip()]
    if not wanted:
        wanted = list(DEFAULT_PLATFORMS)
    for p in wanted:
        if p not in SOCIAL_PLATFORM_VALUES:
            raise PublishGateError(
                f"platform must be one of {list(SOCIAL_PLATFORM_VALUES)}, got {p!r}"
            )

    clip = db.get(Clip, clip_id)
    if clip is None:
        raise PublishGateError(f"Clip {clip_id} not found")

    if clip.qa_status != "pass":
        raise PublishGateError(
            f"clip {clip_id} qa_status={clip.qa_status!r}, need 'pass'"
        )
    if clip.status != "approved":
        raise PublishGateError(
            f"clip {clip_id} status={clip.status!r}, need 'approved'"
        )
    if clip.location not in OK_LOCATIONS:
        raise PublishGateError(
            f"clip {clip_id} location={clip.location!r}, need pending_upload|uploaded"
        )

    already = clip.publish_approved_at is not None
    if not already:
        clip.publish_approved_at = _now()

    pubs: list[ClipPublication] = []
    for platform in wanted:
        existing = db.execute(
            select(ClipPublication).where(
                ClipPublication.clip_id == clip.id,
                ClipPublication.platform == platform,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ClipPublication(
                clip_id=clip.id,
                platform=platform,
                status="pending",
                submit_status="pending",
            )
            db.add(existing)
        pubs.append(existing)

    db.commit()
    db.refresh(clip)
    for p in pubs:
        db.refresh(p)

    logger.info(
        "clip %s publish approved already=%s platforms=%s",
        clip.id, already, wanted,
    )
    return clip, pubs, already
