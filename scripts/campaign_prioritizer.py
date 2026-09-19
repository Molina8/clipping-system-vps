#!/usr/bin/env python3
"""Campaign prioritizer cron entrypoint — Step 2 of architecture_flow.md.

Standalone script (no FastAPI context) so the OpenClaw scheduler can run it
as a `command` payload under cwd=/opt/clipping-system.

What it does:
  1. Pull all campaigns with status='ready' from PostgreSQL.
  2. Compute a composite priority score (no LLM, pure Python):
       score = 0.50 * norm_cpm
             + 0.30 * norm_prize_pool
             + 0.10 * spec_completeness
             + 0.10 * assets_available
  3. Sort desc, pick top N=PRIORITY_TOP as 'priority_campaigns',
     mark bottom (score == 0 OR cpm_missing) as 'low_priority'.
  4. Persist score + tier into campaigns.source_metadata (JSONB) and
     campaigns.spec.extra so clip-decider-tick can read it cheaply.
  5. Announce to Telegram ONLY if there were changes (new top tier,
     campaigns moved between tiers, or errors). Otherwise exit 0 silent.

Usage:
    cd /opt/clipping-system
    source venv/bin/activate
    python scripts/campaign_prioritizer.py            # full run
    python scripts/campaign_prioritizer.py --top 10   # top 10 priority
    python scripts/campaign_prioritizer.py --dry-run  # score only, no DB writes
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=os.environ.get("CAMPAIGN_PRIORITIZER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "campaign_prioritizer.log"),
    ],
)
logger = logging.getLogger("campaign_prioritizer")

# Scoring weights — tune here if Molina wants to rebalance.
W_CPM = 0.50
W_PRIZE = 0.30
W_SPEC = 0.10
W_ASSETS = 0.10

# How many campaigns to mark as 'priority' per run.
DEFAULT_TOP = 5

# Threshold below which a campaign is marked low_priority.
LOW_PRIORITY_THRESHOLD = 0.05


def _norm(value: float | None, max_value: float) -> float:
    """Normalize a value to [0, 1] using a known max. None/0 → 0."""
    if value is None or value <= 0 or max_value <= 0:
        return 0.0
    return min(value / max_value, 1.0)


def _spec_completeness(spec: dict) -> float:
    """Return [0,1] based on which spec fields are populated."""
    if not spec:
        return 0.0
    keys = ("format", "duration_min", "duration_max", "language")
    present = sum(1 for k in keys if spec.get(k))
    return present / len(keys)


def _load_campaigns(db) -> list:
    """All 'ready' campaigns (with or without assets)."""
    from app.models.campaign import Campaign, CampaignStatus
    from sqlalchemy import select

    q = select(Campaign).where(Campaign.status == CampaignStatus.READY.value)
    return list(db.execute(q).scalars())


def _assets_available_count(db, campaign_id: int) -> int:
    from app.models.asset import Asset
    from sqlalchemy import func, select

    q = select(func.count(Asset.id)).where(Asset.campaign_id == campaign_id)
    return int(db.execute(q).scalar() or 0)


def score_campaign(c: dict, max_cpm: float, max_prize: float) -> dict:
    """Compute score and tier for one campaign dict."""
    cpm = c.get("cpm_usd_per_1k")
    prize = c.get("prize_pool_usd")
    spec = c.get("spec") or {}
    assets_count = c.get("assets_count", 0) or 0

    n_cpm = _norm(cpm, max_cpm) if max_cpm > 0 else 0.0
    n_prize = _norm(prize, max_prize) if max_prize > 0 else 0.0
    n_spec = _spec_completeness(spec)
    # Assets: at least 1 asset gets full credit; 0 assets → 0.
    n_assets = 1.0 if assets_count > 0 else 0.0

    score = (
        W_CPM * n_cpm
        + W_PRIZE * n_prize
        + W_SPEC * n_spec
        + W_ASSETS * n_assets
    )

    if (cpm is None or cpm <= 0) and (prize is None or prize <= 0):
        tier = "low_priority"
    elif score < LOW_PRIORITY_THRESHOLD:
        tier = "low_priority"
    else:
        tier = "standard"

    return {
        "score": round(score, 4),
        "tier": tier,
        "components": {
            "norm_cpm": round(n_cpm, 3),
            "norm_prize": round(n_prize, 3),
            "spec_completeness": round(n_spec, 3),
            "assets_available": n_assets,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Campaign prioritizer cron entrypoint")
    p.add_argument("--top", type=int, default=DEFAULT_TOP, help="top N priority")
    p.add_argument("--dry-run", action="store_true", help="score only, no DB writes")
    args = p.parse_args()

    from app.db.database import SessionLocal

    db = SessionLocal()
    summary: dict = {
        "scored": 0,
        "priority_top": [],
        "low_priority_count": 0,
        "changes": [],
        "dry_run": args.dry_run,
    }
    try:
        campaigns = _load_campaigns(db)
        if not campaigns:
            logger.info("no ready campaigns to prioritize")
            print(json.dumps(summary))
            return 0

        # Build working dicts + asset counts in one pass.
        rows = []
        max_cpm = 0.0
        max_prize = 0.0
        for c in campaigns:
            # CPM / prize live in source_metadata (populated by Whop provider).
            md = c.source_metadata or {}
            cpm = md.get("cpm_usd_per_1k")
            prize = md.get("prize_pool_usd")
            # Fallback: try spec.extra if md missing.
            if cpm is None and isinstance(c.spec, dict):
                cpm = (c.spec.get("extra") or {}).get("cpm_usd_per_1k")
            if prize is None and isinstance(c.spec, dict):
                prize = (c.spec.get("extra") or {}).get("prize_pool_usd")

            assets_count = c.assets_count or _assets_available_count(db, c.id)
            rows.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "cpm_usd_per_1k": cpm,
                    "prize_pool_usd": prize,
                    "spec": c.spec if isinstance(c.spec, dict) else {},
                    "assets_count": assets_count,
                    "campaign_obj": c,
                    "existing_tier": (c.source_metadata or {}).get("priority_tier"),
                    "existing_score": (c.source_metadata or {}).get("priority_score"),
                }
            )
            if cpm and cpm > max_cpm:
                max_cpm = float(cpm)
            if prize and prize > max_prize:
                max_prize = float(prize)

        # Score everything.
        scored = []
        for r in rows:
            s = score_campaign(r, max_cpm, max_prize)
            r["score_info"] = s
            scored.append(r)

        # Sort: priority first by score desc, then low_priority last.
        scored.sort(key=lambda x: (0 if x["score_info"]["tier"] == "priority" else 1, -x["score_info"]["score"]))

        # Assign tiers.
        top_n = max(0, args.top)
        promoted = 0
        for i, r in enumerate(scored):
            old_tier = r["score_info"]["tier"]
            if i < top_n and r["score_info"]["score"] >= LOW_PRIORITY_THRESHOLD:
                r["score_info"]["tier"] = "priority"
            else:
                # Keep 'standard' or 'low_priority' as decided.
                pass
            new_tier = r["score_info"]["tier"]
            if old_tier != new_tier:
                promoted += 1
            r["promoted"] = promoted

        summary["scored"] = len(scored)
        summary["priority_top"] = [
            {
                "id": r["id"],
                "name": r["name"],
                "score": r["score_info"]["score"],
                "cpm": r["cpm_usd_per_1k"],
                "prize": r["prize_pool_usd"],
            }
            for r in scored
            if r["score_info"]["tier"] == "priority"
        ]
        summary["low_priority_count"] = sum(
            1 for r in scored if r["score_info"]["tier"] == "low_priority"
        )

        # Persist (unless dry-run) + detect changes.
        changes = []
        for r in scored:
            old_tier = r["existing_tier"]
            old_score = r["existing_score"]
            new_tier = r["score_info"]["tier"]
            new_score = r["score_info"]["score"]
            if old_tier == new_tier and old_score == new_score:
                continue
            changes.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "from": old_tier,
                    "to": new_tier,
                    "score": new_score,
                }
            )
            if not args.dry_run:
                c = r["campaign_obj"]
                md = dict(c.source_metadata or {})
                md["priority_score"] = new_score
                md["priority_tier"] = new_tier
                md["priority_components"] = r["score_info"]["components"]
                c.source_metadata = md
                spec = dict(c.spec or {})
                extra = dict(spec.get("extra") or {})
                extra["priority_score"] = new_score
                extra["priority_tier"] = new_tier
                spec["extra"] = extra
                c.spec = spec

        if not args.dry_run and changes:
            db.commit()
            logger.info("prioritizer persisted %d tier changes", len(changes))

        summary["changes"] = changes

        # Announce policy: only print if priority_top is non-empty
        # (first run / first scoring) OR if there are tier changes.
        if summary["priority_top"] or changes:
            print(json.dumps(summary))
        else:
            # Quiet success — print nothing so cron stays silent.
            logger.info("no priority changes; staying silent")
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("campaign_prioritizer failed: %s", e)
        summary["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(summary))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
