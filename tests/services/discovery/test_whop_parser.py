"""Unit tests for WhopProvider.fetch_detail refined parser."""
import unittest
import unittest.mock as mock

from app.services.discovery.providers.whop import WhopProvider


DETAIL_HTML_OFFICIAL = """
<html>
<body>
<a href="https://www.youtube.com">YouTube</a>
<a href="https://www.tiktok.com">TikTok</a>
<a href="https://www.youtube.com/@WhopIO">Whop YouTube</a>
<a href="https://www.tiktok.com/@whophq">Whop TikTok</a>
<a href="https://www.instagram.com/whop">Whop IG</a>
<a href="https://x.com/whop">Whop X</a>
</body>
</html>
"""

DETAIL_HTML_REAL_ASSETS = """
<html>
<body>
<a href="https://drive.google.com/file/d/abc123/view">Drive file</a>
<a href="https://docs.google.com/spreadsheets/d/xyz789/edit">Campaign sheet</a>
<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">Specific video</a>
<a href="https://mega.nz/file/abcdef">Mega archive</a>
<a href="https://dropbox.com/s/foo/campaign.zip?dl=0">Dropbox zip</a>
<a href="https://assets-2-prod.whop.com/public/uploads/user_13472183/image/bots/2025-11-24/abc.jpg">User avatar</a>
</body>
</html>
"""

DETAIL_HTML_NO_ASSETS = """
<html>
<body>
<p>This campaign is gated behind join. Sign up to access assets.</p>
</body>
</html>
"""


class TestWhopParser(unittest.TestCase):
    def setUp(self):
        self.provider = WhopProvider(
            tenant_url="https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com/discover"
        )

    def _run(self, html_text):
        """Run fetch_detail with mocked HTML and return the DiscoveredCampaign."""
        from app.services.discovery.models import DiscoveredCampaign
        c = DiscoveredCampaign(
            name="Test Campaign",
            provider="whop",
            external_id="exp_TEST/camp_TEST",
            detail_url="https://whop.com/experiences/exp_TEST/campaigns/camp_TEST",
            raw={},
        )
        with mock.patch.object(self.provider, "_fetch", return_value=html_text):
            return self.provider.fetch_detail(c)

    def test_filters_official_whop_accounts(self):
        """Bare social roots and /whop handles must be filtered out.

        @WhopIO and @whophq are NOT filtered (conservative parser), but they
        alone do NOT trigger join_required because some links did remain.
        """
        out = self._run(DETAIL_HTML_OFFICIAL)
        urls = out.asset_links
        # 4 of the 6 are filtered:
        #   - 2 bare roots (https://www.youtube.com, https://www.tiktok.com)
        #   - 2 /whop handles (https://www.instagram.com/whop, https://x.com/whop)
        # 2 remain (@WhopIO, @whophq) because they are profile pages of Whop
        # staff accounts and the parser is conservative.
        self.assertEqual(len(urls), 2)
        joined = "\n".join(urls)
        self.assertIn("@WhopIO", joined)
        self.assertIn("@whophq", joined)
        # join_required is NOT set because at least one URL survived.
        self.assertNotIn("join_required", out.raw)

    def test_keeps_non_official_youtube_watch(self):
        """Specific YouTube watch URLs must be kept (not filtered as official)."""
        out = self._run("""<html><a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">v</a></html>""")
        self.assertEqual(len(out.asset_links), 1)
        self.assertIn("watch?v=dQw4w9WgXcQ", out.asset_links[0])

    def test_keeps_real_asset_hosts(self):
        """URLs to drive/youtube-specific/sheets/mega/dropbox/whop-cdn must be kept."""
        out = self._run(DETAIL_HTML_REAL_ASSETS)
        urls = out.asset_links
        joined = "\n".join(urls)
        # Real asset hosts preserved
        self.assertIn("drive.google.com/file/d/abc123", joined)
        self.assertIn("docs.google.com/spreadsheets/d/xyz789", joined)
        self.assertIn("youtube.com/watch?v=dQw4w9WgXcQ", joined)
        self.assertIn("mega.nz/file/abcdef", joined)
        self.assertIn("dropbox.com/s/foo/campaign.zip", joined)
        self.assertIn("assets-2-prod.whop.com/public/uploads/user_13472183", joined)
        # join_required should NOT be set when assets were found
        self.assertNotIn("join_required", out.raw)

    def test_no_assets_marks_join_required(self):
        """Empty/whop-only detail should set join_required=True."""
        out = self._run(DETAIL_HTML_NO_ASSETS)
        self.assertEqual(out.asset_links, [])
        self.assertTrue(out.raw.get("join_required"))

    def test_dedupes_assets(self):
        """Repeated URLs should be deduplicated preserving order."""
        html = """
        <a href="https://drive.google.com/file/d/aaa">x</a>
        <a href="https://drive.google.com/file/d/aaa">y</a>
        <a href="https://drive.google.com/file/d/bbb">z</a>
        """
        out = self._run(html)
        self.assertEqual(len(out.asset_links), 2)
        self.assertEqual(out.asset_links[0], "https://drive.google.com/file/d/aaa")
        self.assertEqual(out.asset_links[1], "https://drive.google.com/file/d/bbb")


if __name__ == "__main__":
    unittest.main()
