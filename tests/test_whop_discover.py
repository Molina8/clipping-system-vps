"""
tests/test_whop_discover.py
============================
Tests for app.whop_integration.discover_fetcher.

Validates:
- HTML parsing extracts the campaigns array from RSC chunks.
- The Campaign dataclass normalizes all known fields.
- Brace-matching survives escaped quotes inside descriptions.
- Failure modes raise DiscoverFetchError with helpful messages.
- (Network) Live fetch against the public /discover endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.whop_integration.discover_fetcher import (
    DEFAULT_APP_BASE_URL,
    Campaign,
    DiscoverFetchError,
    DiscoverFetcher,
)


SAMPLE_HTML_PATH = Path("/tmp/cs_app_disc.html")


@pytest.fixture(scope="session")
def sample_html() -> str:
    """Load the canned /discover HTML captured during research.

    If the file is missing (e.g., on a fresh CI runner), the test is skipped.
    """
    if not SAMPLE_HTML_PATH.exists():
        pytest.skip(
            f"Canned HTML sample not available at {SAMPLE_HTML_PATH}. "
            "Re-run the research step that captures /tmp/cs_app_disc.html."
        )
    return SAMPLE_HTML_PATH.read_text(errors="ignore")


@pytest.fixture(scope="session")
def raw_campaigns(sample_html: str) -> list[dict]:
    fetcher = DiscoverFetcher()
    return fetcher._extract_campaigns_array(sample_html)


class TestCampaignFromRaw:
    def test_minimal_required_fields(self):
        c = Campaign.from_raw({"id": "abc", "title": "Test"})
        assert c.id == "abc"
        assert c.title == "Test"
        assert c.brand == ""
        assert c.campaign_type == ""
        assert c.cpm_label == ""
        assert c.payout_sort_raw == 0.0
        assert c.platforms == []

    def test_full_normalization(self):
        raw = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "title": "Yomi Denzel Clipping - 1$ par 1000 vues",
            "brand": "Yomi Denzel Clipping",
            "description": "Clippe le contenu",
            "category": "Personal Brand",
            "type": "cpm",
            "ratePer1kLabel": "$$1",
            "payoutSortRaw": 1.0,
            "budgetTotalLabel": "$$238K",
            "budgetTotalRaw": 238000,
            "budgetSpentLabel": "$$219K",
            "budgetSpentRaw": 219334.41,
            "availableBudgetRaw": 18665.59,
            "creatorCountRaw": 267,
            "fundedAgo": "9mo",
            "createdAtMs": 1786790447000,
            "thumbnail": "https://example.com/x.png",
            "avatar": "https://example.com/a.jpg",
            "avatarSeed": "seed-1",
            "isVerified": True,
            "requiresApplication": False,
            "platforms": ["tiktok", "instagram"],
            "progressPercentage": 92.1,
            "organizationExperienceId": "exp_123",
        }
        c = Campaign.from_raw(raw)
        assert c.id == raw["id"]
        assert c.brand == "Yomi Denzel Clipping"
        assert c.campaign_type == "cpm"
        assert c.cpm_label == "$$1"
        assert c.payout_sort_raw == 1.0
        assert c.budget_total_raw == 238000
        assert c.budget_spent_raw == 219334.41
        assert c.available_budget_raw == 18665.59
        assert c.creator_count_raw == 267
        assert c.platforms == ["tiktok", "instagram"]
        assert c.is_verified is True
        assert c.requires_application is False
        assert c.progress_percentage == 92.1

    def test_platforms_string_to_list(self):
        c = Campaign.from_raw({"id": "x", "platforms": "tiktok, instagram, youtube"})
        assert c.platforms == ["tiktok", "instagram", "youtube"]

    def test_platforms_none_becomes_empty_list(self):
        c = Campaign.from_raw({"id": "x"})
        assert c.platforms == []

    def test_to_dict_roundtrip(self):
        raw = {"id": "x", "title": "t", "payoutSortRaw": "2.5"}
        c = Campaign.from_raw(raw)
        d = c.to_dict()
        assert d["id"] == "x"
        assert d["payout_sort_raw"] == 2.5
        assert d["title"] == "t"


class TestExtractCampaignsArray:
    def test_extracts_at_least_50_campaigns(self, raw_campaigns: list[dict]):
        assert len(raw_campaigns) >= 50, (
            f"Expected >=50 campaigns on /discover, got {len(raw_campaigns)}"
        )

    def test_each_campaign_has_id_and_title(self, raw_campaigns: list[dict]):
        missing = [i for i, c in enumerate(raw_campaigns)
                   if not c.get("id") or not c.get("title")]
        assert missing == [], f"Campaigns missing id/title at indexes: {missing}"

    def test_known_campaign_present(self, raw_campaigns: list[dict]):
        titles = [c.get("title", "").lower() for c in raw_campaigns]
        assert any("yomi denzel" in t for t in titles), (
            "Expected 'Yomi Denzel Clipping' in /discover first page"
        )

    def test_no_html_garbage_in_descriptions(self, raw_campaigns: list[dict]):
        for c in raw_campaigns:
            d = c.get("description") or ""
            assert "<script" not in d.lower()
            assert "self.__next_f" not in d


class TestHtmlParsingEdgeCases:
    def test_no_rsc_chunks_raises(self):
        with pytest.raises(DiscoverFetchError, match="No RSC chunks found"):
            DiscoverFetcher._extract_campaigns_array("<html><body>nope</body></html>")

    def test_missing_campaigns_marker_raises(self):
        bad = '<html><script>self.__next_f.push([1,"hello"])</script></html>'
        with pytest.raises(DiscoverFetchError, match="No 'campaigns' array found"):
            DiscoverFetcher._extract_campaigns_array(bad)

    def test_unbalanced_brackets_raises(self):
        bad = '<html><script>self.__next_f.push([1,"\"campaigns\":[{\"id\":\"a\""])</script></html>'
        with pytest.raises(DiscoverFetchError, match="Unbalanced brackets"):
            DiscoverFetcher._extract_campaigns_array(bad)

    def test_invalid_json_raises(self):
        bad = '<html><script>self.__next_f.push([1,"\"campaigns\":[{not-json]"])</script></html>'
        with pytest.raises(DiscoverFetchError, match="Failed to parse campaigns JSON"):
            DiscoverFetcher._extract_campaigns_array(bad)


class TestFetcherDefaults:
    def test_default_app_base_url(self):
        f = DiscoverFetcher()
        assert f.app_base_url == DEFAULT_APP_BASE_URL
        assert f.app_base_url.startswith("https://")

    def test_default_user_agent_present(self):
        f = DiscoverFetcher()
        assert "Mozilla" in f.user_agent

    def test_url_trailing_slash_stripped(self):
        f = DiscoverFetcher(app_base_url="https://example.com/")
        assert f.app_base_url == "https://example.com"


@pytest.mark.network
class TestLiveFetch:
    """Optional: hit the real public endpoint if reachable from CI."""

    def test_live_discover_returns_campaigns(self):
        f = DiscoverFetcher(timeout=15)
        campaigns = f.fetch_campaigns()
        assert len(campaigns) >= 10
        assert all(isinstance(c, Campaign) for c in campaigns)
        assert campaigns[0].id
        assert campaigns[0].title
