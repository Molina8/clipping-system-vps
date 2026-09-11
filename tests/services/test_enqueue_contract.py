"""Contract tests for the enqueue_pipeline endpoint.

These tests pin down the *runtime behaviour* of `POST /campaigns/{id}/enqueue`,
exercising the real endpoint through TestClient (not a mirrored payload
dictionary).

Source of truth for the Worker contract:
  clipping-windows-worker/app/jobs/download.py
  clipping-windows-worker/app/jobs/transcribe.py

Why "exactly 1 download job" matters (2026-09-11 race-fix):
  The endpoint used to create download + transcribe + render in one batch,
  but transcribe and render were sent with `source_url` instead of the local
  downloaded path. The Worker therefore tried to transcribe a URL that was
  not yet on disk. The fix is to enqueue ONLY the download here and let
  job_state_transitions.on_download_completed auto-create transcribe using
  asset.local_path (the real file). Render is created later by
  candidate_lifecycle.approve_candidate.
"""
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.database import SessionLocal
from app.main import app
from app.models.asset import Asset
from app.services.job_state_transitions import on_download_completed


def _make_ready_campaign(client, headers, source_url):
    """Create a campaign in status='ready' with one processable video asset.

    The campaign status transitions are normally driven by the analyze cron
    loop, so we set status directly via SQL to keep the test focused on
    the enqueue contract.
    """
    name = f"enqueue-ct-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/campaigns",
        json={
            "name": name,
            "source_provider": "youtube",
            "source_id": "dQw4w9WgXcQ",
            "source_url": source_url,
            "source_metadata": {"channel": "test"},
            "spec": {
                "duration_min": 20,
                "duration_max": 60,
                "captions_required": True,
                "format": "9:16",
                "keywords": ["ai"],
            },
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]

    # Force status='ready' directly — bypass the analyze cron loop for this test.
    sess = SessionLocal()
    try:
        sess.execute(
            text("UPDATE campaigns SET status='ready' WHERE id=:id"),
            {"id": cid},
        )
        sess.commit()

        a = Asset(
            campaign_id=cid,
            source_url=source_url,
            source_provider="youtube",
            asset_type="video",
        )
        sess.add(a)
        sess.commit()
        sess.refresh(a)
        asset_id = str(a.id)
    finally:
        sess.close()

    return cid, asset_id


