"""Deterministic campaign score. No LLM.

Used by 3c and by 3a preview (to skip hard campaigns before Drive resolve).
"""
from __future__ import annotations

from typing import Any

MIN_SCORE_TO_RUN = 50.0


def difficulty_penalties(rules: dict[str, Any] | None, content_kinds: list[str] | None = None) -> dict[str, float]:
    rules = rules or {}
    kinds = set(content_kinds or rules.get("content_source_kinds") or [])
    pen: dict[str, float] = {}
    if rules.get("watermark_required"):
        pen["watermark"] = 20.0
    if rules.get("captions_required"):
        pen["captions"] = 10.0
    if rules.get("on_screen_text_required"):
        pen["on_screen_text"] = 10.0
    if rules.get("tagging_required"):
        pen["tagging"] = 5.0
    if rules.get("extra_music_forbidden"):
        pen["no_extra_music"] = 2.0
    hard_hosts = {"mediasilo", "unsupported_host"}
    if kinds & hard_hosts or rules.get("unsupported_video_host"):
        pen["unsupported_host"] = 25.0
    if rules.get("heavy_source_files"):
        pen["heavy_files"] = 15.0
    return pen


def base_score(real_assets: int, cpm: float, prize: float, verified: bool) -> float:
    s = 15.0
    s += min(max(real_assets, 0), 12) * 4.0
    s += min(max(cpm, 0.0), 10.0) * 4.0
    s += min(max(prize, 0.0) / 10000.0, 20.0)
    if verified:
        s += 8.0
    return s


def score_campaign(
    *,
    real_assets: int,
    cpm: float,
    prize: float,
    verified: bool,
    rules: dict[str, Any] | None = None,
    content_kinds: list[str] | None = None,
) -> dict[str, Any]:
    raw = base_score(real_assets, cpm, prize, verified)
    pens = difficulty_penalties(rules, content_kinds)
    total_pen = sum(pens.values())
    value = round(min(100.0, max(0.0, raw - total_pen)), 2)
    return {
        "value": value,
        "base": round(raw, 2),
        "penalties": pens,
        "penalty_total": total_pen,
        "min_to_run": MIN_SCORE_TO_RUN,
        "eligible": value >= MIN_SCORE_TO_RUN and real_assets > 0 and "unsupported_host" not in pens,
    }
