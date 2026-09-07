"""Job service: state transitions, atomic claim, retry logic, lease recovery."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.job import JOB_STATUS_VALUES, Job, JobStatus


LEASE_DURATION_SECONDS = 300  # 5 minutes


VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.ASSIGNED, JobStatus.CANCELLED},
    JobStatus.ASSIGNED: {JobStatus.PROCESSING, JobStatus.PENDING, JobStatus.CANCELLED},
    JobStatus.PROCESSING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PENDING},
    JobStatus.FAILED: {JobStatus.PENDING},
    JobStatus.COMPLETED: set(),
    JobStatus.CANCELLED: set(),
}


class InvalidTransition(Exception):
    pass


logger = logging.getLogger(__name__)


# State transition dispatch is imported lazily inside complete_job()
# and fail_job() to avoid the circular import:
#   job_service -> job_state_transitions -> job_service (create_job)
# Wired 2026-09-07 to fix Bug #2 (VPS, not Worker): the Worker reports
# success but the next step in the pipeline (transcribe/render/qa)
# was never created.


class JobNotFound(Exception):
    pass


class WorkerMismatch(Exception):
    pass


def _backoff_seconds(attempts: int) -> int:
    """Simple exponential backoff: 30, 60, 120, 240 ..."""
    return 30 * (2 ** max(0, attempts - 1))


def _ensure_transition(current: JobStatus, new: JobStatus) -> None:
    if new not in VALID_TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"invalid transition {current.value} -> {new.value}")


def create_job(
    db: Session,
    *,
    job_type: str,
    payload: dict,
    priority: int = 5,
    max_attempts: int = 3,
) -> Job:
    job = Job(
        job_type=job_type,
        payload=payload,
        priority=priority,
        max_attempts=max_attempts,
        status=JobStatus.PENDING.value,
        available_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return job


def get_job(db: Session, job_id: UUID) -> Optional[Job]:
    return db.get(Job, job_id)


def list_jobs(
    db: Session,
    *,
    status: Optional[JobStatus] = None,
    job_type: Optional[str] = None,
    worker_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    q = select(Job)
    if status is not None:
        q = q.where(Job.status == status.value)
    if job_type is not None:
        q = q.where(Job.job_type == job_type)
    if worker_id is not None:
        q = q.where(Job.worker_id == worker_id)
    q = q.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(q).scalars().all())


def _recover_expired_leases(db: Session, now: datetime) -> int:
    """Reset jobs whose lease expired back to pending. Returns count."""
    stmt = (
        update(Job)
        .where(
            Job.status.in_([JobStatus.ASSIGNED.value, JobStatus.PROCESSING.value]),
            Job.lease_until.is_not(None),
            Job.lease_until < now,
        )
        .values(
            status=JobStatus.PENDING.value,
            lease_until=None,
            worker_id=None,
        )
    )
    result = db.execute(stmt)
    return result.rowcount or 0


def claim_next_job(db: Session, *, worker_id: str) -> Optional[Job]:
    """
    Atomically claim the next available job for this worker.
    1. Recovers expired leases back to pending.
    2. SELECT ... FOR UPDATE SKIP LOCKED on the highest-priority pending job.
    3. Marks it as assigned with a fresh lease.
    Returns the claimed job or None if no job is available.
    """
    now = datetime.now(timezone.utc)
    lease_expires = now + timedelta(seconds=LEASE_DURATION_SECONDS)

    _recover_expired_leases(db, now)

    claim_q = (
        select(Job)
        .where(
            Job.status == JobStatus.PENDING.value,
            Job.available_at <= now,
        )
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = db.execute(claim_q).scalar_one_or_none()
    if job is None:
        db.commit()
        return None

    job.status = JobStatus.ASSIGNED.value
    job.worker_id = worker_id
    job.started_at = now
    job.lease_until = lease_expires
    db.commit()
    db.refresh(job)
    return job


def start_processing(db: Session, job_id: UUID, *, worker_id: str) -> Job:
    """Worker marks a claimed job as actively processing (extends lease)."""
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.PROCESSING)
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    now = datetime.now(timezone.utc)
    job.status = JobStatus.PROCESSING.value
    job.lease_until = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    db.commit()
    db.refresh(job)
    return job


def complete_job(db: Session, job_id: UUID, *, worker_id: str, result: dict) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.COMPLETED)
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    now = datetime.now(timezone.utc)
    job.status = JobStatus.COMPLETED.value
    job.result = result
    job.completed_at = now
    job.lease_until = None
    db.commit()
    db.refresh(job)

    # Fire the state transition handler for this job_type. Lazy import
    # to avoid circular dep with job_state_transitions. Failures here
    # MUST NOT roll back the completion — log and move on.
    try:
        from app.services.job_state_transitions import (
            on_download_completed,
            on_qa_completed,
            on_render_completed,
            on_transcribe_completed,
        )
        handlers = {
            "download": on_download_completed,
            "transcribe": on_transcribe_completed,
            "render": on_render_completed,
            "qa": on_qa_completed,
        }
        handler = handlers.get(job.job_type)
        if handler:
            handler(db, job, result or {})
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "state transition %s failed for job %s: %s",
            job.job_type, job.id, e,
        )
    return job


def fail_job(db: Session, job_id: UUID, *, worker_id: str, error_message: str) -> Job:
    """
    Mark job as failed.
    - If attempts < max_attempts: increment attempts, return to PENDING with backoff.
    - Otherwise: terminal FAILED state.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.FAILED)
    if job.worker_id != worker_id:
        raise WorkerMismatch()

    now = datetime.now(timezone.utc)
    job.attempts = job.attempts + 1

    if job.attempts < job.max_attempts:
        # Retry: transient failed -> pending with backoff
        job.status = JobStatus.PENDING.value
        job.error_message = error_message
        job.available_at = now + timedelta(seconds=_backoff_seconds(job.attempts))
        job.worker_id = None
        job.lease_until = None
    else:
        # Terminal failure
        job.status = JobStatus.FAILED.value
        job.error_message = error_message
        job.lease_until = None

    db.commit()
    db.refresh(job)

    # Only invoke on_job_failed for terminal failures (after retries exhausted)
    if job.status == JobStatus.FAILED.value:
        try:
            on_job_failed(db, job)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "on_job_failed handler failed for job %s: %s", job.id, e,
            )
    return job


def heartbeat(db: Session, job_id: UUID, *, worker_id: str) -> Job:
    """
    Renew lease_until for a job claimed by worker_id.
    Allowed only when job is in assigned or processing state.
    Lightweight: just updates lease_until, no state transition.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    current_status = JobStatus(job.status)
    if current_status not in {JobStatus.ASSIGNED, JobStatus.PROCESSING}:
        raise InvalidTransition(f"cannot heartbeat job in status {job.status}")
    now = datetime.now(timezone.utc)
    job.lease_until = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    db.commit()
    db.refresh(job)
    return job


def cancel_job(db: Session, job_id: UUID) -> Job:
    """Cancel a pending or assigned job."""
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.CANCELLED)
    job.status = JobStatus.CANCELLED.value
    job.lease_until = None
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job
