"""Clip selection agent — Steps 12-13 of architecture_flow.md.

Step 12: pick up transcribed assets.
Step 13: build prompt -> call LLM -> parse proposals -> validate them
         against campaign rules -> persist valid ones as Candidates.

The agent is idempotent: re-running on the same asset leaves the
previous candidates in place (it just adds new ones and updates
`asset.extra_metadata['clip_selection_at']`). For "clean re-run" use
the DELETE on the candidates table separately.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.campaign_engine.normalizer import normalize
from app.campaign_engine.parser import parse_instructions
from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign
from app.schemas.candidate import CandidateCreate
from app.services.candidate_service import bulk_create_candidates

from .llm_client import LLMClient, MockLLMClient
from .models import ClipSelectionResponse
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .validator import filter_valid

logger = logging.getLogger(__name__)


def _strip_json_fences(raw: str) -> str:
    """Strip markdown code fences that LLMs sometimes add to JSON responses.

    LLMs (especially Anthropic-compatible APIs like MiniMax) often wrap
    JSON output in triple-backtick code fences. This helper removes the
    opening and closing fences and returns the inner JSON string so
    pydantic's model_validate_json can parse it.

    Examples (after Python escape interpretation):
        json + {"x": 1} + ```  ->  {"x": 1}
        ``` + {"x": 1} + ```    ->  {"x": 1}
        {"x": 1}              ->  {"x": 1}  (unchanged)
    """
    s = raw.strip()
    if not s.startswith("```"):
        return s
    # Drop the opening fence line (```json, ```JSON, or ```)
    nl = s.find("\n")
    if nl < 0:
        return s
    s = s[nl + 1:]
    # Drop the closing fence if present
    if s.endswith("```"):
        s = s[:-3].strip()
    return s




