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
