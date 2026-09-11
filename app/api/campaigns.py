"""Campaign API endpoints (Step 4 in architecture_flow.md)."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.campaign import (
    CampaignCreate,
    CampaignOut,
    CampaignUpdate,
)
from app.services.campaign_analyzer import (
    analyze_campaign,
    analyze_due_campaigns,
)
from app.services.campaign_service import (
    create_campaign,
    get_campaign,
    list_campaigns,
    update_campaign,
)
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# --- Asset URL filter --------------------------------------------------------
# Only enqueue pipeline jobs for assets that the Worker can actually process.
# Whop CDN banners/icons are stored in the DB as 'external' kind but are NOT
# videos — FFmpeg would reject them.
#
# REJECTED patterns (verified against real Worker failures, 2026-09-11):
#   - Bare profile pages: https://www.youtube.com/@WhopIO
#   - Bare root profiles: https://www.instagram.com/whop/
#   - These look like hosts to my host filter but the Worker can't process them.
#
# ACCEPTED patterns:
#   - YouTube:    /watch?v=, /shorts/, youtu.be/<id>
#   - TikTok:     /@user/video/<id>  (NOT bare /@user)
#   - Instagram:  /p/<id>, /reel/<id>  (NOT bare /<user>)
#   - Drive/Dropbox/Mega/Vimeo: any URL on these hosts
#   - Direct media: .mp4, .mov, .webm, .mkv, .avi, .m4v, .webp, .zip

import re as _re

_VIDEO_EXTENSIONS = (
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".zip",
)

# Regex patterns that MUST match for a URL to be accepted.
_VIDEO_PATTERNS = (
    _re.compile(r"youtube\.com/watch\?v=", _re.I),
    _re.compile(r"youtube\.com/shorts/", _re.I),
    _re.compile(r"youtu\.be/[A-Za-z0-9_-]+", _re.I),
    _re.compile(r"tiktok\.com/@[^/]+/video/\d+", _re.I),
    _re.compile(r"instagram\.com/p/[A-Za-z0-9_-]+", _re.I),
    _re.compile(r"instagram\.com/reel/[A-Za-z0-9_-]+", _re.I),
    _re.compile(r"drive\.google\.com/", _re.I),
    _re.compile(r"docs\.google\.com/", _re.I),
    _re.compile(r"dropbox\.com/", _re.I),
    _re.compile(r"mega\.nz/", _re.I),
    _re.compile(r"vimeo\.com/", _re.I),
)


def _is_real_video_url(url: str | None) -> bool:
    """Return True iff the URL points at a processable video.

    Conservative: when in doubt, returns False (caller should NOT enqueue).
    """
    if not url:
        return False
    u = url.lower()

    # Direct media file extension (most reliable signal).
    if any(u.endswith(ext) or f"{ext}?" in u for ext in _VIDEO_EXTENSIONS):
        return True

    # Specific video URL pattern (not just "host is known").
    if any(p.search(u) for p in _VIDEO_PATTERNS):
        return True

    return False


@router.post("", response_model=CampaignOut, status_code=201)
@router.post("/", response_model=CampaignOut, status_code=201)
def create(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = create_campaign(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return c


@router.get("", response_model=List[CampaignOut])
@router.get("/", response_model=List[CampaignOut])
def list_all(
    status_filter: Optional[str] = Query(None, alias="status"),
    source_provider: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_campaigns(
        db,
        status=status_filter,
        source_provider=source_provider,
        limit=limit,
        offset=offset,
    )


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_one(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    c = get_campaign(db, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = update_campaign(db, campaign_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c



# --- Steps 2 + 3 of architecture_flow.md: analyze a campaign ---------------

@router.post("/analyze_due", response_model=dict)
def analyze_due(
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Analyze every draft campaign with source_instructions.

    Useful for manual triggering and smoke-testing. The cron loop inside
    main.py calls this same function on a timer.
    """
    results = analyze_due_campaigns(db, settings=settings, limit=limit)
    return {"analyzed": len(results), "results": results}


