"""Asset Resolver — Step 5/6 of architecture_flow.md.

Given a campaign + a list of candidate asset URLs, register each as an `Asset`
row with `source_url` + `kind` (drive/youtube/googlesheets/dropbox/mega/external).
Idempotent on (campaign_id, source_url).

Drive URL canonicalization
--------------------------
Some Whop campaigns link to Google Drive *features* (raw uploaded videos,
project files, B-roll packs) inside `referenceMaterials`. These arrive as
either:

    1. https://drive.google.com/file/d/<ID>/view?usp=sharing     (preview URL)
    2. https://drive.google.com/uc?export=download&id=<ID>       (download URL)
    3. https://drive.google.com/drive/folders/<ID>?usp=sharing   (folder URL)
    4. https://drive.google.com/drive/u/<N>/folders/<ID>?usp=sharing
    5. https://docs.google.com/document/d/<ID>/edit?usp=sharing  (Docs file)

We rewrite variant (1) into variant (2) so the Worker can download the file
directly without having to follow Drive's preview redirect. Variants (3) and
(4) are *folders* — we can't download them as a single file, so we reject
them (return ``None``) so the caller can decide to list-and-enumerate instead.
Variant (5) is also rejected: a Google Doc is not a video clip; we'd waste a
Worker slot trying to download HTML.

This module is the single source of truth for that policy; callers should
never try to be cleverer than ``rewrite_drive_url``.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetStatus

logger = logging.getLogger(__name__)


# ----------------------------- classification -----------------------------


def classify_link(url: str) -> str:
    """Return the asset kind: drive, youtube, googlesheets, dropbox, mega, external."""
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


# ----------------------------- Drive URL policy ---------------------------

# A "file" preview URL — Drive shows this when you click "Share -> Copy link"
# on any uploaded file (videos, project files, etc.).
_DRIVE_FILE_PREVIEW_RE = re.compile(
    r"https?://drive\.google\.com/(?:file/d|open)(?:/d)?/([A-Za-z0-9_-]{20,})"
    r"(?:/view)?(?:\?[^#\s]*)?(?:#[^\s]*)?$",
    re.IGNORECASE,
)

# Already-canonical Drive download URL.
_DRIVE_FILE_DOWNLOAD_RE = re.compile(
    r"https?://drive\.google\.com/uc\?(?:[^#\s]*&)*id=([A-Za-z0-9_-]{20,})"
    r"(?:[^#\s]*)?$",
    re.IGNORECASE,
)

# Drive *folder* URLs — we never auto-download these; the caller must list them.
_DRIVE_FOLDER_RE = re.compile(
    r"https?://drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{20,})"
    r"(?:\?[^#\s]*)?(?:#[^\s]*)?$",
    re.IGNORECASE,
)

# Drive "filefolders" (rare, but seen in the wild).
_DRIVE_FILEFOLDER_RE = re.compile(
    r"https?://drive\.google\.com/drive/(?:u/\d+/)?filefolders/([A-Za-z0-9_-]{20,})"
    r"(?:\?[^#\s]*)?(?:#[^\s]*)?$",
    re.IGNORECASE,
)

# Google Docs/Sheets/Slides/Forms — hosted editors, not raw files.
# We reject ANY docs.google.com/* URL because they are editor pages, not raw
# video files that the Worker can download. The rewrite to uc?export=download
# is meaningless for these hosts (Drive only handles /file/d/<ID> patterns).
_DOCS_GOOGLE_RE = re.compile(
    r"https?://docs\.google\.com/",
    re.IGNORECASE,
)


def rewrite_drive_url(url: str) -> Optional[str]:
    """Canonicalize a Drive URL into a Worker-downloadable form.

    Returns:
        - ``None`` if the URL is a Drive *folder* (cannot be downloaded as a
          single file) or a Google Docs editor URL (not a video).
        - The canonical ``https://drive.google.com/uc?export=download&id=<ID>``
          form for any *file* URL (preview link or already-rewritten).
        - The original URL unchanged if it's not a Drive URL at all.

    The check is conservative: when in doubt (unknown host, ambiguous path)
    we pass the URL through unchanged so the downstream Worker sees exactly
    what the campaign author wrote.
    """
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None

    # Anything that isn't google drive / docs / etc → leave it alone.
    lowered = raw.lower()
    if "drive.google.com" not in lowered and "docs.google.com" not in lowered:
        return raw

    # Reject folders before file rewrites — the patterns can overlap on bare IDs.
    if _DRIVE_FILEFOLDER_RE.match(raw):
        logger.info("drive_resolver: rejecting filefolder URL: %s", raw)
        return None
    if _DRIVE_FOLDER_RE.match(raw):
        logger.info("drive_resolver: rejecting folder URL: %s", raw)
        return None

    # Reject Google Docs editor URLs — these are not raw video files.
    if _DOCS_GOOGLE_RE.match(raw):
        logger.info("drive_resolver: rejecting docs.google.com editor URL: %s", raw)
        return None

    # Already-canonical download URL → return as-is (preserve any extra params
    # like confirm=t for the virus-scan warning bypass).
    m = _DRIVE_FILE_DOWNLOAD_RE.match(raw)
    if m:
        return raw

    # Preview URL → rewrite to canonical download form.
    m = _DRIVE_FILE_PREVIEW_RE.match(raw)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    # Unknown Drive-shaped URL — pass through so the Worker can fail loud
    # rather than silently losing the campaign author's intent.
    return raw


# ----------------------------- id extraction -----------------------------


def _extract_id(kind: str, url: str) -> str | None:
    """Extract a platform-specific id from a URL."""
    if kind == "youtube":
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{6,})", url)
        return m.group(1) if m else None
    if kind == "googlesheets":
        m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else None
    if kind == "mega":
        m = re.search(r"/file/([^#]+)", url)
        return m.group(1) if m else None
    if kind == "drive":
        m = re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", url)
        if m:
            return m.group(1)
        m = re.search(r"/(?:file/d|folders|d)/([A-Za-z0-9_-]{20,})", url)
        return m.group(1) if m else None
    return None


# ----------------------------- main entrypoint ---------------------------


def resolve_assets_for_campaign(
    db: Session,
    campaign_id: int,
    candidate_links: Iterable[str],
    *,
    discovery_provider: str | None = None,
) -> list[Asset]:
    """Register each candidate link as an Asset row if not already present.

    Drive URLs are canonicalized via :func:`rewrite_drive_url` before storage:

      * ``file/d/<ID>/view`` → ``uc?export=download&id=<ID>``
      * Already-canonical download URL → unchanged
      * Drive **folder** URL (``drive/folders/<ID>``) → dropped (returns
        empty for that link; the campaign's parent resolver should expand
        folders separately via ``DriveFetcher.list_folder``)
      * ``docs.google.com/document|presentation|forms|drawings|folder`` →
        dropped (not a video file)

    `discovery_provider` is the provider that *found* the asset (e.g. "whop"),
    while `source_provider` is the platform the asset actually lives on
    (e.g. "youtube", "drive", "dropbox"). The asset row needs both, plus a
    `kind` for compatibility with the existing classification.

    Returns the list of newly created Asset rows (existing ones are skipped).
    """
    created: list[Asset] = []
    rejected: list[tuple[str, str]] = []  # (url, reason)
    for url in candidate_links:
        original = url.strip()
        if not original or not original.startswith(("http://", "https://")):
            continue

        # Drive URLs: canonicalize. rewrite_drive_url returns:
        #   - canonical download URL  (use it)
        #   - the same URL            (non-Drive, pass through)
        #   - None                    (folder or docs.google.com — drop)
        url = rewrite_drive_url(original)
        if url is None:
            rejected.append((original, "drive_folder_or_docs"))
            continue

        kind = classify_link(url)

        # Dedupe against the *canonicalized* URL so re-runs don't pile up.
        existing = db.execute(
            select(Asset).where(
                Asset.campaign_id == campaign_id,
                Asset.source_url == url,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        asset = Asset(
            campaign_id=campaign_id,
            source_url=url,
            source_id=_extract_id(kind, url),
            source_provider=discovery_provider or kind,
            asset_type="video" if kind in ("youtube", "drive", "dropbox", "mega", "external") else "sheet",
            status=AssetStatus.PENDING.value,
            extra_metadata={
                "kind": kind,
                "discovered_by": discovery_provider or "manual",
                "original_url": original if original != url else None,
            },
        )
        db.add(asset)
        created.append(asset)
    if created:
        db.commit()
        for a in created:
            db.refresh(a)
        logger.info(
            "asset_resolver: created %d new assets for campaign %s",
            len(created), campaign_id,
        )
    if rejected:
        logger.info(
            "asset_resolver: rejected %d Drive URLs for campaign %s: %s",
            len(rejected), campaign_id,
            [{"url": u, "reason": r} for u, r in rejected[:5]],
        )
    return created