class ClipSelectionAgent:
    """Orchestrates clip selection for one asset at a time."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or MockLLMClient()

    def run(self, db: Session, asset_id: str, top_n: int = 5) -> Dict[str, Any]:
        """Process a single asset end-to-end.

        Proposals from Minimax are ranked (highest score first) by the
        SYSTEM_PROMPT contract. We sort and take only the top_n best.

        Returns a summary dict with counts and the persisted candidates.
        Raises ValueError if the asset is missing or not yet transcribed.
        """
        # --- 1. Load asset + campaign -------------------------------------
        try:
            asset_uuid = uuid.UUID(str(asset_id))
        except (TypeError, ValueError):
            raise ValueError(f"invalid asset_id: {asset_id}")

        asset = db.get(Asset, asset_uuid)
        if asset is None:
            raise ValueError(f"asset {asset_id} not found")
        if asset.status != AssetStatus.TRANSCRIBED.value:
            raise ValueError(
                f"asset {asset_id} is not transcribed "
                f"(current status: {asset.status})"
            )
        logger.info(
            "clip_selection.start asset=%s campaign_id=%d model=%s",
            asset_id, asset.campaign_id,
            getattr(self.llm_client, 'model', '?') if self.llm_client else '?',
        )
        logger.info(
            "clip_selection.start asset=%s campaign_id=%d model=%s",
            asset_id, asset.campaign_id,
            getattr(self.llm_client, 'model', '?') if self.llm_client else '?',
        )

        logger.info(
            "clip_selection.start asset=%s campaign_id=%d",
            asset_id, asset.campaign_id,
        )

        campaign = db.get(Campaign, asset.campaign_id)
        if campaign is None:
            raise ValueError(
                f"campaign {asset.campaign_id} for asset {asset_id} not found"
            )

        # --- 2. Derive NormalizedSpec from source_instructions -----------
        hints = parse_instructions(campaign.source_instructions or "")
        spec = normalize(hints, campaign.source_provider)
        spec_dict = spec.model_dump()

        # --- 2b. Pull campaign brief (if any) from extra_metadata --------
        # A campaign may have one or more 'brief' assets (PDFs / Google Docs
        # extracted by brief_extractor). We pick the most recently extracted
        # one with the largest char_count as the canonical brief for this
        # campaign and feed it to the LLM so it has the *real* rules.
        campaign_brief: dict | None = None
        try:
            brief_assets = (
                db.query(Asset)
                .filter(
                    Asset.campaign_id == campaign.id,
                    Asset.asset_type == "brief",
                )
                .all()
            )
            for ba in brief_assets:
                em = ba.extra_metadata or {}
                b = em.get("brief")
                if not isinstance(b, dict):
                    continue
                if not (b.get("text") or "").strip():
                    continue
                if campaign_brief is None or int(b.get("char_count") or 0) > int(
                    campaign_brief.get("char_count") or 0
                ):
                    campaign_brief = b
        except Exception as exc:  # noqa: BLE001
            logger.warning("campaign_brief load failed for %d: %s", campaign.id, exc)

        # --- 3. Pull transcription from asset.extra_metadata ------------
        transcription = (asset.extra_metadata or {}).get("transcription") or {}
        transcription_text = transcription.get("text", "") or ""
        segs = transcription.get("segments") or []

        # --- 4. Build prompt + call LLM ----------------------------------
        user_prompt = build_user_prompt(
            source_url=asset.source_url,
            duration_seconds=float(asset.duration_seconds or 0.0),
            transcription=transcription,
            spec=spec_dict,
            campaign_brief=campaign_brief,
        )
        try:
            raw_text, model_id = self.llm_client.complete(SYSTEM_PROMPT, user_prompt)
        except Exception as e:  # noqa: BLE001
            logger.exception("LLM call failed for asset %s: %s", asset_id, e)
            return self._summary(
                asset_id=asset_id,
                error=f"llm_failed: {e}",
            )

        # --- 5. Parse LLM response ---------------------------------------
        # LLMs (especially Minimax via Anthropic-compatible API) often wrap
        # their JSON output in markdown code fences like ```json\n{...}\n```.
        # Strip those before parsing so we don't fail on valid responses.
        try:
            response = ClipSelectionResponse.model_validate_json(
                _strip_json_fences(raw_text)
            )
        except (ValueError, json.JSONDecodeError) as e:
            logger.exception("LLM response parse failed for asset %s: %s", asset_id, e)
            return self._summary(
                asset_id=asset_id,
                model_id=model_id,
                error=f"parse_failed: {e}",
                raw=raw_text[:500],
            )

        # --- 5b. Rank proposals (highest score first) and take top_n ---
        # The SYSTEM_PROMPT requires Minimax to already return them ranked,
        # but we enforce ordering + dedup here as defense in depth.
        proposals = sorted(
            response.proposals, key=lambda p: float(p.score or 0.0),
            reverse=True,
        )[:max(1, top_n)]
        logger.info(
            "clip_selection.ranked asset=%s top_n=%d kept=%d scores=%s",
            asset_id, top_n, len(proposals),
            [round(float(p.score or 0.0), 2) for p in proposals],
        )

        # --- 6. Validate against spec ------------------------------------
        valid, rejected = filter_valid(proposals, spec, transcription_text)

        # --- 7. Compute covered text per valid proposal (for metadata) ---
        covered_texts: list[str] = [""] * len(valid)
        if segs:
            for i, p in enumerate(valid):
                chunks = [
                    str(s.get("text", ""))
                    for s in segs
                    if float(s.get("end", 0)) > p.start_time
                    and float(s.get("start", 0)) < p.end_time
                ]
                covered_texts[i] = " ".join(c for c in chunks if c).strip()

        # --- 8. Persist valid candidates ---------------------------------
        payloads = [
            CandidateCreate(
                campaign_id=asset.campaign_id,
                asset_id=asset.id,
                start_time=p.start_time,
                end_time=p.end_time,
                score=p.score,
                reasoning=p.reasoning,
                extra_metadata={
                    "matched_keywords": p.matched_keywords,
                    "covered_text": covered_texts[i],
                    "llm_model": model_id,
                    "source_provider": campaign.source_provider,
                    "selected_via": "clip_selection_agent",
                },
            )
            for i, p in enumerate(valid)
        ]
        created = bulk_create_candidates(db, payloads) if payloads else []

        # --- 9. Update asset.extra_metadata with a timestamp ------------
        meta = dict(asset.extra_metadata or {})
        meta["clip_selection_at"] = datetime.now(timezone.utc).isoformat()
        meta["clip_selection_count"] = len(created)
        meta["clip_selection_model"] = model_id
        asset.extra_metadata = meta
        db.commit()
        db.refresh(asset)

        # Log structured summary for debugging + audit
        logger.info(
            "clip_selection.done asset=%s generated=%d valid=%d persisted=%d model=%s",
            asset_id, len(proposals), len(valid), len(created), model_id or "?",
        )
        logger.info(
            "clip_selection.brief asset=%s has_brief=%s chars=%d urls=%d",
            asset_id,
            "yes" if campaign_brief else "no",
            int((campaign_brief or {}).get("char_count") or 0),
            len((campaign_brief or {}).get("feature_urls") or []),
        )
        logger.info(
            "clip_selection.done asset=%s generated=%d valid=%d persisted=%d model=%s",
            asset_id, len(proposals), len(valid), len(created), model_id or "?",
        )
        logger.info(
            "clip_selection.done asset=%s generated=%d valid=%d persisted=%d model=%s",
            asset_id, len(proposals), len(valid), len(created), model_id or "?",
        )
        return {
            "asset_id": str(asset_id),
            "generated": len(proposals),
            "valid": len(valid),
            "persisted": len(created),
            "candidates": [
                {
                    "id": str(c.id),
                    "start_time": c.start_time,
                    "end_time": c.end_time,
                    "score": c.score,
                    "reasoning": c.reasoning,
                }
                for c in created
            ],
            "rejected": [
                {"proposal": p.model_dump(), "reason": reason}
                for p, reason in rejected
            ],
            "llm_model": model_id,
        }

    @staticmethod
    def _summary(
        asset_id: str,
        model_id: Optional[str] = None,
        error: Optional[str] = None,
        raw: Optional[str] = None,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "asset_id": str(asset_id),
            "generated": 0,
            "valid": 0,
            "persisted": 0,
            "candidates": [],
            "rejected": [],
            "llm_model": model_id,
        }
        if error:
            out["error"] = error
        if raw:
            out["raw"] = raw
        return out
