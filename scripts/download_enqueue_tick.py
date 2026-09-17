#!/usr/bin/env python3
"""Download enqueue tick — Paso 7 of architecture_flow.md (pipeline v2).

Standalone script (no FastAPI context) so the OpenClaw scheduler can run it
as a `command` payload under cwd=/opt/clipping-system.

What it does (architecture_flow.md step 7 — pipeline v2, 2026-09-17):
  1. Pull every campaign with status='scored' (pipeline v2) OR status='ready'
     (legacy, kept for backward compat with campaigns created before paso 3c).
  2. For each campaign: find assets with status='pending' AND no open download
     job, and enqueue one `download` job per asset using the same payload
     contract the Worker expects (download.py requires payload["url"]).
  3. Idempotent: if a download job already exists for the campaign in
     pending/processing/assigned, skip.
  4. After enqueueing, also drain pending clip selections (backlog), reusing
     process_pending_clip_selections so transcribed assets whose decider never
     ran get caught up (same piggy-back as enqueue_pipeline).

This is the **bridge** between paso 3c (campaign-scorer leaves status='scored')
and paso 8 (Worker downloads via /worker/jobs/next). Without this tick the
Worker has nothing to grab and the pipeline stalls at "scored" forever.

Output policy (kept tight to avoid spamming Telegram):
  - Logs go to `/opt/clipping-system/logs/download_enqueue_tick.log`.
  - Stderr surfaces failures so the cron announces them.
  - Stdout emits a single short line ONLY when something was enqueued or
    when an error happened. No-op runs (nothing to enqueue) print nothing
    and exit 0, so the cron announces no message to Telegram.

Usage:
    cd /opt/clipping-system && source venv/bin/activate
    python scripts/download_enqueue_tick.py                # full sweep
    python scripts/download_enqueue_tick.py --limit 25     # smaller sweep
    python scripts/download_enqueue_tick.py --dry-run      # report only, no enqueue
"""
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
    handlers=[
        logging.FileHandler(LOG_DIR / "download_enqueue_tick.log"),
    ],
)
logger = logging.getLogger("download_enqueue_tick")


# Statuses that this tick considers "ready to download".
# 'scored' is the pipeline-v2 terminal state from paso 3c.
# 'ready' is the legacy state from the old analyze path; we keep it
# so campaigns that never went through paso 3c still flow.
SCORED_STATUSES = ("scored", "ready")


def _is_real_video_url(url: str) -> bool:
    """Mirror of app/api/campaigns.py::_is_real_video_url — kept local
    so this script has zero dependency on the FastAPI app imports."""
    if not url:
        return False
    from urllib.parse import urlparse

    p = urlparse(url)
    host = (p.netloc or "").lower()
    path = (p.path or "").lower()

    # Direct media extensions
    media_ext = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
    if any(path.endswith(ext) for ext in media_ext):
        return True

    # Processable hosts
    processable_hosts = (
        "youtube.com", "youtu.be",
        "drive.google.com",
        "dropbox.com", "mega.nz", "vimeo.com",
    )
    bare_profile_hosts = ("instagram.com", "tiktok.com")

    for h in processable_hosts:
        if host == h or host.endswith("." + h):
            # youtube bare profile reject
            if h == "youtube.com" and path in ("", "/"):
                return False
            return True

    # Bare profile pages are NOT processable
    for h in bare_profile_hosts:
        if host == h or host.endswith("." + h):
            if "/p/" in path or "/reel/" in path or "/video/" in path:
                return True
            return False

    return False


