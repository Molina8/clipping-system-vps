"""State transition extensions for job_service."""
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
    val = payload.get("asset_id") if isinstance(payload, dict) else None
    if not val:
        val = payload.get("source_asset_id") if isinstance(payload, dict) else None
    return val


def on_download_completed(
    db: Session, job: Job, result_data: dict
) -> None:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning("download job %s completed but no asset_id in payload", job.id)
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("download job %s: asset %s not found", job.id, asset_id)
        return

    _update_asset_status(db, asset, AssetStatus.DOWNLOADED.value)
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

    from app.services.download_payload_validation import (
        validate_download_result, MediaFormatInvalid,
    )
    meta = dict(asset.extra_metadata or {})
    try:
        validate_download_result(
            file_path=asset.local_path,
            mime_type=asset.mime_type or meta.get("mime_type"),
            result_data=result_data,
            kind=meta.get("kind"),
            source_name=meta.get("name"),
        )
    except MediaFormatInvalid as e:
        meta["skip_download"] = True
        meta["last_error_kind"] = "unsupported_media_format"
        meta["last_error_detail"] = str(e)[:500]
        meta["terminal_failed_at"] = _now().isoformat()
        meta["file_path_reported"] = (asset.local_path or "")[:500]
        asset.extra_metadata = meta
        _update_asset_status(db, asset, AssetStatus.FAILED.value)
        logger.warning(
            "asset %s NOT transcribed: invalid media format (%s) file_path=%s mime=%s",
            asset.id, e, asset.local_path, asset.mime_type,
        )
        return

    try:
        create_job(
            db,
            job_type="transcribe",
            payload={
                "asset_id": str(asset.id),
                "campaign_id": str(asset.campaign_id),
                "video": asset.local_path or asset.source_url,
                "video_path": asset.local_path or asset.source_url,
                "source_url": asset.source_url,
                "language": meta.get("language"),
            },
            priority=job.priority,
            max_attempts=job.max_attempts,
        )
        logger.info("auto-created transcribe job for asset %s (download %s done)", asset.id, job.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to auto-create transcribe job: %s", e)


def on_transcribe_completed(
    db: Session, job: Job, result_data: dict
) -> None:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning("transcribe job %s completed but no asset_id in payload", job.id)
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("transcribe job %s: asset %s not found", job.id, asset_id)
        return

    _update_asset_status(db, asset, AssetStatus.TRANSCRIBED.value)
    meta = dict(asset.extra_metadata or {})
    meta["transcription"] = result_data
    asset.extra_metadata = meta
    db.commit()
    db.refresh(asset)
    logger.info("transcription stored for asset %s", asset.id)

    try:
        from app.services.silent_clip_cutter import maybe_cut_silent
        cut = maybe_cut_silent(db, asset)
        logger.info("post-transcribe clip route asset=%s %s", asset.id, cut)
    except Exception as e:  # noqa: BLE001
        logger.exception("silent cut failed asset %s: %s", asset.id, e)


def on_render_completed(
    db: Session, job: Job, result_data: dict
) -> tuple[Optional[Clip], Optional[Job]]:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        return None, None
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        return None, None

    candidate_id = None
    if isinstance(job.payload, dict):
        cand = job.payload.get("candidate_id")
        if cand:
            try:
                cand_uuid = uuid.UUID(cand)
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

    qa_job: Optional[Job] = None
    try:
        qa_rules: dict[str, Any] = {}
        try:
            from app.models.campaign import Campaign
            campaign = db.get(Campaign, asset.campaign_id)
            if campaign is not None and isinstance(campaign.spec, dict):
                spec = campaign.spec
                if spec.get("duration_min") is not None:
                    qa_rules["min_duration"] = float(spec["duration_min"])
                if spec.get("duration_max") is not None:
                    qa_rules["max_duration"] = float(spec["duration_max"])
                extra = spec.get("extra") or {}
                if isinstance(extra, dict):
                    extra_qa = extra.get("qa_rules") or {}
                    if isinstance(extra_qa, dict):
                        for key in ("width", "height", "min_fps", "require_audio", "codec"):
                            if key in extra_qa:
                                qa_rules[key] = extra_qa[key]
        except Exception as e:  # noqa: BLE001
            logger.warning("could not load QA rules from campaign %s: %s", asset.campaign_id, e)

        qa_payload: dict[str, Any] = {
            "clip_id": str(clip.id),
            "asset_id": str(asset.id),
            "campaign_id": str(asset.campaign_id),
            "file_path": clip.file_path,
        }
        if qa_rules:
            qa_payload["rules"] = qa_rules
        qa_job = create_job(db, job_type="qa", payload=qa_payload, priority=job.priority, max_attempts=job.max_attempts)
        clip.qa_job_id = qa_job.id
        db.commit()
        db.refresh(clip)
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to auto-create qa job: %s", e)
    return clip, qa_job


def on_qa_completed(db: Session, job: Job, result_data: dict) -> Optional[Clip]:
    clip_id = job.payload.get("clip_id") if isinstance(job.payload, dict) else None
    if not clip_id:
        return None
    try:
        clip = db.get(Clip, uuid.UUID(clip_id))
    except (TypeError, ValueError):
        return None
    if clip is None:
        return None

    raw_status = (result_data.get("status") or "review").lower()
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
    if qa_status == ClipQAStatus.PASS.value:
        try:
            from app.services.clip_storage_service import set_clip_location
            set_clip_location(db, clip.id, "pending_upload", final_path_worker=(result_data or {}).get("final_path_worker"))
        except Exception as e:  # noqa: BLE001
            logger.exception("clip %s step18 failed: %s", clip.id, e)
    return clip


TERMINAL_ERROR_KINDS = frozenset({
    "not_a_video", "youtube_channel", "youtube_playlist", "instagram_profile",
    "instagram_story_unavailable", "instagram_post_unavailable", "tiktok_account",
    "channel_page", "404_not_found", "403_forbidden", "external_404",
    "external_non_video", "page_not_video", "html_instead_of_media", "media_too_small",
})


def _classify_job_error(job: Job) -> tuple[str | None, str | None]:
    result = job.result if isinstance(job.result, dict) else {}
    err = result.get("error") if isinstance(result.get("error"), dict) else result
    kind = err.get("kind") if isinstance(err, dict) else None
    detail = err.get("detail") if isinstance(err, dict) else None
    if not kind and job.error_message:
        em = str(job.error_message)
        for k in TERMINAL_ERROR_KINDS:
            if em.startswith(k):
                return k, em
    return (str(kind) if kind else None, detail)


def on_job_failed(db: Session, job: Job) -> None:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        return
    if asset.status not in (AssetStatus.PENDING.value, AssetStatus.DOWNLOADED.value):
        return
    error_kind, error_detail = _classify_job_error(job)
    asset.status = AssetStatus.FAILED.value
    meta = dict(asset.extra_metadata or {})
    if error_kind and error_kind in TERMINAL_ERROR_KINDS:
        meta["skip_download"] = True
        meta["last_error_kind"] = error_kind
        meta["last_error_detail"] = (error_detail or "")[:500]
        meta["terminal_failed_at"] = _now().isoformat()
        asset.extra_metadata = meta
    elif meta.get("last_error_kind"):
        meta.pop("skip_download", None)
        meta.pop("last_error_kind", None)
        meta.pop("last_error_detail", None)
        meta.pop("terminal_failed_at", None)
        asset.extra_metadata = meta
    db.commit()
