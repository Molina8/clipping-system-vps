"""Backlog processor for transcribed assets without clip_selection.

Triggered retroactively from `app.api.campaigns.enqueue_pipeline` (option A,
2026-09-11) so that assets that were transcribed BEFORE the
`on_transcribe_completed` handler ran for them (or for assets in any other
campaign whose transcription completed without invoking the agent) get
processed too.

This intentionally does NOT schedule a Python timer / loop — the openclaw
cron system owns all scheduling. The trigger piggybacks on the existing
`enqueue_pipeline` call which the cron already makes.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clip_selection.agent import ClipSelectionAgent
from app.models.asset import Asset, AssetStatus

logger = logging.getLogger(__name__)


def process_pending_clip_selections(
    db: Session, limit: int = 10
) -> Dict[str, Any]:
    """Process up to `limit` transcribed assets without `clip_selection_at`.

    Mirrors the logic of `app.api.clip_selection.process_all` but as a
    plain function (no FastAPI dependency) so it can be called from
    `enqueue_pipeline` without a request context.

    Returns a summary dict with the per-asset results (errors included
    so the caller can surface them in the API response if desired).
    """
    candidate_ids: List[uuid.UUID] = list(
        db.execute(
            select(Asset.id)
            .where(Asset.status == AssetStatus.TRANSCRIBED.value)
            .limit(limit * 2)
        ).scalars().all()
    )

    agent = ClipSelectionAgent()
    results: List[Dict[str, Any]] = []
    skipped = 0

    for aid in candidate_ids:
        asset = db.get(Asset, aid)
        if asset is None:
            continue
        # Skip assets already processed — defensive: the agent also writes
        # this timestamp, but the predicate here keeps the call idempotent.
        if (asset.extra_metadata or {}).get("clip_selection_at") is not None:
            skipped += 1
            continue
        try:
            results.append(agent.run(db, str(aid)))
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "process_pending_clip_selections failed for asset %s: %s",
                aid, e,
            )
            results.append({"asset_id": str(aid), "error": str(e)})
        if len(results) >= limit:
            break

    return {
        "processed": len(results),
        "skipped": skipped,
        "results": results,
    }
