"""Duration-only clip windows for assets without usable speech."""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.asset import Asset
from app.models.campaign import Campaign
from app.models.candidate import Candidate
from app.models.job import Job

logger = logging.getLogger(__name__)

MIN_SPEECH_CHARS = 24
MIN_SPEECH_WORDS = 6
DEFAULT_MIN = 15.0
DEFAULT_MAX = 35.0
MIN_BYTES_FOR_UNKNOWN_DURATION = 2_000_000


def transcript_text(tx: Any) -> str:
    if not isinstance(tx, dict):
        return ""
    parts = []
    if isinstance(tx.get("text"), str):
        parts.append(tx["text"])
    segs = tx.get("segments") or tx.get("chunks") or []
    if isinstance(segs, list):
        for s in segs:
            if isinstance(s, dict) and s.get("text"):
                parts.append(str(s["text"]))
            elif isinstance(s, str):
                parts.append(s)
    return " ".join(parts).strip()


def is_speech(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) < MIN_SPEECH_CHARS:
        return False
    words = re.findall(r"[A-Za-zÀ-ɏ0-9']+", cleaned)
    return len(words) >= MIN_SPEECH_WORDS


def asset_duration(asset: Asset, tx: Any) -> float:
    if asset.duration_seconds:
        try:
            return float(asset.duration_seconds)
        except (TypeError, ValueError):
            pass
    if isinstance(tx, dict) and tx.get("duration"):
        try:
            return float(tx["duration"])
        except (TypeError, ValueError):
            pass
    segs = (tx or {}).get("segments") if isinstance(tx, dict) else []
    end = 0.0
    if isinstance(segs, list):
        for s in segs:
            if isinstance(s, dict):
                for key in ("end", "end_time"):
                    try:
                        end = max(end, float(s.get(key) or 0))
                    except (TypeError, ValueError):
                        pass
    return end


def campaign_duration_window(campaign: Campaign | None) -> tuple[float, float]:
    rules, spec = {}, {}
    if campaign is not None:
        meta = campaign.source_metadata or {}
        if isinstance(meta, dict):
            rules = meta.get("rules") or {}
        spec = campaign.spec or {}
    dmin = (
        rules.get("duration_min") or rules.get("min_duration")
        or (spec.get("duration_min") if isinstance(spec, dict) else None)
        or DEFAULT_MIN
    )
    dmax = (
        rules.get("duration_max") or rules.get("max_duration")
        or (spec.get("duration_max") if isinstance(spec, dict) else None)
        or DEFAULT_MAX
    )
    try:
        dmin, dmax = float(dmin), float(dmax)
    except (TypeError, ValueError):
        dmin, dmax = DEFAULT_MIN, DEFAULT_MAX
    if dmax <= dmin:
        dmax = dmin + 10
    return dmin, dmax


def windows(duration: float, dmin: float, dmax: float, file_size) -> list[tuple[float, float]]:
    if duration <= 1:
        try:
            size = int(file_size or 0)
        except (TypeError, ValueError):
            size = 0
        if size >= MIN_BYTES_FOR_UNKNOWN_DURATION:
            return [(0.0, round(dmax, 2))]
        return []
    if duration <= dmax:
        return [(0.0, round(duration, 2))]
    first = (0.0, round(dmax, 2))
    start2 = max(dmax * 0.9, duration * 0.45)
    end2 = min(duration, start2 + dmax)
    if end2 - start2 < dmin or abs(start2 - first[0]) < dmin:
        return [first]
    return [first, (round(start2, 2), round(end2, 2))]


def _render_in_flight(db: Session) -> bool:
    return (
        db.query(Job)
        .filter(Job.job_type == "render")
        .filter(Job.status.in_(("pending", "assigned", "processing")))
        .first()
    ) is not None


def maybe_cut_silent(db: Session, asset: Asset) -> dict[str, Any]:
    tx = (asset.extra_metadata or {}).get("transcription") or {}
    text = transcript_text(tx)
    if is_speech(text):
        return {"kind": "speech", "created": 0}

    existing = db.query(Candidate).filter(Candidate.asset_id == asset.id).count()
    if existing:
        return {"kind": "silent", "created": 0, "skipped": "already_has_candidates"}

    campaign = db.get(Campaign, asset.campaign_id)
    dmin, dmax = campaign_duration_window(campaign)
    dur = asset_duration(asset, tx)
    wins = windows(dur, dmin, dmax, asset.file_size)
    if not wins:
        return {"kind": "silent", "created": 0, "skipped": "no_duration"}

    created = []
    first = None
    for start, end in wins:
        c = Candidate(
            campaign_id=asset.campaign_id,
            asset_id=asset.id,
            start_time=start,
            end_time=end,
            score=0.5,
            reasoning="silent/gameplay: duration windows, no LLM",
            extra_metadata={"source": "duration_cut", "kind": "silent"},
            status="pending",
        )
        db.add(c)
        db.flush()
        if first is None:
            first = c
        created.append({"id": str(c.id), "start": start, "end": end})
    db.commit()

    approved = None
    if first is not None and not _render_in_flight(db):
        try:
            from app.services.candidate_lifecycle import approve_candidate
            approved = approve_candidate(db, first.id)
        except Exception as e:  # noqa: BLE001
            logger.exception("auto-approve silent failed asset=%s: %s", asset.id, e)
            approved = {"status": "error", "reason": str(e)}
    else:
        approved = {"status": "deferred", "reason": "render_in_flight"}

    logger.info("silent cut asset=%s windows=%s approve=%s", asset.id, created, approved)
    return {
        "kind": "silent",
        "created": len(created),
        "candidates": created,
        "approved": approved,
    }
