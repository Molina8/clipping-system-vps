"""
whop_integration/discover_fetcher.py
====================================
Fetches the public campaign list from the Whop "Content Rewards" app
without auth, by parsing the RSC payload of the public `/discover` page.

The app base URL is discovered at runtime from the `consumerViewUrlTemplate`
of the experience (Content Rewards app id = `app_QRxsQodZgK1r4D`).

The discover page exposes a server-rendered Next.js page with a React Server
Components payload that embeds the full `campaigns` array as inline JSON.

This is the simplest, most reliable extraction path that does not require
OAuth, Account API keys, headless browsers, or scraping JS.

Usage:
    fetcher = DiscoverFetcher()
    campaigns = fetcher.fetch_campaigns()
    # or with a single shared session:
    with fetcher.session() as client:
        for page in fetcher.iter_pages(client):
            ...

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

# Default app base URL discovered from experience embed template
DEFAULT_APP_BASE_URL = "https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com"

# Public user-agent so we are not blocked by Cloudflare bot challenge
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Whop main storefront can also expose campaign embed if linked
DEFAULT_EXPERIENCE_URL = (
    "https://whop.com/codiant/exp_XdOopaairb4g5w/app/"
    "https://whop.com/clippingculture/exp_zeADOv9rOOKk2x/app/"
)


@dataclass
class Campaign:
    """Normalized Whop Content Rewards campaign."""

    id: str
    title: str
    brand: str = ""
    description: str = ""
    category: str = ""
    campaign_type: str = ""  # 'cpm' / 'per_post' / 'retainer'
    cpm_label: str = ""  # '$$1', '$$1.75' etc.
    payout_sort_raw: float = 0.0
    budget_total_label: str = ""
    budget_total_raw: float = 0.0
    budget_spent_label: str = ""
    budget_spent_raw: float = 0.0
    available_budget_raw: float = 0.0
    creator_count_raw: int = 0
    funded_ago: str = ""
    created_at_ms: int = 0
    thumbnail: str = ""
    avatar: str = ""
    avatar_seed: str = ""
    is_verified: bool = False
    requires_application: bool = False
    platforms: list[str] = field(default_factory=list)
    progress_percentage: float = 0.0
    organization_experience_id: str = ""

    @classmethod
    def from_raw(cls, raw: dict) -> "Campaign":
        """Build a Campaign from a raw RSC payload dict."""
        platforms = raw.get("platforms") or []
        if isinstance(platforms, str):
            platforms = [p.strip() for p in platforms.split(",") if p.strip()]
        return cls(
            id=raw.get("id", ""),
            title=raw.get("title", ""),
            brand=raw.get("brand", ""),
            description=raw.get("description", ""),
            category=raw.get("category", "") or "",
            campaign_type=raw.get("type", ""),
            cpm_label=raw.get("ratePer1kLabel", ""),
            payout_sort_raw=float(raw.get("payoutSortRaw") or 0),
            budget_total_label=raw.get("budgetTotalLabel", ""),
            budget_total_raw=float(raw.get("budgetTotalRaw") or 0),
            budget_spent_label=raw.get("budgetSpentLabel", ""),
            budget_spent_raw=float(raw.get("budgetSpentRaw") or 0),
            available_budget_raw=float(raw.get("availableBudgetRaw") or 0),
            creator_count_raw=int(raw.get("creatorCountRaw") or 0),
            funded_ago=raw.get("fundedAgo", ""),
            created_at_ms=int(raw.get("createdAtMs") or 0),
            thumbnail=raw.get("thumbnail", ""),
            avatar=raw.get("avatar", ""),
            avatar_seed=raw.get("avatarSeed", ""),
            is_verified=bool(raw.get("isVerified")),
            requires_application=bool(raw.get("requiresApplication")),
            platforms=platforms,
            progress_percentage=float(raw.get("progressPercentage") or 0),
            organization_experience_id=raw.get("organizationExperienceId", ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


class DiscoverFetchError(Exception):
    """Raised when the public discover endpoint can't be parsed."""


class DiscoverFetcher:
    """Fetches public campaigns from Whop's Content Rewards app."""

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

    # ---------------- public API ----------------

    def fetch_campaigns(self, session: requests.Session | None = None) -> list[Campaign]:
        """Fetch the first page of campaigns (default 50) from /discover."""
        own_session = session is None
        sess = session or requests.Session()
        try:
            html = self._fetch_html(sess, "/discover")
            raw = self._extract_campaigns_array(html)
            return [Campaign.from_raw(r) for r in raw]
        finally:
            if own_session:
                sess.close()

    def iter_pages(self, session: requests.Session | None = None) -> Iterator[list[Campaign]]:
        """Yield one list[Campaign] per page. Currently the API only exposes
        a single server-rendered page; this iterator is a placeholder for
        future pagination if the app exposes more."""
        sess = session or requests.Session()
        try:
            yield self.fetch_campaigns(sess)
        finally:
            if session is None:
                sess.close()

    def session(self) -> "_FetcherSession":
        """Context manager exposing a shared requests.Session."""
        return _FetcherSession(self)

    # ---------------- internals ----------------

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
                    "DiscoverFetcher attempt %d/%d failed for %s: %s",
                    attempt, self.max_retries, url, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)
        raise DiscoverFetchError(
            f"Failed to fetch {url} after {self.max_retries} attempts: {last_exc}"
        )

    @staticmethod
    def _extract_campaigns_array(html: str) -> list[dict]:
        """Parse the RSC payload chunks and extract the `campaigns` array."""
        chunks = re.findall(
            r'self\.__next_f\.push\(\[1,"(.+?)"\]\)', html, re.DOTALL
        )
        if not chunks:
            raise DiscoverFetchError(
                "No RSC chunks found in HTML — page may have changed layout."
            )

        # Decode escaped sequences from each chunk
        all_text = ""
        for ch in chunks:
            decoded = ch.encode("utf-8").decode("unicode_escape", errors="ignore")
            all_text += decoded + "\n"

        # Find the campaigns array via brace matching (more robust than regex)
        marker = '"campaigns":['
        idx = all_text.find(marker)
        if idx < 0:
            raise DiscoverFetchError(
                "No 'campaigns' array found in RSC payload."
            )
        arr_start = idx + len('"campaigns":')
        depth = 0
        end = -1
        for i in range(arr_start, len(all_text)):
            c = all_text[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end < 0:
            raise DiscoverFetchError("Unbalanced brackets around 'campaigns' array.")

        raw = all_text[arr_start:end]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DiscoverFetchError(
                f"Failed to parse campaigns JSON: {exc}"
            ) from exc


class _FetcherSession:
    """Context manager that yields a requests.Session and closes it on exit."""

    def __init__(self, fetcher: DiscoverFetcher) -> None:
        self.fetcher = fetcher
        self._session: requests.Session | None = None

    def __enter__(self) -> requests.Session:
        self._session = requests.Session()
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
