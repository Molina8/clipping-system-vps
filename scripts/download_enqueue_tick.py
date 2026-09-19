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
    """Import the canonical filter from app.api.campaigns so this script
    never drifts from what the API endpoint considers processable.

    The local copy used to live here but it diverged: it accepted
    https://www.youtube.com/@WhopIO (bare profile) which the API rejects.
    Importing the canonical function eliminates that whole class of bugs.
    """
    from app.api.campaigns import _is_real_video_url as _canonical
    return _canonical(url)

    from urllib.parse import urlparse
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
    # 2026-09-18 (Molina): filtro explícito Asset.asset_type != 'folder'.
    # Drive/Dropbox folder assets son placeholders que el resolver expande
    # en hijos; SIEMPRE se saltan. (Antes solo confiábamos en
    # _is_real_video_url, que no excluía /drive/folders/ explícitamente.)
    candidates = (
        db.query(Asset)
        .filter(Asset.campaign_id == campaign_id)
        .filter(Asset.asset_type != "folder")
        .filter(Asset.status == "pending")
        .order_by(Asset.created_at.asc())
        .all()
    )
    # Defense in depth: filtra cualquier folder (p.ej. registros viejos con
    # asset_type mal puesto) y respeta skip_download.
    # 2026-09-19 (Molina): drive-resolver deja el folder "cabecera" como asset
    # (asset_type='video', extra_metadata.kind='drive_folder') además de los
    # archivos reales que expande. Esos folders + brand_assets (Google Docs)
    # + profile URLs NO se descargan. Filtramos por extra_metadata.kind Y
    # por patrones de URL como red de seguridad.
    _SKIP_KINDS = frozenset({
        "drive_folder",
        "dropbox_folder",
        "brand_asset",
        "youtube_profile",
        "twitter_profile",
        "tiktok_profile",
        "instagram_profile",
        "profile",
    })
    _SKIP_URL_PATTERNS = (
        "/drive/folders/",
        "/drive/u/",
        "/document/d/",
        "/documents/d/",
        "/forms/d/",
        "/spreadsheets/d/",
        "/presentation/d/",
        "/file/d/",
        "/folders/",
        "@",  # bare profile handles like https://www.youtube.com/@WhopIO
    )

    def _is_skippable(a):
        if a.asset_type == "folder":
            return True
        if (a.extra_metadata or {}).get("skip_download") is True:
            return True
        kind = (a.extra_metadata or {}).get("kind")
        if kind in _SKIP_KINDS:
            return True
        url = (a.source_url or "").lower()
        if any(pat in url for pat in _SKIP_URL_PATTERNS):
            return True
        return False
    processable = [a for a in candidates if not _is_skippable(a)]
    asset = next(
        (a for a in processable if _is_real_video_url(a.source_url)),
        None,
    )
    if asset is None:
        return {"created": 0, "skipped": 0, "asset_id": None,
                "skipped_reason": "no_processable_asset"}

    asset_id = str(asset.id)

    # Idempotency per-asset: don't re-enqueue the SAME asset if it already has
    # a job in flight (pending/processing/assigned) or already finished
    # (completed/failed/cancelled). 2026-09-19 (Molina): we WANT to enqueue
    # multiple assets per campaign in parallel -- the previous cap ("any
    # terminal job for this campaign → skip") limited us to 1 download per
    # campaign forever. Now we only skip when THIS asset already has a job.
    existing = (
        db.query(Job)
        .filter(Job.job_type == "download")
        .filter(Job.payload["asset_id"].astext == asset_id)
        .filter(Job.status.in_((
            "pending", "processing", "assigned",
            "completed", "failed", "cancelled",
        )))
        .first()
    )
    if existing is not None:
        return {"created": 0, "skipped": 1, "asset_id": asset_id,
                "existing_job_id": str(existing.id),
                "skipped_reason": "asset_already_has_job"}

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
        #    2026-09-18 (Molina): capturar IntegrityError por campaña — un INSERT
        #    que dispare la CHECK constraint de jobs (p.ej. por un asset legacy
        #    mal marcado como 'drive_folder' con url de carpeta) NO debe matar
        #    el run entero; se rollbackea la sesión local, se cuenta como
        #    'skipped_folder_legacy' y se continúa con la siguiente campaña.
        from sqlalchemy.exc import IntegrityError
        for c in scored:
            try:
                r = _enqueue_download_for_campaign(db, c.id, dry_run=args.dry_run)
            except IntegrityError as e:
                # Check constraint violation (p.ej. ck_jobs_no_folder_download).
                # Si toca, la campaña se queda sin encolar pero el resto sigue.
                try:
                    db.rollback()
                except Exception:
                    pass
                err_kind = "integrity_violation"
                err_msg = str(e.orig)[:300] if hasattr(e, "orig") else str(e)[:300]
                logger.warning(
                    "enqueue skipped for campaign %s (%s): %s",
                    c.id, err_kind, err_msg,
                )
                summary["skipped"] += 1
                summary["campaigns"].append(
                    {
                        "id": c.id, "name": c.name, "status": c.status,
                        "error": err_kind, "error_detail": err_msg,
                    }
                )
                continue
            except Exception as e:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:
                    pass
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