class TestEnqueuePipelineContract(unittest.TestCase):
    """Real-endpoint contract tests (2026-09-11)."""

    def setUp(self):
        self.client = TestClient(app)
        from app.config import settings
        self.headers = {"Authorization": f"Bearer {settings.api_token}"}
        # Clean jobs table for isolation.
        sess = SessionLocal()
        try:
            sess.execute(text("DELETE FROM jobs"))
            sess.commit()
        finally:
            sess.close()

    def test_enqueue_creates_exactly_one_download_job(self):
        """Race-fix: enqueue creates ONLY download, not transcribe or render."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        cid, _ = _make_ready_campaign(self.client, self.headers, url)

        r = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()

        self.assertEqual(body["total_created"], 1, f"Expected 1 job, got {body}")
        self.assertEqual(len(body["created"]), 1)
        self.assertEqual(body["created"][0]["job_type"], "download")
        self.assertEqual(body["created"][0]["campaign_id"], str(cid))

    def test_download_payload_contains_url_field(self):
        """Worker contract: download.py requires payload['url']."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        cid, _ = _make_ready_campaign(self.client, self.headers, url)

        r = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)

        sess = SessionLocal()
        try:
            row = sess.execute(
                text(
                    "SELECT id, payload FROM jobs "
                    "WHERE job_type='download' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            ).first()
            self.assertIsNotNone(row, "download job was not persisted")
            payload = row.payload
            self.assertIn("url", payload, "download payload missing 'url' (Worker requires it)")
            self.assertEqual(payload["url"], url)
            self.assertEqual(payload["source_url"], url)  # backwards-compat
        finally:
            sess.close()

    def test_no_transcribe_or_render_jobs_created_at_enqueue(self):
        """transcribe/render must NOT be pre-created; they are chained later."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        cid, _ = _make_ready_campaign(self.client, self.headers, url)

        r = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)

        sess = SessionLocal()
        try:
            rows = sess.execute(
                text("SELECT job_type FROM jobs WHERE payload->>'campaign_id' = :cid"),
                {"cid": str(cid)},
            ).all()
            types = [row.job_type for row in rows]
            self.assertIn("download", types)
            self.assertNotIn(
                "transcribe", types,
                "transcribe must not be pre-created (race-fix: it's chained after download)",
            )
            self.assertNotIn(
                "render", types,
                "render must not be pre-created (race-fix: it's chained after candidate approval)",
            )
        finally:
            sess.close()

    def test_enqueue_is_idempotent_on_duplicate_call(self):
        """Calling enqueue twice for the same campaign should not create 2 jobs."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        cid, _ = _make_ready_campaign(self.client, self.headers, url)

        r1 = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r1.status_code, 200, r1.text)
        self.assertEqual(r1.json()["total_created"], 1)

        r2 = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["total_created"], 0)
        self.assertEqual(r2.json()["total_skipped"], 1)

    def test_enqueue_requires_campaign_in_ready_status(self):
        """Campaign in 'draft' cannot be enqueued (returns 409)."""
        # Create campaign (status will be 'draft') without forcing 'ready'.
        name = f"draft-{uuid.uuid4().hex[:8]}"
        r = self.client.post(
            "/campaigns",
            json={
                "name": name,
                "source_provider": "youtube",
                "source_id": "x",
                "source_url": "https://youtube.com/watch?v=x",
                "spec": {"format": "9:16"},
            },
            headers=self.headers,
        )
        cid = r.json()["id"]

        r = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("ready", r.json()["detail"])

    def test_enqueue_returns_404_for_missing_campaign(self):
        r = self.client.post("/campaigns/999999999/enqueue", headers=self.headers)
        self.assertEqual(r.status_code, 404, r.text)

    def test_enqueue_includes_backlog_clip_selection_summary(self):
        """Option A (2026-09-11): enqueue_pipeline runs the backlog drain for
        transcribed assets without clip_selection. The summary is exposed
        in the response under `backlog_clip_selection`."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        cid, _ = _make_ready_campaign(self.client, self.headers, url)

        r = self.client.post(f"/campaigns/{cid}/enqueue", headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("backlog_clip_selection", body)
        self.assertIn("processed", body["backlog_clip_selection"])
        self.assertIn("skipped", body["backlog_clip_selection"])
        # processed/skipped are ints >= 0
        self.assertIsInstance(body["backlog_clip_selection"]["processed"], int)
        self.assertIsInstance(body["backlog_clip_selection"]["skipped"], int)
        self.assertGreaterEqual(body["backlog_clip_selection"]["processed"], 0)
        self.assertGreaterEqual(body["backlog_clip_selection"]["skipped"], 0)


def _make_job(sess, job_type, payload):
    from app.models.job import Job
    job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
        status="completed",
        priority=5,
        payload=payload,
    )
    sess.add(job)
    sess.commit()
    sess.refresh(job)
    return job


class TestOnDownloadCompletedCreatesTranscribe(unittest.TestCase):
    """Verify the auto-creation chain that replaces the old batch behaviour."""

    def setUp(self):
        from app.config import settings
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {settings.api_token}"}
        sess = SessionLocal()
        try:
            sess.execute(text("DELETE FROM jobs"))
            sess.commit()
        finally:
            sess.close()

    def test_on_download_completed_uses_local_path_in_transcribe_payload(self):
        """After download, the chained transcribe job must use asset.local_path,
        not the remote source_url. This is the whole point of the race-fix."""
        url = "https://youtube.com/watch?v=dQw4w9WgXcQ"
        _, asset_id = _make_ready_campaign(self.client, self.headers, url)

        sess = SessionLocal()
        try:
            job = _make_job(
                sess, "download",
                {"asset_id": asset_id, "url": url, "source_url": url},
            )

            on_download_completed(
                sess, job,
                result_data={
                    "file_path": "C:\\CODIANT\\clipping\\data\\video.mp4",
                    "file_size": 12345,
                    "duration_seconds": 30.0,
                },
            )

            sess.expire_all()
            rows = sess.execute(
                text(
                    "SELECT payload FROM jobs "
                    "WHERE job_type='transcribe' "
                    "AND payload->>'asset_id' = :aid"
                ),
                {"aid": asset_id},
            ).all()
            self.assertEqual(len(rows), 1)
            payload = rows[0].payload
            self.assertEqual(
                payload["video"], "C:\\CODIANT\\clipping\\data\\video.mp4",
                "transcribe payload must reference asset.local_path (post-download), "
                "not the remote URL — otherwise the Worker tries to read a URL it "
                "has not downloaded yet.",
            )
            self.assertEqual(
                payload["video_path"], "C:\\CODIANT\\clipping\\data\\video.mp4",
            )
        finally:
            sess.close()


if __name__ == "__main__":
    unittest.main()
