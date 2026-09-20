"""Clip API endpoints (Steps 16-19 in architecture_flow.md)."""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.clip import (
    ApprovePublishIn,
    ApprovePublishOut,
    ClipCreate,
    ClipOut,
    ClipPublicationOut,
    ClipUpdate,
)
from app.services.clip_service import (
    create_clip,
    get_clip,
    list_clips,
    update_clip,
)
from app.services.clip_storage_service import (
    VALID_LOCATIONS,
    list_clips_by_campaign,
    mark_clip_uploaded,
    set_clip_location,
)
from app.services.publish_gate import (
    PublishGateError,
    approve_clip_publish,
    list_publications_for_clip,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clips", tags=["clips"])


@router.post("", response_model=ClipOut, status_code=201)
@router.post("/", response_model=ClipOut, status_code=201)
def create(
    payload: ClipCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = create_clip(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return c


@router.get("", response_model=List[ClipOut])
@router.get("/", response_model=List[ClipOut])
def list_all(
    campaign_id: Optional[int] = Query(None),
    asset_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    qa_status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_clips(
        db,
        campaign_id=campaign_id,
        asset_id=asset_id,
        status=status,
        qa_status=qa_status,
        limit=limit,
        offset=offset,
    )


@router.get("/by_campaign/{campaign_id}/storage", response_model=List[ClipOut])
def list_by_campaign_storage(
    campaign_id: int,
    location: Optional[str] = Query(
        None,
        description="Filter by storage location",
        enum=list(VALID_LOCATIONS),
    ),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """List clips for a campaign, optionally filtered by storage location
    (`pending_upload`, `uploaded`, `archived`).

    The Worker keeps the actual `.mp4` files in
    `C:\\CODIANT\\clipping\\storage\\clips\\<campaign_id>\\{pending_upload|uploaded|archived}\\`.
    This endpoint returns the same clips with their `file_path` /
    `final_path_worker` pointing at the right folder.
    """
    try:
        return list_clips_by_campaign(
            db,
            campaign_id=campaign_id,
            location=location,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{clip_id}", response_model=ClipOut)
def get_one(
    clip_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    c = get_clip(db, clip_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return c


@router.patch("/{clip_id}", response_model=ClipOut)
def update(
    clip_id: uuid.UUID,
    payload: ClipUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    try:
        c = update_clip(db, clip_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if c is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return c


@router.post("/{clip_id}/approve_publish", response_model=ApprovePublishOut)
def approve_publish(
    clip_id: uuid.UUID,
    payload: ApprovePublishIn = Body(default_factory=ApprovePublishIn),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Human gate: allow this clip to be picked by publish_enqueue_tick.

    Requires qa=pass, status=approved, location=pending_upload.
    Default platform is youtube (milestone 1). Does not enqueue a job.
    Idempotent.
    """
    try:
        clip, pubs, already = approve_clip_publish(
            db, clip_id, platforms=payload.platforms
        )
    except PublishGateError as e:
        msg = str(e)
        code = 404 if "not found" in msg.lower() else 400
        raise HTTPException(status_code=code, detail=msg) from e
    return ApprovePublishOut(
        clip=clip,
        already_approved=already,
        publications=pubs,
    )


@router.get("/{clip_id}/publications", response_model=List[ClipPublicationOut])
def list_publications(
    clip_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    clip = get_clip(db, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return list_publications_for_clip(db, clip_id)


@router.post("/{clip_id}/mark_uploaded", response_model=ClipOut)
def mark_uploaded(
    clip_id: uuid.UUID,
    final_path_worker: Optional[str] = Query(
        None,
        max_length=1024,
        description="New Windows path after the Worker moves the file to "
                    "`uploaded/`. Optional; if omitted, the previous "
                    "`final_path_worker` is preserved.",
    ),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Worker calls this after successfully publishing the clip to social media.

    Sets `location='uploaded'` and `published_at=now()`. The Worker is
    responsible for actually moving the .mp4 from `pending_upload/` to
    `uploaded/` before calling this endpoint.
    """
    clip = mark_clip_uploaded(db, clip_id, final_path_worker=final_path_worker)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip


@router.post("/{clip_id}/location/{location}", response_model=ClipOut)
def set_location(
    clip_id: uuid.UUID,
    location: str,
    final_path_worker: Optional[str] = Query(None, max_length=1024),
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    """Generic endpoint for the Worker to record where the clip lives.

    Used when the Worker needs to re-tag an already-uploaded clip (e.g. move
    it to `archived/` after the campaign ends). For the common
    `qa_pass -> pending_upload` transition, prefer the implicit hook in
    `job_state_transitions.on_qa_completed`.
    """
    if location not in VALID_LOCATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"location must be one of {list(VALID_LOCATIONS)}, got {location!r}",
        )
    clip = set_clip_location(db, clip_id, location, final_path_worker=final_path_worker)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip
