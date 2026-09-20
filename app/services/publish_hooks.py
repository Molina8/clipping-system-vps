"""Hooks after a publish job completes. Dry-run does not move files."""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.models.clip_publication import ClipPublication
from app.models.job import Job

logger = logging.getLogger(__name__)


def on_publish_completed(db: Session, job: Job, result_data: dict) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    pub_id = payload.get("publication_id")
    dry = bool((result_data or {}).get("dry_run") or payload.get("dry_run"))
    pub = None
    if pub_id:
        try:
            pub = db.get(ClipPublication, uuid.UUID(str(pub_id)))
        except (TypeError, ValueError):
            pub = None
    if pub is None:
        logger.info("publish job %s done dry_run=%s (no publication row)", job.id, dry)
        return
    pubs = (result_data or {}).get("publications") or []
    first = pubs[0] if pubs else {}
    if dry:
        pub.error_message = "dry_run"
        if first.get("post_url"):
            pub.post_url = first["post_url"]
        db.commit()
        logger.info("publish job %s dry-run recorded on publication %s", job.id, pub.id)
        return
    status = (first.get("status") or "").lower()
    if status == "posted":
        pub.status = "posted"
        pub.post_url = first.get("post_url")
        from datetime import datetime, timezone
        pub.posted_at = datetime.now(timezone.utc)
    elif status == "failed":
        pub.status = "failed"
        pub.error_message = first.get("error") or job.error_message
    db.commit()