def _enqueue_download_for_campaign(db, campaign_id: int, dry_run: bool) -> dict:
    """Create a download job for the first processable asset of a campaign.

    Returns {"created": int, "skipped": int, "asset_id": str|None}.
    Mirrors the logic in app/api/campaigns.py::enqueue_pipeline but called
    out-of-band so the OpenClaw cron can drive it without HTTP.
    """
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.job import Job

    c = db.get(Campaign, campaign_id)
    if c is None:
        return {"created": 0, "skipped": 0, "asset_id": None, "error": "campaign_not_found"}

    # Pick the first processable asset (mirror enqueue_pipeline logic).
    candidates = (
        db.query(Asset)
        .filter(Asset.campaign_id == campaign_id)
        .filter(Asset.status == "pending")
        .order_by(Asset.created_at.asc())
        .all()
    )
    asset = next((a for a in candidates if _is_real_video_url(a.source_url)), None)
    if asset is None:
        return {"created": 0, "skipped": 0, "asset_id": None,
                "skipped_reason": "no_processable_asset"}

    asset_id = str(asset.id)

    # Idempotency: don't create a new pending job if one is open.
    existing = (
        db.query(Job)
        .filter(Job.job_type == "download")
        .filter(Job.payload["campaign_id"].astext == str(c.id))
        .filter(Job.status.in_(("pending", "processing", "assigned")))
        .first()
    )
    if existing is not None:
        return {"created": 0, "skipped": 1, "asset_id": asset_id,
                "existing_job_id": str(existing.id)}

    if dry_run:
        return {"created": 1, "skipped": 0, "asset_id": asset_id, "dry_run": True}

    payload = {
        "campaign_id": str(c.id),
        "asset_id": asset_id,
        "url": asset.source_url,                # Worker contract (download.py)
        "source_url": asset.source_url,         # backwards-compat
        "destination": f"/tmp/cs_{c.id}_video.mp4",
    }
    job = Job(
        id=uuid.uuid4(),
        job_type="download",
        status="pending",
        priority=5,
        payload=payload,
        max_attempts=3,
    )
    db.add(job)
    db.commit()

    return {"created": 1, "skipped": 0, "asset_id": asset_id, "job_id": str(job.id)}


def main() -> int:
    p = argparse.ArgumentParser(description="Download enqueue tick (paso 7)")
    p.add_argument("--limit", type=int, default=50, help="max campaigns per run")
    p.add_argument("--dry-run", action="store_true",
                   help="report only, do not enqueue jobs")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign

    db = SessionLocal()
    summary = {
        "scanned": 0,
        "enqueued": 0,
        "skipped": 0,
        "by_status": {s: 0 for s in SCORED_STATUSES},
        "campaigns": [],
        "dry_run": args.dry_run,
        "limit": args.limit,
    }
    try:
        # 1) Scored (pipeline v2) + ready (legacy) campaigns.
        scored = (
            db.query(Campaign)
            .filter(Campaign.status.in_(SCORED_STATUSES))
            .filter(Campaign.source_provider != "manual")  # don't re-enqueue manual
            .order_by(Campaign.id.asc())
            .limit(args.limit)
            .all()
        )
        summary["scanned"] = len(scored)
        logger.info(
            "download_enqueue_tick: %d candidates (status in %s)",
            len(scored), SCORED_STATUSES,
        )

        # 2) Enqueue download job per campaign.
        for c in scored:
            try:
                r = _enqueue_download_for_campaign(db, c.id, dry_run=args.dry_run)
            except Exception as e:  # noqa: BLE001
                logger.exception("enqueue failed for campaign %s: %s", c.id, e)
                summary["skipped"] += 1
                summary["campaigns"].append(
                    {"id": c.id, "name": c.name, "status": c.status, "error": str(e)}
                )
                continue

            summary["by_status"][c.status] = summary["by_status"].get(c.status, 0) + 1
            summary["campaigns"].append(
                {"id": c.id, "name": c.name, "status": c.status, "result": r}
            )
            if r.get("created", 0) > 0:
                summary["enqueued"] += 1
            else:
                summary["skipped"] += 1

        # 3) Piggy-back: drain pending clip selections so transcribed assets
        #    whose decider never ran get caught up.
        try:
            from app.clip_selection.processor import process_pending_clip_selections
            backlog = process_pending_clip_selections(db, limit=10)
            summary["backlog_clip_selection"] = backlog
        except Exception as e:  # noqa: BLE001
            logger.warning("backlog clip_selection drain skipped: %s", e)
            summary["backlog_clip_selection"] = {"error": str(e)}

        # Only print if something happened.
        if summary["enqueued"] > 0 or args.dry_run:
            print(
                f"download_enqueue_tick: "
                f"scanned={summary['scanned']} "
                f"enqueued={summary['enqueued']} "
                f"skipped={summary['skipped']} "
                f"by_status={summary['by_status']}"
            )
        # else: silent — no-op tick, no Telegram spam.

        logger.info(
            "download_enqueue_tick done: %s",
            {k: v for k, v in summary.items() if k != "campaigns"},
        )
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("download_enqueue_tick failed: %s", e)
        summary["error"] = f"{type(e).__name__}: {e}"

        print(f"download_enqueue_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
