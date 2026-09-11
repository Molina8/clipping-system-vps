"""WhopProvider — discovers campaigns from a Whop tenant sub-app.

Whop renders campaign data in plain HTML on the `discover` sub-app:
  https://{tenant}.apps.whop.com/discover
This works without API key / OAuth / browser headless.

Each card is anchored by an `<a href="https://whop.com/experiences/{exp}/campaigns/{uuid}">`
that contains the campaign name. The CPM/prize/joined numbers are in
sibling text (outside the </a>) but rendered in the same chunk of HTML.

This provider's `discover()` parses those cards and returns DiscoveredCampaign
records. `fetch_detail()` then loads the campaign detail page to extract
asset links (Drive/YouTube/Sheets/Dropbox/Mega).
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from app.services.discovery.base import CampaignProvider
from app.services.discovery.models import DiscoveredCampaign

logger = logging.getLogger(__name__)


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
        try:
            src = self._fetch(url)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.warning("whop discover fetch failed: %s", e)
            return []

        cards = self._parse_cards(src)
        out: list[DiscoveredCampaign] = []
        seen: set[tuple[str, str]] = set()
        for c in cards:
            key = (c["experience_id"], c["campaign_uuid"])
            if key in seen:
                continue
            seen.add(key)
            dc = DiscoveredCampaign(
                provider="whop",
                external_id=f"{c['experience_id']}/{c['campaign_uuid']}",
                detail_url=c["detail_url"],
                name=c["name"],
                description=c.get("description"),
                cpm_usd_per_1k=c.get("cpm"),
                prize_pool_usd=c.get("prize_pool"),
                joined=c.get("joined"),
                asset_links=[],
                raw={
                    "experience_id": c["experience_id"],
                    "campaign_uuid": c["campaign_uuid"],
                    "card_text": c.get("description"),
                },
            )
            out.append(dc)
            if len(out) >= limit:
                break
        logger.info("whop discover: %d unique campaigns from %s", len(out), self.tenant_url)
        return out

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
