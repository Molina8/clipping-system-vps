"""Candidate lifecycle (Step 14 -> Step 15 of architecture_flow.md).

Step 14 [OPENCLAW CRON/AGENTE]: validates that candidates comply with the
campaign rules; if they do, marks them as approved. Step 15 [VPS BACKEND]
auto-creates a RENDER job for each approved candidate.

This module holds the pure logic for the two transitions:
  approve_candidate(db, candidate_id) -> dict
  reject_candidate(db, candidate_id, reason=None) -> Candidate | None

It re-validates against the current NormalizedSpec (so if rules changed
since the candidate was proposed, the candidate can be rejected even if
it was originally accepted by the agent). Idempotent on re-approve.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.campaign_engine.normalizer import normalize
from app.campaign_engine.parser import parse_instructions
from app.clip_selection.models import ClipProposal
from app.clip_selection.validator import validate_proposal
from app.models.asset import Asset
from app.models.candidate import Candidate, CandidateStatus
from app.models.campaign import Campaign
from app.services.job_service import create_job

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build_spec_for_campaign(campaign: Campaign):
    """Re-derive NormalizedSpec from current campaign.source_instructions."""
    hints = parse_instructions(campaign.source_instructions or "")
    return normalize(hints, campaign.source_provider)


def _build_proposal_from_candidate(candidate: Candidate) -> ClipProposal:
    """Convert a Candidate row into a ClipProposal for validation."""
    return ClipProposal(
        start_time=float(candidate.start_time),
        end_time=float(candidate.end_time),
        score=float(candidate.score or 0.0),
        reasoning=candidate.reasoning or "",
        matched_keywords=list(
            (candidate.extra_metadata or {}).get("matched_keywords", [])
        ),
    )


def _get_transcription_text(asset: Asset) -> str:
    """Get the transcription text from an asset (for exclude_keywords check)."""
    transcription = (asset.extra_metadata or {}).get("transcription") or {}
    return transcription.get("text", "") or ""


def _find_render_job_for_candidate(
    db: Session, candidate_id: uuid.UUID
) -> Optional[Any]:
    """Find an existing render job that points to this candidate_id.

    MVP: scan render jobs and filter in Python. Replace with JSONB index
    query if volume grows (jobs.payload has a GIN index already).
    """
    from app.models.job import Job

    cand_str = str(candidate_id)
    rows = db.execute(
        select(Job).where(Job.job_type == "render")
    ).scalars().all()
    for j in rows:
        if isinstance(j.payload, dict) and j.payload.get("candidate_id") == cand_str:
            return j
    return None


def approve_candidate(
    db: Session,
    candidate_id: uuid.UUID,
    *,
    max_attempts: int = 1,
    priority: int = 5,
) -> Dict[str, Any]:
    """Validate and approve a candidate.

    Flow:
      - Load candidate + asset + campaign.
      - Re-derive NormalizedSpec from current source_instructions.
      - Validate duration window + exclude_keywords.
      - On failure: mark candidate as 'rejected' with reason.
      - On success: mark as 'approved' and auto-create a RENDER job
        with payload ready for the Windows Worker (Step 16).

    Idempotent: if the candidate is already approved AND has a render
    job, returns the same render_job_id with `idempotent: True`.
    """
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise ValueError(f"candidate {candidate_id} not found")

    # --- Idempotency short-circuit --------------------------------------
    if candidate.status == CandidateStatus.APPROVED.value:
        existing = _find_render_job_for_candidate(db, candidate_id)
        if existing is not None:
            logger.info(
                "candidate %s already approved (render job %s)",
                candidate_id, existing.id,
            )
            return {
                "candidate_id": str(candidate_id),
                "status": "approved",
                "render_job_id": str(existing.id),
                "idempotent": True,
            }

    # --- Load related entities -------------------------------------------
    asset = db.get(Asset, candidate.asset_id)
    if asset is None:
        raise ValueError(
            f"asset {candidate.asset_id} not found for candidate {candidate_id}"
        )
    campaign = db.get(Campaign, candidate.campaign_id)
    if campaign is None:
        raise ValueError(
            f"campaign {candidate.campaign_id} not found for candidate {candidate_id}"
        )

    # --- Validate against current spec ----------------------------------
    spec = _build_spec_for_campaign(campaign)
    proposal = _build_proposal_from_candidate(candidate)
    transcription_text = _get_transcription_text(asset)

    ok, reason = validate_proposal(proposal, spec, transcription_text)

    meta = dict(candidate.extra_metadata or {})

    if not ok:
        # Reject
        candidate.status = CandidateStatus.REJECTED.value
        meta["rejected_reason"] = reason
        meta["rejected_at"] = _now().isoformat()
        candidate.extra_metadata = meta
        db.commit()
        db.refresh(candidate)
        logger.info("candidate %s rejected: %s", candidate_id, reason)
        return {
            "candidate_id": str(candidate_id),
            "status": "rejected",
            "reason": reason,
        }

    # --- Approve and create RENDER job ----------------------------------
    candidate.status = CandidateStatus.APPROVED.value
    meta["approved_at"] = _now().isoformat()
    candidate.extra_metadata = meta
    db.commit()
    db.refresh(candidate)

    render_payload = {
        "candidate_id": str(candidate.id),
        "asset_id": str(asset.id),
        "campaign_id": int(campaign.id),
        "source_url": asset.source_url,
        "local_path": asset.local_path,
        # Worker contract: requiere "input_video" (path local en el Worker).
        # El Worker ya descargó el asset en el paso 8; local_path apunta
        # a esa misma copia sincronizada por el Asset Resolver.
        "input_video": asset.local_path,
        # Worker en esta versión lee payload["start"]/payload["end"];
        # añadimos alias sin romper start_time/end_time (compatibilidad).
        "start": float(candidate.start_time),
        "end": float(candidate.end_time),
        "start_time": float(candidate.start_time),
        "end_time": float(candidate.end_time),
        "format": spec.format,
        "captions_required": bool(spec.captions_required),
        "watermark_url": spec.watermark_url,
        "language": spec.language,
    }

    try:
        render_job = create_job(
            db,
            job_type="render",
            payload=render_payload,
            priority=priority,
            max_attempts=max_attempts,
        )
        meta["render_job_id"] = str(render_job.id)
        candidate.extra_metadata = meta
        db.commit()
        db.refresh(candidate)
        logger.info(
            "candidate %s approved, render job %s created",
            candidate_id, render_job.id,
        )
        return {
            "candidate_id": str(candidate_id),
            "status": "approved",
            "render_job_id": str(render_job.id),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "failed to create render job for candidate %s: %s",
            candidate_id, e,
        )
        # Roll back approval so the user can retry
        candidate.status = CandidateStatus.PENDING.value
        meta.pop("approved_at", None)
        meta["render_job_error"] = str(e)
        candidate.extra_metadata = meta
        db.commit()
        db.refresh(candidate)
        return {
            "candidate_id": str(candidate_id),
            "status": "error",
            "reason": f"render_job_failed: {e}",
        }


def reject_candidate(
    db: Session,
    candidate_id: uuid.UUID,
    reason: Optional[str] = None,
) -> Optional[Candidate]:
    """Mark a candidate as rejected. Returns the row or None if missing."""
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        return None
    if candidate.status == CandidateStatus.RENDERED.value:
        raise ValueError(
            f"cannot reject candidate {candidate_id}: already rendered"
        )
    candidate.status = CandidateStatus.REJECTED.value
    meta = dict(candidate.extra_metadata or {})
    meta["rejected_reason"] = reason or "manual"
    meta["rejected_at"] = _now().isoformat()
    candidate.extra_metadata = meta
    db.commit()
    db.refresh(candidate)
    logger.info("candidate %s rejected (manual): %s", candidate_id, reason)
    return candidate
