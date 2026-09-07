"""Job API endpoints (admin and worker)."""
import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.models.job import JobStatus
from app.services import job_service


router = APIRouter()


def _validate_transcribe_payload(payload: dict) -> Optional[str]:
    """Lightweight pre-enqueue validation for transcribe jobs.
    Returns an error message (string) if invalid, None if OK or unverifiable.

    Strategy:
      - video field is required.
      - For HTTP(S) URLs: HEAD with short timeout; reject if 4xx/5xx or unreachable.
      - For absolute Linux paths (starts with /): must exist on the VPS filesystem.
      - For Windows paths (e.g. C:\\...) or Tailscale shared paths: VPS cannot verify,
        so we accept and let the Worker validate at processing time.
    """
    video = payload.get("video")
    if not video or not isinstance(video, str):
        return "transcribe payload must include 'video' (string)"
    parsed = urlparse(video)
    if parsed.scheme in ("http", "https"):
        try:
            r = httpx.head(video, timeout=5.0, follow_redirects=True)
            if r.status_code >= 400:
                return f"video URL not accessible: HTTP {r.status_code}"
        except httpx.HTTPError as e:
            return f"video URL unreachable: {type(e).__name__}"
        return None
    if video.startswith("/"):
        if not os.path.isfile(video):
            return f"local video file not found: {video}"
        return None
    # Windows path or Tailscale shared path: VPS cannot verify; Worker will.
    return None


class JobCreate(BaseModel):
    job_type: str = Field(..., min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    max_attempts: int = Field(default=3, ge=1, le=10)


class JobResponse(BaseModel):
    id: UUID
    job_type: str
    status: JobStatus
    priority: int
    payload: dict
    result: Optional[dict] = None
    error_message: Optional[str] = None
    worker_id: Optional[str] = None
    attempts: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    available_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    lease_until: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WorkerClaimResponse(BaseModel):
    id: UUID
    job_type: str
    payload: dict
    priority: int
    attempts: int
    max_attempts: int
    lease_until: datetime


class WorkerResultCreate(BaseModel):
    result: dict = Field(default_factory=dict)


class WorkerFailCreate(BaseModel):
    error_message: str = Field(..., min_length=1, max_length=2048)


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_bearer)],
)
def create_job_endpoint(payload: JobCreate, db: Session = Depends(get_db)):
    if payload.job_type == "transcribe":
        err = _validate_transcribe_payload(payload.payload)
        if err is not None:
            raise HTTPException(status_code=400, detail=err)
    job = job_service.create_job(
        db,
        job_type=payload.job_type,
        payload=payload.payload,
        priority=payload.priority,
        max_attempts=payload.max_attempts,
    )
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer)],
)
def get_job_endpoint(job_id: UUID, db: Session = Depends(get_db)):
    job = job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get(
    "/jobs",
    response_model=list[JobResponse],
    dependencies=[Depends(require_bearer)],
)
def list_jobs_endpoint(
    status_filter: Optional[JobStatus] = Query(None, alias="status"),
    job_type: Optional[str] = Query(None),
    worker_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return job_service.list_jobs(
        db,
        status=status_filter,
        job_type=job_type,
        worker_id=worker_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/worker/jobs/next",
    dependencies=[Depends(require_bearer)],
)
def claim_next_job_endpoint(
    worker_id: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
):
    job = job_service.claim_next_job(db, worker_id=worker_id)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return WorkerClaimResponse(
        id=job.id,
        job_type=job.job_type,
        payload=job.payload,
        priority=job.priority,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        lease_until=job.lease_until,
    )


@router.post(
    "/worker/jobs/{job_id}/start",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer)],
)
def worker_start_endpoint(
    job_id: UUID,
    worker_id: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
):
    try:
        job = job_service.start_processing(db, job_id, worker_id=worker_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    except job_service.WorkerMismatch:
        raise HTTPException(status_code=403, detail="job belongs to another worker")
    except job_service.InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return job


@router.post(
    "/worker/jobs/{job_id}/result",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer)],
)
def worker_result_endpoint(
    job_id: UUID,
    payload: WorkerResultCreate,
    worker_id: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
):
    try:
        job = job_service.complete_job(
            db, job_id, worker_id=worker_id, result=payload.result
        )
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    except job_service.WorkerMismatch:
        raise HTTPException(status_code=403, detail="job belongs to another worker")
    except job_service.InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return job


@router.post(
    "/worker/jobs/{job_id}/fail",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer)],
)
def worker_fail_endpoint(
    job_id: UUID,
    payload: WorkerFailCreate,
    worker_id: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
):
    try:
        # Truncate error_message to schema max (2048) so the Worker can
        # always report failures. Without this, a long subprocess error
        # output causes 422 and leaves the job stuck in 'processing'.
        msg = (payload.error_message or "")[:2048]
        job = job_service.fail_job(
            db, job_id, worker_id=worker_id, error_message=msg
        )
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    except job_service.WorkerMismatch:
        raise HTTPException(status_code=403, detail="job belongs to another worker")
    except job_service.InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return job


@router.post(
    "/worker/jobs/{job_id}/heartbeat",
    response_model=JobResponse,
    dependencies=[Depends(require_bearer)],
)
def worker_heartbeat_endpoint(
    job_id: UUID,
    worker_id: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
):
    """
    Renew lease_until for a claimed job.
    Allowed only when job is in assigned or processing state.
    Worker must send its worker_id as query param.
    """
    try:
        job = job_service.heartbeat(db, job_id, worker_id=worker_id)
    except job_service.JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    except job_service.WorkerMismatch:
        raise HTTPException(status_code=403, detail="job belongs to another worker")
    except job_service.InvalidTransition as e:
        raise HTTPException(status_code=409, detail=str(e))
    return job
