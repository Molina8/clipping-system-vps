"""Step 18 (architecture_flow.md) — clip per-campaign storage tests.

Pins the contract between the VPS (recording location + path) and the
Windows Worker (moving the .mp4 between per-campaign folders).

Storage locations:
    pending_upload  -> QA passed, awaiting social-media upload
    uploaded        -> already published to social media
    archived        -> taken out of the active rotation
    NULL            -> legacy clip, no location yet
"""
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.main import app
from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip
from app.models.job import Job  # noqa: F401  (registers FK target for clips.render_job_id / qa_job_id)
from app.services.clip_storage_service import (
    VALID_LOCATIONS,
    list_clips_by_campaign,
    mark_clip_uploaded,
    set_clip_location,
)


def _make_clip_in_db(location):
    """Insert campaign+asset+candidate+clip with the desired location. Returns (clip_id, campaign_id).

    Uses its own SessionLocal so it survives the conftest autouse cleanup
    that wipes `jobs` between tests (this helper does not touch `jobs`).
    """
    db = SessionLocal()
    try:
        c = Campaign(
            name=f"test_storage_{uuid.uuid4().hex[:8]}",
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
            end_time=60.0,
            score=0.8,
            extra_metadata={},
        )
        db.add(cand)
        db.flush()
        clip = Clip(
            campaign_id=c.id,
            asset_id=a.id,
            candidate_id=cand.id,
            file_path=f"C:\\test\\{uuid.uuid4()}.mp4",
            qa_status="pass",
            qa_result={},
            status="approved",
            location=location,
        )
        db.add(clip)
        db.commit()
        db.refresh(clip)
        return clip.id, c.id
    finally:
        db.close()


# ── Service-level tests (pytest-style, using the `db` fixture) ────────────────


class TestClipStorageService(unittest.TestCase):
    """Unit tests for app.services.clip_storage_service."""

    def setUp(self):
        # Fresh session per test; flush pending state from earlier tests.
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_set_clip_location_pending(self):
        clip_id, _ = _make_clip_in_db(None)
        clip = set_clip_location(
            self.db, clip_id, "pending_upload",
            final_path_worker=r"C:\CODIANT\clipping\storage\clips\1\pending_upload\x.mp4",
        )
        self.assertIsNotNone(clip)
        self.assertEqual(clip.location, "pending_upload")
        self.assertEqual(
            clip.final_path_worker,
            r"C:\CODIANT\clipping\storage\clips\1\pending_upload\x.mp4",
        )
        self.assertIsNotNone(clip.location_updated_at)

    def test_set_clip_location_invalid_raises(self):
        clip_id, _ = _make_clip_in_db(None)
        with self.assertRaises(ValueError):
            set_clip_location(self.db, clip_id, "nonsense")

    def test_mark_clip_uploaded_stamps_published_at(self):
        clip_id, _ = _make_clip_in_db("pending_upload")
        clip = mark_clip_uploaded(
            self.db, clip_id,
            final_path_worker=r"C:\CODIANT\clipping\storage\clips\1\uploaded\x.mp4",
        )
        self.assertIsNotNone(clip)
        self.assertEqual(clip.location, "uploaded")
        self.assertIsNotNone(clip.published_at)

    def test_list_clips_by_campaign_filters_location(self):
        _, campaign_id = _make_clip_in_db("pending_upload")
        # New session to avoid cache issues.
        db = SessionLocal()
        try:
            all_clips = list_clips_by_campaign(db, campaign_id)
            self.assertEqual(len(all_clips), 1, f"all_clips={[(c.id, c.location) for c in all_clips]}")
            only_pending = list_clips_by_campaign(db, campaign_id, location="pending_upload")
            self.assertEqual(len(only_pending), 1)
            only_uploaded = list_clips_by_campaign(db, campaign_id, location="uploaded")
            self.assertEqual(len(only_uploaded), 0)
        finally:
            db.close()


# ── HTTP-level tests (TestClient against FastAPI) ────────────────────────────


class TestClipStorageAPI(unittest.TestCase):
    """HTTP-level tests for the /clips/storage endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.headers = {"Authorization": f"Bearer {settings.api_token}"}

    def test_list_by_campaign_storage(self):
        clip_id, campaign_id = _make_clip_in_db("pending_upload")
        r = self.client.get(
            f"/clips/by_campaign/{campaign_id}/storage",
            params={"location": "pending_upload"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        ids = [c["id"] for c in r.json()]
        self.assertIn(str(clip_id), ids)

    def test_mark_uploaded(self):
        clip_id, _ = _make_clip_in_db("pending_upload")
        r = self.client.post(
            f"/clips/{clip_id}/mark_uploaded",
            params={"final_path_worker": r"C:\CODIANT\storage\u\x.mp4"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["location"], "uploaded")
        self.assertIsNotNone(body["published_at"])

    def test_mark_uploaded_404(self):
        r = self.client.post(
            f"/clips/{uuid.uuid4()}/mark_uploaded",
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 404)

    def test_set_location_invalid(self):
        clip_id, _ = _make_clip_in_db("pending_upload")
        r = self.client.post(
            f"/clips/{clip_id}/location/nonsense",
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 400)

    def test_set_location_archived(self):
        clip_id, _ = _make_clip_in_db("uploaded")
        r = self.client.post(
            f"/clips/{clip_id}/location/archived",
            params={"final_path_worker": r"C:\CODIANT\storage\a\x.mp4"},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["location"], "archived")


if __name__ == "__main__":
    unittest.main()
