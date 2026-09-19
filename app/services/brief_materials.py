"""Fetch brief materials linked from Whop discovery (Google Docs, etc.).

No gog required for public Docs export. Private Docs stay empty + error flag.
Does not list MediaSilo / video hosts — only extracts URLs for scoring.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Any

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_DOC_ID = re.compile(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")
_URL = re.compile(r"https?://[^\s\]\\)<>\"']+")

UNSUPPORTED_VIDEO_HOSTS = (
    "mediasilo.com",
    "we.tl",
    "wetransfer.com",
    "frame.io",
    "dropbox.com",
    "mega.nz",
)


def classify_url(url: str) -> str:
    u = (url or "").lower()
    if "docs.google.com/document" in u:
        return "google_doc"
    if "drive.google.com" in u and "/folders/" in u:
        return "drive_folder"
    if "drive.google.com" in u and "/file/" in u:
        return "drive_file"
    if "mediasilo.com" in u:
        return "mediasilo"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if any(h in u for h in UNSUPPORTED_VIDEO_HOSTS):
        return "unsupported_host"
    return "external"


def extract_urls(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in _URL.findall(text or ""):
        u = raw.rstrip(".,);")
        if u not in seen and u.startswith("http"):
            seen.add(u)
            out.append(u)
    return out


def google_doc_id(url: str) -> str | None:
    m = _DOC_ID.search(url or "")
    return m.group(1) if m else None


def fetch_google_doc_text(url: str, *, timeout_s: float = 20.0) -> tuple[str | None, str | None]:
    doc_id = google_doc_id(url)
    if not doc_id:
        return None, "not_a_google_doc"
    export = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    req = urllib.request.Request(
        export,
        headers={"User-Agent": _UA, "Accept": "text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            raw = r.read()
        text = raw.decode("utf-8", errors="replace")
        if "<html" in text[:200].lower():
            return None, "doc_not_public_html"
        return text.strip(), None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return None, f"{type(e).__name__}: {e}"


def heuristic_flags(text: str) -> dict[str, Any]:
    t = (text or "").lower()
    return {
        "watermark_required": any(
            k in t for k in ("watermark", "logo requirement", "official" + " logo", "logo download")
        )
        or ("watermark" in t and "logo" in t),
        "captions_required": "caption" in t and ("required" in t or "must" in t),
        "on_screen_text_required": "on-screen text" in t or "on screen text" in t,
        "tagging_required": "tag @" in t or "tagging requirement" in t,
        "extra_music_forbidden": "no added background music" in t or "only the original audio" in t,
        "min_seconds_hint": _min_seconds(t),
    }


def _min_seconds(t: str) -> float | None:
    m = re.search(r"at least\s+(\d+)\s+seconds", t)
    if m:
        return float(m.group(1))
    return None


def collect_reference_urls(discovered: dict) -> list[str]:
    urls: list[str] = []
    for rm in discovered.get("reference_materials") or []:
        if isinstance(rm, dict) and rm.get("url"):
            urls.append(rm["url"])
        elif isinstance(rm, str):
            urls.append(rm)
    for u in discovered.get("asset_links") or []:
        if isinstance(u, str):
            urls.append(u)
    urls.extend(extract_urls(discovered.get("description") or ""))
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out
