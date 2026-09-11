"""State transition extensions for job_service.

Per architecture_flow.md:
  Step 9:  on download job completed -> asset.status = 'downloaded',
           auto-create transcribe job
  Step 11: on transcribe job completed -> asset.status = 'transcribed'
  Step 15: (manual) RENDER jobs for approved candidates
  Step 17: on render job completed -> create Clip record,
           auto-create QA job
  Step 19: on QA job completed -> update Clip qa_status and qa_at

These functions are invoked from the /worker/jobs/{id}/result and
/worker/jobs/{id}/fail endpoints in api/jobs.py (added separately).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetStatus
from app.models.clip import Clip, ClipQAStatus, ClipStatus
from app.models.job import Job, JobStatus  # JobType is a string, not enum
from app.services.job_service import create_job

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _update_asset_status(db: Session, asset: Asset, new_status: str) -> None:
    """Update asset.status and corresponding timestamp."""
    if new_status not in (
        AssetStatus.PENDING.value,
        AssetStatus.DOWNLOADED.value,
        AssetStatus.TRANSCRIBED.value,
        AssetStatus.FAILED.value,
    ):
        logger.warning("invalid asset status: %s", new_status)
        return
    asset.status = new_status
    now = _now()
    if new_status == AssetStatus.DOWNLOADED.value and asset.downloaded_at is None:
        asset.downloaded_at = now
    if new_status == AssetStatus.TRANSCRIBED.value and asset.transcribed_at is None:
        asset.transcribed_at = now
    db.commit()
    db.refresh(asset)
    logger.info("asset %s -> %s", asset.id, new_status)


def _asset_id_from_payload(payload: dict) -> Optional[str]:
    """Read asset_id from job payload. Returns None if not present."""
    val = payload.get("asset_id") if isinstance(payload, dict) else None
    if not val:
        # Some flows may put it under different key
        val = payload.get("source_asset_id") if isinstance(payload, dict) else None
    return val


def on_download_completed(
    db: Session, job: Job, result_data: dict
) -> None:
    """Step 9: download job completed successfully.

    - Mark asset.status = 'downloaded'
    - Create a 'transcribe' job for the same asset
    """
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning(
            "download job %s completed but no asset_id in payload",
            job.id,
        )
        return

    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("download job %s: asset %s not found", job.id, asset_id)
        return

    _update_asset_status(db, asset, AssetStatus.DOWNLOADED.value)

    # Update asset local_path / file_size / duration if present in result
    if "file_path" in result_data:
        asset.local_path = result_data["file_path"]
    if "file_size" in result_data:
        asset.file_size = int(result_data["file_size"])
    if "duration_seconds" in result_data:
        asset.duration_seconds = float(result_data["duration_seconds"])
    if "sha256" in result_data:
        asset.sha256 = result_data["sha256"]
    if "mime_type" in result_data:
        asset.mime_type = result_data["mime_type"]
    db.commit()
    db.refresh(asset)

    # Auto-create transcribe job
    try:
        # Worker contract: transcribe requires `video` (or legacy `video_path`).
        # Usamos `asset.local_path` que acabamos de actualizar desde el result.
        # Fallback a source_url solo si el Worker no devolvió `file_path` (job
        # legacy o Worker que aún no soporta el canon) — el Worker fallará
        # ruidosamente en ese caso, mejor que un silencio.
        transcribe_payload = {
            "asset_id": str(asset.id),
            "campaign_id": str(asset.campaign_id),
            "video": asset.local_path or asset.source_url,
            "video_path": asset.local_path or asset.source_url,  # alias
            "source_url": asset.source_url,                       # backwards-compat
            "language": (asset.extra_metadata or {}).get("language"),
        }
        create_job(
            db,
            job_type="transcribe",
            payload=transcribe_payload,
            priority=job.priority,
            max_attempts=job.max_attempts,
        )
        logger.info(
            "auto-created transcribe job for asset %s (download %s done)",
            asset.id, job.id,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to auto-create transcribe job: %s", e)


def on_transcribe_completed(
    db: Session, job: Job, result_data: dict
) -> None:
    """Step 11: transcribe job completed successfully.

    - Mark asset.status = 'transcribed'
    - Store transcription in asset.extra_metadata (so OpenClaw can read it in step 12-13)
    """
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning(
            "transcribe job %s completed but no asset_id in payload",
            job.id,
        )
        return

    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("transcribe job %s: asset %s not found", job.id, asset_id)
        return

    _update_asset_status(db, asset, AssetStatus.TRANSCRIBED.value)

    # Store transcription under extra_metadata['transcription']
    meta = dict(asset.extra_metadata or {})
    meta["transcription"] = result_data
    asset.extra_metadata = meta
    db.commit()
    db.refresh(asset)
    logger.info("transcription stored for asset %s", asset.id)

    # --- Auto clip_selection (fix cuello de botella asset->candidate) ---
    try:
        from app.clip_selection.agent import ClipSelectionAgent
        agent = ClipSelectionAgent()
        result = agent.run(db, asset_id=str(asset.id))
        logger.info("clip_selection auto-run asset %s: %s", asset.id, result)
    except Exception as e:  # noqa: BLE001
        logger.exception("clip_selection auto-run failed asset %s: %s", asset.id, e)


def on_render_completed(
    db: Session, job: Job, result_data: dict
) -> tuple[Optional[Clip], Optional[Job]]:
    """Step 17: render job completed successfully.

    - Create a Clip record from the result
    - Auto-create a QA job for the new clip
    Returns (clip, qa_job) for the caller to use.
    """
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning(
            "render job %s completed but no asset_id in payload",
            job.id,
        )
        return None, None

    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("render job %s: asset %s not found", job.id, asset_id)
        return None, None

    candidate_id = None
    if isinstance(job.payload, dict):
        cand = job.payload.get("candidate_id")
        if cand:
            try:
                cand_uuid = uuid.UUID(cand)
                # Validate the candidate exists in DB; ignore if not
                from app.models.candidate import Candidate
                if db.get(Candidate, cand_uuid) is not None:
                    candidate_id = cand_uuid
            except (TypeError, ValueError):
                candidate_id = None

    clip = Clip(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        candidate_id=candidate_id,
        render_job_id=job.id,
        file_path=result_data.get("file_path"),
        duration_seconds=result_data.get("duration_seconds"),
        file_size=result_data.get("file_size"),
        qa_status=ClipQAStatus.PENDING.value,
        qa_result={},
        status=ClipStatus.CREATED.value,
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)
    logger.info("clip %s created from render %s", clip.id, job.id)

    # Auto-create QA job
    qa_job: Optional[Job] = None
    try:
        # Build QA rules from campaign.spec (Step 3 architecture_flow.md):
        #   - Technical rules (width/height/min_fps/require_audio/codec) live in
        #     spec.extra["qa_rules"] (OpenClaw/MiniMax populated when generating the spec).
        #   - Duration window (spec.duration_min/max) is fused into rules.min_duration/
        #     max_duration so the QA Worker enforces the same window the campaign wants
        #     semantically. No redundant fields.
        qa_rules: dict[str, Any] = {}
        try:
            from app.models.campaign import Campaign
            campaign = db.get(Campaign, asset.campaign_id)
            if campaign is not None and isinstance(campaign.spec, dict):
                spec = campaign.spec
                # Duration window fused from CampaignSpec
                if spec.get("duration_min") is not None:
                    qa_rules["min_duration"] = float(spec["duration_min"])
                if spec.get("duration_max") is not None:
                    qa_rules["max_duration"] = float(spec["duration_max"])
                # Technical rules from spec.extra["qa_rules"]
                extra = spec.get("extra") or {}
                if isinstance(extra, dict):
                    extra_qa = extra.get("qa_rules") or {}
                    if isinstance(extra_qa, dict):
                        for key in (
                            "width",
                            "height",
                            "min_fps",
                            "require_audio",
                            "codec",
                        ):
                            if key in extra_qa:
                                qa_rules[key] = extra_qa[key]
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "could not load QA rules from campaign %s: %s",
                getattr(asset, "campaign_id", None),
                e,
            )

        qa_payload: dict[str, Any] = {
            "clip_id": str(clip.id),
            "asset_id": str(asset.id),
            "file_path": clip.file_path,
        }
        if qa_rules:
            qa_payload["rules"] = qa_rules
        qa_job = create_job(
            db,
            job_type="qa",
            payload=qa_payload,
            priority=job.priority,
            max_attempts=job.max_attempts,
        )
        clip.qa_job_id = qa_job.id
        db.commit()
        db.refresh(clip)
        logger.info("auto-created qa job %s for clip %s", qa_job.id, clip.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to auto-create qa job: %s", e)

    return clip, qa_job


def on_qa_completed(
    db: Session, job: Job, result_data: dict
) -> Optional[Clip]:
    """Step 19: QA job completed successfully.

    - Update clip.qa_status from result_data['status']
    - Store qa_result
    - Update clip.status accordingly (pass->approved, fail->rejected, review->review)
    """
    clip_id = None
    if isinstance(job.payload, dict):
        clip_id = job.payload.get("clip_id")
    if not clip_id:
        logger.warning(
            "qa job %s completed but no clip_id in payload",
            job.id,
        )
        return None

    try:
        clip_uuid = uuid.UUID(clip_id)
    except (TypeError, ValueError):
        return None

    clip = db.get(Clip, clip_uuid)
    if clip is None:
        logger.warning("qa job %s: clip %s not found", job.id, clip_id)
        return None

    raw_status = (result_data.get("status") or "review").lower()
    # normalize
    if raw_status in ("pass", "passed", "ok"):
        qa_status = ClipQAStatus.PASS.value
    elif raw_status in ("fail", "failed", "error"):
        qa_status = ClipQAStatus.FAIL.value
    else:
        qa_status = ClipQAStatus.REVIEW.value

    clip.qa_status = qa_status
    clip.qa_result = result_data
    clip.qa_at = _now()

    if qa_status == ClipQAStatus.PASS.value:
        clip.status = ClipStatus.APPROVED.value
    elif qa_status == ClipQAStatus.FAIL.value:
        clip.status = ClipStatus.REJECTED.value
    else:
        clip.status = ClipStatus.REVIEW.value

    clip.qa_job_id = job.id
    db.commit()
    db.refresh(clip)
    logger.info(
        "clip %s qa=%s status=%s", clip.id, qa_status, clip.status,
    )

    # ── Step 18: QA pass -> clip lives in pending_upload/ ──
    # The Worker copies the .mp4 to <storage>/clips/<campaign>/pending_upload/
    # and reports the new path via `result_data["final_path_worker"]`.
    # We just record it. If the Worker hasn't reported yet (legacy flow),
    # we still stamp `location='pending_upload'` so the clip is visible in
    # the per-campaign folder listing.
    if qa_status == ClipQAStatus.PASS.value:
        try:
            from app.services.clip_storage_service import set_clip_location
            final_path = None
            if isinstance(result_data, dict):
                final_path = result_data.get("final_path_worker")
            set_clip_location(db, clip.id, "pending_upload", final_path_worker=final_path)
            logger.info(
                "clip %s step18: moved to pending_upload (final_path=%s)",
                clip.id, final_path,
            )
        except Exception as e:  # noqa: BLE001
            # Never fail the QA handler because of step 18 — log and continue.
            logger.exception(
                "clip %s step18: failed to set pending_upload location: %s",
                clip.id, e,
            )

    return clip


def on_job_failed(db: Session, job: Job) -> None:
    """If a job fails, mark the related asset as 'failed' (only if the
    asset was related to a download/transcribe that was its first attempt)."""
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        return
    if asset.status in (
        AssetStatus.PENDING.value,
        AssetStatus.DOWNLOADED.value,
    ):
        asset.status = AssetStatus.FAILED.value
        db.commit()
        db.refresh(asset)
        logger.info("asset %s marked failed (job %s)", asset.id, job.id)
