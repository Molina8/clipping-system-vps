"""Deterministic campaign score. No LLM.

Few small videos score better than a pile or multi-GB files.
Resolver already caps listed assets (MAX_FILES=15).
"""
from __future__ import annotations

from typing import Any

MIN_SCORE_TO_RUN = 50.0
HEAVY_ONE_BYTES = 500 * 1024 * 1024
HEAVY_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def difficulty_penalties(
    rules: dict[str, Any] | None,
    content_kinds: list[str] | None = None,
    *,
    real_assets: int = 0,
    file_sizes: list[int] | None = None,
) -> dict[str, float]:
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
    if real_assets >= 10:
        pen["many_assets"] = 12.0
    elif real_assets >= 6:
        pen["many_assets"] = 6.0
    sizes = [int(s) for s in (file_sizes or []) if s]
    if sizes:
        if max(sizes) >= HEAVY_ONE_BYTES:
            pen["heavy_one"] = 12.0
        if sum(sizes) >= HEAVY_TOTAL_BYTES:
            pen["heavy_total"] = 15.0
    elif rules.get("heavy_source_files"):
        pen["heavy_files"] = 15.0
    return pen


def base_score(real_assets: int, cpm: float, prize: float, verified: bool) -> float:
    s = 15.0
    if real_assets <= 0:
        n = 0.0
    elif real_assets <= 3:
        n = real_assets * 8.0
    else:
        n = 24.0 + min(real_assets - 3, 12) * 1.5
    s += n
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
    file_sizes: list[int] | None = None,
) -> dict[str, Any]:
    raw = base_score(real_assets, cpm, prize, verified)
    pens = difficulty_penalties(
        rules,
        content_kinds,
        real_assets=real_assets,
        file_sizes=file_sizes,
    )
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
