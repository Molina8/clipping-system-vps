#!/usr/bin/env python3
"""
scripts/clip_scanner.py
=======================
End-to-end clip candidate generator (OpenClaw CRON, architecture_flow.md
steps 12+13).

For each asset in `transcribed` status, runs ClipSelectionAgent
(which uses HttpLLMClient -> MiniMax portal) to propose candidate
clips. Persists valid candidates into the `candidates` table. Optionally
auto-approves candidates above a score threshold (step 14), which
triggers the VPS to auto-create RENDER + QA jobs (steps 15-19).

Usage:
    cd /opt/clipping-system
    source venv/bin/activate
    python scripts/clip_scanner.py                       # scan all transcribed
    python scripts/clip_scanner.py --asset <uuid>        # one specific asset
    python scripts/clip_scanner.py --auto-approve 0.7    # auto-approve score>=0.7
    python scripts/clip_scanner.py --limit 5             # max 5 assets per run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

from app.clip_selection.agent import ClipSelectionAgent
from app.clip_selection.llm_client import build_http_llm_client
from app.config import settings
from app.db.database import SessionLocal
from app.models.asset import Asset, AssetStatus
from sqlalchemy import select

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("clip_scanner")


def list_transcribed_assets(db, asset_id):
    q = select(Asset).where(Asset.status == AssetStatus.TRANSCRIBED.value)
    if asset_id:
        q = q.where(Asset.id == asset_id)
    return list(db.execute(q.order_by(Asset.created_at.asc())).scalars())


def maybe_auto_approve(db, candidates, threshold):
    """Auto-approve candidates with score >= threshold via candidate_lifecycle.

    `candidates` here are summary dicts from agent.run() (NOT ORM Candidate
    objects), so we read score from `cand["score"]` not `cand.extra_metadata`.
    """
    from app.services.candidate_lifecycle import approve_candidate
    approved = 0
    for cand in candidates:
        score = float((cand or {}).get("score") or 0.0)
        if score >= threshold:
            cand_id = cand.get("id")
            try:
                # FIX: convert str UUID -> uuid.UUID; remove invalid 'approve=True' kwarg
                approve_candidate(db, uuid.UUID(cand_id))
                approved += 1
                logger.info("auto-approved candidate %s (score=%.2f)", cand_id, score)
            except Exception as exc:
                logger.warning("auto-approve failed for %s: %s", cand_id, exc)
    db.commit()
    return approved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", help="process only this asset UUID")
    parser.add_argument(
        "--auto-approve",
        type=float,
        metavar="THRESHOLD",
        help="auto-approve candidates with score >= THRESHOLD",
    )
    parser.add_argument("--limit", type=int, default=10,
                        help="max assets to process per run (default 10)")
    parser.add_argument("--top-n", type=int, default=5, dest="top_n",
                        help="max candidates per asset to persist, ranked by score (default 5)")
    args = parser.parse_args()

    try:
        client = build_http_llm_client()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 2

    logger.info(
        "HttpLLMClient ready: base=%s model=%s",
        settings.anthropic_base_url, settings.anthropic_model,
    )

    agent = ClipSelectionAgent(llm_client=client)

    with SessionLocal() as db:
        assets = list_transcribed_assets(db, args.asset)[: args.limit]
        if not assets:
            logger.info("No transcribed assets to process.")
            return 0

        logger.info("Found %d transcribed asset(s) to scan.", len(assets))

        overall_start = time.time()
        summaries = []
        total_persisted = 0
        total_approved = 0

        for asset in assets:
            asset_id = str(asset.id)
            logger.info("Processing asset %s (campaign_id=%d)...", asset_id, asset.campaign_id)
            t0 = time.time()
            try:
                summary = agent.run(db, asset_id, top_n=args.top_n)
            except Exception as exc:
                logger.exception("agent.run raised for %s: %s", asset_id, exc)
                continue
            summary["elapsed_seconds"] = round(time.time() - t0, 2)
            summaries.append(summary)
            total_persisted += summary.get("persisted", 0)

            if args.auto_approve is not None and summary.get("candidates"):
                approved = maybe_auto_approve(
                    db, summary["candidates"], args.auto_approve
                )
                total_approved += approved

        elapsed = round(time.time() - overall_start, 2)
        logger.info(
            "Done. %d assets, %d candidates persisted, %d auto-approved, %.2fs total.",
            len(assets), total_persisted, total_approved, elapsed,
        )

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for s in summaries:
        print(json.dumps(s, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
