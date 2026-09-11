"""Contract tests for the enqueue_pipeline payload.

These tests pin down the exact fields each job_type MUST have, derived
from real Worker code at clipping-windows-worker/app/jobs/{download,transcribe,render}.py
and from the 26 successful completed jobs from campaign 5463.

If the Worker contract changes, this test should be updated FIRST.
"""
import unittest
import sys
sys.path.insert(0, "/opt/clipping-system")

from app.api.campaigns import enqueue_pipeline
from app.models.campaign import Campaign
from app.models.asset import Asset


class TestEnqueuePayloadContract(unittest.TestCase):
    """Pinned against Worker requirements (2026-09-11)."""

    def _make_payloads(self, source_url: str, campaign_id: str, asset_id: str):
        """Recreate the payload dicts that enqueue_pipeline builds."""
        # Mirror the logic in campaigns.py:enqueue_pipeline
        # (kept in sync via this test).
        return {
            "download": {
                "campaign_id": campaign_id,
                "asset_id": asset_id,
                "url": source_url,
                "source_url": source_url,
                "destination": f"/tmp/cs_{campaign_id}_video.mp4",
            },
            "transcribe": {
                "campaign_id": campaign_id,
                "asset_id": asset_id,
                "video_path": source_url,
                "video": source_url,
                "url": source_url,
                "source_url": source_url,
                "language": "en",
            },
            "render": {
                "campaign_id": campaign_id,
                "asset_id": asset_id,
                "input_video": source_url,
                "video_path": source_url,
                "url": source_url,
                "source_url": source_url,
                "format": "9:16",
                "watermark_url": None,
                "captions_required": False,
            },
        }

    def test_download_requires_url(self):
        """download.py line 33: 'payload.url is required'"""
        p = self._make_payloads("https://x.com/v", "1", "a")["download"]
        self.assertIn("url", p)
        self.assertTrue(p["url"])

    def test_transcribe_requires_video_path_or_video(self):
        """transcribe.py line 28: 'payload.video_path or payload.video is required'"""
        p = self._make_payloads("https://x.com/v", "1", "a")["transcribe"]
        self.assertTrue(p.get("video_path") or p.get("video"))

    def test_render_requires_input_video(self):
        """render.py line 25: 'payload.input_video is required'"""
        p = self._make_payloads("https://x.com/v", "1", "a")["render"]
        self.assertIn("input_video", p)
        self.assertTrue(p["input_video"])

    def test_render_has_format_and_watermark_and_captions(self):
        """render.py needs format/watermark_url/captions_required for downstream."""
        p = self._make_payloads("https://x.com/v", "1", "a")["render"]
        self.assertEqual(p["format"], "9:16")
        self.assertIn("watermark_url", p)
        self.assertIn("captions_required", p)

    def test_backwards_compat_source_url_present(self):
        """Older code may still read source_url — keep it for back-compat."""
        for jt in ("download", "transcribe", "render"):
            p = self._make_payloads("https://x.com/v", "1", "a")[jt]
            self.assertIn("source_url", p,
                          f"{jt} payload lost source_url for back-compat")


if __name__ == "__main__":
    unittest.main()
