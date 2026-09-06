"""Tests for candidate lifecycle (Step 14 -> 15)."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app


# --- Fixtures --------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_token}"}


@pytest.fixture
def db():
    """Clean DB session for lifecycle tests."""
    session = SessionLocal()
    try:
        # Clean up in FK order
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM candidates"))
        session.execute(text("DELETE FROM assets"))
        session.execute(text("DELETE FROM campaigns"))
        session.commit()
        yield session
    finally:
        session.close()


def _seed(client, auth_headers, *, instructions="Make 30-60s clips in english.",
          provider="youtube", transcript_text="hello world",
          duration_seconds=300.0) -> Tuple[int, str]:
    """Create a campaign + asset (transcribed). Returns (campaign_id, asset_id)."""
    r = client.post("/campaigns", json={
        "name": f"lc-camp-{uuid.uuid4().hex[:6]}",
        "source_provider": provider,
        "source_instructions": instructions,
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    campaign_id = r.json()["id"]

    r = client.post("/assets", json={
        "campaign_id": campaign_id,
        "source_url": f"https://example.com/v/{uuid.uuid4().hex}",
        "source_provider": provider,
        "duration_seconds": duration_seconds,
        "extra_metadata": {
            "transcription": {
                "text": transcript_text,
                "segments": [
                    {"start": 0.0, "end": 5.0, "text": transcript_text},
                ],
            },
            "clip_selection_at": "2026-01-01T00:00:00Z",
        },
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    asset_id = r.json()["id"]
    return campaign_id, asset_id


def _create_candidate(
    client, auth_headers, campaign_id, asset_id,
    start_time=10.0, end_time=40.0,
) -> Dict[str, Any]:
    r = client.post("/candidates", json={
        "campaign_id": campaign_id,
        "asset_id": asset_id,
        "start_time": start_time,
        "end_time": end_time,
        "score": 0.8,
        "reasoning": "test fixture",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    return r.json()


# --- Service tests ---------------------------------------------------------

def test_approve_creates_render_job(db):
    """Approving a valid candidate creates a render job with proper payload."""
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate, CandidateStatus
    from app.models.job import Job
    from app.services.candidate_lifecycle import approve_candidate

    c = Campaign(
        name=f"lc-{uuid.uuid4().hex[:6]}",
        source_provider="youtube",
        source_instructions="Make 30-60s clips in english.",
    )
    db.add(c)
    db.commit()

    a = Asset(
        campaign_id=c.id, source_url="https://example.com/v",
        source_provider="youtube", source_id="x", asset_type="video",
        status=AssetStatus.TRANSCRIBED.value, duration_seconds=300.0,
        extra_metadata={"transcription": {"text": "hello world"}},
    )
    db.add(a)
    db.commit()
    db.refresh(a)

    cand = Candidate(
        campaign_id=c.id, asset_id=a.id,
        start_time=10.0, end_time=40.0,
        score=0.8, reasoning="good hook",
        status=CandidateStatus.PENDING.value,
        extra_metadata={},
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    result = approve_candidate(db, cand.id)
    assert result["status"] == "approved"
    assert result.get("render_job_id") is not None

    # Candidate updated
    db.refresh(cand)
    assert cand.status == "approved"
    assert cand.extra_metadata.get("render_job_id") == result["render_job_id"]

    # Render job exists with expected payload
    job = db.get(Job, result["render_job_id"])
    assert job is not None
    assert job.job_type == "render"
    assert job.payload["candidate_id"] == str(cand.id)
    assert job.payload["start_time"] == 10.0
    assert job.payload["end_time"] == 40.0
    assert job.payload["format"]  # not empty


def test_approve_out_of_window_rejects(db):
    """Candidate whose duration is outside [min,max] gets rejected."""
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate, CandidateStatus
    from app.services.candidate_lifecycle import approve_candidate

    c = Campaign(
        name=f"lc-{uuid.uuid4().hex[:6]}",
        source_provider="youtube",
        source_instructions="Make 30-60s clips in english.",
    )
    db.add(c)
    db.commit()

    a = Asset(
        campaign_id=c.id, source_url="https://example.com/v",
        source_provider="youtube", source_id="x", asset_type="video",
        status=AssetStatus.TRANSCRIBED.value, duration_seconds=300.0,
        extra_metadata={"transcription": {"text": "hello"}},
    )
    db.add(a)
    db.commit()
    db.refresh(a)

    cand = Candidate(
        campaign_id=c.id, asset_id=a.id,
        start_time=10.0, end_time=15.0,  # 5s — too short
        score=0.8, reasoning="bad",
        status=CandidateStatus.PENDING.value,
        extra_metadata={},
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    result = approve_candidate(db, cand.id)
    assert result["status"] == "rejected"
    assert "too short" in result["reason"]
    db.refresh(cand)
    assert cand.status == "rejected"
    assert "rejected_reason" in cand.extra_metadata


def test_approve_excluded_keyword_rejects(db):
    """Candidate whose transcript contains an exclude_keyword gets rejected."""
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate, CandidateStatus
    from app.services.candidate_lifecycle import approve_candidate

    c = Campaign(
        name=f"lc-{uuid.uuid4().hex[:6]}",
        source_provider="youtube",
        source_instructions="Make 30-60s clips. Exclude: spoiler.",
    )
    db.add(c)
    db.commit()

    a = Asset(
        campaign_id=c.id, source_url="https://example.com/v",
        source_provider="youtube", source_id="x", asset_type="video",
        status=AssetStatus.TRANSCRIBED.value, duration_seconds=300.0,
        extra_metadata={"transcription": {"text": "warning: spoiler ahead"}},
    )
    db.add(a)
    db.commit()
    db.refresh(a)

    cand = Candidate(
        campaign_id=c.id, asset_id=a.id,
        start_time=10.0, end_time=40.0,
        score=0.8, reasoning="contains spoiler",
        status=CandidateStatus.PENDING.value,
        extra_metadata={},
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    result = approve_candidate(db, cand.id)
    assert result["status"] == "rejected"
    assert "spoiler" in result["reason"]


def test_double_approve_is_idempotent(db):
    """Approving the same candidate twice returns the same render_job_id."""
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate, CandidateStatus
    from app.services.candidate_lifecycle import approve_candidate

    c = Campaign(
        name=f"lc-{uuid.uuid4().hex[:6]}",
        source_provider="youtube",
        source_instructions="Make 30-60s clips in english.",
    )
    db.add(c)
    db.commit()

    a = Asset(
        campaign_id=c.id, source_url="https://example.com/v",
        source_provider="youtube", source_id="x", asset_type="video",
        status=AssetStatus.TRANSCRIBED.value, duration_seconds=300.0,
        extra_metadata={"transcription": {"text": "hello"}},
    )
    db.add(a)
    db.commit()
    db.refresh(a)

    cand = Candidate(
        campaign_id=c.id, asset_id=a.id,
        start_time=10.0, end_time=40.0,
        score=0.8, reasoning="good",
        status=CandidateStatus.PENDING.value,
        extra_metadata={},
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    r1 = approve_candidate(db, cand.id)
    r2 = approve_candidate(db, cand.id)

    assert r1["status"] == "approved"
    assert r2["status"] == "approved"
    assert r1["render_job_id"] == r2["render_job_id"]
    assert r2.get("idempotent") is True


def test_reject_marks_with_reason(db):
    """reject_candidate sets status + reason + timestamp in metadata."""
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate, CandidateStatus
    from app.services.candidate_lifecycle import reject_candidate

    c = Campaign(name=f"lc-{uuid.uuid4().hex[:6]}", source_provider="youtube")
    db.add(c)
    db.commit()
    a = Asset(
        campaign_id=c.id, source_url="https://example.com/v",
        source_provider="youtube", source_id="x", asset_type="video",
        status=AssetStatus.TRANSCRIBED.value, duration_seconds=300.0,
        extra_metadata={"transcription": {"text": "x"}},
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    cand = Candidate(
        campaign_id=c.id, asset_id=a.id,
        start_time=10.0, end_time=40.0,
        score=0.5, reasoning="x",
        status=CandidateStatus.PENDING.value,
        extra_metadata={},
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    out = reject_candidate(db, cand.id, reason="manual review said no")
    assert out is not None
    assert out.status == "rejected"
    assert out.extra_metadata["rejected_reason"] == "manual review said no"
    assert "rejected_at" in out.extra_metadata


def test_reject_already_rendered_raises(db):
    """Cannot reject a candidate that was already rendered."""
    from app.models.asset import Asset, AssetStatus
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate, CandidateStatus
    from app.services.candidate_lifecycle import reject_candidate

    c = Campaign(name=f"lc-{uuid.uuid4().hex[:6]}", source_provider="youtube")
    db.add(c)
    db.commit()
    a = Asset(
        campaign_id=c.id, source_url="https://example.com/v",
        source_provider="youtube", source_id="x", asset_type="video",
        status=AssetStatus.TRANSCRIBED.value, duration_seconds=300.0,
        extra_metadata={"transcription": {"text": "x"}},
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    cand = Candidate(
        campaign_id=c.id, asset_id=a.id,
        start_time=10.0, end_time=40.0,
        score=0.5, reasoning="x",
        status=CandidateStatus.RENDERED.value,  # already rendered
        extra_metadata={},
    )
    db.add(cand)
    db.commit()
    db.refresh(cand)

    with pytest.raises(ValueError):
        reject_candidate(db, cand.id)


# --- Endpoint tests --------------------------------------------------------

def test_approve_endpoint_creates_render_job(client, auth_headers):
    cid, aid = _seed(client, auth_headers)
    cand = _create_candidate(client, auth_headers, cid, aid, 10.0, 40.0)
    r = client.post(f"/candidates/{cand['id']}/approve", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "approved"
    assert data["render_job_id"] is not None


def test_approve_endpoint_404(client, auth_headers):
    r = client.post(
        f"/candidates/{uuid.uuid4()}/approve", headers=auth_headers
    )
    assert r.status_code == 404


def test_reject_endpoint(client, auth_headers):
    cid, aid = _seed(client, auth_headers)
    cand = _create_candidate(client, auth_headers, cid, aid, 10.0, 40.0)
    r = client.post(
        f"/candidates/{cand['id']}/reject",
        json={"reason": "manual test"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_reject_endpoint_404(client, auth_headers):
    r = client.post(
        f"/candidates/{uuid.uuid4()}/reject", headers=auth_headers
    )
    assert r.status_code == 404


def test_lifecycle_endpoints_require_auth(client):
    """No auth header -> 401/403. Use a valid random UUID for the path."""
    r = client.post(f"/candidates/{uuid.uuid4()}/approve")
    assert r.status_code in (401, 403)
    r2 = client.post(f"/candidates/{uuid.uuid4()}/reject")
    assert r2.status_code in (401, 403)
