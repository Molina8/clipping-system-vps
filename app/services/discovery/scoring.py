"""Scoring algorithm — should we invest Worker time in this campaign?

Combines CPM + prize pool vs difficulty. See:
  ~/.openclaw/workspace/skills/campaign-discovery/scoring-algorithm.md

Defaults are configurable in `app.config.settings` (read at call time).
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.services.discovery.models import ScoreResult

logger = logging.getLogger(__name__)


def _get_cfg(key: str, default: Any) -> Any:
    """Read a config value from settings, with a default."""
    return getattr(settings, key, default)


def score_campaign(campaign: Any) -> ScoreResult:
    """Score a Campaign (DB row) using its spec + metadata + source_instructions.

    `campaign` must have `.spec` (dict), `.source_metadata` (dict),
    `.source_instructions` (str | None), `.source_provider` (str).
    """
    spec = campaign.spec if isinstance(campaign.spec, dict) else {}
    meta = campaign.source_metadata if isinstance(campaign.source_metadata, dict) else {}
    instructions = (campaign.source_instructions or "") if hasattr(campaign, "source_instructions") else ""

    # 1. CPM score: prefer spec.cpm (already parsed), fallback to meta
    cpm_score = 0.0
    spec_cpm = spec.get("cpm_usd_per_1k")
    meta_cpm = meta.get("cpm_usd_per_1k")
    if isinstance(spec_cpm, (int, float)) and spec_cpm > 0:
        cpm_score = float(spec_cpm)
    elif isinstance(meta_cpm, (int, float)) and meta_cpm > 0:
        cpm_score = float(meta_cpm)
    if cpm_score <= 0:
        cpm_score = 0.0

    # 2. Prize pool
    pool_score = 0.0
    spec_pool = spec.get("prize_pool_usd")
    meta_pool = meta.get("prize_pool_usd")
    if isinstance(spec_pool, (int, float)) and spec_pool > 0:
        pool_score = float(spec_pool)
    elif isinstance(meta_pool, (int, float)) and meta_pool > 0:
        pool_score = float(meta_pool)
    if pool_score <= 0:
        pool_score = 0.0

    # 3. Difficulty (sum)
    difficulty = 0
    reasons: list[str] = []

    dur_min = spec.get("duration_min")
    if isinstance(dur_min, (int, float)) and dur_min < _get_cfg("duracion_min_dificil", 15.0):
        difficulty += 1
        reasons.append(f"duration_min={dur_min}<15")

    idioma = (spec.get("language") or "").lower().strip()
    idiomas_pen = _get_cfg(
        "idiomas_penalizacion",
        ["fr", "de", "ja", "ko", "zh", "ru"],
    )
    if idioma and idioma in idiomas_pen:
        difficulty += 2
        reasons.append(f"language={idioma} penalized")

    req_pen = _get_cfg(
        "requisitos_dificiles",
        [
            "subtitles", "captions", "voiceover", "talking head",
            "en español", "voice over", "lip sync",
        ],
    )
    instructions_lc = instructions.lower()
    for kw in req_pen:
        if kw.lower() in instructions_lc:
            difficulty += 1
            reasons.append(f"requirement:{kw}")

    # Asset externality
    asset_links = meta.get("asset_links") or []
    if any(_classify_for_difficulty(u) in ("drive", "dropbox", "mega") for u in asset_links):
        difficulty += 1
        reasons.append("external_auth_asset")

    if cpm_score <= 0:
        difficulty += 3
        reasons.append("no_cpm_parsed")
    if pool_score <= 0:
        difficulty += 2
        reasons.append("no_pool_parsed")

    difficulty = min(difficulty, 10)

    # 4. Final score
    if cpm_score <= 0 or pool_score <= 0:
        final = 0.0
    else:
        final = (cpm_score * pool_score) / (1 + difficulty)

    # 5. Decision
    cpm_min = _get_cfg("cpm_min_usd_per_1k", 0.50)  # Low-bar: tune up later
    pool_min = _get_cfg("prize_pool_min_usd", 5_000.0)  # Low-bar: tune up later
    interesting = cpm_score >= cpm_min and pool_score >= pool_min and final >= 1.0
    if not interesting:
        reasons.append(
            f"below_threshold: cpm={cpm_score:.2f}<{cpm_min} "
            f"pool={pool_score:.0f}<{pool_min} score={final:.2f}<1.0"
        )

    return ScoreResult(
        cpm_score=cpm_score,
        prize_pool_score=pool_score,
        difficulty_score=float(difficulty),
        final_score=final,
        interesting=interesting,
        reasons=reasons,
    )


def _classify_for_difficulty(url: str) -> str:
    """Quick classify for difficulty scoring (subset of asset_resolver)."""
    u = url.lower()
    if "drive.google.com" in u:
        return "drive"
    if "dropbox.com" in u:
        return "dropbox"
    if "mega.nz" in u:
        return "mega"
    return "external"
