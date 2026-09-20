"""Milestone 1 — approve_publish gate."""
import uuid
from fastapi.testclient import TestClient

from app.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.job import Job  # noqa: F401
from app.services.publish_gate import approve_clip_publish, PublishGateError


def _make_clip(**overrides):
    db = SessionLocal()
    try:
        c = Campaign(
            name=f"test_pub_{uuid.uuid4().hex[:8]}",
            status=CampaignStatus.READY.value,
            source_provider="whop",
            source_url=f"https://whop.com/{uuid.uuid4()}",
            source_metadata={},
            source_instructions="",
            spec={},
        )
        db.add(c)
        db.flush()
        a = Asset(
            campaign_id=c.id,
            status=AssetStatus.TRANSCRIBED.value,
            source_url=f"https://whop.com/{uuid.uuid4()}",
            local_path=f"C:\\test\\{uuid.uuid4()}.mp4",
            extra_metadata={},
        )
        db.add(a)
        db.flush()
        cand = Candidate(
            campaign_id=c.id,
            asset_id=a.id,
            status=CandidateStatus.APPROVED.value,
            start_time=0.0,
            end_time=30.0,
            score=0.8,
            extra_metadata={},
        )
        db.add(cand)
        db.flush()
        fields = dict(
            campaign_id=c.id,
            asset_id=a.id,
            candidate_id=cand.id,
            file_path=f"C:\\test\\{uuid.uuid4()}.mp4",
            qa_status="pass",
            qa_result={},
            status="approved",
            location="pending_upload",
        )
        fields.update(overrides)
        clip = Clip(**fields)
        db.add(clip)
        db.commit()
        db.refresh(clip)
        return clip.id
    finally:
        db.close()


def test_approve_happy_path():
    clip_id = _make_clip()
    db = SessionLocal()
    try:
        clip, pubs, already = approve_clip_publish(db, clip_id)
        assert already is False
        assert clip.publish_approved_at is not None
        assert len(pubs) == 1
        assert pubs[0].platform == "youtube"
        assert pubs[0].status == "pending"
        _, pubs2, already2 = approve_clip_publish(db, clip_id)
        assert already2 is True
        assert len(pubs2) == 1
    finally:
        db.close()


def test_approve_rejects_not_pending_upload():
    clip_id = _make_clip(location=None, qa_status="pass", status="approved")
    db = SessionLocal()
    try:
        try:
            approve_clip_publish(db, clip_id)
            assert False, "expected PublishGateError"
        except PublishGateError as e:
            assert "pending_upload" in str(e)
    finally:
        db.close()


def test_approve_rejects_bad_platform():
    clip_id = _make_clip()
    db = SessionLocal()
    try:
        try:
            approve_clip_publish(db, clip_id, platforms=["myspace"])
            assert False, "expected PublishGateError"
        except PublishGateError:
            pass
    finally:
        db.close()


def test_http_approve_publish():
    clip_id = _make_clip()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    r = client.post(f"/clips/{clip_id}/approve_publish", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["already_approved"] is False
    assert body["clip"]["publish_approved_at"] is not None
    assert body["publications"][0]["platform"] == "youtube"

    r2 = client.post(f"/clips/{clip_id}/approve_publish", headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["already_approved"] is True

    missing = client.post(f"/clips/{uuid.uuid4()}/approve_publish", headers=headers)
    assert missing.status_code == 404


def test_http_social_accounts():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    r = client.get("/social_accounts", headers=headers)
    assert r.status_code == 200, r.text
    platforms = [row["platform"] for row in r.json()]
    assert "youtube" in platforms
