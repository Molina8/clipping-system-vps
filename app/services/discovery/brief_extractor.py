"""Brief Extractor — read campaign rules and feature URLs from Drive docs/PDFs.

A Whop campaign's `referenceMaterials` often points at Google Drive files
that aren't raw video — they're PDFs or Google Docs containing:

  - the campaign rules (lanes, payout rates, banned content)
  - official audio links (TikTok/Instagram/YouTube)
  - sometimes direct links to the source videos clippers should use

This module downloads those files (using the same DriveFetcher the Worker
uses for actual videos) and extracts structured information:

  - `text`: full plain text of the document
  - `feature_urls`: every http/https link found inside
  - `sections`: parsed headings + body text (WHAT TO CLIP, PAYOUT RULES, FAQ, ...)
  - `page_count`: number of pages (PDFs only)

Docs vs PDFs:
  - Google Docs → export via `docs.google.com/document/d/<ID>/export?format=txt`
    (returns plain text, no auth required for shared docs)
  - PDF files on Drive → already downloaded via DriveFetcher, parse with pypdf
  - Plain text/markdown → read directly
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

# Default user agent (mirror DriveFetcher's so we don't get HTML "unsupported
# browser" pages).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class BriefContent:
    """Structured extraction of a campaign brief (PDF/Doc/markdown)."""

    text: str
    feature_urls: list[str] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    page_count: int | None = None
    mime_type: str = ""
    char_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------- Google Docs export URL ----------------

_DOCS_EXPORT_TXT_RE = re.compile(
    r"^https?://docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})/?",
    re.IGNORECASE,
)


def docs_export_url(url: str, fmt: str = "txt") -> str | None:
    """Return the `docs.google.com/.../export?format=<fmt>` URL for a Doc.

    Returns None if `url` is not a Google Docs URL.
    Supported fmts: txt, html, pdf, docx.
    """
    m = _DOCS_EXPORT_TXT_RE.match(url.strip())
    if not m:
        return None
    doc_id = m.group(1)
    return (
        f"https://docs.google.com/document/d/{doc_id}/export?format={fmt}"
    )


# ---------------- Downloaders ----------------


def fetch_url_to_bytes(url: str, *, timeout: int = 30, user_agent: str = DEFAULT_USER_AGENT) -> bytes:
    """Fetch a URL and return raw bytes. Raises on HTTP error."""
    resp = requests.get(
        url,
        headers={"User-Agent": user_agent, "Accept": "*/*"},
        timeout=timeout,
        allow_redirects=True,
    )
    resp.raise_for_status()
    return resp.content


def fetch_docs_text(url: str, *, timeout: int = 30) -> str:
    """Download a Google Doc as plain text via the export endpoint."""
    export = docs_export_url(url, fmt="txt")
    if export is None:
        raise ValueError(f"Not a Google Docs URL: {url}")
    return fetch_url_to_bytes(export, timeout=timeout).decode("utf-8", errors="replace")


def fetch_pdf_bytes(url: str, *, timeout: int = 60, dest_dir: Path | None = None) -> Path:
    """Download a PDF from a URL to a temporary file and return its path.

    If `dest_dir` is given, the file is saved there; otherwise we use
    `tempfile.gettempdir()`. The file is named after the URL's last path
    segment, falling back to a hash of the URL.
    """
    import hashlib
    import tempfile

    content = fetch_url_to_bytes(url, timeout=timeout)
    if dest_dir is None:
        dest_dir = Path(tempfile.gettempdir())
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Derive a filename: prefer URL's last segment, else hash the URL.
    last = url.rsplit("/", 1)[-1].split("?")[0]
    if last and len(last) < 64 and last.lower().endswith(".pdf"):
        name = last
    else:
        name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".pdf"
    out = dest_dir / name
    out.write_bytes(content)
    return out


# ---------------- PDF text extraction ----------------


def extract_pdf_text(pdf_path: Path) -> tuple[str, int]:
    """Extract plain text from a PDF. Returns (text, page_count).

    Uses pypdf. Falls back to an empty string if pypdf is unavailable or the
    file is not a valid PDF (caller should check mime_type separately).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf not installed; cannot extract PDF text")
        return "", 0

    reader = PdfReader(str(pdf_path))
    pages_text: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception as exc:
            logger.warning("PDF page extraction error: %s", exc)
            t = ""
        pages_text.append(t)
    return "\n".join(pages_text), len(reader.pages)


# ---------------- URL extraction ----------------

_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+")


