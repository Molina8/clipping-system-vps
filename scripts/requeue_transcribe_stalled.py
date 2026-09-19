#!/usr/bin/env python3
"""Re-queue transcribe jobs for assets blocked by the .bin false positive.

Safe to re-run: skips assets that already have a transcribe job.
Does not re-download.

Usage (VPS):
    cd /opt/clipping-system && source venv/bin/activate
    python scripts/requeue_transcribe_stalled.py --dry-run
    python scripts/requeue_transcribe_stalled.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.asset import Asset
    from app.models.job import Job
    from app.services.job_service import create_job

    db = SessionLocal()
    created = 0
    skipped = 0
    try:
        assets = (
            db.query(Asset)
            .filter(Asset.status.in_(("downloaded", "failed")))
            .filter(Asset.asset_type != "folder")
            .order_by(Asset.created_at.asc())
            .limit(args.limit)
            .all()
        )
        for asset in assets:
            meta = dict(asset.extra_metadata or {})
            detail = (meta.get("last_error_kind") or "")
            path = asset.local_path or meta.get("file_path_reported") or ""
            if detail != "unsupported_media_format" and ".bin" not in path:
                skipped += 1
                continue
            if not path:
                skipped += 1
                continue
            existing = (
                db.query(Job)
                .filter(Job.job_type == "transcribe")
                .filter(Job.payload["asset_id"].astext == str(asset.id))
                .filter(Job.status.in_(("pending", "assigned", "processing", "completed")))
                .first()
            )
            if existing is not None:
                skipped += 1
                continue
            print(f"requeue asset={asset.id} path={path[:80]}")
            if args.dry_run:
                created += 1
                continue
            meta.pop("skip_download", None)
            meta.pop("last_error_kind", None)
            meta.pop("last_error_detail", None)
            meta.pop("terminal_failed_at", None)
            asset.extra_metadata = meta
            if not asset.local_path and path:
                asset.local_path = path
            asset.status = "downloaded"
            create_job(
                db,
                job_type="transcribe",
                payload={
                    "asset_id": str(asset.id),
                    "campaign_id": str(asset.campaign_id),
                    "video": asset.local_path,
                    "video_path": asset.local_path,
                    "source_url": asset.source_url,
                    "language": meta.get("language"),
                },
                priority=5,
                max_attempts=3,
            )
            created += 1
        if not args.dry_run:
            db.commit()
        print(f"requeue_transcribe_stalled created={created} skipped={skipped} dry_run={args.dry_run}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
