"""WhopProvider — discovers campaigns from a Whop tenant sub-app.

PRIMARY path (2026-09-11): JSON API.

The Whop `discover` sub-app exposes a public, unauthenticated JSON endpoint
that the SPA fetches to render its cards:

    GET {tenant}/api/campaign/campaigns/discover?limit={n}&sortBy={trending|...}

It returns `{"data":[...], "pagination":{...}, "success":true}` where each
campaign has:
  - id (UUID), name, description
  - cpmMinRateCents / cpmMaxRateCents (USD cents per 1k views)
  - budgetCents (USD cents, total prize pool)
  - metrics.approvedSubmissionCount (joined)
  - payouts[] with platform + rateCents
  - referenceMaterials[] with mediaType + url (asset links)
  - organizationExperienceId / organizationName

So we just call the API. Fast (~200ms), no Playwright needed, no auth.

FALLBACK path (kept for resilience): Playwright scraping of the SPA cards.
Used only if the API returns 4xx/5xx or empty data. Disabled by default
since the API works.

This provider's `discover()` returns DiscoveredCampaign records.
`fetch_detail()` enriches each with asset links from `referenceMaterials`.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

from app.services.discovery.base import CampaignProvider
from app.services.discovery.models import DiscoveredCampaign

logger = logging.getLogger(__name__)


_USE_PLAYWRIGHT = os.environ.get("WHOP_USE_PLAYWRIGHT", "0") != "0"
_PLAYWRIGHT_TIMEOUT_MS = int(os.environ.get("WHOP_DISCOVER_TIMEOUT_MS", "30000"))
_API_TIMEOUT_S = float(os.environ.get("WHOP_API_TIMEOUT_S", "15"))
_API_PAGE_SIZE = int(os.environ.get("WHOP_API_PAGE_SIZE", "50"))


_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


_K_NUMBER = r"[\d.,]+[KkMm]?"


def _parse_cpm(text: str) -> float | None:
    """Parse '$X / 1K' or '$X / 10,000' into USD per 1K views."""
    # "$1.75 / 1K", "$1 / 1K", "$10/10,000"
    m = re.search(r"\$([\d.,]+)\s*/\s*(1[Kk]|10[,.]000|1000)\b", text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    if unit.startswith("1k") or unit == "1000":
        return val
    if unit.startswith("10"):
        return val  # already per 10,000 -> same as per 1K numerically
    return val


def _parse_prize_pool_usd(text: str) -> float | None:
    """Parse '$220k / $238k' -> 238000 (the second number is the total pool)."""
    m = re.search(
        rf"\$({_K_NUMBER})\s*/\s*\$({_K_NUMBER})", text
    )
    if not m:
        return None
    return _to_usd(m.group(2))


def _to_usd(s: str) -> float:
    """'$238k' -> 238000.0, '$1.2m' -> 1200000.0."""
    s = s.strip().replace(",", "")
    mult = 1.0
    if s.endswith(("k", "K")):
        mult = 1_000.0
        s = s[:-1]
    elif s.endswith(("m", "M")):
        mult = 1_000_000.0
        s = s[:-1]
    return float(s) * mult


def _parse_joined(text: str) -> int | None:
    """Parse '264', '2.6K' followed by 'joined' -> 264 / 2600."""
    m = re.search(r"(\d[\d.,]*\s*[KkMm]?)\s+joined", text)
    if not m:
        return None
    s = m.group(1).strip().replace(",", "")
    mult = 1
    if s.endswith(("k", "K")):
        mult = 1_000
        s = s[:-1]
    elif s.endswith(("m", "M")):
        mult = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def _classify_link(url: str) -> str:
    """Return the asset kind for a URL: drive, youtube, googlesheets, dropbox, mega, external."""
    u = url.lower()
    if "drive.google.com" in u:
        return "drive"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "docs.google.com/spreadsheets" in u:
        return "googlesheets"
    if "dropbox.com" in u:
        return "dropbox"
    if "mega.nz" in u:
        return "mega"
    return "external"


def _extract_links(html_block: str) -> list[str]:
    """Pull all reasonable http(s) URLs from an HTML chunk."""
    found = re.findall(r'https?://[^\s"<>`]+', html_block)
    # filter some junk
    cleaned = []
    for u in found:
        u = u.rstrip(".,);")
        if 12 < len(u) < 200 and "w3.org" not in u and "schema.org" not in u:
            cleaned.append(u)
    return list(dict.fromkeys(cleaned))  # dedupe, preserve order


class WhopProvider(CampaignProvider):
    name = "whop"

    def __init__(self, tenant_url: str, *, ua: str = _DEFAULT_UA, timeout_s: float = 30.0):
        self.tenant_url = tenant_url.rstrip("/")
        self.ua = ua
        self.timeout_s = timeout_s

    # --- public API --------------------------------------------------------

    def discover(self, *, limit: int = 50) -> list[DiscoveredCampaign]:
        url = f"{self.tenant_url}/discover"
        cards: list[dict[str, Any]] = []
        fetch_failed = False
        # PRIMARY: JSON API.
        api_cards, api_err = self._discover_via_api(limit=limit)
        if api_err:
            fetch_failed = True
            logger.warning("whop API discover failed: %s", api_err)
        elif api_cards:
            cards = api_cards
            logger.info("whop API discover: %d cards", len(cards))
        # FALLBACK: Playwright SPA scrape (opt-in via WHOP_USE_PLAYWRIGHT=1).
        if not cards and _USE_PLAYWRIGHT:
            pw_cards, pw_error = self._discover_with_playwright(url)
            if pw_error:
                fetch_failed = True
                logger.warning("whop playwright discover failed: %s", pw_error)
            elif pw_cards:
                cards = pw_cards
                logger.info("whop playwright discover: %d cards", len(cards))
        # FALLBACK: plain HTML regex (rarely useful since the SPA renders cards client-side).
        if not cards and not fetch_failed:
            try:
                src = self._fetch(url)
                cards = self._parse_cards_from_html(src)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                logger.warning("whop plain-html discover failed: %s", e)
                fetch_failed = True

        out: list[DiscoveredCampaign] = []
        seen: set[str] = set()
        for c in cards:
            # 2026-09-17: _api_card_to_struct now returns full DiscoveredCampaign-
            # shaped dict (including external_id). Other paths (HTML/Playwright)
            # still emit the older flat shape. Normalize both.
            if "external_id" in c and "detail_url" in c:
                # New shape from _api_card_to_struct (pipeline v2, 2026-09-17).
                # Pass EVERY field the API card exposes through to the model
                # so downstream cron 3a (brief-reader) can read the full Whop
                # surface from `source_metadata.discovered`. Previous version
                # hardcoded payouts=[]/reference_materials=[] here, which
                # silently dropped the JSONB in BD even though _api_card_to_struct
                # had filled it correctly — root cause of the "upsert empty"
                # bug found during smoke test 2026-09-17.
                dc = DiscoveredCampaign(
                    provider=c.get("provider", "whop"),
                    external_id=c["external_id"],
                    detail_url=c["detail_url"],
                    name=c["name"],
                    description=c.get("description"),
                    cpm_usd_per_1k=c.get("cpm_usd_per_1k"),
                    prize_pool_usd=c.get("prize_pool_usd"),
                    joined=c.get("joined"),
                    asset_links=c.get("asset_links", []),
                    payouts=c.get("payouts", []),
                    reference_materials=c.get("reference_materials", []),
                    organization_name=c.get("organization_name"),
                    organization_verified=c.get("organization_verified"),
                    organization_id=c.get("organization_id"),
                    categories=c.get("categories", []),
                    platforms=c.get("platforms", []),
                    status=c.get("status"),
                    requires_application=c.get("requires_application"),
                    primary_payout_cents=c.get("primary_payout_cents"),
                    raw={**c.get("raw", {}), "fetch_failed": fetch_failed},
                )
                camp_uuid = c["external_id"].split("/", 1)[-1]
            else:
                camp_uuid = c["campaign_uuid"]
                dc = DiscoveredCampaign(
                    provider="whop",
                    external_id=f"{c.get('experience_id','?')}/{camp_uuid}",
                    detail_url=c["detail_url"],
                    name=c["name"],
                    description=c.get("description"),
                    cpm_usd_per_1k=c.get("cpm"),
                    prize_pool_usd=c.get("prize_pool"),
                    joined=c.get("joined"),
                    asset_links=c.get("asset_links", []),
                    raw={
                        "experience_id": c.get("experience_id"),
                        "campaign_uuid": camp_uuid,
                        "card_text": c.get("description"),
                        "fetch_failed": fetch_failed,
                    },
                )
            if camp_uuid in seen:
                continue
            seen.add(camp_uuid)
            out.append(dc)
            if len(out) >= limit:
                break
        logger.info(
            "whop discover: %d unique campaigns from %s (fetch_failed=%s)",
            len(out), self.tenant_url, fetch_failed,
        )
        return out

    # --- API discovery -----------------------------------------------------

    def _discover_via_api(self, *, limit: int) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Call Whop's public JSON API for the campaign list.

        Returns (cards, None) on success, (None, error) on failure, and
        ([], None) when the API returns no data.
        """
        from urllib.parse import urlencode

        api_base = f"{self.tenant_url}/api/campaign/campaigns/discover"
        per_page = min(limit, _API_PAGE_SIZE) if _API_PAGE_SIZE > 0 else limit
        params = urlencode({"limit": per_page, "sortBy": "trending"})
        url = f"{api_base}?{params}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.ua,
                    "Accept": "application/json",
                    "Referer": f"{self.tenant_url}/discover",
                    "Origin": self.tenant_url,
                },
            )
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT_S) as r:
                payload = json.loads(r.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
            return None, f"{type(e).__name__}: {e}"

        if not isinstance(payload, dict) or payload.get("success") is not True:
            return None, f"unexpected_payload: {str(payload)[:200]}"
        data = payload.get("data") or []
        if not isinstance(data, list):
            return None, "data_not_list"
        cards: list[dict[str, Any]] = []
        for c in data:
            card = self._api_card_to_struct(c)
            if card:
                cards.append(card)
        return cards, None

    def _api_card_to_struct(self, c: dict[str, Any]) -> dict[str, Any] | None:
        """Map a Whop JSON API campaign record to the internal card dict.

        Redesigned 2026-09-17 (pipeline v2): we now keep the FULL API surface
        (payouts[], referenceMaterials[], organization, categories, platforms,
        status, requires_application) so downstream cron 3a (brief-reader) can
        consume everything from `source_metadata.discovered` without re-fetching
        the canonical `source_url` (which Whop auth-gates).

        Returns a dict shaped to fit DiscoveredCampaign.model_validate(...).
        """
        camp_uuid = c.get("id")
        exp_id = c.get("organizationExperienceId") or ""
        if not camp_uuid or not exp_id:
            return None
        try:
            name = (c.get("name") or "").strip() or f"Whop campaign {camp_uuid[:8]}"
            description = c.get("description") or None
            # Filter "Submissions closed" / closed campaigns (2026-09-18):
            # Coinpoker added with `status='active'` but the card shows a
            # "Submissions closed" badge — the public JSON API doesn't expose
            # a dedicated `submissionsClosed` field, so we use the only
            # inequivocal signals we have: budget fully spent (10000 bps)
            # or explicitly hidden from public discover. Refine when Whop
            # exposes the real badge-driving field.
            metrics = c.get("metrics") or {}
            bps = metrics.get("budgetProgressBps")
            if isinstance(bps, (int, float)) and bps >= 10000:
                logger.info(
                    "whop skip %s: budgetProgressBps=%s (>=10000 = fully spent)",
                    camp_uuid, bps,
                )
                return None
            if c.get("showOnDiscover") is False:
                logger.info(
                    "whop skip %s: showOnDiscover=False (hidden from public discover)",
                    camp_uuid,
                )
                return None
            # TEMPORAL hardcoded blacklist (2026-09-18): Whop pinta "Submissions
            # closed" en la card pero NO expone el campo en el JSON público.
            # Bloqueamos por external_id/name las campañas problemáticas
            # conocidas hasta que encontremos el endpoint auth-only o Whop
            # exponga el campo. Coinpoker id=b147171f-1c01-4a21-803b-ed03db59d82f
            # aparece como "active" con budgetProgressBps=4921 y showOnDiscover=null.
            _CLOSED_NAME_PATTERNS = (
                "coinpoker logo general campaign",
            )
            name_lc = name.lower()
            for bad in _CLOSED_NAME_PATTERNS:
                if bad in name_lc:
                    logger.info(
                        "whop skip %s: name matches closed-card blacklist (%r)",
                        camp_uuid, bad,
                    )
                    return None

            budget_cents = c.get("budgetCents")
            pool = float(budget_cents) / 100.0 if budget_cents is not None else None
            metrics = c.get("metrics") or {}
            joined_raw = metrics.get("approvedSubmissionCount")
            try:
                joined = int(joined_raw) if joined_raw is not None else None
            except (TypeError, ValueError):
                joined = None

            payouts: list[dict[str, Any]] = []
            for pay in c.get("payouts") or []:
                if not isinstance(pay, dict):
                    continue
                payouts.append({
                    "platform": pay.get("platform"),
                    "payout_type": pay.get("payoutType"),
                    "rate_cents": pay.get("rateCents"),
                    "min_payout_cents": pay.get("minPayoutCents"),
                    "max_payout_cents": pay.get("maxPayoutCents"),
                })

            cpm_min_cents = c.get("cpmMinRateCents")
            cpm_min_usd = (
                float(cpm_min_cents) / 100.0 if cpm_min_cents is not None else None
            )

            reference_materials: list[dict[str, Any]] = []
            asset_links: list[str] = []
            for rm in c.get("referenceMaterials") or []:
                if not isinstance(rm, dict) or not rm.get("url"):
                    continue
                reference_materials.append({
                    "media_type": rm.get("mediaType"),
                    "type": rm.get("type"),
                    "url": rm["url"],
                    "name": rm.get("name"),
                })
                asset_links.append(rm["url"])

            categories = c.get("categories") or []
            platforms = c.get("platforms") or []

            return {
                "provider": "whop",
                "external_id": f"{exp_id}/{camp_uuid}",
                "detail_url": f"https://whop.com/experiences/{exp_id}/campaigns/{camp_uuid}",
                "name": name,
                "description": description,
                "cpm_usd_per_1k": cpm_min_usd,
                "prize_pool_usd": pool,
                "joined": joined,
                "payouts": payouts,
                "reference_materials": reference_materials,
                "organization_name": c.get("organizationName"),
                "organization_verified": c.get("organizationVerified"),
                "organization_id": c.get("organizationId"),
                "categories": categories,
                "platforms": platforms,
                "status": c.get("status"),
                "requires_application": c.get("requiresApplication"),
                "primary_payout_cents": c.get("primaryPayoutCents"),
                "asset_links": asset_links,
                "raw": {
                    "organization_name": c.get("organizationName"),
                    "organization_verified": c.get("organizationVerified"),
                    "platforms": platforms,
                    "payouts": c.get("payouts"),
                    "referenceMaterials": c.get("referenceMaterials"),
                    "requires_application": c.get("requiresApplication"),
                    "status": c.get("status"),
                    "primary_payout_cents": c.get("primaryPayoutCents"),
                    "banner": c.get("banner"),
                    "categories": categories,
                    "metrics": metrics,
                    "organization_logo_url": c.get("organizationLogoUrl"),
                },
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("whop api_card_to_struct failed for %s: %s", c.get("id"), e)
            return None

    # --- Playwright (SPA) discovery ----------------------------------------

    def _discover_with_playwright(self, url: str) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Render `/discover` in headless Chromium and extract cards.

        Returns (cards, None) on success, (None, error) on failure, and
        ([], None) when no cards were rendered.
        """
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as e:  # noqa: BLE001
            return None, f"playwright_not_installed: {e}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    page = browser.new_page(
                        viewport={"width": 1400, "height": 900},
                        user_agent=self.ua,
                        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    )
                    page.goto(url, wait_until="domcontentloaded", timeout=_PLAYWRIGHT_TIMEOUT_MS)
                    try:
                        page.wait_for_selector(
                            'a[href*="/campaigns/"]', timeout=_PLAYWRIGHT_TIMEOUT_MS
                        )
                    except Exception:  # noqa: BLE001
                        logger.info("whop SPA: no /campaigns/ anchors in %dms", _PLAYWRIGHT_TIMEOUT_MS)
                        return [], None
                    # Give the SPA another beat to render sibling numbers.
                    page.wait_for_timeout(2500)
                    raw_cards = page.evaluate(
                        r"""
                        () => {
                          const anchors = Array.from(document.querySelectorAll('a[href*="/campaigns/"]'));
                          const seen = new Set();
                          const out = [];
                          for (const a of anchors) {
                            if (seen.has(a.href)) continue;
                            seen.add(a.href);
                            let card = a;
                            let txt = '';
                            for (let i = 0; i < 8 && card; i++) {
                              card = card.parentElement;
                              if (!card) break;
                              txt = (card.innerText || '');
                              if (txt.includes('$') && txt.length < 800 && txt.length > 30) break;
                            }
                            out.push({
                              href: a.href,
                              text: (txt || '').replace(/\\s+/g, ' ').trim().slice(0, 800),
                            });
                          }
                          return out;
                        }
                        """
                    )
                finally:
                    browser.close()
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}"
        # Parse the (href, card_text) tuples into the same dict shape as
        # _parse_cards_from_html.
        return self._parse_cards_from_rendered(raw_cards), None

    def _parse_cards_from_rendered(self, raw_cards: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Parse [{href, text}, ...] tuples into the structured card dicts."""
        out: list[dict[str, Any]] = []
        anchor_re = re.compile(
            r"^https?://whop\.com/experiences/(exp_[A-Za-z0-9_]+)/campaigns/([a-f0-9-]{36})/?$"
        )
        for rc in raw_cards:
            m = anchor_re.match(rc.get("href", ""))
            if not m:
                continue
            exp_id, camp_uuid = m.group(1), m.group(2)
            plano = (rc.get("text") or "").strip()
            if not plano:
                continue
            # Name = first phrase before the first "$" or before "joined".
            nombre = re.split(r"\s*\$", plano, maxsplit=1)[0].strip()
            nombre = re.split(r"\s+\d[\d.,]*\s*[KkMm]?\s+joined", nombre, maxsplit=1)[0].strip()
            nombre = re.sub(r"^(?:\d+\s*[a-z]+\s+ago|\d+[moMO]+)\s+", "", nombre)
            nombre = re.sub(r"\s+\d+\s*$", "", nombre)
            nombre = re.sub(r"\s+", " ", nombre)
            nombre = re.sub(r"^Join Campaign Preview\s+", "", nombre, flags=re.I)
            if not nombre or len(nombre) < 3:
                first_chunk = re.split(r"[\s,]+", plano, maxsplit=8)
                cleaned = [
                    w for w in first_chunk
                    if not w.startswith("$") and not w.endswith("ago") and not w.endswith("mo") and len(w) > 2
                ][:6]
                nombre = " ".join(cleaned) + f" ({exp_id[:8]})" if cleaned else f"Whop campaign {exp_id[:8]}"
            if len(nombre) > 120:
                nombre = nombre[:120]
            out.append({
                "experience_id": exp_id,
                "campaign_uuid": camp_uuid,
                "detail_url": f"https://whop.com/experiences/{exp_id}/campaigns/{camp_uuid}",
                "name": nombre,
                "cpm": _parse_cpm(plano),
                "prize_pool": _parse_prize_pool_usd(plano),
                "joined": _parse_joined(plano),
                "description": plano[:400],
            })
        return out

    def _parse_cards_from_html(self, src: str) -> list[dict[str, Any]]:
        """Plain-HTML fallback parser (kept for resilience)."""
        pat = re.compile(
            r'<a[^>]*href="(https://whop\.com/experiences/(exp_[A-Za-z0-9]{14,16})'
            r'/campaigns/([a-f0-9-]{36}))"[^>]*>',
            re.S,
        )
        anchors = list(pat.finditer(src))
        cards: list[dict[str, Any]] = []
        for i, m in enumerate(anchors):
            detail_url, exp_id, camp_uuid = m.group(1), m.group(2), m.group(3)
            start = m.end()
            end = anchors[i + 1].start() if i + 1 < len(anchors) else min(start + 8000, len(src))
            bloque = src[start:end]
            plano = re.sub(r"<[^>]+>", " ", bloque)
            plano = html.unescape(plano)
            plano = re.sub(r"\s+", " ", plano).strip()
            cards.append({
                "experience_id": exp_id,
                "campaign_uuid": camp_uuid,
                "detail_url": detail_url,
                "name": self._extract_name_from_text(plano, exp_id),
                "cpm": _parse_cpm(plano),
                "prize_pool": _parse_prize_pool_usd(plano),
                "joined": _parse_joined(plano),
                "description": plano[:400],
            })
        return cards

    @staticmethod
    def _extract_name_from_text(plano: str, exp_id: str) -> str:
        nombre = re.split(r"\s*\$", plano, maxsplit=1)[0].strip()
        nombre = re.split(r"\s+\d[\d.,]*\s*[KkMm]?\s+joined", nombre, maxsplit=1)[0].strip()
        nombre = re.sub(r"^(?:\d+\s*[a-z]+\s+ago|\d+[moMO]+)\s+", "", nombre)
        nombre = re.sub(r"\s+\d+\s*$", "", nombre)
        nombre = re.sub(r"\s+", " ", nombre)
        nombre = re.sub(r"^Join Campaign Preview\s+", "", nombre, flags=re.I)
        if not nombre or len(nombre) < 3:
            first_chunk = re.split(r"[\s,]+", plano, maxsplit=8)
            cleaned = [
                w for w in first_chunk
                if not w.startswith("$") and not w.endswith("ago") and not w.endswith("mo") and len(w) > 2
            ][:6]
            nombre = " ".join(cleaned) + f" ({exp_id[:8]})" if cleaned else f"Whop campaign {exp_id[:8]}"
        if len(nombre) > 120:
            nombre = nombre[:120]
        return nombre

    # Hosts where asset links ACTUALLY live. Discovered by inspecting real
    # Whop detail HTML (Yomi Denzel, 2026-09-10): asset binaries are served from
    # whop's own CDN (assets-2-prod.whop.com, img-v2-prod.whop.com) or R2/S3
    # buckets. Public detail pages do NOT expose drive/youtube/sheets URLs —
    # those are gated behind Whop's join/login flow.
    _ASSET_HOSTS = (
        # Public assets (visible in detail HTML)
        "assets-2-prod.whop.com",
        "assets-prod.whop.com",
        "img-v2-prod.whop.com",
        "whop-static.com",
        "whopstatic.com",
        # Generic CDN hosts where clips/banners may live
        "r2.dev",
        "amazonaws.com",
        "cloudfront.net",
        # External asset hosts (rare in public detail, common after join)
        "drive.google.com",
        "docs.google.com/spreadsheets",
        "dropbox.com",
        "mega.nz",
    )

    # Social platforms — these are mostly *official Whop accounts*, not campaign
    # assets, but kept as last resort for social-clip campaigns. Filtered by
    # `_looks_like_official_account` to drop obvious noise.
    _SOCIAL_HOSTS = (
        "youtube.com", "youtu.be",
        "tiktok.com",
        "instagram.com",
        "twitter.com", "x.com",
        "reddit.com",
        "twitch.tv",
    )

    @staticmethod
    def _looks_like_official_account(url: str) -> bool:
        """Return True if the URL looks like a generic profile, not a specific asset."""
        u = url.lower().rstrip("/")
        # bare social roots or /@official_handle patterns
        bare_roots = (
            "https://www.youtube.com",
            "https://youtube.com",
            "https://www.tiktok.com",
            "https://tiktok.com",
            "https://www.instagram.com",
            "https://instagram.com",
            "https://www.x.com",
            "https://x.com",
            "https://www.twitter.com",
            "https://twitter.com",
            "https://www.reddit.com",
            "https://reddit.com",
            "https://www.twitch.tv",
            "https://twitch.tv",
        )
        if u in bare_roots:
            return True
        # Official Whop handles
        for handle in ("/whop", "/whopapp", "/whopcom", "/whop_com"):
            if handle in u:
                return True
        # No path beyond root → just a profile page
        from urllib.parse import urlparse
        p = urlparse(url)
        if not p.path or p.path == "/" or p.path.strip("/") == "":
            return True
        return False

    def fetch_detail(self, campaign: DiscoveredCampaign) -> DiscoveredCampaign | None:
        try:
            src = self._fetch(campaign.detail_url)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.info("whop fetch_detail failed for %s: %s", campaign.detail_url, e)
            return campaign
        # Filter "Submissions closed" / closed campaigns (2026-09-18):
        # The public JSON listing API returns status='active' for ALL cards
        # (Coinpoker included, even though the card badge says "Submissions
        # closed"). Whop hides the badge-driving field behind the auth-gated
        # detail page. To avoid adding no-go campaigns we check the detail
        # HTML (including embedded JSON/scripts) for clear closed-state signals.
        if self._detail_signals_closed(src):
            logger.info(
                "whop skip %s: detail HTML contains closed/submissions-ended signal",
                campaign.detail_url,
            )
            return None
        links = _extract_links(src)
        url_lc = [u.lower() for u in links]
        keep: list[str] = []
        for orig, lc in zip(links, url_lc):
            # Real asset hosts (Whop CDN, R2, S3, drive/youtube/etc.) → keep
            if any(h in lc for h in self._ASSET_HOSTS):
                keep.append(orig)
                continue
            # Social hosts only if it looks like a campaign-specific link (not
            # an official account profile).
            if any(h in lc for h in self._SOCIAL_HOSTS):
                if not self._looks_like_official_account(orig):
                    keep.append(orig)
                continue
        # De-dupe preserving order
        seen = set()
        deduped: list[str] = []
        for u in keep:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        campaign.asset_links = deduped
        campaign.raw["detail_links_total"] = len(links)
        campaign.raw["detail_links_kept"] = len(deduped)
        # If the public detail page exposed nothing, mark the campaign as
        # join-required so the Worker (with a logged-in Whop session) can
        # pull the .zip from the joined dashboard.
        if not deduped:
            campaign.raw["join_required"] = True
        return campaign

    # --- internals ---------------------------------------------------------

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return r.read().decode("utf-8", errors="replace")

    def _parse_cards(self, src: str) -> list[dict[str, Any]]:
        """Slice the discover HTML by card anchor and extract fields.

        Strategy: find every <a href=".../campaigns/UUID"> in document order,
        slice from each anchor to the next (or 8KB max), then parse the
        rendered text of that slice for CPM/prize/joined.
        """
        pat = re.compile(
            r'<a[^>]*href="(https://whop\.com/experiences/(exp_[A-Za-z0-9]{14,16})'
            r'/campaigns/([a-f0-9-]{36}))"[^>]*>',
            re.S,
        )
        anchors = list(pat.finditer(src))
        cards: list[dict[str, Any]] = []
        for i, m in enumerate(anchors):
            detail_url, exp_id, camp_uuid = m.group(1), m.group(2), m.group(3)
            start = m.end()
            end = anchors[i + 1].start() if i + 1 < len(anchors) else min(start + 8000, len(src))
            bloque = src[start:end]

            plano = re.sub(r"<[^>]+>", " ", bloque)
            plano = html.unescape(plano)
            plano = re.sub(r"\s+", " ", plano).strip()

            # Name = first phrase before the first "$" or before "joined"
            nombre = re.split(r"\s*\$", plano, maxsplit=1)[0].strip()
            nombre = re.split(r"\s+\d[\d.,]*\s*[KkMm]?\s+joined", nombre, maxsplit=1)[0].strip()
            nombre = re.sub(r"^(?:\d+\s*[a-z]+\s+ago|\d+[moMO]+)\s+", "", nombre)
            nombre = re.sub(r"\s+\d+\s*$", "", nombre)
            nombre = re.sub(r"\s+", " ", nombre)
            # Strip "Join Campaign Preview" prefix from raw card text if present
            nombre = re.sub(r"^Join Campaign Preview\s+", "", nombre, flags=re.I)
            if not nombre or len(nombre) < 3 or nombre.lower().startswith("join campaign preview"):
                # Fallback: synthesize a readable name from exp_id + first card words
                first_chunk = re.split(r"[\s,]+", plano, maxsplit=8)
                cleaned = [w for w in first_chunk if not w.startswith("$") and not w.endswith("ago") and not w.endswith("mo") and len(w) > 2][:6]
                if cleaned:
                    nombre = " ".join(cleaned) + f" ({exp_id[:8]})"
                else:
                    nombre = f"Whop campaign {exp_id[:8]}"
            if len(nombre) > 120:
                nombre = nombre[:120]

            cards.append({
                "experience_id": exp_id,
                "campaign_uuid": camp_uuid,
                "detail_url": detail_url,
                "name": nombre,
                "cpm": _parse_cpm(plano),
                "prize_pool": _parse_prize_pool_usd(plano),
                "joined": _parse_joined(plano),
                "description": plano[:400],
            })
        return cards
