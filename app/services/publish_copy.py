"""Title/description for social posts. 0 LLM. Uses campaign name + brief flags."""
from __future__ import annotations

import re
from typing import Any, Optional

# "[whop] · $0.75/1k · $248k · FR Yomi …"
_WHOP_PREFIX = re.compile(
    r"^\s*\[whop\]\s*(?:·|\|)\s*\$[^\u00b7|]+(?:·|\|)\s*\$[^\u00b7|]+(?:·|\|)?\s*",
    re.IGNORECASE,
)


def _meta(campaign: Any) -> dict:
    if campaign is None:
        return {}
    sm = getattr(campaign, "source_metadata", None) or {}
    if not isinstance(sm, dict):
        return {}
    return sm


def clean_campaign_title(raw: str) -> str:
    text = (raw or "").strip()
    text = _WHOP_PREFIX.sub("", text).strip(" ·|-+")
    text = re.sub(r"\s+", " ", text)
    return text[:100] if text else "clip"


def youtube_copy(campaign: Any, clip: Any) -> tuple[str, str, list[str]]:
    raw_name = (getattr(campaign, "name", None) if campaign is not None else None) or ""
    title = clean_campaign_title(raw_name)
    sm = _meta(campaign)
    discovered = sm.get("discovered") if isinstance(sm.get("discovered"), dict) else {}
    rules = sm.get("rules") if isinstance(sm.get("rules"), dict) else {}

    hashtags: list[str] = []
    extra = rules.get("hashtags") or discovered.get("hashtags") or []
    if isinstance(extra, str):
        extra = extra.split()
    for tag in extra:
        t = str(tag).strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t.lstrip("#")
        if t.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(t)
    if "#Shorts" not in hashtags:
        hashtags.append("#Shorts")

    lines = [title]
    desc_brief = rules.get("caption") or rules.get("description") or discovered.get("description")
    if isinstance(desc_brief, str) and desc_brief.strip() and desc_brief.strip() != title:
        lines.append(desc_brief.strip()[:400])
    if hashtags:
        lines.append(" ".join(hashtags[:8]))
    description = "\n\n".join(lines)[:5000]
    return title, description, hashtags
