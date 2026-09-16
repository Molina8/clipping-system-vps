"""Asset Resolver — Step 5/6 of architecture_flow.md.

Given a campaign + a list of candidate asset URLs, register each as an `Asset`
row with `source_url` + `kind`:

  - `brief`  : Google Docs, PDFs, or any docs describing the campaign rules
               (and possibly linking to the actual feature videos). The resolver
               downloads + extracts text/URLs/sections on the spot, so the
               downstream LLM has real campaign rules instead of guessing.
  - `feature`: raw videos to be clipped (Drive file, YouTube, Dropbox, mega,
               external mp4). Drive previews are canonicalised to
               `uc?export=download&id=<ID>` so the Worker downloads directly.

Rejected categories (return None) — these cannot be processed at all:
  - Drive **folders** (`drive/folders/<ID>`) — must be enumerated first via
    `DriveFetcher.list_folder` and the individual file URLs re-fed here.
  - Anything else: pass through unchanged so the caller / Worker sees what
    the campaign author wrote.

Idempotent on (campaign_id, source_url).
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
    """Return a coarse category: drive, docs, youtube, dropbox, mega, external."""
    u = url.lower()
    if "drive.google.com" in u:
        return "drive"
    if "docs.google.com/document" in u or "docs.google.com/presentation" in u or \
       "docs.google.com/forms" in u or "docs.google.com/drawings" in u:
        return "docs"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "docs.google.com/spreadsheets" in u:
        return "googlesheets"
    if "dropbox.com" in u:
        return "dropbox"
    if "mega.nz" in u:
        return "mega"
    return "external"


def is_brief_kind(kind: str, url: str = "") -> bool:
    """True if the asset should be treated as a campaign brief (docs/PDF),
    not as a raw feature video."""
    if kind == "docs":
        return True
    if url.lower().endswith(".pdf"):
        return True
    return False


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


def rewrite_drive_url(url: str) -> Optional[str]:
    """Canonicalize a Drive URL into a Worker-downloadable form.

    Returns:
        - ``None`` if the URL is a Drive *folder* (cannot be downloaded as a
          single file). The caller must enumerate it via ``DriveFetcher.list_folder``.
        - The canonical ``https://drive.google.com/uc?export=download&id=<ID>``
          form for any *file* URL (preview link or already-rewritten).
        - The original URL unchanged if it's not a Drive URL at all.

    `docs.google.com/*` URLs are *not* touched here — they're handled by
    ``brief_extractor`` and treated as campaign briefs.
    """
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None

    lowered = raw.lower()
    if "drive.google.com" not in lowered:
        return raw

    # Reject folders before file rewrites — patterns can overlap on bare IDs.
    if _DRIVE_FILEFOLDER_RE.match(raw):
        logger.info("drive_resolver: rejecting filefolder URL: %s", raw)
        return None
    if _DRIVE_FOLDER_RE.match(raw):
        logger.info("drive_resolver: rejecting folder URL: %s", raw)
        return None

    # Already-canonical download URL → return as-is (preserve extra params).
    m = _DRIVE_FILE_DOWNLOAD_RE.match(raw)
    if m:
        return raw

    # Preview URL → rewrite to canonical download form.
    m = _DRIVE_FILE_PREVIEW_RE.match(raw)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"

    # Unknown Drive-shaped URL — pass through so the Worker can fail loud.
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
    if kind in ("drive", "docs"):
        m = re.search(r"[?&]id=([A-Za-z0-9_-]{20,})", url)
        if m:
            return m.group(1)
        m = re.search(r"/(?:file/d|folders|document/d|d)/([A-Za-z0-9_-]{20,})", url)
        return m.group(1) if m else None
    return None


# ----------------------------- main entrypoint ---------------------------


def resolve_assets_for_campaign(
    db: Session,
    campaign_id: int,
    candidate_links: Iterable[str],
    *,
    discovery_provider: str | None = None,
    extract_briefs: bool = True,
) -> list[Asset]:
    """Register each candidate link as an Asset row if not already present.

    Drive URLs are canonicalized via :func:`rewrite_drive_url` before storage:

      * ``file/d/<ID>/view`` → ``uc?export=download&id=<ID>``
      * Already-canonical download URL → unchanged
      * Drive **folder** URL (``drive/folders/<ID>``) → dropped

    ``docs.google.com/document|presentation|forms|drawings/...`` URLs and
    direct PDF links are kept (not rejected) and treated as **campaign briefs**:

      * Asset row gets ``asset_type='brief'`` and ``source_provider='brief'``
      * On insert, :func:`extract_brief_sync` is called: it downloads the
        file, extracts text + URLs + sections, and saves them into
        ``extra_metadata.brief`` (in addition to the original ``original_url``).
      * If extraction fails, the row is still created with an empty brief
        payload and an ``extraction_error`` field so retries are possible.

    `discovery_provider` is the provider that *found* the asset (e.g. "whop").

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
        #   - None                    (folder — drop)
        canonical = rewrite_drive_url(original)
        if canonical is None:
            rejected.append((original, "drive_folder"))
            continue
        url = canonical

        kind = classify_link(url)
        is_brief = is_brief_kind(kind, url)

        # Dedupe against the *canonicalized* URL.
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
            source_provider=discovery_provider or ("brief" if is_brief else kind),
            asset_type="brief" if is_brief else (
                "video" if kind in ("youtube", "drive", "dropbox", "mega", "external")
                else "sheet"
            ),
            status=AssetStatus.PENDING.value,
            extra_metadata={
                "kind": kind,
                "is_brief": is_brief,
                "discovered_by": discovery_provider or "manual",
                "original_url": original if original != url else None,
            },
        )
        db.add(asset)
        db.flush()  # we need asset.id for the brief extraction if we save a path

        # If it's a brief, extract now (sync) so the LLM has real campaign
        # context on the very next clip_selection run.
        if is_brief and extract_briefs:
            try:
                from app.services.discovery.brief_extractor import extract_from_url
                brief = extract_from_url(url)
                asset.extra_metadata = {
                    **asset.extra_metadata,
                    "brief": brief.to_dict(),
                    "extraction_error": None,
                }
                # If we got useful text, mark the asset as `downloaded` so the
                # Worker doesn't try to re-download the PDF/Doc as a video.
                if brief.char_count > 0:
                    asset.status = AssetStatus.DOWNLOADED.value
                    asset.mime_type = brief.mime_type
                    asset.downloaded_at = None  # let onupdate stamp it
            except Exception as exc:
                logger.warning(
                    "brief_extractor failed for asset %s url=%s: %s",
                    asset.id, url, exc,
                )
                asset.extra_metadata["extraction_error"] = str(exc)

        created.append(asset)

    if created:
        db.commit()
        for a in created:
            db.refresh(a)
        logger.info(
            "asset_resolver: created %d new assets for campaign %s (rejected=%d)",
            len(created), campaign_id, len(rejected),
        )
    if rejected:
        logger.info(
            "asset_resolver: rejected %d Drive URLs for campaign %s: %s",
            len(rejected), campaign_id,
            [{"url": u, "reason": r} for u, r in rejected[:5]],
        )
    return created
