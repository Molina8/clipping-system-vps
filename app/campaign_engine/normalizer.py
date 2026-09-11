"""Campaign Engine: rule_normalizer.

Architecture_flow.md Step 3: turn CampaignHints (parser output) into
the final NormalizedSpec that gets stored in Campaign.spec.

Fills defaults based on source_provider:
  - youtube: language=en, captions_required=True (auto-captions), format=9:16
  - tiktok: format=9:16, duration_min=15, duration_max=180
  - instagram: format=1:1, duration_min=3, duration_max=90
  - twitter: format=16:9, duration_min=15, duration_max=140
  - manual: format=9:16, language=es (default for Molina)

Any field the parser already filled wins over the default.
"""
from __future__ import annotations

from typing import Optional

from app.campaign_engine.models import CampaignHints, NormalizedSpec


# Technical QA rules derived from format/quality. These are the values the
# QA Worker (Windows) enforces with FFprobe. Stored under spec.extra["qa_rules"]
# so the VPS-side job_state_transitions can attach them to qa_payload.
# Defaults favor "vertical short-form" (9:16) which is what most clipping
# campaigns want.
_QA_RULES_BY_FORMAT: dict[str, dict] = {
    "9:16":  {"width": 1080, "height": 1920, "min_fps": 24.0, "require_audio": True, "codec": "h264"},
    "1:1":   {"width": 1080, "height": 1080, "min_fps": 24.0, "require_audio": True, "codec": "h264"},
    "16:9":  {"width": 1920, "height": 1080, "min_fps": 24.0, "require_audio": True, "codec": "h264"},
}


def _default_qa_rules(fmt: str) -> dict:
    return dict(_QA_RULES_BY_FORMAT.get(fmt, _QA_RULES_BY_FORMAT["9:16"]))


_PROVIDER_DEFAULTS: dict[str, dict] = {
    "twitter": {
        "format": "16:9",
        "language": "en",
        "duration_min": 15.0,
        "duration_max": 140.0,
        "captions_required": False,
    },
    "youtube": {
        "format": "9:16",
        "language": "en",
        "duration_min": 30.0,
        "duration_max": 60.0,
        "captions_required": True,
    },
    "instagram": {
        "format": "1:1",
        "language": "en",
        "duration_min": 3.0,
        "duration_max": 90.0,
        "captions_required": False,
    },
    "tiktok": {
        "format": "9:16",
        "language": "en",
        "duration_min": 15.0,
        "duration_max": 180.0,
        "captions_required": False,
    },
    "reddit": {
        "format": "16:9",
        "language": "en",
        "duration_min": 30.0,
        "duration_max": 600.0,
        "captions_required": False,
    },
    "twitch": {
        "format": "16:9",
        "language": "en",
        "duration_min": 30.0,
        "duration_max": 1800.0,
        "captions_required": False,
    },
    "manual": {
        "format": "9:16",
        "language": "es",
        "duration_min": 20.0,
        "duration_max": 60.0,
        "captions_required": True,
    },
    "other": {
        "format": "9:16",
        "language": "en",
        "duration_min": 30.0,
        "duration_max": 60.0,
        "captions_required": False,
    },
}


def normalize(hints: CampaignHints, source_provider: str) -> NormalizedSpec:
    """Merge parser hints with provider defaults. Hints win on conflict."""
    defaults = dict(_PROVIDER_DEFAULTS.get(source_provider, _PROVIDER_DEFAULTS["other"]))

    duration_min = (
        hints.duration_min
        if hints.duration_min is not None
        else defaults["duration_min"]
    )
    duration_max = (
        hints.duration_max
        if hints.duration_max is not None
        else defaults["duration_max"]
    )
    if duration_max <= duration_min:
        # enforce sane window
        duration_max = duration_min + 10.0

    captions_required = hints.captions_required or defaults["captions_required"]
    fmt = hints.format or defaults["format"]
    language = hints.language or defaults["language"]

    # Build qa_rules from format defaults. Hints can override them later
    # via the LLM step (Step 3 architecture_flow.md) which populates
    # spec.extra["qa_rules"] with richer per-campaign values.
    qa_rules = _default_qa_rules(fmt)
    extra: dict = {}
    if hints.extra_notes:
        extra["notes"] = list(hints.extra_notes)
    extra["qa_rules"] = qa_rules

    return NormalizedSpec(
        duration_min=duration_min,
        duration_max=duration_max,
        captions_required=captions_required,
        watermark_url=hints.watermark_url,
        format=fmt,
        language=language,
        keywords=hints.keywords,
        exclude_keywords=hints.exclude_keywords,
        source_provider=source_provider,
        extra=extra,
    )
