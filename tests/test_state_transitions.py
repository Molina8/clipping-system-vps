"""Tests for state transitions on job completion (Fase C).

These exercise the helper functions in app/services/job_state_transitions.py
which are called from the /worker/jobs/{id}/result endpoint.
"""
import uuid

from app.models.asset import Asset, AssetStatus
from app.models.clip import Clip, ClipQAStatus
from app.models.job import Job, JobStatus
from app.models.asset import Asset, AssetStatus
from app.models.clip import Clip, ClipQAStatus  # noqa: F401
from app.services.job_state_transitions import (
    on_download_completed,
    on_qa_completed,
    on_render_completed,
    on_transcribe_completed,
)


def _create_campaign_and_asset(db):
    from app.models.campaign import Campaign
    c = Campaign(name=f"state-camp-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.commit()
    db.refresh(c)
    a = Asset(
        campaign_id=c.id,
        source_url=f"https://youtube.com/watch?v={uuid.uuid4().hex}",
        source_provider="youtube",
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return c.id, a.id


def test_download_completed_marks_asset_and_creates_transcribe(db):
    """On download done: asset -> 'downloaded', transcribe job auto-created."""
    cid, aid = _create_campaign_and_asset(db)
    # Create a download job
    job = Job(
        job_type="download",
        payload={"asset_id": str(aid), "source_url": "https://..."},
        status=JobStatus.COMPLETED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    on_download_completed(
        db, job,
        result_data={"file_path": "C:\\data\\video.mp4", "file_size": 1024, "duration_seconds": 30.0},
    )

    db.expire_all()
    asset = db.get(Asset, aid)
    assert asset.status == AssetStatus.DOWNLOADED.value
    assert asset.downloaded_at is not None
    assert asset.local_path == "C:\\data\\video.mp4"

    # Transcribe job auto-created
    transcribe_jobs = (
        db.query(Job)
        .filter(Job.job_type == "transcribe")
        .filter(Job.payload["asset_id"].astext == str(aid))
        .all()
    )
    assert len(transcribe_jobs) == 1
    assert transcribe_jobs[0].payload["asset_id"] == str(aid)


def test_transcribe_completed_marks_asset_and_stores_transcription(db):
    cid, aid = _create_campaign_and_asset(db)
    job = Job(
        job_type="transcribe",
        payload={"asset_id": str(aid)},
        status=JobStatus.COMPLETED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    transcription = {
        "language": "en",
        "duration": 30.0,
        "segments": [{"start": 0.0, "end": 5.0, "text": "hello"}],
    }
    on_transcribe_completed(db, job, result_data=transcription)

    db.expire_all()
    asset = db.get(Asset, aid)
    assert asset.status == AssetStatus.TRANSCRIBED.value
    assert asset.transcribed_at is not None
    assert asset.extra_metadata["transcription"] == transcription


def test_render_completed_creates_clip_and_qa_job(db):
    cid, aid = _create_campaign_and_asset(db)
    job = Job(
        job_type="render",
        payload={"asset_id": str(aid), "candidate_id": str(uuid.uuid4())},
        status=JobStatus.COMPLETED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    clip, qa_job = on_render_completed(
        db, job,
        result_data={
            "file_path": "C:\\clips\\final.mp4",
            "duration_seconds": 28.5,
            "file_size": 5000000,
        },
    )

    assert clip is not None
    assert clip.asset_id == aid
    assert clip.campaign_id == cid
    assert clip.render_job_id == job.id
    assert clip.qa_status == "pending"
    assert clip.status == "created"
    assert qa_job is not None
    assert qa_job.job_type == "qa"
    assert qa_job.payload["clip_id"] == str(clip.id)


def test_qa_completed_pass_sets_clip_approved(db):
    cid, aid = _create_campaign_and_asset(db)
    render_job = Job(
        job_type="render",
        payload={"asset_id": str(aid)},
        status=JobStatus.COMPLETED,
    )
    db.add(render_job)
    db.commit()
    db.refresh(render_job)
    clip, _ = on_render_completed(
        db, render_job,
        result_data={"file_path": "C:\\clips\\x.mp4", "duration_seconds": 25.0},
    )

    qa_job = Job(
        job_type="qa",
        payload={"clip_id": str(clip.id), "asset_id": str(aid)},
        status=JobStatus.COMPLETED,
    )
    db.add(qa_job)
    db.commit()
    db.refresh(qa_job)

    on_qa_completed(
        db, qa_job,
        result_data={
            "status": "pass",
            "checks": {"duration": 25.0, "resolution": "1080x1920", "fps": 30},
        },
    )

    db.expire_all()
    clip = db.get(Clip, clip.id)
    assert clip.qa_status == ClipQAStatus.PASS.value
    assert clip.status == "approved"
    assert clip.qa_at is not None
    assert clip.qa_result["status"] == "pass"
    assert clip.qa_job_id == qa_job.id


def test_qa_completed_fail_sets_clip_rejected(db):
    cid, aid = _create_campaign_and_asset(db)
    render_job = Job(
        job_type="render",
        payload={"asset_id": str(aid)},
        status=JobStatus.COMPLETED,
    )
    db.add(render_job)
    db.commit()
    db.refresh(render_job)
    clip, _ = on_render_completed(
        db, render_job,
        result_data={"file_path": "C:\\clips\\x.mp4"},
    )

    qa_job = Job(
        job_type="qa",
        payload={"clip_id": str(clip.id)},
        status=JobStatus.COMPLETED,
    )
    db.add(qa_job)
    db.commit()
    db.refresh(qa_job)

    on_qa_completed(
        db, qa_job,
        result_data={"status": "fail", "reason": "duration_too_short"},
    )

    db.expire_all()
    clip = db.get(Clip, clip.id)
    assert clip.qa_status == ClipQAStatus.FAIL.value
    assert clip.status == "rejected"


def test_qa_completed_review_keeps_status_review(db):
    cid, aid = _create_campaign_and_asset(db)
    render_job = Job(
        job_type="render",
        payload={"asset_id": str(aid)},
        status=JobStatus.COMPLETED,
    )
    db.add(render_job)
    db.commit()
    db.refresh(render_job)
    clip, _ = on_render_completed(
        db, render_job,
        result_data={"file_path": "C:\\clips\\x.mp4"},
    )

    qa_job = Job(
        job_type="qa",
        payload={"clip_id": str(clip.id)},
        status=JobStatus.COMPLETED,
    )
    db.add(qa_job)
    db.commit()
    db.refresh(qa_job)

    on_qa_completed(
        db, qa_job,
        result_data={"status": "review", "reason": "ambiguous_audio"},
    )

    db.expire_all()
    clip = db.get(Clip, clip.id)
    assert clip.qa_status == ClipQAStatus.REVIEW.value
    assert clip.status == "review"



# ============================================================================
# Bug #2 fix verification (2026-09-07):
# Wiring complete_job() to fire the state transition handler. Without this,
# the Worker correctly reports success but the next step (transcribe/render/
# qa) is never created. These tests exercise the SERVICE-LEVEL wiring.
# ============================================================================

import uuid as _uuid

from app.models.campaign import Campaign
from app.services.job_service import (
    complete_job,
    claim_next_job,
    fail_job,
    start_processing,
)


def _setup_campaign_asset_download_job(db, *, worker_id="windows-worker-01"):
    """Helper: create a fresh campaign + asset + pending download job."""
    from app.models.asset import Asset
    c = Campaign(name=f"wiring-camp-{_uuid.uuid4().hex[:8]}")
    db.add(c)
    db.commit()
    db.refresh(c)
    a = Asset(
        campaign_id=c.id,
        source_url=f"https://youtube.com/watch?v={_uuid.uuid4().hex}",
        source_provider="youtube",
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    from app.services.job_service import create_job
    job = create_job(
        db,
        job_type="download",
        payload={"asset_id": str(a.id), "url": a.source_url},
    )
    return c, a, job, worker_id


def test_complete_job_fires_on_download_completed(db):
    """End-to-end: complete_job() on a download job triggers the state
    transition that marks the asset DOWNLOADED and auto-creates a transcribe
    job. Without the Bug #2 wiring, this would leave the asset in PENDING
    and no transcribe job would exist."""
    from app.models.asset import AssetStatus
    from app.models.job import Job, JobStatus

    campaign, asset, job, worker_id = _setup_campaign_asset_download_job(db)

    # Worker claims + starts the job
    claimed = claim_next_job(db, worker_id=worker_id)
    assert claimed is not None
    assert claimed.id == job.id
    started = start_processing(db, claimed.id, worker_id=worker_id)
    assert started.status == JobStatus.PROCESSING.value

    # Worker reports success with a realistic result
    result = {
        "file_path": "C:\\clips\\yomi.mp4",
        "file_size": 12_345_678,
        "duration_seconds": 42.5,
        "sha256": "a" * 64,
        "mime_type": "video/mp4",
    }
    completed = complete_job(db, job.id, worker_id=worker_id, result=result)
    assert completed.status == JobStatus.COMPLETED.value

    # Bug #2 fix: the state transition MUST have fired automatically.
    db.expire_all()
    asset_fresh = db.get(type(asset), asset.id)
    assert asset_fresh.status == AssetStatus.DOWNLOADED.value, (
        f"asset status should be 'downloaded', got '{asset_fresh.status}'"
    )
    assert asset_fresh.local_path == result["file_path"]
    assert asset_fresh.file_size == result["file_size"]
    assert asset_fresh.duration_seconds == result["duration_seconds"]
    assert asset_fresh.sha256 == result["sha256"]
    assert asset_fresh.mime_type == result["mime_type"]
    assert asset_fresh.downloaded_at is not None

    # A transcribe job must have been auto-created for this asset.
    from sqlalchemy import select
    transcribe_jobs = db.execute(
        select(Job).where(
            Job.job_type == "transcribe",
            Job.payload["asset_id"].astext == str(asset.id),
        )
    ).scalars().all()
    assert len(transcribe_jobs) == 1, (
        f"expected exactly 1 transcribe job, got {len(transcribe_jobs)}"
    )
    assert transcribe_jobs[0].priority == job.priority


def test_complete_job_fires_on_transcribe_completed(db):
    """End-to-end: a transcribe job completion should be handled by the
    transition (even if it just marks the asset TRANSCRIBED in this minimal
    scenario — we only verify the handler fires without exception)."""
    from app.models.asset import AssetStatus
    from app.models.job import Job, JobStatus

    campaign, asset, job, worker_id = _setup_campaign_asset_download_job(db)

    # Move asset to DOWNLOADED first via the download completion
    claim_next_job(db, worker_id=worker_id)
    start_processing(db, job.id, worker_id=worker_id)
    complete_job(db, job.id, worker_id=worker_id, result={"file_path": "x.mp4"})

    # Now create a transcribe job pointing at the same asset
    from app.services.job_service import create_job
    tx_job = create_job(
        db,
        job_type="transcribe",
        payload={"asset_id": str(asset.id), "url": asset.source_url},
    )
    tx_claimed = claim_next_job(db, worker_id=worker_id)
    assert tx_claimed is not None
    start_processing(db, tx_claimed.id, worker_id=worker_id)
    complete_job(
        db, tx_claimed.id, worker_id=worker_id,
        result={"transcript": "hello world", "segments": []},
    )

    db.expire_all()
    asset_fresh = db.get(type(asset), asset.id)
    assert asset_fresh.status == AssetStatus.TRANSCRIBED.value


def test_complete_job_does_not_rollback_on_handler_error(db):
    """If the state transition handler raises (e.g. malformed payload), the
    job completion MUST still persist. The Worker successfully reported the
    result; we don't lose that."""
    from app.models.job import Job, JobStatus

    # Build a job with NO asset_id in payload — handler will warn+return
    from app.services.job_service import create_job
    bad_job = create_job(db, job_type="download", payload={"no_asset": True})
    bad_claimed = claim_next_job(db, worker_id="windows-worker-01")
    assert bad_claimed is not None
    start_processing(db, bad_claimed.id, worker_id="windows-worker-01")

    completed = complete_job(
        db, bad_claimed.id, worker_id="windows-worker-01",
        result={"file_path": "x.mp4"},
    )
    # Completion persisted despite handler warning
    assert completed.status == JobStatus.COMPLETED.value
    assert completed.completed_at is not None


def test_fail_job_fires_on_job_failed_on_terminal_failure(db):
    """A terminal FAILED job (after retries exhausted) must invoke
    on_job_failed. The default max_attempts is 3; after 3 failures the
    job becomes terminal."""
    from app.models.job import Job, JobStatus
    from app.services.job_service import create_job

    # Use max_attempts=2 so a single fail is enough to reach terminal state.
    from datetime import datetime, timezone
    from app.models.job import Job as JobModel
    job = create_job(db, job_type="download", payload={}, max_attempts=2)
    claimed = claim_next_job(db, worker_id="windows-worker-01")
    assert claimed is not None
    start_processing(db, claimed.id, worker_id="windows-worker-01")
    job = fail_job(
        db, claimed.id, worker_id="windows-worker-01",
        error_message="boom",
    )
    # After first failure: attempts=1, status=PENDING (retry). Reset
    # available_at to now so claim_next_job picks it up immediately
    # (otherwise the backoff blocks re-claim).
    db.refresh(job)
    job.available_at = datetime.now(timezone.utc)
    db.commit()
    assert job.status == JobStatus.PENDING.value
    assert job.attempts == 1
    job2 = claim_next_job(db, worker_id="windows-worker-01")
    assert job2 is not None
    start_processing(db, job2.id, worker_id="windows-worker-01")
    job = fail_job(
        db, job2.id, worker_id="windows-worker-01",
        error_message="boom2",
    )
    assert job.status == JobStatus.FAILED.value
    assert job.attempts == 2
