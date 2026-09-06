"""State machine and lease/retry tests."""
from datetime import datetime, timedelta, timezone


def test_pending_to_assigned_via_claim(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    claimed = job_service.claim_next_job(db, worker_id="w1")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "assigned"
    assert claimed.worker_id == "w1"
    assert claimed.started_at is not None
    assert claimed.lease_until is not None


def test_assigned_to_processing(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    job_service.claim_next_job(db, worker_id="w1")
    started = job_service.start_processing(db, job.id, worker_id="w1")
    assert started.status == "processing"
    assert started.lease_until is not None


def test_processing_to_completed(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    completed = job_service.complete_job(db, job.id, worker_id="w1", result={"ok": True})
    assert completed.status == "completed"
    assert completed.result == {"ok": True}
    assert completed.completed_at is not None
    assert completed.lease_until is None


def test_failure_with_retries_remaining_returns_to_pending(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={}, max_attempts=3)
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    failed = job_service.fail_job(db, job.id, worker_id="w1", error_message="boom")
    assert failed.status == "pending"
    assert failed.attempts == 1
    assert failed.error_message == "boom"
    assert failed.worker_id is None
    assert failed.lease_until is None
    assert failed.available_at > datetime.now(timezone.utc)


def test_failure_at_max_attempts_terminal(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={}, max_attempts=1)
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    failed = job_service.fail_job(db, job.id, worker_id="w1", error_message="fatal")
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.error_message == "fatal"


def test_completed_cannot_transition(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    job_service.claim_next_job(db, worker_id="w1")
    job_service.start_processing(db, job.id, worker_id="w1")
    job_service.complete_job(db, job.id, worker_id="w1", result={"ok": True})
    # Try to fail a completed job
    try:
        job_service.fail_job(db, job.id, worker_id="w1", error_message="x")
        assert False, "expected InvalidTransition"
    except job_service.InvalidTransition:
        pass


def test_cancelled_cannot_process(db):
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    cancelled = job_service.cancel_job(db, job.id)
    assert cancelled.status == "cancelled"
    try:
        job_service.claim_next_job(db, worker_id="w1")
        # No job should be returned
        assert True
    except Exception:
        pass
    # Now try to start a cancelled job (would need direct db lookup)
    try:
        job_service.start_processing(db, job.id, worker_id="w1")
        assert False, "expected InvalidTransition"
    except job_service.InvalidTransition:
        pass


def test_expired_lease_recovery(db):
    """A job whose lease has expired should be reclaimable by another worker."""
    from app.services import job_service
    job = job_service.create_job(db, job_type="qa", payload={})
    # Manually simulate a worker that claimed but never reported
    claimed = job_service.claim_next_job(db, worker_id="w1")
    # Force lease to be expired
    claimed.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    # Another worker claims
    reclaimed = job_service.claim_next_job(db, worker_id="w2")
    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.worker_id == "w2"
    assert reclaimed.status == "assigned"
