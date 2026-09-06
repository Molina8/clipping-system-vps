"""Concurrency tests for atomic job claim."""
import threading

from app.db.database import SessionLocal
from app.services import job_service


def test_two_workers_never_get_the_same_job(db):
    """Two workers racing for the same single job: only one wins."""
    job_service.create_job(db, job_type="render", payload={"x": 1})

    results: dict[str, str | None] = {}
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        session = SessionLocal()
        try:
            barrier.wait()  # synchronize start
            job = job_service.claim_next_job(session, worker_id=worker_id)
            if job is not None:
                results[worker_id] = str(job.id)
        finally:
            session.close()

    t1 = threading.Thread(target=claim, args=("worker-a",))
    t2 = threading.Thread(target=claim, args=("worker-b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one worker got the job
    assert len(results) == 1


def test_two_workers_split_multiple_jobs(db):
    """Multiple jobs, two workers: each gets a different one."""
    for i in range(3):
        job_service.create_job(db, job_type="render", payload={"i": i})

    results: dict[str, str | None] = {}
    barrier = threading.Barrier(2)

    def claim(worker_id: str):
        session = SessionLocal()
        try:
            barrier.wait()
            job = job_service.claim_next_job(session, worker_id=worker_id)
            if job is not None:
                results[worker_id] = str(job.id)
        finally:
            session.close()

    t1 = threading.Thread(target=claim, args=("worker-a",))
    t2 = threading.Thread(target=claim, args=("worker-b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert results["worker-a"] != results["worker-b"]


def test_high_priority_claimed_first(db):
    """A high-priority job should be claimed before a normal-priority one."""
    low = job_service.create_job(db, job_type="render", payload={}, priority=1)
    high = job_service.create_job(db, job_type="render", payload={}, priority=10)
    session = SessionLocal()
    try:
        job = job_service.claim_next_job(session, worker_id="w1")
    finally:
        session.close()
    assert job is not None
    assert job.id == high.id
    assert job.id != low.id
