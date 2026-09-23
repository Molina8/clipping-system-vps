"""Create publish jobs for approve_publish clips. No LLM. Milestone 1 = youtube."""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.job import Job
from app.models.social_account import SocialAccount
from app.services.job_service import create_job
from app.services.publish_copy import youtube_copy

logger = logging.getLogger(__name__)

OPEN_JOB_STATUSES = ("pending", "assigned", "processing")
MILESTONE_PLATFORM = "youtube"
OK_LOCATIONS = ("pending_upload", "uploaded")


def _env_dry_run(cli_dry_run: bool) -> bool:
    if cli_dry_run:
        return True
    raw = (os.environ.get("PUBLISH_DRY_RUN") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _blocks_requeue(db: Session, clip_id: uuid.UUID, platform: str, *, live: bool) -> bool:
    rows = (
        db.query(Job)
        .filter(Job.job_type == "publish")
        .filter(Job.payload["clip_id"].astext == str(clip_id))
        .filter(Job.payload["platform"].astext == platform)
        .all()
    )
    for job in rows:
        if job.status in OPEN_JOB_STATUSES:
            return True
        if job.status == "failed" and not live:
            return True
        if job.status == "completed":
            res = job.result if isinstance(job.result, dict) else {}
            was_dry = bool(res.get("dry_run", True))
            if live and was_dry:
                continue
            if res.get("source_moved") or not was_dry:
                return True
    return False


def enqueue_publish_jobs(
    db: Session,
    *,
    limit: int = 1,
    platform: str = MILESTONE_PLATFORM,
    dry_run: bool = False,
    create_jobs: bool = True,
) -> list[dict]:
    payload_dry = _env_dry_run(dry_run)
    live = not payload_dry
    account = db.get(SocialAccount, platform)
    if account is None or account.status != "healthy":
        logger.warning("publish enqueue skipped: social_accounts.%s missing/unhealthy", platform)
        return [{
            "skipped": True,
            "reason": f"blocked_missing_account:{platform}",
        }]

    pubs = list(
        db.execute(
            select(ClipPublication)
            .where(ClipPublication.platform == platform)
            .where(ClipPublication.status == "pending")
            .order_by(ClipPublication.created_at.asc())
        ).scalars()
    )

    out: list[dict] = []
    made = 0
    for pub in pubs:
        if made >= limit:
            break
        clip = db.get(Clip, pub.clip_id)
        if clip is None:
            continue
        if clip.publish_approved_at is None:
            continue
        if clip.location not in OK_LOCATIONS:
            continue
        if clip.qa_status != "pass" or clip.status != "approved":
            continue
        if _blocks_requeue(db, clip.id, platform, live=live):
            continue

        campaign = db.get(Campaign, clip.campaign_id)
        title, description, hashtags = youtube_copy(campaign, clip)
        file_path = clip.final_path_worker or clip.file_path
        payload = {
            "clip_id": str(clip.id),
            "campaign_id": clip.campaign_id,
            "publication_id": str(pub.id),
            "platform": platform,
            "file_path": file_path,
            "platforms": [platform],
            "caption": description,
            "hashtags": hashtags,
            "mentions": [],
            "title": title,
            "dry_run": payload_dry,
        }
        row = {
            "clip_id": str(clip.id),
            "campaign_id": clip.campaign_id,
            "platform": platform,
            "file_path": file_path,
            "title": title,
            "dry_run": payload_dry,
        }
        if not create_jobs:
            row["job_id"] = None
            row["would_create"] = True
            out.append(row)
            made += 1
            continue

        job = create_job(db, job_type="publish", payload=payload, priority=4, max_attempts=3)
        pub.job_id = job.id
        db.commit()
        row["job_id"] = str(job.id)
        out.append(row)
        made += 1
        logger.info(
            "enqueued publish job %s clip=%s platform=%s dry_run=%s title=%s",
            job.id, clip.id, platform, payload_dry, title,
        )
    return out
