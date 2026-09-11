"""Step 18 of architecture_flow.md: track where each clip lives on the Worker.

The Windows Worker organizes clips in per-campaign folders:

    C:\\CODIANT\\clipping\\storage\\clips\\<campaign_id>\\
        pending_upload\\<clip_id>.mp4   <- QA passed, awaiting social-media upload
        uploaded\\<clip_id>.mp4         <- already published
        archived\\<clip_id>.mp4         <- taken out of rotation

The VPS only stores the resulting path in `clips.location` /
`clips.final_path_worker` / `clips.location_updated_at`. The Worker is
responsible for the actual file I/O (copy / move between folders).

Public API:
    set_clip_location(db, clip_id, location, final_path) -> Optional[Clip]
    mark_clip_uploaded(db, clip_id, final_path=None) -> Optional[Clip]
    list_clips_by_campaign(db, campaign_id, location=None) -> list[Clip]
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clip import Clip

logger = logging.getLogger(__name__)


VALID_LOCATIONS = ("pending_upload", "uploaded", "archived")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def set_clip_location(
    db: Session,
    clip_id: uuid.UUID,
    location: str,
    final_path_worker: Optional[str] = None,
) -> Optional[Clip]:
    """Record that the clip lives in `location` (and at `final_path_worker`).

    Used by `on_qa_completed` (qa=pass -> 'pending_upload') and by
    `mark_clip_uploaded` (after social-media publication -> 'uploaded').

    Idempotent: re-calling with the same location is a no-op aside from
    refreshing `final_path_worker` and `location_updated_at`.
    """
    if location not in VALID_LOCATIONS:
        raise ValueError(
            f"location must be one of {VALID_LOCATIONS}, got {location!r}"
        )

    clip = db.get(Clip, clip_id)
    if clip is None:
        logger.warning("set_clip_location: clip %s not found", clip_id)
        return None

    prev_location = clip.location
    clip.location = location
    if final_path_worker is not None:
        clip.final_path_worker = final_path_worker
    clip.location_updated_at = _now()

    db.commit()
    db.refresh(clip)

    logger.info(
        "clip %s location: %s -> %s (path=%s)",
        clip.id, prev_location, location, clip.final_path_worker,
    )
    return clip


def mark_clip_uploaded(
    db: Session,
    clip_id: uuid.UUID,
    final_path_worker: Optional[str] = None,
) -> Optional[Clip]:
    """Mark a clip as uploaded to social media.

    Sets `location='uploaded'`. Optionally updates `final_path_worker` if
    the Worker moved the file to the `uploaded/` subfolder. Also stamps
    `clips.published_at` (the existing publish marker used elsewhere).
    """
    clip = set_clip_location(db, clip_id, "uploaded", final_path_worker)
    if clip is None:
        return None
    # Keep published_at in sync with the rest of the codebase's publish marker.
    clip.published_at = _now()
    db.commit()
    db.refresh(clip)
    logger.info("clip %s marked uploaded (published_at=%s)", clip.id, clip.published_at)
    return clip


def list_clips_by_campaign(
    db: Session,
    campaign_id: int,
    location: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Clip]:
    """List clips for a campaign, optionally filtered by storage location."""
    stmt = select(Clip).where(Clip.campaign_id == campaign_id)
    if location is not None:
        if location not in VALID_LOCATIONS:
            raise ValueError(
                f"location must be one of {VALID_LOCATIONS}, got {location!r}"
            )
        stmt = stmt.where(Clip.location == location)
    stmt = stmt.order_by(Clip.location_updated_at.desc().nullslast(), Clip.updated_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())
