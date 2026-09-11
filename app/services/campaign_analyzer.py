"""Campaign analyzer — Steps 2 + 3 of architecture_flow.md (VPS-side wiring).

This module is the VPS-side entrypoint that turns a `draft` Campaign into a
`ready` Campaign with a populated `spec` (CampaignSpec JSONB).

OpenClaw + Mini­Max own the *decision* of which campaigns are interesting
and the *rich extraction* of rules from `source_instructions`. This module:

  1. Runs the local rule-based parser (`parser.parse_instructions`) as a
     deterministic baseline so a campaign always gets *some* spec.
  2. Calls Mini­Max (HttpLLMClient via the configured Anthropic-compatible
     endpoint) to optionally enrich `spec.extra["qa_rules"]` with
     per-campaign technical rules. If Mini­Max is unreachable, the local
     baseline (with provider-derived defaults) is persisted as-is.
  3. Persists the merged spec, transitions status to `ready` (or keeps
     `draft` if spec is empty / insufficient).

The whole pipeline is idempotent: re-running on the same campaign
overwrites `spec` and resets status to `ready` if it had moved past.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaign_engine.models import NormalizedSpec
from app.campaign_engine.normalizer import normalize
from app.campaign_engine.parser import parse_instructions
from app.models.campaign import Campaign, CampaignStatus

logger = logging.getLogger(__name__)


# Mini­Max prompt: enrich technical QA rules for the campaign format/duration.
_ENRICH_PROMPT = """You are Mini­Max, a video QA rules expert. Given a campaign
spec (format, duration window, language) and its source provider, return ONLY
a JSON object with technical QA rules for the QA Worker (FFprobe-based).

Keys allowed (all optional):
  - width (int): required pixel width
  - height (int): required pixel height
  - min_fps (float): minimum frames per second
  - require_audio (bool): whether audio stream is required
  - codec (string): expected video codec name (e.g. "h264", "hevc")

Return ONLY the JSON object. No prose. If the campaign is ambiguous, return {{}}.

Campaign provider: {provider}
Campaign format: {format}
Duration window (s): {duration_min} - {duration_max}
Language: {language}
"""


def _build_local_spec(campaign: Campaign) -> NormalizedSpec:
    """Run parser + normalizer locally, no LLM."""
    hints = parse_instructions(campaign.source_instructions or "")
    return normalize(hints, campaign.source_provider)


def _try_llm_enrich(
    spec: NormalizedSpec,
    *,
    api_key: str,
    base_url: str,
    model: str,
    version: str,
    timeout_s: float = 8.0,
) -> dict[str, Any]:
    """Best-effort Mini­Max call to enrich qa_rules. Returns {} on failure."""
    if not api_key:
        return {}
    try:
        import urllib.request
        import urllib.error

        body = json.dumps(
            {
                "model": model,
                "max_tokens": 256,
                "system": (
                    "You are a strict JSON generator. Output only valid JSON. "
                    "No markdown, no prose."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": _ENRICH_PROMPT.format(
                            provider=spec.source_provider,
                            format=spec.format,
                            duration_min=spec.duration_min,
                            duration_max=spec.duration_max,
                            language=spec.language or "",
                        ),
                    }
                ],
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/v1/messages",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": version,
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        text = ""
        for part in data.get("content", []):
            if isinstance(part, dict) and part.get("type") == "text":
                text += part.get("text", "")
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return {}
        allowed = {"width", "height", "min_fps", "require_audio", "codec"}
        return {k: v for k, v in obj.items() if k in allowed}
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as e:
        logger.info("Mini­Max enrich unavailable, using local defaults: %s", e)
        return {}


def analyze_campaign(
    db: Session,
    campaign: Campaign,
    *,
    settings: Any = None,
) -> dict[str, Any]:
    """Analyze one campaign: derive spec, optionally enrich via LLM, persist."""
    if campaign is None:
        return {"campaign_id": None, "status": "missing", "spec": {}}

    local_spec = _build_local_spec(campaign)
    spec_dict = local_spec.model_dump()

    extra = dict(spec_dict.get("extra") or {})
    local_qa = dict(extra.get("qa_rules") or {})
    enriched_from = "local"

    if settings is not None:
        llm_overrides = _try_llm_enrich(
            local_spec,
            api_key=getattr(settings, "anthropic_api_key", ""),
            base_url=getattr(settings, "anthropic_base_url", "https://api.minimax.io/anthropic"),
            model=getattr(settings, "anthropic_model", "minimax-m3"),
            version=getattr(settings, "anthropic_version", "2023-06-01"),
        )
        if llm_overrides:
            local_qa.update(llm_overrides)
            extra["qa_rules"] = local_qa
            extra["qa_rules_source"] = "llm"
            spec_dict["extra"] = extra
            enriched_from = "llm"

    campaign.spec = spec_dict
    has_min_duration = spec_dict.get("duration_min") is not None
    has_format = bool(spec_dict.get("format"))
    if has_min_duration and has_format:
        campaign.status = CampaignStatus.READY.value
    else:
        campaign.status = CampaignStatus.DRAFT.value
    db.commit()
    db.refresh(campaign)

    logger.info(
        "campaign %s analyzed: status=%s enriched_from=%s format=%s "
        "duration=%s-%s",
        campaign.id,
        campaign.status,
        enriched_from,
        spec_dict.get("format"),
        spec_dict.get("duration_min"),
        spec_dict.get("duration_max"),
    )

    return {
        "campaign_id": campaign.id,
        "status": campaign.status,
        "spec": spec_dict,
        "enriched_from": enriched_from,
    }


def analyze_due_campaigns(
    db: Session,
    *,
    settings: Any = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Analyze all draft campaigns that have source_instructions."""
    q = (
        select(Campaign)
        .where(Campaign.status == CampaignStatus.DRAFT.value)
        .where(Campaign.source_instructions.is_not(None))
        .limit(limit)
    )
    rows = list(db.execute(q).scalars())
    results: list[dict[str, Any]] = []
    for c in rows:
        try:
            results.append(analyze_campaign(db, c, settings=settings))
        except Exception as e:  # noqa: BLE001
            logger.exception("analyze_campaign failed for %s: %s", c.id, e)
            results.append(
                {"campaign_id": c.id, "status": "error", "error": str(e)}
            )
    return results
