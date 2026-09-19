#!/usr/bin/env python3
"""Re-enqueue download jobs for assets whose Worker temp .bin is gone.

Cancels leftover failed/pending transcribe jobs for the same asset.
Does not touch assets that already have a pending/processing download.
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
    from app.models.campaign import Campaign  # noqa: F401
    from app.models.asset import Asset
    from app.models.job import Job
    from app.services.job_service import create_job

    db = SessionLocal()
    created = 0
    skipped = 0
    cancelled = 0
    try:
        assets = (
            db.query(Asset)
            .filter(Asset.campaign_id == 6)
            .filter(Asset.asset_type != "folder")
            .order_by(Asset.created_at.asc())
            .limit(args.limit)
            .all()
        )
        for asset in assets:
            meta = dict(asset.extra_metadata or {})
            if meta.get("kind") in {"drive_folder", "brand_asset"}:
                skipped += 1
                continue
            if meta.get("skip_download") and meta.get("last_error_kind") not in {
                "unsupported_media_format",
                None,
            }:
                skipped += 1
                continue

            inflight = (
                db.query(Job)
                .filter(Job.job_type == "download")
                .filter(Job.payload["asset_id"].astext == str(asset.id))
                .filter(Job.status.in_(("pending", "assigned", "processing")))
                .first()
            )
            if inflight is not None:
                skipped += 1
                continue

            stale = (
                db.query(Job)
                .filter(Job.job_type == "transcribe")
                .filter(Job.payload["asset_id"].astext == str(asset.id))
                .filter(Job.status.in_(("pending", "failed")))
                .all()
            )
            print(f"redownload asset={asset.id} name={meta.get('name')} stale_transcribe={len(stale)}")
            if args.dry_run:
                created += 1
                continue

            for job in stale:
                job.status = "cancelled"
                cancelled += 1

            meta.pop("skip_download", None)
            meta.pop("last_error_kind", None)
            meta.pop("last_error_detail", None)
            meta.pop("terminal_failed_at", None)
            asset.extra_metadata = meta
            asset.status = "pending"
            asset.local_path = None

            create_job(
                db,
                job_type="download",
                payload={
                    "campaign_id": str(asset.campaign_id),
                    "asset_id": str(asset.id),
                    "url": asset.source_url,
                    "source_url": asset.source_url,
                    "filename": meta.get("name"),
                    "kind": meta.get("kind"),
                },
                priority=5,
                max_attempts=3,
            )
            created += 1
        if not args.dry_run:
            db.commit()
        print(
            f"requeue_download_missing created={created} "
            f"cancelled_transcribe={cancelled} skipped={skipped} dry_run={args.dry_run}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
