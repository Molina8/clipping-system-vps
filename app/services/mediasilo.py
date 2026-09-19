"""List assets from a public MediaSilo review link.

URL shapes:
  https://app.mediasilo.com/review/<reviewId>
  https://app.mediasilo.com/review/<reviewId>/f/<folderOrAssetId>

Uses the same unauthenticated JSON the SPA calls. Datacenter IPs often
get empty 404; run on the VPS / residential path.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.mediasilo.com/v3"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REVIEW_RE = re.compile(
    r"mediasilo\.com/review/([a-zA-Z0-9]+)(?:/f/([a-zA-Z0-9-]+))?",
    re.I,
)


def parse_review_url(url: str) -> tuple[str, str | None] | None:
    m = REVIEW_RE.search(url or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def _get(path: str, referer: str) -> Any:
    url = API + path
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://app.mediasilo.com",
            "Referer": referer,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"mediasilo HTTP {e.code} {path} {body[:180]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"mediasilo {type(e).__name__}: {e}") from e
    if not raw:
        raise RuntimeError(f"mediasilo empty body {path}")
    return json.loads(raw)


def _as_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in ("data", "assets", "items", "results"):
            val = payload.get(k)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def _is_video(item: dict) -> bool:
    t = str(item.get("type") or item.get("assetType") or item.get("kind") or "").lower()
    mime = str(item.get("mimeType") or item.get("contentType") or "").lower()
    name = str(item.get("title") or item.get("name") or item.get("filename") or "").lower()
    if t in {"video", "movie", "clip"} or mime.startswith("video/"):
        return True
    return name.endswith((".mp4", ".mov", ".mkv", ".webm", ".m4v"))


def _size(item: dict) -> int | None:
    for k in ("fileSize", "filesize", "size", "bytes"):
        v = item.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    src = item.get("source") or item.get("file") or {}
    if isinstance(src, dict):
        for k in ("fileSize", "size", "bytes"):
            v = src.get(k)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
    return None


def list_review(review_id: str, folder_id: str | None = None) -> list[dict]:
    referer = f"https://app.mediasilo.com/review/{review_id}"
    if folder_id:
        referer += f"/f/{folder_id}"
    _get(f"/quicklinks/{review_id}", referer)
    if folder_id:
        path = (
            f"/quicklinks/{review_id}/folders/{folder_id}/assets"
            f"?_page=1&_pageSize=50&_sortBy=_default&_sort=asc"
        )
    else:
        path = (
            f"/quicklinks/{review_id}/assets"
            f"?_page=1&_pageSize=50&_sortBy=_default&_sort=asc"
        )
    items = _as_list(_get(path, referer))
    out = []
    for item in items:
        if not _is_video(item):
            continue
        aid = str(item.get("id") or item.get("assetId") or "")
        if not aid:
            continue
        name = str(item.get("title") or item.get("name") or item.get("filename") or aid)
        out.append(
            {
                "id": aid,
                "name": name,
                "file_size": _size(item),
                "source_url": f"https://app.mediasilo.com/review/{review_id}/f/{aid}",
                "raw_type": item.get("type") or item.get("assetType"),
            }
        )
    return out
