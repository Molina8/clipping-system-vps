#!/usr/bin/env python3
"""Download enqueue tick — paso 7. Run from systemd/cron, not OpenClaw."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=os.environ.get("DOWNLOAD_ENQUEUE_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "download_enqueue_tick.log")],
)
logger = logging.getLogger("download_enqueue_tick")

SCORED_STATUSES = ("scored", "ready")
_SKIP_KINDS = frozenset({
    "drive_folder", "dropbox_folder", "brand_asset",
    "youtube_profile", "twitter_profile", "tiktok_profile",
    "instagram_profile", "profile",
})
_SKIP_URL_PATTERNS = (
    "/drive/folders/", "/drive/u/", "/document/d/", "/documents/d/",
    "/forms/d/", "/spreadsheets/d/", "/presentation/d/", "/file/d/",
    "/folders/", "@",
)


def _is_skippable(a) -> bool:
    if a.asset_type in {"folder", "brand_asset"}:
        return True
    meta = a.extra_metadata or {}
    if meta.get("skip_download") is True:
        return True
    if meta.get("kind") in _SKIP_KINDS:
        return True
    url = (a.source_url or "").lower()
    return any(pat in url for pat in _SKIP_URL_PATTERNS)


def _has_download_job(db, asset_id: str) -> bool:
    from app.models.job import Job
    return (
        db.query(Job)
        .filter(Job.job_type == "download")
        .filter(Job.payload["asset_id"].astext == asset_id)
        .first()
    ) is not None


def _enqueue_one(db, campaign, asset, dry_run: bool) -> dict:
    from app.models.job import Job
    asset_id = str(asset.id)
    if dry_run:
        return {"created": 1, "asset_id": asset_id, "dry_run": True}
    job = Job(
        id=uuid.uuid4(),
        job_type="download",
        status="pending",
        priority=5,
        payload={
            "campaign_id": str(campaign.id),
            "asset_id": asset_id,
            "url": asset.source_url,
            "source_url": asset.source_url,
            "filename": (asset.extra_metadata or {}).get("name"),
            "kind": (asset.extra_metadata or {}).get("kind"),
        },
        max_attempts=3,
    )
    db.add(job)
    db.commit()
    return {"created": 1, "asset_id": asset_id, "job_id": str(job.id)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1, help="max download jobs this run")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.models.asset import Asset
    from app.api.campaigns import _is_real_video_url

    db = SessionLocal()
    enqueued = 0
    scanned = 0
    try:
        campaigns = (
            db.query(Campaign)
            .filter(Campaign.status.in_(SCORED_STATUSES))
            .filter(Campaign.source_provider != "manual")
            .order_by(Campaign.id.asc())
            .all()
        )
        for c in campaigns:
            assets = (
                db.query(Asset)
                .filter(Asset.campaign_id == c.id)
                .filter(Asset.status == "pending")
                .order_by(Asset.created_at.asc())
                .all()
            )
            for asset in assets:
                scanned += 1
                if _is_skippable(asset):
                    continue
                if not _is_real_video_url(asset.source_url):
                    continue
                if _has_download_job(db, str(asset.id)):
                    continue
                r = _enqueue_one(db, c, asset, args.dry_run)
                enqueued += r.get("created", 0)
                print(
                    f"download_enqueue_tick campaign={c.id} "
                    f"asset={asset.id} created={r.get('created')} "
                    f"job={r.get('job_id', 'dry-run')}"
                )
                if enqueued >= args.limit:
                    return 0
        if enqueued == 0 and args.dry_run:
            print(f"download_enqueue_tick scanned={scanned} enqueued=0")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("download_enqueue_tick failed: %s", e)
        print(f"download_enqueue_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
