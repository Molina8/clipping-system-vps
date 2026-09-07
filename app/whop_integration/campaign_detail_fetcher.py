"""
whop_integration/campaign_detail_fetcher.py
============================================
Fetches the full detail of a single Whop Content Rewards campaign without
authentication, by parsing the RSC payload of the public `/campaigns/<id>`
page.

The same `/campaigns/<id>` route is used both for the public listing and
the per-creator detail view; the difference is the campaign UUID in the
URL. The detail page exposes the full campaign object inline in the
React Server Components payload.

The returned `CampaignDetail` includes:
- identity (id, name, brand, description, status, verified, private)
- payout rules per platform (CPM / per-post / retainer; min/max payout)
- platforms accepted
- reference materials (URLs to brand assets clippers must use)
- creator metrics (creatorCount, approvedSubmissionCount, totalViews,
  paidOutCents, budgetProgressBps, chartPoints)
- top earners (rank, username, views, earned)
- gating flags (requiresApplication, retainerSpotsTotal)

The `referenceMaterials` URLs are typically Google Drive / Dropbox /
Loom / external links. Use `whop_integration/drive_fetcher.py` to
download public assets from those.

Usage:
    fetcher = CampaignDetailFetcher()
    detail = fetcher.fetch_campaign("14f743b2-c5ca-4f3e-8c0d-3010c485005d")
    print(detail.platforms, detail.payouts, detail.reference_materials)

Author: jarvismolinabot / clipper integration
Date: 2026-09-07
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Iterator

import requests

logger = logging.getLogger(__name__)

DEFAULT_APP_BASE_URL = "https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ---------------- payout sub-model ----------------

@dataclass
class PayoutRule:
    """Per-platform payout rule for a campaign."""
    platform: str
    payout_type: str         # 'cpm' / 'per_post' / 'retainer'
    rate_cents: int          # raw rate in cents
    min_payout_cents: int
    max_payout_cents: int

    @property
    def rate_per_1k_usd(self) -> float:
        return self.rate_cents / 100.0

    @property
    def min_payout_usd(self) -> float:
        return self.min_payout_cents / 100.0

    @property
    def max_payout_usd(self) -> float:
        return self.max_payout_cents / 100.0

    @classmethod
    def from_raw(cls, raw: dict) -> "PayoutRule":
        return cls(
            platform=raw.get("platform", ""),
            payout_type=raw.get("payoutType", ""),
            rate_cents=int(raw.get("rateCents") or 0),
            min_payout_cents=int(raw.get("minPayoutCents") or 0),
            max_payout_cents=int(raw.get("maxPayoutCents") or 0),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- reference material sub-model ----------------

@dataclass
class ReferenceMaterial:
    """A brand asset / reference link the clipper must use."""
    url: str
    type: str = ""           # 'brandAsset' / 'video' / 'image' / etc.
    media_type: str = ""     # 'external' / 'youtube' / 'drive' / etc.

    @classmethod
    def from_raw(cls, raw: dict) -> "ReferenceMaterial":
        return cls(
            url=raw.get("url", ""),
            type=raw.get("type", ""),
            media_type=raw.get("mediaType", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- top earner sub-model ----------------

@dataclass
class TopEarner:
    rank: int
    user_id: str
    username: str
    approved_submission_count: int
    total_views: int
    earned_cents: int

    @property
    def earned_usd(self) -> float:
        return self.earned_cents / 100.0

    @classmethod
    def from_raw(cls, raw: dict) -> "TopEarner":
        return cls(
            rank=int(raw.get("rank") or 0),
            user_id=raw.get("userId", ""),
            username=raw.get("username", ""),
            approved_submission_count=int(raw.get("approvedSubmissionCount") or 0),
            total_views=int(raw.get("totalViews") or 0),
            earned_cents=int(raw.get("earnedCents") or 0),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- chart point sub-model ----------------

@dataclass
class DailyMetric:
    """One day's worth of aggregated campaign metrics."""
    bucket: str                       # ISO 8601 date (start of day UTC)
    approved_submission_count: int
    total_views: int

    @classmethod
    def from_raw(cls, raw: dict) -> "DailyMetric":
        return cls(
            bucket=raw.get("bucket", ""),
            approved_submission_count=int(raw.get("approvedSubmissionCount") or 0),
            total_views=int(raw.get("totalViews") or 0),
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- main model ----------------

@dataclass
class CampaignDetail:
    """Normalized Whop Content Rewards campaign detail."""

    # Identity
    id: str
    name: str
    organization_id: str = ""
    organization_name: str = ""
    organization_verified: bool = False
    organization_experience_id: str = ""
    description: str = ""
    status: str = ""
    private: bool = False
    categories: list[str] = field(default_factory=list)
    featured_score: int = 0

    # Lifecycle
    created_at: str = ""
    listed_at: str = ""
    updated_at: str = ""
    metrics_updated_at: str = ""

    # Payout rules
    payout_type: str = ""
    platforms: list[str] = field(default_factory=list)
    payouts: list[PayoutRule] = field(default_factory=list)
    primary_payout_cents: int = 0
    cpm_min_rate_cents: int = 0
    cpm_max_rate_cents: int = 0

    # Budget
    budget_cents: int = 0
    budget_spent_cents: int = 0
    budget_progress_bps: int = 0          # 0-10000 (100.00% = 10000)
    paid_out_cents: int = 0

    # Gating
    requires_application: bool = False
    retainer_spots_total: int = 0

    # Reference materials (the rules / brand assets)
    reference_materials: list[ReferenceMaterial] = field(default_factory=list)

    # Competition / activity metrics
    creator_count: int = 0
    approved_submission_count: int = 0
    total_views: int = 0
    top_earners: list[TopEarner] = field(default_factory=list)
    chart_points: list[DailyMetric] = field(default_factory=list)

    # Assets
    banner_url: str = ""
    banner_blur: str = ""
    organization_logo_url: str = ""
    organization_logo_src: str = ""

    # ---- derived helpers ----

    @property
    def budget_usd(self) -> float:
        return self.budget_cents / 100.0

    @property
    def budget_spent_usd(self) -> float:
        return self.budget_spent_cents / 100.0

    @property
    def budget_available_usd(self) -> float:
        return (self.budget_cents - self.budget_spent_cents) / 100.0

    @property
    def budget_progress_pct(self) -> float:
        return self.budget_progress_bps / 100.0

    @property
    def paid_out_usd(self) -> float:
        return self.paid_out_cents / 100.0

    @property
    def primary_rate_usd_per_1k(self) -> float:
        return self.primary_payout_cents / 100.0

    @property
    def cpm_min_usd(self) -> float:
        return self.cpm_min_rate_cents / 100.0

    @property
    def cpm_max_usd(self) -> float:
        return self.cpm_max_rate_cents / 100.0

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def has_drive_assets(self) -> bool:
        return any(
            "drive.google.com" in rm.url.lower() or "docs.google.com" in rm.url.lower()
            for rm in self.reference_materials
        )

    def payout_for_platform(self, platform: str) -> PayoutRule | None:
        """Return the PayoutRule for a specific platform, or None."""
        for p in self.payouts:
            if p.platform.lower() == platform.lower():
                return p
        return None

    # ---- (de)serialization ----

    @classmethod
    def from_raw(cls, raw: dict) -> "CampaignDetail":
        return cls(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            organization_id=raw.get("organizationId", ""),
            organization_name=raw.get("organizationName", ""),
            organization_verified=bool(raw.get("organizationVerified")),
            organization_experience_id=raw.get("organizationExperienceId", ""),
            description=raw.get("description", ""),
            status=raw.get("status", ""),
            private=bool(raw.get("private")),
            categories=list(raw.get("categories") or []),
            featured_score=int(raw.get("featuredScore") or 0),
            created_at=raw.get("createdAt", ""),
            listed_at=raw.get("listedAt", ""),
            updated_at=raw.get("updatedAt", ""),
            metrics_updated_at=raw.get("metrics", {}).get("metricsUpdatedAt", ""),
            payout_type=raw.get("payoutType", ""),
            platforms=list(raw.get("platforms") or []),
            payouts=[PayoutRule.from_raw(p) for p in (raw.get("payouts") or [])],
            primary_payout_cents=int(raw.get("primaryPayoutCents") or 0),
            cpm_min_rate_cents=int(raw.get("cpmMinRateCents") or 0),
            cpm_max_rate_cents=int(raw.get("cpmMaxRateCents") or 0),
            budget_cents=int(raw.get("budgetCents") or 0),
            budget_spent_cents=int(
                (raw.get("metrics") or {}).get("budgetSpentCents") or 0
            ),
            budget_progress_bps=int(
                (raw.get("metrics") or {}).get("budgetProgressBps") or 0
            ),
            paid_out_cents=int(
                (raw.get("metrics") or {}).get("paidOutCents") or 0
            ),
            requires_application=bool(raw.get("requiresApplication")),
            retainer_spots_total=int(raw.get("retainerSpotsTotal") or 0),
            reference_materials=[
                ReferenceMaterial.from_raw(m)
                for m in (raw.get("referenceMaterials") or [])
            ],
            creator_count=int((raw.get("metrics") or {}).get("creatorCount") or 0),
            approved_submission_count=int(
                (raw.get("metrics") or {}).get("approvedSubmissionCount") or 0
            ),
            total_views=int((raw.get("metrics") or {}).get("totalViews") or 0),
            top_earners=[
                TopEarner.from_raw(t)
                for t in ((raw.get("metrics") or {}).get("topEarners") or [])
            ],
            chart_points=[
                DailyMetric.from_raw(p)
                for p in ((raw.get("metrics") or {}).get("chartPoints") or [])
            ],
            banner_url=raw.get("bannerUrl", ""),
            banner_blur=raw.get("bannerBlur", ""),
            organization_logo_url=raw.get("organizationLogoUrl", ""),
            organization_logo_src=raw.get("organizationLogoSrc", ""),
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------- fetcher ----------------

class CampaignDetailFetchError(Exception):
    """Raised when the public /campaigns/<id> endpoint can't be parsed."""


class CampaignDetailFetcher:
    """Fetches the public detail of a single Content Rewards campaign."""

    def __init__(
        self,
        app_base_url: str = DEFAULT_APP_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ) -> None:
        self.app_base_url = app_base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    # ---- public API ----

    def fetch_campaign(
        self, campaign_id: str, session: requests.Session | None = None
    ) -> CampaignDetail:
        """Fetch full detail for one campaign."""
        own_session = session is None
        sess = session or requests.Session()
        try:
            html = self._fetch_html(sess, f"/campaigns/{campaign_id}")
            raw = self._extract_campaign_object(html)
            return CampaignDetail.from_raw(raw)
        finally:
            if own_session:
                sess.close()

    def iter_campaigns(
        self, ids: list[str], session: requests.Session | None = None
    ) -> Iterator[CampaignDetail]:
        """Yield CampaignDetail for each id, one at a time."""
        sess = session or requests.Session()
        try:
            for cid in ids:
                yield self.fetch_campaign(cid, sess)
        finally:
            if session is None:
                sess.close()

    def session(self) -> "_FetcherSession":
        return _FetcherSession(self)

    # ---- internals ----

    def _fetch_html(self, sess: requests.Session, path: str) -> str:
        url = f"{self.app_base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                r = sess.get(
                    url,
                    headers={
                        "User-Agent": self.user_agent,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout=self.timeout,
                )
                r.raise_for_status()
                return r.text
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "CampaignDetailFetcher attempt %d/%d failed for %s: %s",
                    attempt, self.max_retries, url, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
        raise CampaignDetailFetchError(
            f"Failed to fetch {url} after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _extract_campaign_object(html: str) -> dict:
        """Parse the RSC payload and extract the full campaign object.

        The campaign object is the JSON that contains the marker
        '"status":"active"' (or '"status":"paused"' etc.) and lives at
        the top level of one of the RSC chunks.
        """
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,"(.+?)"\]\)', html, re.DOTALL
        )
        if not chunks:
            raise CampaignDetailFetchError(
                "No RSC chunks found in HTML — page may have changed layout."
            )

        all_text = ""
        for ch in chunks:
            decoded = ch.encode("utf-8").decode("unicode_escape", errors="ignore")
            all_text += decoded + "\n"

        # Find a status marker that belongs to a campaign (not a user/post)
        # The campaign object has 'requiresApplication' nearby.
        marker = '"status":"active"'
        idx = all_text.find(marker)
        if idx < 0:
            # Try paused / draft / completed
            for fallback in ('"status":"paused"', '"status":"draft"',
                             '"status":"completed"'):
                idx = all_text.find(fallback)
                if idx >= 0:
                    break
        if idx < 0:
            raise CampaignDetailFetchError(
                "No campaign object with status marker found in RSC payload."
            )

        # Walk backward to find the opening '{' of this object (string-aware).
        # `idx` points at the opening '"' of the marker key (e.g. "status").
        # Start the walk from idx-1 (the char BEFORE the opening '"') so
        # in_string tracking begins outside any string boundary. Starting
        # from idx itself would init us "inside" the string and cause the
        # '{' just before the marker to be skipped.
        depth = 0
        obj_start = -1
        in_string = False
        escape = False
        for i in range(idx - 1, -1, -1):
            ch = all_text[i]
            if escape: escape = False; continue
            if ch == "\\": escape = True; continue
            if ch == '"': in_string = not in_string; continue
            if in_string: continue
            if ch == "}": depth += 1
            elif ch == "{":
                if depth == 0:
                    obj_start = i
                    break
                depth -= 1
        if obj_start < 0:
            raise CampaignDetailFetchError(
                "Could not find opening '{' before status marker."
            )

        # Walk forward to find the matching close '}' (string-aware)
        depth = 0
        obj_end = -1
        in_string = False
        escape = False
        for i in range(obj_start, len(all_text)):
            ch = all_text[i]
            if escape: escape = False; continue
            if ch == "\\": escape = True; continue
            if ch == '"': in_string = not in_string; continue
            if in_string: continue
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj_end = i + 1
                    break
        if obj_end < 0:
            raise CampaignDetailFetchError(
                "Could not find matching '}' for campaign object."
            )

        raw = all_text[obj_start:obj_end]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CampaignDetailFetchError(
                f"Failed to parse campaign object JSON: {exc}"
            ) from exc


class _FetcherSession:
    """Context manager that yields a requests.Session and closes it on exit."""

    def __init__(self, fetcher: CampaignDetailFetcher) -> None:
        self.fetcher = fetcher
        self._session: requests.Session | None = None

    def __enter__(self) -> requests.Session:
        self._session = requests.Session()
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