@router.post("/{campaign_id}/analyze", response_model=dict)
def analyze_one(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Analyze one campaign by id (idempotent: overwrites spec)."""
    c = get_campaign(db, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return analyze_campaign(db, c, settings=settings)


# --- Step 7 of architecture_flow.md: enqueue pipeline jobs ---------------

@router.post("/{campaign_id}/enqueue", response_model=dict)
def enqueue_pipeline(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Enqueue the download job for a campaign.

    IMPORTANT (2026-09-11 race-fix):
      Previously this endpoint enqueued download + transcribe + render in a
      single atomic batch, sending source_url as the input path for both
      transcribe and render. That caused the Worker to try to read a remote
      URL it had not downloaded yet, producing hard failures.

      Correct flow now:
        1) This endpoint enqueues ONLY the download job.
        2) on_download_completed (job_state_transitions.py) auto-creates the
           transcribe job for the same asset, using asset.local_path (the
           real on-disk file path) instead of source_url.
        3) on_transcribe_completed triggers ClipSelectionAgent to produce
           candidates. Render jobs are created later by candidate_lifecycle
           once a candidate is approved (not pre-created here).

    Idempotent: existing pending/processing download jobs for the same
    campaign are left untouched. Returns the count of jobs created.

    The campaign must be in status='ready' (analyzed with qa_rules). The
    first processable asset of the campaign becomes the source.
    """
    import uuid as _uuid
    from app.models.job import Job
    from app.models.campaign import Campaign
    from app.models.asset import Asset

    c = db.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if c.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Campaign is in status='{c.status}', expected 'ready'",
        )

    # Pick the first PROCESSABLE asset for this campaign.
    # We must iterate because the first asset by created_at is usually a
    # banner/icon from whop CDN — useless for the Worker. We pick the
    # earliest asset whose URL is a real video / external media.
    from sqlalchemy import or_

    candidates = (
        db.query(Asset)
        .filter(Asset.campaign_id == campaign_id)
        .filter(Asset.asset_type == "video")
        .order_by(Asset.created_at.asc())
        .all()
    )
    asset = next((a for a in candidates if _is_real_video_url(a.source_url)), None)
    if asset is None:
        # Fallback: any asset_type, first processable URL
        candidates = (
            db.query(Asset)
            .filter(Asset.campaign_id == campaign_id)
            .order_by(Asset.created_at.asc())
            .all()
        )
        asset = next((a for a in candidates if _is_real_video_url(a.source_url)), None)
    if asset is None:
        raise HTTPException(
            status_code=409,
            detail="Campaign has no processable assets — cannot enqueue pipeline",
        )

    asset_id = str(asset.id)

    # Filter: only enqueue if the asset is a processable video URL.
    # Banners / icons from whop CDN are stored in BD but not enqueued —
    # the Worker would just FFmpeg-fail on them.
    if not _is_real_video_url(asset.source_url):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Asset {asset_id} is not a processable video URL "
                f"(host={asset.source_url[:80]}...) — not enqueueing."
            ),
        )

    created = []
    skipped = []

    # Worker contract (verified against clipping-windows-worker payload
    # schemas from real completed jobs, 2026-09-11):
    #   download.py   → requires payload["url"]
    #   transcribe.py → requires payload["video"] or payload["video_path"]
    #                    (filled by on_download_completed with asset.local_path)
    #   render.py     → requires payload["input_video"] (path local en Worker)
    #                    (created by candidate_lifecycle.approve_candidate
    #                    with asset.local_path, NOT here)
    #
    # Solo creamos el job download. El resto se encadena vía
    # job_state_transitions.on_download_completed → on_transcribe_completed.
    jobs_to_enqueue = [
        (
            "download",
            {
                "campaign_id": str(c.id),
                "asset_id": asset_id,
                "url": asset.source_url,                # Worker contract
                "source_url": asset.source_url,         # backwards-compat
                "destination": f"/tmp/cs_{c.id}_video.mp4",
            },
        ),
    ]

    for job_type, payload in jobs_to_enqueue:
        # Idempotency: don't create a new pending job if one is open.
        existing = (
            db.query(Job)
            .filter(Job.job_type == job_type)
            .filter(Job.payload["campaign_id"].astext == str(c.id))
            .filter(Job.status.in_(("pending", "processing", "assigned")))
            .first()
        )
        if existing is not None:
            skipped.append({"job_type": job_type, "existing_job_id": str(existing.id)})
            continue

        job = Job(
            id=_uuid.uuid4(),
            job_type=job_type,
            status="pending",
            priority=5,
            payload=payload,
            max_attempts=3,
        )
        db.add(job)
        created.append({"job_type": job_type, "job_id": str(job.id)})

    db.commit()

    return {
        "campaign_id": c.id,
        "asset_id": asset_id,
        "created": created,
        "skipped": skipped,
        "total_created": len(created),
        "total_skipped": len(skipped),
    }


@router.post("/enqueue_ready", response_model=dict)
def enqueue_all_ready(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Backlog drain: enqueue pipeline for every 'ready' campaign without open jobs.

    Called by the analyze cron loop (main.py) every 10 minutes.
    """
    from app.models.campaign import Campaign

    ready = (
        db.query(Campaign)
        .filter(Campaign.status == "ready")
        .filter(Campaign.source_provider != "manual")  # don't re-enqueue manual ones
        .order_by(Campaign.id.asc())
        .limit(limit)
        .all()
    )

    enqueued = 0
    skipped = 0
    for c in ready:
        try:
            r = enqueue_pipeline(c.id, db=db, _=True)  # bearer already validated
            if r["total_created"] > 0:
                enqueued += 1
            else:
                skipped += 1
        except HTTPException as e:
            logger.warning("enqueue skipped for %s: %s", c.id, e.detail)
            skipped += 1

    return {"scanned": len(ready), "enqueued": enqueued, "skipped": skipped}
