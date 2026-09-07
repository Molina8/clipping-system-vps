"""
tests/test_whop_campaign_detail.py
==================================
Tests for app.whop_integration.campaign_detail_fetcher.

Validates:
- All sub-dataclasses (PayoutRule, ReferenceMaterial, TopEarner, DailyMetric)
  normalize correctly from raw API responses.
- CampaignDetail.from_raw covers every documented field.
- Brace-matching survives escaped quotes inside descriptions.
- Failure modes raise CampaignDetailFetchError with helpful messages.
- (Network) Live fetch against the public /campaigns/<id> endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.whop_integration.campaign_detail_fetcher import (
    DEFAULT_APP_BASE_URL,
    CampaignDetail,
    CampaignDetailFetchError,
    CampaignDetailFetcher,
    DailyMetric,
    PayoutRule,
    ReferenceMaterial,
    TopEarner,
)


SAMPLE_HTML_PATH = Path("/tmp/cs_camp_detail.html")


@pytest.fixture(scope="session")
def sample_html() -> str:
    """Load the canned /campaigns/<id> HTML captured during research."""
    if not SAMPLE_HTML_PATH.exists():
        pytest.skip(
            f"Canned HTML sample not available at {SAMPLE_HTML_PATH}. "
            "Re-run the research step that captures /tmp/cs_camp_detail.html."
        )
    return SAMPLE_HTML_PATH.read_text(errors="ignore")


@pytest.fixture(scope="session")
def raw_campaign(sample_html: str) -> dict:
    fetcher = CampaignDetailFetcher()
    return fetcher._extract_campaign_object(sample_html)


# ---------------- sub-dataclass tests ----------------

class TestPayoutRule:
    def test_from_raw_basic(self):
        raw = {
            "maxPayoutCents": 10500,
            "minPayoutCents": 2800,
            "payoutType": "cpm",
            "platform": "instagram",
            "rateCents": 100,
        }
        p = PayoutRule.from_raw(raw)
        assert p.platform == "instagram"
        assert p.payout_type == "cpm"
        assert p.rate_cents == 100
        assert p.min_payout_cents == 2800
        assert p.max_payout_cents == 10500

    def test_usd_helpers(self):
        p = PayoutRule(
            platform="tiktok",
            payout_type="cpm",
            rate_cents=175,
            min_payout_cents=3500,
            max_payout_cents=15000,
        )
        assert p.rate_per_1k_usd == 1.75
        assert p.min_payout_usd == 35.0
        assert p.max_payout_usd == 150.0


class TestReferenceMaterial:
    def test_from_raw(self):
        raw = {
            "mediaType": "external",
            "type": "brandAsset",
            "url": "https://drive.google.com/drive/folders/XXX?usp=sharing",
        }
        rm = ReferenceMaterial.from_raw(raw)
        assert rm.url.startswith("https://drive.google.com/")
        assert rm.type == "brandAsset"
        assert rm.media_type == "external"


class TestTopEarner:
    def test_from_raw(self):
        raw = {
            "approvedSubmissionCount": 2200,
            "earnedCents": 2134727,
            "rank": 1,
            "totalViews": 42194044,
            "userId": "abc-123",
            "username": "antoinedrd",
        }
        t = TopEarner.from_raw(raw)
        assert t.rank == 1
        assert t.username == "antoinedrd"
        assert t.approved_submission_count == 2200
        assert t.earned_usd == 21347.27


class TestDailyMetric:
    def test_from_raw(self):
        raw = {
            "approvedSubmissionCount": 134,
            "bucket": "2026-09-01T00:00:00.000Z",
            "totalViews": 265718,
        }
        d = DailyMetric.from_raw(raw)
        assert d.bucket == "2026-09-01T00:00:00.000Z"
        assert d.approved_submission_count == 134
        assert d.total_views == 265718


# ---------------- CampaignDetail normalization ----------------

class TestCampaignDetailFromRaw:
    def test_full_normalization(self, raw_campaign: dict):
        c = CampaignDetail.from_raw(raw_campaign)
        # Identity
        assert c.id == "14f743b2-c5ca-4f3e-8c0d-3010c485005d"
        assert c.name == "Yomi Denzel Clipping - 1$ par 1000 vues"
        assert c.organization_name == "Yomi Denzel Clipping"
        assert c.organization_verified is True
        assert c.organization_experience_id == "exp_G3sOXQjjhODJPX"
        assert c.status == "active"
        assert c.private is False
        # Description
        assert "Yomi Denzel" in c.description
        # Payout rules
        assert c.payout_type == "cpm"
        assert set(c.platforms) == {"instagram", "tiktok", "youtube"}
        assert len(c.payouts) == 3
        ig = next(p for p in c.payouts if p.platform == "instagram")
        assert ig.rate_per_1k_usd == 1.0
        assert ig.min_payout_usd == 28.0
        assert ig.max_payout_usd == 105.0
        # Reference materials
        assert len(c.reference_materials) >= 1
        assert any("drive.google.com" in rm.url for rm in c.reference_materials)
        # Budget
        assert c.budget_usd == 238000.0
        assert c.budget_spent_usd > 200000.0
        assert c.budget_progress_pct > 90.0
        # Gating
        assert c.requires_application is True
        # Metrics
        assert c.creator_count >= 200
        assert c.total_views > 400_000_000
        assert len(c.top_earners) >= 3
        assert len(c.chart_points) >= 30
        # Assets
        assert c.banner_url.startswith("https://")
        assert c.organization_logo_src.startswith("https://")

    def test_usd_helpers(self, raw_campaign: dict):
        c = CampaignDetail.from_raw(raw_campaign)
        # All derived values are floats
        assert isinstance(c.budget_usd, float)
        assert isinstance(c.budget_available_usd, float)
        assert isinstance(c.paid_out_usd, float)
        assert isinstance(c.primary_rate_usd_per_1k, float)
        assert c.budget_available_usd > 0  # not fully spent

    def test_is_active(self, raw_campaign: dict):
        c = CampaignDetail.from_raw(raw_campaign)
        assert c.is_active is True

    def test_has_drive_assets(self, raw_campaign: dict):
        c = CampaignDetail.from_raw(raw_campaign)
        assert c.has_drive_assets is True

    def test_payout_for_platform(self, raw_campaign: dict):
        c = CampaignDetail.from_raw(raw_campaign)
        ig = c.payout_for_platform("instagram")
        assert ig is not None
        assert ig.platform == "instagram"
        assert ig.rate_per_1k_usd == 1.0
        # Case-insensitive lookup
        assert c.payout_for_platform("Instagram") is not None
        # Unknown platform
        assert c.payout_for_platform("snapchat") is None

    def test_to_dict_roundtrip(self, raw_campaign: dict):
        c = CampaignDetail.from_raw(raw_campaign)
        d = c.to_dict()
        assert d["id"] == c.id
        assert d["name"] == c.name
        assert isinstance(d["payouts"], list)
        assert isinstance(d["reference_materials"], list)
        assert isinstance(d["chart_points"], list)

    def test_minimal_required_fields(self):
        c = CampaignDetail.from_raw({"id": "abc", "name": "Test"})
        assert c.id == "abc"
        assert c.name == "Test"
        assert c.platforms == []
        assert c.payouts == []
        assert c.reference_materials == []
        assert c.organization_verified is False
        assert c.requires_application is False
        assert c.is_active is False
        assert c.has_drive_assets is False


# ---------------- parsing edge cases ----------------

class TestHtmlParsingEdgeCases:
    def test_no_rsc_chunks_raises(self):
        with pytest.raises(CampaignDetailFetchError, match="No RSC chunks found"):
            CampaignDetailFetcher._extract_campaign_object("<html><body>nope</body></html>")

    def test_no_status_marker_raises(self):
        bad = '<html><script>self.__next_f.push([1,"hello world"])</script></html>'
        with pytest.raises(CampaignDetailFetchError, match="status marker"):
            CampaignDetailFetcher._extract_campaign_object(bad)

    def test_falls_back_to_paused_status(self):
        # Should pick up paused campaigns too
        html = '<script>self.__next_f.push([1,"{\"status\":\"paused\",\"name\":\"x\"}"])</script>'
        obj = CampaignDetailFetcher._extract_campaign_object(html)
        assert obj["status"] == "paused"
        assert obj["name"] == "x"

    def test_handles_escaped_quotes_in_description(self):
        # Real Whop HTML has literal backslash-quote sequences (\\") in chunk
        # content. The decode("unicode_escape") step turns those into ", which
        # JSON parses as a literal " inside the string value. This test exercises
        # the full decode path with that pattern.
        html = (
            '<script>self.__next_f.push([1,"'
            '{"status":"active","name":"x","description":"some \\\\\\"quoted\\\\\\" text"}'
            '"])</script>'
        )
        obj = CampaignDetailFetcher._extract_campaign_object(html)
        assert obj["name"] == "x"
        assert 'quoted' in obj["description"]


# ---------------- fetcher defaults ----------------

class TestFetcherDefaults:
    def test_default_app_base_url(self):
        f = CampaignDetailFetcher()
        assert f.app_base_url == DEFAULT_APP_BASE_URL
        assert f.app_base_url.startswith("https://")

    def test_default_user_agent_present(self):
        f = CampaignDetailFetcher()
        assert "Mozilla" in f.user_agent

    def test_url_trailing_slash_stripped(self):
        f = CampaignDetailFetcher(app_base_url="https://example.com/")
        assert f.app_base_url == "https://example.com"


@pytest.mark.network
class TestLiveFetch:
    """Optional: hit the real public endpoint if reachable from CI."""

    def test_live_campaign_detail_returns_full_object(self):
        # Use the same campaign id we captured during research
        cid = "14f743b2-c5ca-4f3e-8c0d-3010c485005d"
        f = CampaignDetailFetcher(timeout=15)
        detail = f.fetch_campaign(cid)
        assert isinstance(detail, CampaignDetail)
        assert detail.id == cid
        assert detail.is_active
        assert detail.payout_for_platform("instagram") is not None
        # At least one Drive link in reference materials
        assert any("drive.google.com" in rm.url for rm in detail.reference_materials)
