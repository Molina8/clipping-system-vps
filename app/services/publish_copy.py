"""Title/description for social posts. 0 LLM on the publish path."""
from __future__ import annotations

import re
from typing import Any

_WHOP_PREFIX = re.compile(
    r"^\s*\[whop\]\s*(?:·|\|)\s*\$[^·|]+(?:·|\|)\s*\$[^·|]+(?:·|\|)?\s*",
    re.IGNORECASE,
)


def _meta(campaign: Any) -> dict:
    if campaign is None:
        return {}
    sm = getattr(campaign, "source_metadata", None) or {}
    return sm if isinstance(sm, dict) else {}


def clean_campaign_title(raw: str) -> str:
    text = (raw or "").strip()
    text = _WHOP_PREFIX.sub("", text).strip(" ·|-+")
    text = re.sub(r"\s+", " ", text)
    return text[:100] if text else "clip"


def _hashtags(campaign: Any) -> list[str]:
    sm = _meta(campaign)
    discovered = sm.get("discovered") if isinstance(sm.get("discovered"), dict) else {}
    rules = sm.get("rules") if isinstance(sm.get("rules"), dict) else {}
    extra = rules.get("hashtags") or discovered.get("hashtags") or []
    if isinstance(extra, str):
        extra = extra.split()
    tags: list[str] = []
    for tag in extra:
        t = str(tag).strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t.lstrip("#")
        if t.lower() not in {h.lower() for h in tags}:
            tags.append(t)
    if "#Shorts" not in tags:
        tags.append("#Shorts")
    return tags[:8]


def youtube_copy(campaign: Any, clip: Any, candidate: Any = None) -> tuple[str, str, list[str]]:
    camp_title = clean_campaign_title(getattr(campaign, "name", None) or "")
    cmeta = {}
    if candidate is not None:
        raw = getattr(candidate, "extra_metadata", None) or {}
        if isinstance(raw, dict):
            cmeta = raw
    kind = str(cmeta.get("kind") or cmeta.get("source") or "")
    title = str(cmeta.get("title") or "").strip()[:100] or camp_title

    caption = (
        str(cmeta.get("caption") or cmeta.get("description") or "").strip()
        or str(getattr(candidate, "reasoning", None) or "").strip()
    )
    if len(caption) < 12 or kind in {"silent", "duration_cut"}:
        caption = (
            f"{camp_title}\n\n"
            "Highlight from the campaign. Watch till the end."
        )
    elif caption.lower() in {"grok", "short", "speech"}:
        caption = f"{camp_title}\n\n{caption}"

    tags = _hashtags(campaign)
    description = f"{caption}\n\n{' '.join(tags)}"[:5000]
    return title, description, tags