def extract_urls(text: str) -> list[str]:
    """Extract every http/https URL from a block of text, deduplicated and
    in order of first appearance. Trims common trailing punctuation."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?'\"")
        # Drop trailing parens/brackets that snuck in
        url = url.rstrip(")]}>")
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


# ---------------- Section parsing ----------------

# Headings common in Whop campaign briefs.
_HEADING_HINTS = [
    "what to clip",
    "base videos",
    "payout",
    "rules",
    "faq",
    "official audio",
    "hashtag",
    "submit",
    "how to",
    "requirements",
    "do not",
    "don't",
    "requirements",
    "format",
]


def _is_heading(line: str) -> bool:
    """Heuristic: a line is a heading if it's short, ALL CAPS or Title Case,
    and contains one of the campaign-brief keywords (or matches a few generic
    patterns)."""
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if s.endswith(":"):
        s = s[:-1].strip()
    if not s:
        return False
    lowered = s.lower()
    # Has a hint keyword
    if any(h in lowered for h in _HEADING_HINTS):
        return True
    # All-caps short lines (likely a section header in markdown-style brief)
    if s.upper() == s and len(s) <= 60 and any(c.isalpha() for c in s):
        return True
    # Title Case line under 50 chars
    if (
        len(s) <= 50
        and s == s.title()
        and any(c.isalpha() for c in s)
    ):
        return True
    return False


def parse_sections(text: str) -> list[dict]:
    """Split text into sections by detecting headings.

    Returns a list of {title, body} dicts. The first section may have an
    empty title (preamble before the first heading).
    """
    lines = text.splitlines()
    sections: list[dict] = []
    current_title = ""
    current_body: list[str] = []

    def flush() -> None:
        body = "\n".join(current_body).strip()
        if current_title or body:
            sections.append({"title": current_title, "body": body})
        current_body.clear()

    for line in lines:
        if _is_heading(line):
            flush()
            current_title = line.strip().rstrip(":")
        else:
            current_body.append(line)
    flush()
    return sections


# ---------------- High-level extraction ----------------


def extract_from_pdf_path(pdf_path: Path) -> BriefContent:
    """Extract structured info from an already-downloaded PDF on disk."""
    text, pages = extract_pdf_text(pdf_path)
    return BriefContent(
        text=text,
        feature_urls=extract_urls(text),
        sections=parse_sections(text),
        page_count=pages,
        mime_type="application/pdf",
        char_count=len(text),
    )


def extract_from_url(url: str, *, dest_dir: Path | None = None, timeout: int = 60) -> BriefContent:
    """Download a file from a URL and extract structured info.

    Behaviour by URL family:
      - Google Docs (docs.google.com/document/...) → export as text
      - Drive PDFs (drive.google.com/.../file/d/...) → download via DriveFetcher,
        parse with pypdf
      - Direct PDFs (.pdf) → download bytes, parse with pypdf
      - Anything else → fetch as plain text
    """
    from app.whop_integration.drive_fetcher import DriveFetcher

    lowered = url.lower()

    # 1. Google Docs → export as txt
    if "docs.google.com/document/" in lowered:
        try:
            text = fetch_docs_text(url, timeout=timeout)
            return BriefContent(
                text=text,
                feature_urls=extract_urls(text),
                sections=parse_sections(text),
                page_count=None,
                mime_type="text/plain",
                char_count=len(text),
            )
        except Exception as exc:
            logger.warning("Docs export failed for %s: %s", url, exc)
            return BriefContent(text="", mime_type="text/plain", char_count=0)

    # 2. Drive PDFs → use DriveFetcher (handles confirm cookies for big files)
    if "drive.google.com" in lowered:
        try:
            fetcher = DriveFetcher(timeout=timeout)
            pdf_path = fetcher.download_file(url, dest_dir or Path("/tmp/cs_briefs"))
            return extract_from_pdf_path(pdf_path)
        except Exception as exc:
            logger.warning("DriveFetcher download failed for %s: %s", url, exc)
            return BriefContent(text="", mime_type="application/pdf", char_count=0)

    # 3. Direct PDF
    if lowered.endswith(".pdf") or "pdf" in lowered:
        try:
            pdf_path = fetch_pdf_bytes(url, timeout=timeout, dest_dir=dest_dir)
            return extract_from_pdf_path(pdf_path)
        except Exception as exc:
            logger.warning("PDF fetch failed for %s: %s", url, exc)
            return BriefContent(text="", mime_type="application/pdf", char_count=0)

    # 4. Fallback: fetch as text
    try:
        text = fetch_url_to_bytes(url, timeout=timeout).decode("utf-8", errors="replace")
        return BriefContent(
            text=text,
            feature_urls=extract_urls(text),
            sections=parse_sections(text),
            page_count=None,
            mime_type="text/plain",
            char_count=len(text),
        )
    except Exception as exc:
        logger.warning("Generic fetch failed for %s: %s", url, exc)
        return BriefContent(text="", mime_type="text/plain", char_count=0)
